"""Compare ways of storing each column, in bits per fix, without writing any file.

usage: python3 schemes.py [folder]

Guess ("predictor"):
  prev    guess = previous value             -> store the change (what IGCZ v1 does)
  slope   guess = previous value + previous change (keep going the same way)

Storage ("coder"):
  bytes   1+ whole bytes per number (IGCZ v1)
  rice    adaptive Rice code: a few bits per number, adapting to how big numbers are lately
  floor   order-0 entropy per flight: what a perfect simple coder could reach (not a real coder)
"""

import math
import sys
from collections import Counter
from pathlib import Path

from igcz import COLUMNS, UnsupportedFlight, encode
import igcz


ESCAPE = 16


def zigzag(n):
    return 2 * n if n >= 0 else -2 * n - 1


def residuals(values, predictor):
    out = []
    prev = prev_change = 0
    for v in values:
        guess = prev + prev_change if predictor == "slope" else prev
        out.append(v - guess)
        prev_change = v - prev
        prev = v
    return out


def bytes_bits(xs):
    total = 0
    for x in xs:
        z = zigzag(x)
        n = 1
        while z >= 128:
            z >>= 7
            n += 1
        total += 8 * n
    return total


class Rice:
    """Adaptive Rice (as in LOCO-I / JPEG-LS): k follows the recent average size."""

    def __init__(self):
        self.acc, self.count = 4, 1

    def cost(self, x):
        z = zigzag(x)
        k = 0
        while (self.count << k) < self.acc:
            k += 1
        q = z >> k
        self.acc += min(z, 1 << 16)
        self.count += 1
        if self.count == 32:  # forget old history so k follows changes in the flight
            self.acc >>= 1
            self.count >>= 1
        # escape: a rare huge number costs 16 one-bits plus 40 plain bits instead of q bits
        return q + 1 + k if q < ESCAPE else ESCAPE + 40


def rice_bits(xs):
    rice = Rice()
    return sum(rice.cost(x) for x in xs)


def rice_blocks_bits(xs, block=16):
    """Rice, plus one flag bit per block of numbers: 1 = all zero, nothing else stored."""
    rice = Rice()
    total = 0
    for i in range(0, len(xs), block):
        chunk = xs[i : i + block]
        total += 1
        if any(chunk):
            total += sum(rice.cost(x) for x in chunk)
    return total


def floor_bits(xs):
    c = Counter(xs)
    n = len(xs)
    return -sum(v * math.log2(v / n) for v in c.values())


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data" / "flights"
    totals = Counter()
    fixes = 0
    for path in sorted(folder.iterdir()):
        raw = path.read_bytes()
        try:
            encode(raw)
        except UnsupportedFlight:
            continue
        lines = raw.decode("latin-1").replace("\r\n", "\n").split("\n")
        parsed = [f for f in map(igcz._try_parse, lines) if f is not None]
        fixes += len(parsed)
        for name in COLUMNS:
            values = [getattr(f, name) for f in parsed]
            for predictor in ("prev", "slope"):
                r = residuals(values, predictor)
                totals[name, predictor, "bytes"] += bytes_bits(r)
                totals[name, predictor, "rice"] += rice_bits(r)
                totals[name, predictor, "rice16"] += rice_blocks_bits(r)
                totals[name, predictor, "floor"] += floor_bits(r)

    combos = [(p, c) for p in ("prev", "slope") for c in ("bytes", "rice", "rice16", "floor")]
    print(f"{fixes:,} fixes, bits per fix\n")
    print("| column | " + " | ".join(f"{p}+{c}" for p, c in combos) + " |")
    print("|---" * (len(combos) + 1) + "|")
    for name in COLUMNS + ("TOTAL",):
        names = COLUMNS if name == "TOTAL" else (name,)
        cells = [sum(totals[n, p, c] for n in names) / fixes for p, c in combos]
        print(f"| {name} | " + " | ".join(f"{x:.2f}" for x in cells) + " |")


if __name__ == "__main__":
    main()
