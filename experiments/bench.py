"""Convert every flight, check it comes back byte for byte, and write size and speed stats.

usage: python3 bench.py [folder]   (default data/flights)

Writes results/stats.csv (one row per flight) and results/summary.md.
Exits with code 1 if any flight does not come back identical.
"""

import csv
import gzip
import lzma
import sys
import time
from collections import Counter
from pathlib import Path

from igcz import UnsupportedFlight, decode, encode

HERE = Path(__file__).parent
RESULTS = HERE / "results"
OLD_IGCC_BYTES_PER_FIX = 16


def xz(data):
    return len(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "data" / "flights"
    paths = sorted(p for p in folder.iterdir() if p.is_file())
    RESULTS.mkdir(exist_ok=True)

    rows = []
    sections_total = Counter()
    failed, refused = [], []
    for i, path in enumerate(paths, 1):
        raw = path.read_bytes()
        sections = {}
        try:
            t0 = time.perf_counter()
            encoded = encode(raw, sections)
            t1 = time.perf_counter()
            decoded = decode(encoded)
            t2 = time.perf_counter()
        except UnsupportedFlight as e:
            print(f"ERROR {path.name}: {e}", file=sys.stderr)
            refused.append(path.name)
            continue
        if decoded != raw:
            print(f"FAIL {path.name}: decoded file differs from the original", file=sys.stderr)
            failed.append(path.name)
            continue

        sections_total.update(sections)
        fixes = sum(1 for line in raw.splitlines() if line.startswith(b"B"))
        rows.append({
            "file": path.name,
            "fixes": fixes,
            "igc": len(raw),
            "igc_gzip": len(gzip.compress(raw, 9)),
            "igc_xz": xz(raw),
            "igcc_v0": fixes * OLD_IGCC_BYTES_PER_FIX,
            "igcz": len(encoded),
            "igcz_xz": xz(encoded),
            "encode_ms": round((t1 - t0) * 1000, 2),
            "decode_ms": round((t2 - t1) * 1000, 2),
        })
        print(f"\r{i}/{len(paths)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    if rows:
        with open(RESULTS / "stats.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    summary = render_summary(rows, sections_total, len(paths), refused, failed)
    (RESULTS / "summary.md").write_text(summary)
    print(summary)
    sys.exit(1 if failed else 0)


def render_summary(rows, sections, file_count, refused, failed):
    total = lambda key: sum(r[key] for r in rows)
    fixes, igc = total("fixes"), total("igc")
    encode_s, decode_s = total("encode_ms") / 1000, total("decode_ms") / 1000

    out = [
        "# IGCZ benchmark",
        "",
        f"- Flights: {file_count} ({len(rows)} converted, {len(refused)} refused, {len(failed)} failed round trip)",
        f"- Fixes: {fixes:,}",
        "",
        "| Format | Total size | Bytes per fix | Smaller than IGC by |",
        "|---|---|---|---|",
    ]
    for label, key in [
        ("IGC (text)", "igc"),
        ("IGC + gzip", "igc_gzip"),
        ("IGC + xz", "igc_xz"),
        ("igcc v0 (16 B per fix)", "igcc_v0"),
        ("IGCZ", "igcz"),
        ("IGCZ + xz", "igcz_xz"),
    ]:
        size = total(key)
        out.append(f"| {label} | {size / 1e6:.2f} MB | {size / max(fixes, 1):.2f} | {igc / max(size, 1):.1f}x |")

    out += [
        "",
        "## Speed (Python, IGCZ only)",
        "",
        f"- Encode: {encode_s:.2f} s total, {igc / 1e6 / max(encode_s, 1e-9):.1f} MB of IGC per second",
        f"- Decode: {decode_s:.2f} s total, {igc / 1e6 / max(decode_s, 1e-9):.1f} MB of IGC per second",
        "",
        "## Where the IGCZ bytes go",
        "",
        "| Part | Size | Share |",
        "|---|---|---|",
    ]
    igcz = total("igcz")
    for name, size in sections.most_common():
        out.append(f"| {name} | {size / 1e6:.2f} MB | {100 * size / max(igcz, 1):.1f}% |")

    if refused:
        out += ["", "## Refused (midnight rollover)", ""] + [f"- {n}" for n in refused]
    if failed:
        out += ["", "## FAILED round trip", ""] + [f"- {n}" for n in failed]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
