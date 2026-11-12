// MiniPrint.exe - Windows launcher for MiniPrint.
//
// Double-click it: the first start sets up a private Python runtime next to the program (the official
// "embeddable" Python from python.org plus numpy, scipy and Pillow from PyPI, about 60 MB, nothing is
// installed into Windows), then starts MiniPrint and opens it in the browser. Later starts take a second.
// Closing the console window stops MiniPrint.
//
// The release download from GitHub already contains the runtime, so it works without internet.
//
//	MiniPrint.exe            start
//	MiniPrint.exe --repair   download the runtime again
//
// Build (from any OS):  GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o MiniPrint.exe ./launcher
//
// SPDX-License-Identifier: GPL-3.0-or-later
package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	pyVersion = "3.12.10" // embeddable Python used for the runtime
	pyTag     = "cp312"   // wheels must match it
	port      = 8765      // MiniPrint's local web server
	appURL    = "http://127.0.0.1:8765/"
)

var packages = []string{"numpy", "scipy", "pillow"}

func main() {
	exe, err := os.Executable()
	if err != nil {
		fail(err)
	}
	root := filepath.Dir(exe)
	if _, err := os.Stat(filepath.Join(root, "miniprint", "server.py")); err != nil {
		fail(fmt.Errorf("the folder 'miniprint' was not found next to MiniPrint.exe (%s).\n"+
			"Keep MiniPrint.exe in the MiniPrint folder you downloaded", root))
	}
	rt := filepath.Join(root, "runtime")
	repair := len(os.Args) > 1 && (os.Args[1] == "--repair" || os.Args[1] == "/repair")
	if repair || !fileExists(filepath.Join(rt, ".ready")) {
		if err := setup(rt); err != nil {
			fail(fmt.Errorf("setting up the Python runtime failed: %v\n\n"+
				"Check your internet connection and start MiniPrint.exe again (or run it with --repair)", err))
		}
	}
	if portBusy() {
		alreadyRunning(root)
		return
	}
	py := filepath.Join(rt, "python.exe")
	fmt.Println()
	fmt.Println("  MiniPrint is running at " + appURL)
	fmt.Println("  It opens in your browser. Close this window to stop MiniPrint.")
	fmt.Println()
	cmd := exec.Command(py, "-m", "miniprint.server")
	cmd.Dir = root
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		fail(fmt.Errorf("MiniPrint stopped with an error: %v\n"+
			"If this keeps happening, start MiniPrint.exe --repair once", err))
	}
}

// ---------------------------------------------------------------------------------------------
// runtime setup
// ---------------------------------------------------------------------------------------------
func setup(rt string) error {
	if runtime.GOOS == "windows" && runtime.GOARCH != "amd64" {
		return errors.New("only 64-bit Windows (x64) is supported")
	}
	fmt.Println("First start: setting up MiniPrint's own Python runtime (about 60 MB, once).")
	tmp := rt + ".new"
	os.RemoveAll(tmp)
	if err := os.MkdirAll(tmp, 0o755); err != nil {
		return err
	}
	// 1. embeddable Python
	url := fmt.Sprintf("https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip", pyVersion, pyVersion)
	zf := filepath.Join(tmp, "python.zip")
	if err := download(url, zf, "", "Python "+pyVersion); err != nil {
		return err
	}
	if err := unzip(zf, tmp); err != nil {
		return err
	}
	os.Remove(zf)
	// sys.path: the standard library, site-packages and the MiniPrint folder (one level up)
	pth := fmt.Sprintf("python%s._pth", strings.TrimPrefix(pyTag, "cp"))
	content := fmt.Sprintf("python%s.zip\r\n.\r\nLib\\site-packages\r\n..\r\nimport site\r\n", strings.TrimPrefix(pyTag, "cp"))
	if err := os.WriteFile(filepath.Join(tmp, pth), []byte(content), 0o644); err != nil {
		return err
	}
	site := filepath.Join(tmp, "Lib", "site-packages")
	if err := os.MkdirAll(site, 0o755); err != nil {
		return err
	}
	// 2. numpy, scipy, Pillow: newest release that has a wheel for this Python on 64-bit Windows
	ready := "python=" + pyVersion + "\r\n"
	for _, p := range packages {
		ver, u, sum, err := pickWheel(p)
		if err != nil {
			return fmt.Errorf("%s: %v", p, err)
		}
		wf := filepath.Join(tmp, p+".whl")
		if err := download(u, wf, sum, fmt.Sprintf("%s %s", p, ver)); err != nil {
			return err
		}
		if err := unzip(wf, site); err != nil {
			return err
		}
		os.Remove(wf)
		ready += p + "=" + ver + "\r\n"
	}
	if err := os.WriteFile(filepath.Join(tmp, ".ready"), []byte(ready), 0o644); err != nil {
		return err
	}
	// swap in the finished runtime
	os.RemoveAll(rt)
	if err := os.Rename(tmp, rt); err != nil {
		return err
	}
	fmt.Println("Runtime ready.")
	return nil
}

type pypiFile struct {
	Filename    string            `json:"filename"`
	URL         string            `json:"url"`
	Digests     map[string]string `json:"digests"`
	Yanked      bool              `json:"yanked"`
	PackageType string            `json:"packagetype"`
}

func pickWheel(pkg string) (version, url, sha string, err error) {
	resp, err := httpGet("https://pypi.org/pypi/" + pkg + "/json")
	if err != nil {
		return
	}
	defer resp.Body.Close()
	var data struct {
		Releases map[string][]pypiFile `json:"releases"`
	}
	if err = json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return
	}
	var vers []string
	for v := range data.Releases {
		if stable(v) {
			vers = append(vers, v)
		}
	}
	sort.Slice(vers, func(i, j int) bool { return less(vers[j], vers[i]) }) // newest first
	want := "-" + pyTag + "-" + pyTag + "-win_amd64.whl"
	for _, v := range vers {
		for _, f := range data.Releases[v] {
			if f.PackageType == "bdist_wheel" && !f.Yanked && strings.HasSuffix(strings.ToLower(f.Filename), want) {
				return v, f.URL, f.Digests["sha256"], nil
			}
		}
	}
	err = fmt.Errorf("no wheel for Python %s on 64-bit Windows found on PyPI", pyVersion)
	return
}

func stable(v string) bool {
	for _, r := range v {
		if !(r == '.' || (r >= '0' && r <= '9')) {
			return false
		}
	}
	return v != ""
}

func less(a, b string) bool {
	pa, pb := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < len(pa) || i < len(pb); i++ {
		var x, y int
		if i < len(pa) {
			x, _ = strconv.Atoi(pa[i])
		}
		if i < len(pb) {
			y, _ = strconv.Atoi(pb[i])
		}
		if x != y {
			return x < y
		}
	}
	return false
}

// ---------------------------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------------------------
var client = &http.Client{Timeout: 15 * time.Minute}

func httpGet(url string) (*http.Response, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "MiniPrint-launcher")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != 200 {
		resp.Body.Close()
		return nil, fmt.Errorf("%s: HTTP %d", url, resp.StatusCode)
	}
	return resp, nil
}

type progress struct {
	w     io.Writer
	total int64
	done  int64
	label string
	last  time.Time
}

func (p *progress) Write(b []byte) (int, error) {
	n, err := p.w.Write(b)
	p.done += int64(n)
	if time.Since(p.last) > 300*time.Millisecond || (p.total > 0 && p.done >= p.total) {
		p.last = time.Now()
		if p.total > 0 {
			fmt.Printf("\r  %-24s %5.1f / %5.1f MB", p.label, float64(p.done)/1e6, float64(p.total)/1e6)
		} else {
			fmt.Printf("\r  %-24s %5.1f MB", p.label, float64(p.done)/1e6)
		}
	}
	return n, err
}

func download(url, dst, sha, label string) error {
	resp, err := httpGet(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	h := sha256.New()
	pr := &progress{w: io.MultiWriter(f, h), total: resp.ContentLength, label: label}
	_, err = io.Copy(pr, resp.Body)
	f.Close()
	fmt.Println()
	if err != nil {
		return err
	}
	if sha != "" && !strings.EqualFold(hex.EncodeToString(h.Sum(nil)), sha) {
		return fmt.Errorf("%s: download damaged (checksum mismatch)", label)
	}
	return nil
}

func unzip(src, dst string) error {
	r, err := zip.OpenReader(src)
	if err != nil {
		return err
	}
	defer r.Close()
	base, _ := filepath.Abs(dst)
	for _, f := range r.File {
		p := filepath.Join(base, filepath.FromSlash(f.Name))
		if !strings.HasPrefix(p, base+string(os.PathSeparator)) && p != base {
			return fmt.Errorf("bad path in archive: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(p, 0o755)
			continue
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(p)
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func portBusy() bool {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 400*time.Millisecond)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

// alreadyRunning: something answers on MiniPrint's port. If it is this very MiniPrint, just open the page; if
// it is another copy (an older version, or one started from another folder or with MiniPrint.pyw, which has no
// window), say so plainly - otherwise the user works in that other copy without knowing it.
func alreadyRunning(root string) {
	mine := ourVersion(root)
	running := runningVersion()
	if running != "" && running == mine {
		fmt.Println("MiniPrint is already running - opening it in your browser.")
		openBrowser(appURL)
		time.Sleep(2 * time.Second)
		return
	}
	what := "another program"
	if running != "" {
		what = "MiniPrint " + running
	} else if portAnswersHTTP() {
		what = "an older MiniPrint"
	}
	fmt.Println()
	fmt.Printf("  %s is already running on this computer (port %d), so this MiniPrint %s cannot start.\n", what, port, mine)
	fmt.Println("  It may have been started from another folder, or with MiniPrint.pyw, which has no window.")
	fmt.Println()
	fmt.Println("  To use this version: close the other one - its window, or in the Task Manager end")
	fmt.Println("  \"python\" / \"pythonw\" - and start MiniPrint.exe again.")
	fmt.Println()
	fmt.Println("Press Enter to close this window.")
	fmt.Scanln()
}

// ourVersion: __version__ from miniprint/__init__.py next to the program.
func ourVersion(root string) string {
	b, err := os.ReadFile(filepath.Join(root, "miniprint", "__init__.py"))
	if err != nil {
		return "?"
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "__version__") {
			if i, j := strings.Index(line, "\""), strings.LastIndex(line, "\""); i >= 0 && j > i {
				return line[i+1 : j]
			}
		}
	}
	return "?"
}

// runningVersion: the version the MiniPrint on the port reports ("" if it does not answer /api/health,
// which MiniPrint before 0.3.0 did not have).
func runningVersion() string {
	c := &http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get(appURL + "api/health")
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	var h struct {
		Version string `json:"version"`
	}
	if resp.StatusCode != 200 || json.NewDecoder(resp.Body).Decode(&h) != nil {
		return ""
	}
	return h.Version
}

func portAnswersHTTP() bool {
	c := &http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get(appURL + "api/info")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func openBrowser(url string) {
	if runtime.GOOS == "windows" {
		exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
	} else {
		exec.Command("xdg-open", url).Start()
	}
}

func fail(err error) {
	fmt.Println()
	fmt.Println("MiniPrint could not start:")
	fmt.Println("  " + strings.ReplaceAll(err.Error(), "\n", "\n  "))
	fmt.Println()
	fmt.Println("Press Enter to close this window.")
	fmt.Scanln()
	os.Exit(1)
}
