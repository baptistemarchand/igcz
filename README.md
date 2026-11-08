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

Add it to your project:

```sh
zig fetch --save git+https://github.com/baptistemarchand/igcz#v0.1.0
```

```zig
// build.zig
const igcz = b.dependency("igcz", .{ .target = target, .optimize = optimize });
exe.root_module.addImport("igcz", igcz.module("igcz"));
```

Requires Zig 0.16. No C dependencies.

## JavaScript

```sh
npm install @baptistemrchnd/igcz
```

```js
import { encode, decode } from "@baptistemrchnd/igcz";

const packed = await encode(igcBytesOrString);
const igc = await decode(packed);
```

It is the same WebAssembly build as the web converter, packed in one ES module. See [`npm/`](npm/README.md).

## Command line

Install on Linux or macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/baptistemarchand/igcz/main/install.sh | sh
```

It installs to `~/.local/bin` (set `IGCZ_INSTALL_DIR` to change it, `IGCZ_VERSION` to pin a version) and checks the SHA-256 of the download. Windows builds are on the [releases page](https://github.com/baptistemarchand/igcz/releases). To build from source:

```sh
zig build -Doptimize=ReleaseFast
./zig-out/bin/igcz encode flight.igc flight.igcz
./zig-out/bin/igcz decode flight.igcz flight.igc
./zig-out/bin/igcz check flights/     # round trip every file, print sizes and speed
```

About 100 MB of IGC per second to encode and 200 MB/s to decode, on one thread.

## License

[0BSD](LICENSE): do whatever you want, no attribution required.

## Web converter

`web/` is a static page that converts files in the browser with a WebAssembly build of the library. It is deployed to GitHub Pages on every push to `main`.

```sh
zig build wasm                          # writes web/igcz.wasm
python3 -m http.server -d web           # then open http://localhost:8000
```

## Experiments

`experiments/` holds the Python reference implementation and the benchmarks used to design the format.
