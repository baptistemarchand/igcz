"""Adaptive arithmetic coding for number columns.

The coder only ever stores yes/no answers ("bits"), each with a probability it
learns as it goes (a range coder, the same design as in LZMA/xz). A number is
turned into a short series of questions:

    1. was the guess exact?                     (yes for most time values)
    2. was the error negative?
    3. how many binary digits does it have?     (asked one "more digits?" at a time)
    4. what are those digits?

Each question has its own probabilities, and there is a separate set per
"context": how big the last two errors were. A calm stretch of flight and a
turbulent one thus learn different odds, and each gets cheaper.
"""

PROB_BITS = 11
PROB_ONE = 1 << PROB_BITS
PROB_INIT = PROB_ONE // 2
MOVE = 5  # how fast probabilities follow the data: each answer moves them 1/32 of the way
TOP = 1 << 24
MASK32 = 0xFFFFFFFF

CONTEXTS = 8  # size of the last two errors: 0, 1, 2-3, 4-7, ..., 64+
MAX_DIGITS = 64
LEARNED_DIGITS = 6  # digits after the first one that get learned odds; later ones cost exactly 1 bit


class RangeEncoder:
    def __init__(self):
        self.low = 0
        self.range = MASK32
        self.cache = 0
        self.cache_size = 1
        self.out = bytearray()

    def _shift_low(self):
        low = self.low
        if low < 0xFF000000 or low > MASK32:
            # The top byte is final (or a carry just made it final): write it, plus any pending 0xFF bytes.
            carry = low >> 32
            temp = self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if self.cache_size == 0:
                    break
            self.cache = (low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (low & 0x00FFFFFF) << 8

    def bit(self, probs, i, bit):
        """Store one yes/no answer using (and then updating) the probability probs[i]."""
        p = probs[i]
        bound = (self.range >> PROB_BITS) * p
        if bit:
            self.low += bound
            self.range -= bound
            probs[i] = p - (p >> MOVE)
        else:
            self.range = bound
            probs[i] = p + ((PROB_ONE - p) >> MOVE)
        while self.range < TOP:
            self.range <<= 8
            self._shift_low()

    def direct(self, value, nbits):
        """Store bits that are as likely 0 as 1: exactly one bit each."""
        for k in range(nbits - 1, -1, -1):
            self.range >>= 1
            if (value >> k) & 1:
                self.low += self.range
            while self.range < TOP:
                self.range <<= 8
                self._shift_low()

    def size(self):
        """Bytes written so far, including those still pending."""
        return len(self.out) + self.cache_size

    def finish(self):
        for _ in range(5):
            self._shift_low()
        return bytes(self.out)


class RangeDecoder:
    def __init__(self, data, pos):
        self.data = data
        self.range = MASK32
        self.code = int.from_bytes(data[pos : pos + 5].ljust(5, b"\0"), "big")
        self.pos = pos + 5

    def _next_byte(self):
        b = self.data[self.pos] if self.pos < len(self.data) else 0
        self.pos += 1
        return b

    def bit(self, probs, i):
        p = probs[i]
        bound = (self.range >> PROB_BITS) * p
        if self.code < bound:
            self.range = bound
            probs[i] = p + ((PROB_ONE - p) >> MOVE)
            b = 0
        else:
            self.code -= bound
            self.range -= bound
            probs[i] = p - (p >> MOVE)
            b = 1
        while self.range < TOP:
            self.range <<= 8
            self.code = ((self.code << 8) | self._next_byte()) & MASK32
        return b

    def direct(self, nbits):
        value = 0
        for _ in range(nbits):
            self.range >>= 1
            b = 0
            if self.code >= self.range:
                self.code -= self.range
                b = 1
            value = (value << 1) | b
            while self.range < TOP:
                self.range <<= 8
                self.code = ((self.code << 8) | self._next_byte()) & MASK32
        return value


class Model:
    """The learned probabilities for one column."""

    def __init__(self):
        self.exact = [PROB_INIT] * CONTEXTS
        self.negative = [PROB_INIT] * (CONTEXTS * 3)
        self.more_digits = [[PROB_INIT] * MAX_DIGITS for _ in range(CONTEXTS)]
        # one small binary tree of learned odds per digit count
        self.digits = [[PROB_INIT] * (2 << min(max(n - 1, 0), LEARNED_DIGITS)) for n in range(MAX_DIGITS)]


def _context(e1, e2):
    return min((abs(e1) + abs(e2)).bit_length(), CONTEXTS - 1)


def _sign_context(ctx, e1):
    return ctx * 3 + (0 if e1 < 0 else 1 if e1 == 0 else 2)


def _write_error(enc, m, e, e1, e2):
    ctx = _context(e1, e2)
    enc.bit(m.exact, ctx, e != 0)
    if e == 0:
        return
    enc.bit(m.negative, _sign_context(ctx, e1), e < 0)

    magnitude = abs(e)
    n = magnitude.bit_length()
    if n >= MAX_DIGITS:
        raise ValueError(f"number too large: {e}")
    more = m.more_digits[ctx]
    for i in range(n - 1):
        enc.bit(more, i, 1)
    enc.bit(more, n - 1, 0)

    # The first digit is always 1; send the n-1 digits after it.
    learned = min(n - 1, LEARNED_DIGITS)
    tree = m.digits[n]
    node = 1
    for k in range(n - 2, n - 2 - learned, -1):
        b = (magnitude >> k) & 1
        enc.bit(tree, node, b)
        node = node * 2 + b
    rest = n - 1 - learned
    if rest:
        enc.direct(magnitude & ((1 << rest) - 1), rest)


def _read_error(dec, m, e1, e2):
    ctx = _context(e1, e2)
    if not dec.bit(m.exact, ctx):
        return 0
    negative = dec.bit(m.negative, _sign_context(ctx, e1))

    more = m.more_digits[ctx]
    n = 1
    while dec.bit(more, n - 1):
        n += 1

    learned = min(n - 1, LEARNED_DIGITS)
    tree = m.digits[n]
    node = 1
    for _ in range(learned):
        node = node * 2 + dec.bit(tree, node)
    magnitude = node  # the leading 1 plus the learned digits
    rest = n - 1 - learned
    if rest:
        magnitude = (magnitude << rest) | dec.direct(rest)
    return -magnitude if negative else magnitude


def write_column(enc, values, slope):
    """slope=True guesses "same change as last time"; False guesses "same value as last time"."""
    m = Model()
    prev = prev_change = 0
    e1 = e2 = 0
    for v in values:
        e = v - prev - (prev_change if slope else 0)
        _write_error(enc, m, e, e1, e2)
        e2, e1 = e1, e
        prev_change = v - prev
        prev = v


def read_column(dec, count, slope):
    m = Model()
    values = []
    prev = prev_change = 0
    e1 = e2 = 0
    for _ in range(count):
        e = _read_error(dec, m, e1, e2)
        v = prev + (prev_change if slope else 0) + e
        values.append(v)
        e2, e1 = e1, e
        prev_change = v - prev
        prev = v
    return values
