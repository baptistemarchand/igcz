# @baptistemrchnd/igcz

A compact, byte-exact encoding of IGC flight logs, about 20x smaller than the IGC file. This is the WebAssembly build of the [Zig library](https://github.com/baptistemarchand/igcz), so it runs in Node, browsers, workers and bundlers with no native dependency.

```sh
npm install @baptistemrchnd/igcz
```

```js
import { encode, decode } from "@baptistemrchnd/igcz";

const packed = await encode(igcBytesOrString); // Uint8Array
const igc = await decode(packed);              // Uint8Array, the exact original bytes
```

- `encode(igc)` takes a string, `Uint8Array` or `ArrayBuffer`. Anything that is not a regular B-line is kept as raw text, so any input round trips.
- `decode(igcz)` takes a `Uint8Array` or `ArrayBuffer`.
- `version()` gives the IGCZ format version of this build. Files from another version are refused.
- Failures reject with an `IgczError` whose `code` is `NotIgcz`, `UnsupportedVersion`, `Corrupt`, `MidnightRollover`, `NumberTooLarge` or `OutOfMemory`.

Files made here decode with the Zig and Python implementations, and the other way around. The format is described at the top of [`src/igcz.zig`](https://github.com/baptistemarchand/igcz/blob/main/src/igcz.zig). There is also a [converter in the browser](https://baptistemarchand.github.io/igcz/).

The package is one ES module with the wasm embedded, about 49 kB packed.

## License

0BSD
