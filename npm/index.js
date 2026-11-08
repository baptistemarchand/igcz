import wasmBase64 from "./igcz-wasm.js";

const messages = {
  NotIgcz: "not an IGCZ file",
  UnsupportedVersion: "IGCZ file from another format version",
  Corrupt: "truncated or damaged IGCZ file",
  MidnightRollover: "clock jumps back past midnight: not supported",
  NumberTooLarge: "a value is too large to encode",
  OutOfMemory: "out of memory",
};

export class IgczError extends Error {
  constructor(code) {
    super(messages[code] ?? code);
    this.name = "IgczError";
    /** The Zig error name, for example "NotIgcz" or "Corrupt". */
    this.code = code;
  }
}

function base64ToBytes(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

let instance;

// Compiled asynchronously on first use: browsers refuse to compile large modules synchronously
// on the main thread.
function load() {
  instance ??= WebAssembly.instantiate(base64ToBytes(wasmBase64)).then((r) => r.instance.exports);
  return instance;
}

function run(x, fn, bytes) {
  const size = bytes.length || 1;
  const ptr = x.alloc(size);
  if (!ptr) throw new IgczError("OutOfMemory");
  new Uint8Array(x.memory.buffer, ptr, bytes.length).set(bytes);
  const ok = x[fn](ptr, bytes.length);
  x.free(ptr, size);
  // Read after the call: memory may have grown and moved the buffer.
  const out = new Uint8Array(x.memory.buffer, x.resultPtr(), x.resultLen()).slice();
  if (!ok) throw new IgczError(new TextDecoder().decode(out));
  return out;
}

function toBytes(input) {
  if (typeof input === "string") return new TextEncoder().encode(input);
  if (input instanceof Uint8Array) return input;
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  throw new TypeError("expected a string, Uint8Array or ArrayBuffer");
}

/** Encodes an IGC file (text or bytes) to IGCZ. Decoding gives back the exact same bytes. */
export async function encode(igc) {
  return run(await load(), "encode", toBytes(igc));
}

/** Decodes an IGCZ file back to the exact IGC bytes it was made from. */
export async function decode(igcz) {
  return run(await load(), "decode", toBytes(igcz));
}

/** The IGCZ format version this build reads and writes. */
export async function version() {
  return (await load()).version();
}
