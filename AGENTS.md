# AGENTS.md

IGCZ: a compact, byte-exact encoding of IGC flight logs (paragliding GPS tracks).

## Layout

- `experiments/`: Python reference implementation and analysis tools. `igcz.py` defines the format.
- `src/`: Zig library (`igcz.zig`) and CLI (`main.zig`). Must read and write the same files as the Python one.
- `web/`: static converter page for GitHub Pages, runs `src/wasm.zig` (built by `zig build wasm`, not committed).
- `experiments/primer.txt`: deflate preset dictionary, shared by both (embedded in Zig via `build.zig`).

## Commands

```sh
zig build test                                   # Zig tests (Zig 0.16, std.Io API)
zig build -Doptimize=ReleaseFast                 # CLI in zig-out/bin/igcz
./zig-out/bin/igcz check <files or folders>      # round trip + sizes + speed, reads .gz
zig build wasm                                   # web/igcz.wasm for the browser converter
cd experiments && python3 -m unittest            # Python tests (no -I: it breaks local imports)
cd experiments && python3 bench.py --corpus --sample 1000   # results/corpus/summary.md
```

## Corpus

- `../open_igc_database/igc_gz/`: about 186k gzipped flights (override with `IGC_CORPUS`).
- `corpus.py` splits by filename hash: 1% `train` (only for building the primer), 99% `test` (benchmarks).
- Full corpus run: 53 min in Python on 11 processes (measured), about 11 min single-threaded in Zig (estimated from 100 MB/s).

## Format rules

- Decoding must give back the exact input bytes, whatever the input. Anything not a regular B-line is kept as raw text.
- Any change to the format or to `primer.txt` needs a new `VERSION` in `igcz.py` and `version` in `src/igcz.zig`.
- A change goes into both implementations, then gets a cross-check: Python files decode in Zig and Zig files decode in Python, on a corpus sample.
- Encoded files are not byte-identical between the two: Zig's deflate differs from zlib (about 0.1% bigger). Compare by decoding, not by bytes.
- Midnight rollover (time jumps back more than 12 h) is refused on purpose. Negative altitudes are supported.
- The Zig encoder mirrors Python's lenient `int()` (`pyInt`) so both pick the same extension layout.

## Workflow

- Measure every compression idea on a corpus sample before keeping it, and revert ideas that don't pay.
- Commits: English `type(scope): description`, no tool signature or Co-Authored-By line. Never push.
