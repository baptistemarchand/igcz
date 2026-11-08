"""The compact format: an .igc file to bytes and back, byte for byte.

Layout. The first part is bytes, every number written with codec.encode_number:

    "IGCZ" version
    line ending              0 = "\\n", 1 = "\\r\\n"
    other lines              count, then their texts: lengths flag (+ lengths), size,
                             texts joined by "\\n" and deflated with primer.txt as a
                             preset dictionary (their positions are in the coded part)
    fix count
    invalid fixes            count, then their indexes (as changes)
    extension fields         mode 0 (fields from the I-record): field count, then per
                                     field: width, kind (0 = number, 1 = text)
                             mode 1 (no field layout fits): nothing
                             then size + deflated text: text fields (mode 0), or all
                             tails (mode 1)

The second part is one arithmetic-coded stream (see arith.py), each column
with its own learned probabilities:

    time, lat, lng, pressure_alt, gnss_alt     guess "same change as last time"
    mode 0: fields present on each fix,        guess "same value as last time"
            then each number field (only on the fixes that have it)
    mode 1: tail length of each fix
    position of each other line (index of the fix it comes before)

"Other lines" are every line that is not a regular B-line (headers, events,
signature, malformed B-lines), kept as raw text with the index of the fix they
come before, so the file can be rebuilt exactly.
"""

import zlib
from itertools import accumulate
from pathlib import Path

from arith import RangeDecoder, RangeEncoder, read_column, write_column
from codec import decode_number, encode_number
from igc import Fix, format_b, is_midnight_rollover, parse_b

MAGIC = b"IGCZ"
VERSION = 6
# Typical header text known to both sides (see make_primer.py). Part of the format:
# a new primer needs a new VERSION, or older files would no longer decode.
PRIMER = (Path(__file__).parent / "primer.txt").read_bytes()
TAIL_START = 35  # index in a B-line where the extension fields begin
COLUMNS = ("time", "lat", "lng", "pressure_alt", "gnss_alt")
NUMBER, RAW = 0, 1
MAX_NUMBER_WIDTH = 12  # wider fields stay text: their values could overflow the coder


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


def _deflate(data):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=PRIMER)  # -15: raw deflate, no header or checksum
    return c.compress(data) + c.flush()


def _inflate(data):
    d = zlib.decompressobj(-15, zdict=PRIMER)
    return d.decompress(data) + d.flush()


def _write_texts(w, lines):
    """Lines joined by "\\n" and deflated with the primer, so common header phrases cost almost nothing."""
    # With "\r\n" line endings a line may contain a lone "\n"; then joining is ambiguous, so store the lengths.
    with_lengths = any("\n" in line for line in lines)
    w.num(1 if with_lengths else 0)
    if with_lengths:
        for line in lines:
            w.num(len(line))
    packed = _deflate("\n".join(lines).encode("latin-1"))
    w.num(len(packed))
    w.buf += packed


def _read_texts(r, count):
    with_lengths = r.num() == 1
    lengths = [r.num() for _ in range(count)] if with_lengths else None
    size = r.num()
    text = _inflate(r.data[r.pos : r.pos + size]).decode("latin-1")
    r.pos += size
    if count == 0:
        return []
    if not with_lengths:
        return text.split("\n")
    lines, offset = [], 0
    for n in lengths:
        lines.append(text[offset : offset + n])
        offset += n + 1
    return lines


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


def _i_record_widths(other_lines):
    """Widths of the extension fields declared by the I-record, or None."""
    i_line = next((line for _, line in other_lines if line.startswith("I")), None)
    if i_line is None:
        return None
    try:
        widths = []
        expected_start = TAIL_START + 1  # the I-record counts bytes from 1
        for k in range(int(i_line[1:3])):
            field = i_line[3 + 7 * k : 10 + 7 * k]
            start, end = int(field[0:2]), int(field[2:4])
            if start != expected_start or end < start:
                return None
            widths.append(end - start + 1)
            expected_start = end + 1
    except ValueError:
        return None
    return widths


def _tail_layout(other_lines, tails):
    """Field widths such that every tail is exactly its first few fields, or None if there is none."""
    longest = max(map(len, tails), default=0)
    declared = _i_record_widths(other_lines)
    candidates = [declared, [longest] if longest else []]
    if declared is not None and longest > sum(declared):
        # Some loggers write more than their I-record declares: one extra field for the rest.
        candidates.insert(1, declared + [longest - sum(declared)])
    for widths in candidates:
        if widths is None:
            continue
        bounds = set(accumulate(widths, initial=0))
        if all(len(t) in bounds for t in tails):
            return widths
    return None


def _format_number(value, width):
    return f"{value:0{width}d}"  # zero-padded, with the sign inside the width: -34 -> "-0034"


def _is_number_field(values, width):
    if width > MAX_NUMBER_WIDTH:
        return False
    try:
        return all(_format_number(int(v), width) == v for v in values)
    except ValueError:
        return False


def _write_tail(w, fixes, other_lines):
    """Write the extension layout and raw text. Returns the (name, values) columns for the coded part.

    mode 0: the tails are made of fields (I-record). Each fix stores how many fields it has,
            number fields go to the coded part, other fields to the deflated text.
    mode 1: no such layout. Each fix stores its tail length, the text goes deflated.
    """
    tails = [f.tail for f in fixes]
    widths = _tail_layout(other_lines, tails)
    if widths is None:
        w.num(1)
        raw_text, columns = "".join(tails), [("extension lengths", [len(t) for t in tails])]
    else:
        w.num(0)
        w.num(len(widths))
        field_count = {b: i for i, b in enumerate(accumulate(widths, initial=0))}
        present = [field_count[len(t)] for t in tails]
        columns = [("extension field count", present)]
        raw_parts = []
        offset = 0
        for index, width in enumerate(widths):
            values = [t[offset : offset + width] for t, n in zip(tails, present) if n > index]
            w.num(width)
            if _is_number_field(values, width):
                w.num(NUMBER)
                columns.append((f"extension {index}", [int(v) for v in values]))
            else:
                w.num(RAW)
                raw_parts += values
            offset += width
        raw_text = "".join(raw_parts)
    packed = _deflate(raw_text.encode("latin-1")) if raw_text else b""  # usually empty: skip deflate's own bytes
    w.num(len(packed))
    w.buf += packed
    return columns


def _read_tail(r):
    """Reads what _write_tail put in the bytes part. Returns (widths and kinds or None, raw text)."""
    fields = None
    if r.num() == 0:
        fields = [(r.num(), r.num()) for _ in range(r.num())]
    size = r.num()
    raw_text = _inflate(r.data[r.pos : r.pos + size]).decode("latin-1") if size else ""
    r.pos += size
    return fields, raw_text


def _decode_tails(dec, count, fields, raw_text):
    if fields is None:
        tails, offset = [], 0
        for n in read_column(dec, count, slope=False):
            tails.append(raw_text[offset : offset + n])
            offset += n
        return tails

    present = read_column(dec, count, slope=False)
    parts = [[] for _ in range(count)]
    offset = 0
    for index, (width, kind) in enumerate(fields):
        owners = [i for i in range(count) if present[i] > index]
        if kind == NUMBER:
            values = [_format_number(v, width) for v in read_column(dec, len(owners), slope=False)]
        else:
            values = [raw_text[offset + k * width : offset + (k + 1) * width] for k in range(len(owners))]
            offset += len(owners) * width
        for i, v in zip(owners, values):
            parts[i].append(v)
    return ["".join(p) for p in parts]


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
    _write_texts(w, [line for _, line in other_lines])
    sections["other lines"] = len(w.buf) - start

    w.num(len(fixes))

    start = len(w.buf)
    invalid = [i for i, f in enumerate(fixes) if f.valid == "V"]
    w.num(len(invalid))
    w.changes(invalid)
    sections["validity"] = len(w.buf) - start

    start = len(w.buf)
    tail_columns = _write_tail(w, fixes, other_lines)
    sections["extension layout and text"] = len(w.buf) - start

    enc = RangeEncoder()
    for name in COLUMNS:
        start = enc.size()
        write_column(enc, [getattr(f, name) for f in fixes], slope=True)
        sections[name] = enc.size() - start
    for name, values in tail_columns:
        start = enc.size()
        write_column(enc, values, slope=False)
        sections[name] = enc.size() - start
    start = enc.size()
    write_column(enc, [position for position, _ in other_lines], slope=False)
    sections["other line positions"] = enc.size() - start

    return bytes(w.buf) + enc.finish()


def decode(data):
    """Rebuild the exact bytes of the original .igc file."""
    if data[:4] != MAGIC or data[4] != VERSION:
        raise ValueError(f"not an IGCZ v{VERSION} file")
    r = Reader(data, 5)
    eol = "\r\n" if r.num() == 1 else "\n"

    other_count = r.num()
    other_texts = _read_texts(r, other_count)

    count = r.num()
    invalid = set(r.changes(r.num()))

    fields, raw_text = _read_tail(r)

    dec = RangeDecoder(data, r.pos)
    columns = {name: read_column(dec, count, slope=True) for name in COLUMNS}
    tails = _decode_tails(dec, count, fields, raw_text)
    other_lines = list(zip(read_column(dec, other_count, slope=False), other_texts))

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
