# Contributing

Thanks for helping! Bug reports, print results, settings profiles and code are all welcome.

* **Bugs and ideas:** open an issue. For support problems a screenshot of MiniPrint and of the slicer preview
  helps a lot. Please do not attach models you are not allowed to share.
* **Print results:** tell us printer, nozzle, layer height, filament and the settings profile you used. Good
  and bad results both help tune the defaults.
* **Settings profiles:** see [profiles/README.md](profiles/README.md).

## Working on the code

MiniPrint is plain Python (numpy, scipy, Pillow) plus a browser page without frameworks or a build step.
[docs/architecture.md](docs/architecture.md) explains what is where and has recipes for common changes.

    git clone https://github.com/danunachas/MiniPrint && cd MiniPrint
    python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
    python -m pip install -e ".[dev]"

    python -m miniprint.gui                              # the app, at http://127.0.0.1:8765/
    python -m pytest                                     # the tests (about a minute)
    ruff check .                                         # the linter

Edit a `.py` file and restart the app; edit a file in `miniprint/web/` and just reload the page.

Before a pull request:

* `python -m pytest` and `ruff check .` pass (GitHub runs both on every pull request);
* a change to behaviour comes with a test - `tests/test_supports.py` for the geometry,
  `tests/test_gui_api.py` for anything the page asks the server;
* if you changed the supports or the orientation search, run `python tools/run_corpus.py <a folder of your
  STL files>` before and after and mention what changed (tips, unsupported points, islands, time);
* wording in the page follows what slicers say (support raft, overhang angle, on build plate only), and every
  setting has a `data-tip` explanation.

Code style: 120 columns, names that say what a thing is, comments that say *why*. Meshes are `(V, F)` numpy
pairs in millimetres with Z up. No new dependencies without a very good reason - "runs anywhere Python runs"
is a feature.

The Windows launcher is in `launcher/` (Go, standard library only):
`GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o MiniPrint.exe ./launcher`

By contributing you agree that your contribution is licensed under the GPL-3.0-or-later, like the rest of
MiniPrint.
