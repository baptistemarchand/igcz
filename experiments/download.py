"""Download a random sample of flights from the Open IGC Database into data/flights/.

usage: python3 download.py [count]   (default 300; already downloaded files are skipped)
"""

import gzip
import json
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://igc.baptiste.app/flights"
DEST = Path(__file__).parent / "data" / "flights"
PAGE_SIZE = 1000


def get(url):
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "igcc-experiments"})
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
        # urllib does not decompress on its own, and the .igc files are served gzipped.
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    DEST.mkdir(parents=True, exist_ok=True)

    total = json.loads(get(f"{API}?limit=1"))["total"]
    # A fixed seed keeps the sample reproducible; several random pages spread it over the whole database.
    rng = random.Random(42)
    pages = max(1, count // 200)
    urls = []
    for offset in rng.sample(range(0, max(1, total - PAGE_SIZE)), pages):
        urls += [f["url"] for f in json.loads(get(f"{API}?limit={PAGE_SIZE}&offset={offset}"))["flights"]]
    urls = rng.sample(urls, min(count, len(urls)))

    def fetch(url):
        path = DEST / url.rsplit("/", 1)[-1]
        if path.exists():
            return "skipped"
        try:
            path.write_bytes(get(url))
            return "downloaded"
        except Exception as e:
            print(f"ERROR {url}: {e}", file=sys.stderr)
            return "failed"

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(fetch, urls))
    print({r: results.count(r) for r in set(results)}, "->", DEST)


if __name__ == "__main__":
    main()
