"""Bit-level storage for number columns: guess the next value, then Rice-code the error.

Each column is cut into blocks of BLOCK numbers. A block starts with one flag bit:
0 means "every guess in this block was exact" and nothing else is stored,
1 means the BLOCK errors follow, each as an adaptive Rice code.
"""

from codec import unzigzag, zigzag

BLOCK = 16
ESCAPE = 16  # a Rice quotient this big is replaced by ESCAPE one-bits + RAW_BITS plain bits
RAW_BITS = 40


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0  # bits waiting in acc

    def write(self, value, nbits):
        self.acc = (self.acc << nbits) | value
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 0xFF)
        self.acc &= (1 << self.n) - 1

    def bit_length(self):
        return len(self.out) * 8 + self.n

    def finish(self):
        if self.n:
            self.out.append((self.acc << (8 - self.n)) & 0xFF)
            self.n = 0
        return bytes(self.out)


class BitReader:
    def __init__(self, data, byte_pos):
        self.data = data
        self.bit = byte_pos * 8

    def read(self, nbits):
        if nbits == 0:
            return 0
        start = self.bit >> 3
        end = (self.bit + nbits + 7) >> 3
        chunk = int.from_bytes(self.data[start:end], "big")
        shift = (end - start) * 8 - (self.bit & 7) - nbits
        self.bit += nbits
        return (chunk >> shift) & ((1 << nbits) - 1)


class AdaptiveRice:
    """Rice code whose parameter k follows the average size of recent numbers (as in JPEG-LS)."""

    def __init__(self):
        self.acc = 4
        self.count = 1

    def _k(self):
        k = 0
        while (self.count << k) < self.acc:
            k += 1
        return k

    def _update(self, z):
        self.acc += min(z, 1 << 16)
        self.count += 1
        if self.count == 32:  # halve the history so k follows changes during the flight
            self.acc >>= 1
            self.count >>= 1

    def write(self, bw, z):
        k = self._k()
        q = z >> k
        if q < ESCAPE:
            bw.write(((1 << q) - 1) << 1, q + 1)  # q one-bits, then a zero
            bw.write(z & ((1 << k) - 1), k)
        else:
            if z >= 1 << RAW_BITS:
                raise ValueError(f"number too large: {z}")
            bw.write((1 << ESCAPE) - 1, ESCAPE)
            bw.write(z, RAW_BITS)
        self._update(z)

    def read(self, br):
        k = self._k()
        q = 0
        while q < ESCAPE and br.read(1):
            q += 1
        z = (q << k) | br.read(k) if q < ESCAPE else br.read(RAW_BITS)
        self._update(z)
        return z


def _errors(values, slope):
    """Difference between each value and its guess."""
    out = []
    prev = prev_change = 0
    for v in values:
        out.append(v - prev - (prev_change if slope else 0))
        prev_change = v - prev
        prev = v
    return out


def _undo_errors(errors, slope):
    values = []
    prev = prev_change = 0
    for e in errors:
        v = prev + (prev_change if slope else 0) + e
        values.append(v)
        prev_change = v - prev
        prev = v
    return values


def write_column(bw, values, slope):
    """slope=True guesses "same change as last time"; False guesses "same value as last time"."""
    errors = _errors(values, slope)
    rice = AdaptiveRice()
    for i in range(0, len(errors), BLOCK):
        block = errors[i : i + BLOCK]
        if not any(block):
            bw.write(0, 1)
            continue
        bw.write(1, 1)
        for e in block:
            rice.write(bw, zigzag(e))


def read_column(br, count, slope):
    rice = AdaptiveRice()
    errors = []
    while len(errors) < count:
        n = min(BLOCK, count - len(errors))
        if br.read(1):
            errors.extend(unzigzag(rice.read(br)) for _ in range(n))
        else:
            errors.extend([0] * n)
    return _undo_errors(errors, slope)
