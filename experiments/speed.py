"""Compare encode/decode speed of gzip, xz and IGCZ on the same flights, one thread.

usage: python3 speed.py [folder]   (default data/flights; build the Zig CLI first:
                                    zig build -Doptimize=ReleaseFast)

gzip and xz run through Python's zlib/liblzma bindings (C code; the call overhead
is negligible on whole files). IGCZ is timed by the Zig CLI (`igcz check`).
Speeds are MB of IGC text per second; each figure is the best of RUNS runs.
"""

import gzip
import lzma
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
IGCZ = HERE.parent / "zig-out" / "bin" / "igcz"
RUNS = 3


def best_time(fn, items):
    best = float("inf")
    for _ in range(RUNS):
        t0 = time.perf_counter()
        for item in items:
            fn(item)
        best = min(best, time.perf_counter() - t0)
    return best


def measure(name, compress, decompress, flights, mb):
    packed = [compress(raw) for raw in flights]
    size = sum(map(len, packed))
    enc = best_time(compress, flights)
    dec = best_time(decompress, packed)
    print(f"| {name} | {mb / enc:.1f} | {mb / dec:.1f} | {size / 1e6:.2f} MB |")


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "data" / "flights"
    paths = sorted(p for p in folder.iterdir() if p.is_file())
    flights = [p.read_bytes() for p in paths]
    mb = sum(map(len, flights)) / 1e6
    print(f"{len(flights)} flights, {mb:.2f} MB of IGC\n")
    print("| Format | Encode MB/s | Decode MB/s | Size |")
    print("|---|---|---|---|")
    for level in (6, 9):
        measure(f"gzip -{level}", lambda b, l=level: gzip.compress(b, l, mtime=0), gzip.decompress, flights, mb)
    for preset in (6, 9 | lzma.PRESET_EXTREME):
        label = "xz -6" if preset == 6 else "xz -9e"
        measure(label, lambda b, p=preset: lzma.compress(b, preset=p), lzma.decompress, flights, mb)

    best = None
    for _ in range(RUNS):
        out = subprocess.run([IGCZ, "check", folder], capture_output=True, text=True, check=True).stderr
        enc, dec = map(float, re.search(r"encode ([\d.]+) MB.*decode ([\d.]+) MB", out).groups())
        size = re.search(r"IGCZ ([\d.]+) MB", out).group(1)
        best = (max(enc, best[0]), max(dec, best[1]), size) if best else (enc, dec, size)
    print(f"| IGCZ (Zig) | {best[0]:.1f} | {best[1]:.1f} | {best[2]} MB |")


if __name__ == "__main__":
    main()
