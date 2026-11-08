"""Measure how many bytes the 'store the change' idea needs.

usage: python3 sizes.py <folder with .igc files>
"""

import sys

from codec import encode_number
from igc import flight_paths, read_fixes

CHANNELS = ["time", "lat", "lng", "pressure_alt", "gnss_alt"]

total_fixes = 0
total_bytes = {name: 0 for name in CHANNELS}

for path in flight_paths(sys.argv[1]):
    fixes = read_fixes(path)
    total_fixes += len(fixes)
    for name in CHANNELS:
        values = [getattr(f, name) for f in fixes]
        # the first fix is stored as-is, the others as the change from the previous one
        previous = 0
        for value in values:
            total_bytes[name] += len(encode_number(value - previous))
            previous = value

print(f"{total_fixes} fixes")
for name in CHANNELS:
    print(f"{name:14s} {total_bytes[name] / total_fixes:.2f} bytes per fix")
print(f"{'total':14s} {sum(total_bytes.values()) / total_fixes:.2f} bytes per fix  (current igcc: 16)")
