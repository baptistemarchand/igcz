"""Check that text -> numbers -> text gives back the exact same line.

usage: python3 roundtrip.py <folder with .igc files>
"""

import sys

from igc import format_b, flight_paths, parse_b

same = different = 0
for path in flight_paths(sys.argv[1]):
    with open(path, encoding="latin-1") as f:
        for line in f.read().splitlines():
            if not line.startswith("B"):
                continue
            try:
                ok = format_b(parse_b(line)) == line
            except ValueError as e:
                print(f"ERROR {path}: {e}", file=sys.stderr)
                ok = False
            same += ok
            different += not ok

print(f"identical: {same}  different: {different}")
