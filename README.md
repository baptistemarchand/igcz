# IGCZ

A compact file format for IGC flight logs (20x smaller than IGC). 

## How it works

- B-records become columns of numbers (time, lat, lng, altitudes, extension fields).
- Each column guesses the next value from the previous ones, and an adaptive arithmetic coder stores the errors (often well under one bit).
- Every other line (headers, events, signature) is deflated with a preset dictionary of common header text.

The layout is described at the top of [`src/igcz.zig`](src/igcz.zig).

## Zig library

```zig
const igcz = @import("igcz");

const packed = try igcz.encode(gpa, igc_bytes);
defer gpa.free(packed);
const igc = try igcz.decode(gpa, packed);
defer gpa.free(igc);
```

Requires Zig 0.16. No C dependencies.

## Command line

```sh
zig build -Doptimize=ReleaseFast
./zig-out/bin/igcz encode flight.igc flight.igcz
./zig-out/bin/igcz decode flight.igcz flight.igc
./zig-out/bin/igcz check flights/     # round trip every file, print sizes and speed
```

About 100 MB of IGC per second to encode and 200 MB/s to decode, on one thread.

## Web converter

`web/` is a static page that converts files in the browser with a WebAssembly build of the library. It is deployed to GitHub Pages on every push to `main`.

```sh
zig build wasm                          # writes web/igcz.wasm
python3 -m http.server -d web           # then open http://localhost:8000
```

## Experiments

`experiments/` holds the Python reference implementation and the benchmarks used to design the format.
