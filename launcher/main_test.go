package main

import (
	"archive/zip"
	"os"
	"path/filepath"
	"testing"
)

func TestVersionOrder(t *testing.T) {
	if !less("1.9.0", "1.10.0") || less("2.0", "1.99") || !stable("2.1.3") || stable("2.2.0rc1") {
		t.Fatal("version helpers")
	}
}

func TestUnzipRejectsEscapes(t *testing.T) {
	d := t.TempDir()
	zp := filepath.Join(d, "a.zip")
	f, _ := os.Create(zp)
	w := zip.NewWriter(f)
	x, _ := w.Create("ok/inner.txt")
	x.Write([]byte("hi"))
	w.Close()
	f.Close()
	if err := unzip(zp, filepath.Join(d, "out")); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(d, "out", "ok", "inner.txt")); string(b) != "hi" {
		t.Fatal("content")
	}
	zp2 := filepath.Join(d, "b.zip")
	f2, _ := os.Create(zp2)
	w2 := zip.NewWriter(f2)
	y, _ := w2.Create("../evil.txt")
	y.Write([]byte("x"))
	w2.Close()
	f2.Close()
	if err := unzip(zp2, filepath.Join(d, "out2")); err == nil {
		t.Fatal("zip slip not caught")
	}
}
