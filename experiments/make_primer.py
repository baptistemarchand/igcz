"""Build primer.txt: typical header text that encoder and decoder both know in advance.

usage: python3 make_primer.py   (reads the corpus "train" split, see corpus.py; writes primer.txt)

The primer lets deflate copy common phrases ("HFDTM100GPSDATUM:WGS-1984", logger
names...) instead of storing them in every file. It is part of the format:
changing it makes older files unreadable, so it gets a new format version.
"""

import zlib
from collections import Counter
from pathlib import Path

import corpus
import igcz

HERE = Path(__file__).parent
MIN_FLIGHTS = 3  # a line must appear in this many flights to be shared
PERSONAL_KEYS = ("PLT", "CM2", "GID", "CID")  # pilot, co-pilot, glider and competition IDs: keep the key only
BUDGETS = (2048, 4096, 8192, 16384, 32768)  # deflate can look back at most 32 KB


def other_lines(raw):
    text = raw.decode("latin-1")
    eol = "\r\n" if "\r\n" in text else "\n"
    return [line for line in text.split(eol) if igcz._try_parse(line) is None]


def shareable(line):
    if line[:1] in ("G", "F", "B", ""):
        return None  # signatures, satellite lists and broken fixes are unique to each flight
    if line[:1] == "H" and line[2:5] in PERSONAL_KEYS:
        return line.split(":", 1)[0] + ":" if ":" in line else line[:5]
    return line


def build(flights, budget):
    counts = Counter()
    for lines in flights:
        counts.update({s for s in map(shareable, lines) if s})
    chosen, size = [], 0
    for line, count in counts.most_common():
        if count < MIN_FLIGHTS or size + len(line) + 1 > budget:
            continue
        chosen.append(line)
        size += len(line) + 1
    # deflate copies from nearby text more cheaply, so the most common lines go last
    return "\n".join(reversed(chosen)).encode("latin-1")


def deflate_size(text, primer):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=primer)
    return len(c.compress(text) + c.flush())


def main():
    flights = [other_lines(corpus.read_flight(p)) for p in corpus.paths("train")]
    print(f"{len(flights)} training flights from the corpus")
    learn, check = flights[0::2], flights[1::2]
    check_texts = ["\n".join(lines).encode("latin-1") for lines in check]

    best = None
    for budget in BUDGETS:
        primer = build(learn, budget)
        size = sum(deflate_size(t, primer) for t in check_texts) / len(check_texts)
        print(f"budget {budget:6d}: primer {len(primer):6d} B -> {size:6.1f} B per flight")
        if best is None or size < best[1]:
            best = (budget, size)

    primer = build(flights, best[0])
    (HERE / "primer.txt").write_bytes(primer)
    print(f"wrote primer.txt: {len(primer)} bytes (budget {best[0]})")


if __name__ == "__main__":
    main()
