"""Convert flights, check each comes back byte for byte, and write size and speed stats.

usage:
    python3 bench.py [folder]                 every file in a folder (default data/flights)
    python3 bench.py --corpus [--sample N]    the corpus "test" split (see corpus.py)
options:
    --xz          also measure IGC + xz and IGCZ + xz (slow)
    --workers N   parallel processes (default: all cores)
    --name NAME   results go to results/NAME/ (default: "folder" or "corpus")

Writes stats.csv (one row per flight) and summary.md. Exits with code 1 if any
flight does not come back identical.
"""

import argparse
import csv
import gzip
import lzma
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import corpus
from igcz import UnsupportedFlight, decode, encode

HERE = Path(__file__).parent
OLD_IGCC_BYTES_PER_FIX = 16


def xz(data):
    return len(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))


def convert(job):
    """Runs in a worker process. Returns ("ok", row, sections), ("refused", name, reason) or ("failed", name, why)."""
    path, with_xz = job
    name = path.name
    try:
        raw = corpus.read_flight(path)
    except Exception as e:
        return ("unreadable", name, f"{type(e).__name__}: {e}")
    sections = {}
    try:
        t0 = time.perf_counter()
        encoded = encode(raw, sections)
        t1 = time.perf_counter()
        decoded = decode(encoded)
        t2 = time.perf_counter()
    except UnsupportedFlight as e:
        return ("refused", name, str(e))
    except Exception as e:
        return ("failed", name, f"{type(e).__name__}: {e}")
    if decoded != raw:
        return ("failed", name, "decoded file differs from the original")

    fixes = sum(1 for line in raw.splitlines() if line.startswith(b"B"))
    gz_size = path.stat().st_size if name.endswith(".gz") else len(gzip.compress(raw, 9))
    row = {
        "file": name,
        "fixes": fixes,
        "igc": len(raw),
        "igc_gzip": gz_size,
        "igc_xz": xz(raw) if with_xz else "",
        "igcc_v0": fixes * OLD_IGCC_BYTES_PER_FIX,
        "igcz": len(encoded),
        "igcz_xz": xz(encoded) if with_xz else "",
        "encode_ms": round((t1 - t0) * 1000, 2),
        "decode_ms": round((t2 - t1) * 1000, 2),
    }
    return ("ok", row, sections)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default=str(HERE / "data" / "flights"))
    parser.add_argument("--corpus", action="store_true")
    parser.add_argument("--sample", type=int)
    parser.add_argument("--xz", action="store_true")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--name")
    args = parser.parse_args()

    if args.corpus:
        paths = corpus.paths("test", sample=args.sample)
        source = f"corpus test split ({corpus.CORPUS})"
    else:
        paths = sorted(p for p in Path(args.folder).iterdir() if p.is_file())
        source = args.folder
    out = HERE / "results" / (args.name or ("corpus" if args.corpus else "folder"))
    out.mkdir(parents=True, exist_ok=True)

    rows, sections_total, problems = [], Counter(), []
    started = time.perf_counter()
    with Pool(args.workers) as pool:
        jobs = [(p, args.xz) for p in paths]
        for i, result in enumerate(pool.imap_unordered(convert, jobs, chunksize=8), 1):
            if result[0] == "ok":
                rows.append(result[1])
                sections_total.update(result[2])
            else:
                problems.append(result)
                print(f"\n{result[0].upper()} {result[1]}: {result[2]}", file=sys.stderr)
            if i % 100 == 0 or i == len(paths):
                print(f"\r{i}/{len(paths)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    wall = time.perf_counter() - started

    rows.sort(key=lambda r: r["file"])
    if rows:
        with open(out / "stats.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    summary = render_summary(rows, sections_total, len(paths), problems, source, wall, args)
    (out / "summary.md").write_text(summary)
    print(summary)
    sys.exit(1 if any(p[0] == "failed" for p in problems) else 0)


def percentile(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def render_summary(rows, sections, file_count, problems, source, wall, args):
    total = lambda key: sum(r[key] for r in rows)
    fixes, igc = total("fixes"), total("igc")
    encode_s, decode_s = total("encode_ms") / 1000, total("decode_ms") / 1000
    kinds = Counter(p[0] for p in problems)

    out = [
        "# IGCZ benchmark",
        "",
        f"- Source: {source}",
        f"- Flights: {file_count:,} ({len(rows):,} converted, {kinds['refused']} refused, "
        f"{kinds['failed']} failed round trip, {kinds['unreadable']} unreadable)",
        f"- Fixes: {fixes:,}",
        f"- Wall time: {wall:.0f} s with {args.workers} processes",
        "",
        "## Total size",
        "",
        "| Format | Total size | Bytes per fix | Smaller than IGC by |",
        "|---|---|---|---|",
    ]
    formats = [("IGC (text)", "igc"), ("IGC + gzip", "igc_gzip")]
    if args.xz:
        formats.append(("IGC + xz", "igc_xz"))
    formats += [("igcc v0 (16 B per fix)", "igcc_v0"), ("IGCZ", "igcz")]
    if args.xz:
        formats.append(("IGCZ + xz", "igcz_xz"))
    for label, key in formats:
        size = total(key)
        out.append(f"| {label} | {size / 1e6:,.2f} MB | {size / max(fixes, 1):.2f} | {igc / max(size, 1):.1f}x |")

    if rows:
        ratios = sorted(r["igc"] / r["igcz"] for r in rows)
        vs_gzip = sorted(r["igc_gzip"] / r["igcz"] for r in rows)
        per_fix = sorted(r["igcz"] / r["fixes"] for r in rows if r["fixes"])
        out += [
            "",
            "## Per flight",
            "",
            "| | worst 1% | 10% | median | 90% |",
            "|---|---|---|---|---|",
            "| IGC / IGCZ | " + " | ".join(f"{percentile(ratios, q):.1f}x" for q in (0.01, 0.1, 0.5, 0.9)) + " |",
            "| IGC+gzip / IGCZ | " + " | ".join(f"{percentile(vs_gzip, q):.1f}x" for q in (0.01, 0.1, 0.5, 0.9)) + " |",
            "| IGCZ bytes per fix | "
            + " | ".join(f"{percentile(per_fix, q):.2f}" for q in (0.99, 0.9, 0.5, 0.1))
            + " |",
        ]
        worst = sorted(rows, key=lambda r: r["igc"] / r["igcz"])[:5]
        out += ["", "Least compressed flights:", ""]
        out += [f"- {r['file']}: {r['igc'] / r['igcz']:.1f}x ({r['fixes']} fixes, {r['igcz']} B)" for r in worst]

    out += [
        "",
        "## Speed (Python, one process, IGCZ only)",
        "",
        f"- Encode: {encode_s:.1f} s total, {igc / 1e6 / max(encode_s, 1e-9):.1f} MB of IGC per second",
        f"- Decode: {decode_s:.1f} s total, {igc / 1e6 / max(decode_s, 1e-9):.1f} MB of IGC per second",
        "",
        "## Where the IGCZ bytes go",
        "",
        "| Part | Size | Share |",
        "|---|---|---|",
    ]
    igcz = total("igcz")
    for name, size in sections.most_common():
        out.append(f"| {name} | {size / 1e6:,.2f} MB | {100 * size / max(igcz, 1):.1f}% |")

    for kind in ("failed", "unreadable", "refused"):
        listed = [p for p in problems if p[0] == kind]
        if not listed:
            continue
        reasons = Counter(p[2] for p in listed)
        out += ["", f"## {kind.capitalize()} ({len(listed)})", ""]
        out += [f"- {count} x {reason}" for reason, count in reasons.most_common()]
        out += [""] + [f"- {p[1]}" for p in listed[:20]]
        if len(listed) > 20:
            out.append(f"- ... and {len(listed) - 20} more (see stderr)")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
