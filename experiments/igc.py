"""Read B-records (one GPS point per line) into numbers, and write them back."""

import glob
import sys


class Fix:
    """One GPS point. Everything is an integer so nothing is lost to rounding."""

    def __init__(self, time, lat, lng, valid, pressure_alt, gnss_alt, tail):
        self.time = time  # seconds since midnight
        self.lat = lat  # thousandths of a minute, negative = south
        self.lng = lng  # thousandths of a minute, negative = west
        self.valid = valid  # "A" or "V"
        self.pressure_alt = pressure_alt  # metres
        self.gnss_alt = gnss_alt  # metres
        self.tail = tail  # extension fields, kept as raw text for now


def parse_b(line):
    """Cut a B-line at fixed positions and turn the pieces into numbers."""
    time = int(line[1:3]) * 3600 + int(line[3:5]) * 60 + int(line[5:7])

    lat = int(line[7:9]) * 60000 + int(line[9:14])
    if line[14] == "S":
        lat = -lat

    lng = int(line[15:18]) * 60000 + int(line[18:23])
    if line[23] == "W":
        lng = -lng

    return Fix(
        time=time,
        lat=lat,
        lng=lng,
        valid=line[24],
        pressure_alt=int(line[25:30]),
        gnss_alt=int(line[30:35]),
        tail=line[35:],
    )


def format_b(fix):
    """Write the numbers back out as a B-line."""
    h, rest = divmod(fix.time, 3600)
    m, s = divmod(rest, 60)

    lat_deg, lat_min = divmod(abs(fix.lat), 60000)
    lng_deg, lng_min = divmod(abs(fix.lng), 60000)
    lat_h = "S" if fix.lat < 0 else "N"
    lng_h = "W" if fix.lng < 0 else "E"

    return (
        f"B{h:02d}{m:02d}{s:02d}"
        f"{lat_deg:02d}{lat_min:05d}{lat_h}"
        f"{lng_deg:03d}{lng_min:05d}{lng_h}"
        f"{fix.valid}{fix.pressure_alt:05d}{fix.gnss_alt:05d}{fix.tail}"
    )


def is_midnight_rollover(previous_time, time):
    """Loggers sometimes step back a few seconds; only a jump back of half a day is midnight."""
    return time < previous_time - 12 * 3600


def read_fixes(path):
    """All fixes of one .igc file. Logs an error and stops at a midnight rollover."""
    # latin-1 never fails, and some headers contain non-ASCII bytes.
    with open(path, encoding="latin-1") as f:
        lines = f.read().splitlines()

    fixes = []
    for line in lines:
        if not line.startswith("B"):
            continue
        fix = parse_b(line)
        if fixes and is_midnight_rollover(fixes[-1].time, fix.time):
            print(f"ERROR {path}: midnight rollover, stopping", file=sys.stderr)
            break
        fixes.append(fix)
    return fixes


def flight_paths(folder):
    return sorted(glob.glob(f"{folder}/*"))
