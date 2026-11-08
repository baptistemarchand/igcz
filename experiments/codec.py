"""How to store one whole number (possibly negative) in as few bytes as possible."""


def zigzag(n):
    """Fold the sign in, so small numbers stay small: 0->0, -1->1, +1->2, -2->3, +2->4..."""
    return 2 * n if n >= 0 else -2 * n - 1


def unzigzag(z):
    return z // 2 if z % 2 == 0 else -(z + 1) // 2


def encode_number(n):
    """Return the bytes that store the integer `n`: 7 bits per byte, the 8th bit says "more follows"."""
    x = zigzag(n)
    out = bytearray()
    while x >= 128:
        out.append((x & 127) | 128)
        x >>= 7
    out.append(x)
    return bytes(out)


def decode_number(data, pos):
    """Read one number written by encode_number. Returns (number, next position)."""
    x = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        x |= (byte & 127) << shift
        if byte < 128:
            break
        shift += 7
    return unzigzag(x), pos
