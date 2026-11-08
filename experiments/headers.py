"""Compare ways of storing the non-fix lines ("other lines"), in bytes per flight.

usage: python3 headers.py [folder]

Flights are split in two halves: anything learned from data (the primer text)
comes from the first half, sizes are measured on the second half only.
"""

import lzma
import sys
import zlib
from collections import Counter
from pathlib import Path

import arith
import igcz


def other_text(raw):
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return "\n".join(line for line in text.split(eol) if igcz._try_parse(line) is None).encode("latin-1")


def build_primer(texts, budget=4096):
    """Typical header text: the most common lines first, then one example per record kind."""
    lines = Counter()
    kinds = {}
    for t in texts:
        for line in set(t.decode("latin-1").split("\n")):
            if line[:1] in ("G", "F", "B"):
                continue  # random or per-flight: nothing to share
            lines[line] += 1
            kinds.setdefault(line[:5], line)
    out, size = [], 0
    for line in [l for l, c in lines.most_common() if c > 1] + list(kinds.values()):
        if line in out or size + len(line) + 1 > budget:
            continue
        out.append(line)
        size += len(line) + 1
    return "\n".join(out).encode("latin-1")


class ByteModel:
    """Order-n byte model: 8 yes/no answers per byte, odds chosen by the previous n bytes."""

    def __init__(self, order):
        self.order = order
        self.tables = {}

    def code(self, enc, data, history=b""):
        h = history[-self.order :] if self.order else b""
        for byte in data:
            probs = self.tables.setdefault(h, [arith.PROB_INIT] * 256)
            node = 1
            for k in range(7, -1, -1):
                b = (byte >> k) & 1
                enc.bit(probs, node, b)
                node = node * 2 + b
            h = (h + bytes([byte]))[-self.order :] if self.order else b""


class NullEncoder(arith.RangeEncoder):
    """Feeds the model without counting: used to learn from the primer."""


def arith_size(text, order, primer=b""):
    model = ByteModel(order)
    if primer:
        warmup = arith.RangeEncoder()
        model.code(warmup, primer)
    enc = arith.RangeEncoder()
    model.code(enc, text, history=primer)
    return len(enc.finish())


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data" / "flights"
    texts = [other_text(p.read_bytes()) for p in sorted(folder.iterdir())]
    train, test = texts[0::2], texts[1::2]
    primer = build_primer(train)
    print(f"primer: {len(primer)} bytes from {len(train)} flights; measuring on {len(test)} flights\n")

    methods = {
        "raw text (today)": lambda t: len(t),
        "zlib": lambda t: len(zlib.compress(t, 9)),
        "xz": lambda t: len(lzma.compress(t, preset=9 | lzma.PRESET_EXTREME)),
        "zlib + primer": lambda t: len(_zlib_dict(t, primer)),
        "arith order-0": lambda t: arith_size(t, 0),
        "arith order-1": lambda t: arith_size(t, 1),
        "arith order-2": lambda t: arith_size(t, 2),
        "arith order-1 + primer": lambda t: arith_size(t, 1, primer),
        "arith order-2 + primer": lambda t: arith_size(t, 2, primer),
        "arith order-3 + primer": lambda t: arith_size(t, 3, primer),
    }
    for name, size in methods.items():
        total = sum(size(t) for t in test)
        print(f"{name:24s} {total / len(test):7.0f} B per flight")


def _zlib_dict(text, primer):
    c = zlib.compressobj(9, zdict=primer)
    return c.compress(text) + c.flush()


if __name__ == "__main__":
    main()
