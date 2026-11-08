"""The compact format: an .igc file to bytes and back, byte for byte.

Layout. The first part is bytes, every number written with codec.encode_number:

    "IGCZ" version
    line ending              0 = "\\n", 1 = "\\r\\n"
    other lines              count, then their positions (as changes), then their texts
    fix count
    invalid fixes            count, then their indexes (as changes)
    extension fields         mode 1: one text per fix (lines of different lengths)
                             mode 0: field count, then per field: width, kind
                                     (kind 0 = number, stored in the bitstream;
                                      kind 1 = raw text, stored here)

The second part is one bitstream (see bits.py), padded to a whole byte:

    time, lat, lng, pressure_alt, gnss_alt     guess "same change as last time"
    each number extension field                guess "same value as last time"

"Other lines" are every line that is not a regular B-line (headers, events,
signature, malformed B-lines), kept as raw text with the index of the fix they
come before, so the file can be rebuilt exactly.
"""

from bits import BitReader, BitWriter, read_column, write_column
from codec import decode_number, encode_number
from igc import Fix, format_b, is_midnight_rollover, parse_b

MAGIC = b"IGCZ"
VERSION = 2
TAIL_START = 35  # index in a B-line where the extension fields begin
COLUMNS = ("time", "lat", "lng", "pressure_alt", "gnss_alt")
DIGITS = set("0123456789")
NUMBER, RAW = 0, 1


class UnsupportedFlight(Exception):
    """A flight the format deliberately refuses (midnight rollover)."""


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def num(self, n):
        self.buf += encode_number(n)

    def changes(self, values):
        previous = 0
        for value in values:
            self.buf += encode_number(value - previous)
            previous = value

    def text(self, s):
        data = s.encode("latin-1")
        self.num(len(data))
        self.buf += data


class Reader:
    def __init__(self, data, pos):
        self.data = data
        self.pos = pos

    def num(self):
        n, self.pos = decode_number(self.data, self.pos)
        return n

    def changes(self, count):
        values = []
        value = 0
        for _ in range(count):
            value += self.num()
            values.append(value)
        return values

    def raw(self, size):
        chunk = self.data[self.pos : self.pos + size].decode("latin-1")
        self.pos += size
        return chunk

    def text(self):
        return self.raw(self.num())


def _try_parse(line):
    """The Fix for a regular B-line, or None if the line must be kept as raw text."""
    if not line.startswith("B") or len(line) < TAIL_START:
        return None
    try:
        fix = parse_b(line)
    except ValueError:
        return None
    # Anything that would not come back identical (odd validity char, minutes >= 60...) stays raw.
    if fix.valid not in "AV" or format_b(fix) != line:
        return None
    return fix


def _tail_widths(other_lines, tail_len):
    """Widths of the extension fields, read from the I-record. One big field if it does not match."""
    fallback = [tail_len] if tail_len else []
    i_line = next((line for _, line in other_lines if line.startswith("I")), None)
    if i_line is None:
        return fallback
    try:
        widths = []
        expected_start = TAIL_START + 1  # the I-record counts bytes from 1
        for k in range(int(i_line[1:3])):
            field = i_line[3 + 7 * k : 10 + 7 * k]
            start, end = int(field[0:2]), int(field[2:4])
            if start != expected_start or end < start:
                return fallback
            widths.append(end - start + 1)
            expected_start = end + 1
    except ValueError:
        return fallback
    return widths if sum(widths) == tail_len else fallback


def _write_tail(w, fixes, other_lines):
    """Write the extension fields' description. Returns the number columns for the bitstream."""
    tails = [f.tail for f in fixes]
    lengths = {len(t) for t in tails}
    if len(lengths) > 1:
        w.num(1)
        for t in tails:
            w.text(t)
        return []

    w.num(0)
    widths = _tail_widths(other_lines, lengths.pop() if lengths else 0)
    w.num(len(widths))
    number_columns = []
    offset = 0
    for width in widths:
        values = [t[offset : offset + width] for t in tails]
        w.num(width)
        if all(set(v) <= DIGITS for v in values):
            w.num(NUMBER)
            number_columns.append([int(v) for v in values])
        else:
            w.num(RAW)
            w.buf += "".join(values).encode("latin-1")
        offset += width
    return number_columns


def encode(raw, sections=None):
    """Encode the bytes of an .igc file. `sections`, if given, receives the size in bytes of each part."""
    if sections is None:
        sections = {}
    text = raw.decode("latin-1")  # never fails, and maps each byte to one character
    eol = "\r\n" if "\r\n" in text else "\n"

    fixes = []
    other_lines = []  # (index of the fix this line comes before, line)
    for line in text.split(eol):
        fix = _try_parse(line)
        if fix is None:
            other_lines.append((len(fixes), line))
            continue
        if fixes and is_midnight_rollover(fixes[-1].time, fix.time):
            raise UnsupportedFlight("midnight rollover")
        fixes.append(fix)

    w = Writer()
    w.buf += MAGIC + bytes([VERSION])
    w.num(1 if eol == "\r\n" else 0)

    start = len(w.buf)
    w.num(len(other_lines))
    w.changes(position for position, _ in other_lines)
    for _, line in other_lines:
        w.text(line)
    sections["other lines"] = len(w.buf) - start

    w.num(len(fixes))

    start = len(w.buf)
    invalid = [i for i, f in enumerate(fixes) if f.valid == "V"]
    w.num(len(invalid))
    w.changes(invalid)
    sections["validity"] = len(w.buf) - start

    start = len(w.buf)
    number_columns = _write_tail(w, fixes, other_lines)
    sections["extension layout and text"] = len(w.buf) - start

    bw = BitWriter()
    for name in COLUMNS:
        start = bw.bit_length()
        write_column(bw, [getattr(f, name) for f in fixes], slope=True)
        sections[name] = (bw.bit_length() - start) / 8
    for index, values in enumerate(number_columns):
        start = bw.bit_length()
        write_column(bw, values, slope=False)
        sections[f"extension {index}"] = (bw.bit_length() - start) / 8

    return bytes(w.buf) + bw.finish()


def decode(data):
    """Rebuild the exact bytes of the original .igc file."""
    if data[:4] != MAGIC or data[4] != VERSION:
        raise ValueError(f"not an IGCZ v{VERSION} file")
    r = Reader(data, 5)
    eol = "\r\n" if r.num() == 1 else "\n"

    other_count = r.num()
    positions = r.changes(other_count)
    other_lines = list(zip(positions, (r.text() for _ in range(other_count))))

    count = r.num()
    invalid = set(r.changes(r.num()))

    tails = None
    fields = []  # (width, values or None when the values are in the bitstream)
    if r.num() == 1:
        tails = [r.text() for _ in range(count)]
    else:
        for _ in range(r.num()):
            width = r.num()
            values = [r.raw(width) for _ in range(count)] if r.num() == RAW else None
            fields.append((width, values))

    br = BitReader(data, r.pos)
    columns = {name: read_column(br, count, slope=True) for name in COLUMNS}
    if tails is None:
        parts = []
        for width, values in fields:
            if values is None:
                values = [f"{v:0{width}d}" for v in read_column(br, count, slope=False)]
            parts.append(values)
        tails = ["".join(p) for p in zip(*parts)] if parts else [""] * count

    lines = []
    k = 0
    for i in range(count + 1):
        while k < len(other_lines) and other_lines[k][0] == i:
            lines.append(other_lines[k][1])
            k += 1
        if i < count:
            fix = Fix(
                time=columns["time"][i],
                lat=columns["lat"][i],
                lng=columns["lng"][i],
                valid="V" if i in invalid else "A",
                pressure_alt=columns["pressure_alt"][i],
                gnss_alt=columns["gnss_alt"][i],
                tail=tails[i],
            )
            lines.append(format_b(fix))
    return eol.join(lines).encode("latin-1")
