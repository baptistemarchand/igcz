/** Thrown when encoding or decoding fails. */
export class IgczError extends Error {
  /** The Zig error name: "NotIgcz", "UnsupportedVersion", "Corrupt", "MidnightRollover", "NumberTooLarge" or "OutOfMemory". */
  code: string;
}

/** Encodes an IGC file (text or bytes) to IGCZ. Decoding gives back the exact same bytes. */
export function encode(igc: string | Uint8Array | ArrayBuffer): Promise<Uint8Array>;

/** Decodes an IGCZ file back to the exact IGC bytes it was made from. */
export function decode(igcz: Uint8Array | ArrayBuffer): Promise<Uint8Array>;

/** The IGCZ format version this build reads and writes. */
export function version(): Promise<number>;
