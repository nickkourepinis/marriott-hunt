"""QR code generator (byte mode, error correction M, versions 1-10). No dependencies."""
from __future__ import annotations

# version -> (EC codewords per block, [(block count, data codewords per block), ...])
_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
              8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}


def _gf_multiply(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _gf_multiply(coefficient, factor)
    return result


def _capacity(version):
    return sum(count * size for count, size in _BLOCKS[version][1])


def _codewords(data, version):
    """Data bytes -> final interleaved codeword sequence for this version."""
    bits = []

    def push(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)
    push(len(data), 16 if version >= 10 else 8)
    for byte in data:
        push(byte, 8)
    capacity_bits = _capacity(version) * 8
    push(0, min(4, capacity_bits - len(bits)))
    push(0, (8 - len(bits) % 8) % 8)
    pad = 0xEC
    while len(bits) < capacity_bits:
        push(pad, 8)
        pad ^= 0xEC ^ 0x11
    raw = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    ec_len, groups = _BLOCKS[version]
    divisor = _rs_divisor(ec_len)
    blocks = []
    position = 0
    for count, size in groups:
        for _ in range(count):
            chunk = raw[position:position + size]
            position += size
            blocks.append((chunk, _rs_remainder(chunk, divisor)))
    out = []
    longest = max(len(chunk) for chunk, _ in blocks)
    for i in range(longest):
        for chunk, _ in blocks:
            if i < len(chunk):
                out.append(chunk[i])
    for i in range(ec_len):
        for _, ec in blocks:
            out.append(ec[i])
    return out


_MASKS = (
    lambda y, x: (x + y) % 2 == 0,
    lambda y, x: y % 2 == 0,
    lambda y, x: x % 3 == 0,
    lambda y, x: (x + y) % 3 == 0,
    lambda y, x: (y // 2 + x // 3) % 2 == 0,
    lambda y, x: (x * y) % 2 + (x * y) % 3 == 0,
    lambda y, x: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda y, x: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
)


def _penalty(modules):
    size = len(modules)
    score = 0
    lines = [row for row in modules] + [[modules[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
        text = "".join("1" if cell else "0" for cell in line)
        score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for y in range(size - 1):
        for x in range(size - 1):
            cell = modules[y][x]
            if cell == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    dark = sum(sum(1 for cell in row if cell) for row in modules)
    total = size * size
    score += 10 * ((abs(dark * 20 - total * 10) + total - 1) // total - 1)
    return score


def encode(text, version=None, mask=None):
    """Return the QR symbol as a square list of rows of booleans (True = dark)."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if version is None:
        for candidate in range(1, 11):
            header = 4 + (16 if candidate >= 10 else 8)
            if header + 8 * len(data) <= _capacity(candidate) * 8:
                version = candidate
                break
        else:
            raise ValueError("text too long for a QR code (max about 210 bytes)")
    codewords = _codewords(data, version)
    size = version * 4 + 17
    modules = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        modules[y][x] = bool(dark)
        function[y][x] = True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    positions = _ALIGNMENT[version]
    for i, cx in enumerate(positions):
        for j, cy in enumerate(positions):
            if (i == 0 and j == 0) or (i == 0 and j == len(positions) - 1) or (i == len(positions) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask_index):
        data_bits = (0 << 3) | mask_index  # error correction level M is 00
        rem = data_bits
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data_bits << 10) | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1
        for i in range(0, 6):
            put(8, i, bit(i))
        put(8, 7, bit(6))
        put(8, 8, bit(7))
        put(7, 8, bit(8))
        for i in range(9, 15):
            put(14 - i, 8, bit(i))
        for i in range(0, 8):
            put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            put(8, size - 15 + i, bit(i))
        put(8, size - 8, True)

    draw_format(0)
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (version << 12) | rem
        for i in range(18):
            dark = (bits >> i) & 1
            a, b = size - 11 + i % 3, i // 3
            put(a, b, dark)
            put(b, a, dark)

    index = 0
    total_bits = len(codewords) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not function[y][x] and index < total_bits:
                    modules[y][x] = bool((codewords[index >> 3] >> (7 - (index & 7))) & 1)
                    index += 1
        right -= 2

    def apply(mask_index):
        test = _MASKS[mask_index]
        for y in range(size):
            for x in range(size):
                if not function[y][x] and test(y, x):
                    modules[y][x] = not modules[y][x]

    if mask is None:
        best = None
        for candidate in range(8):
            apply(candidate)
            draw_format(candidate)
            score = _penalty(modules)
            if best is None or score < best[0]:
                best = (score, candidate)
            apply(candidate)
        mask = best[1]
    apply(mask)
    draw_format(mask)
    return modules


def to_svg(modules, scale=8, border=4, dark="#000", light="#fff"):
    size = len(modules)
    full = (size + 2 * border) * scale
    parts = []
    for y, row in enumerate(modules):
        x = 0
        while x < size:
            if row[x]:
                start = x
                while x < size and row[x]:
                    x += 1
                parts.append("M%d %dh%dv1h-%dz" % (start + border, y + border, x - start, x - start))
            else:
                x += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
            'aria-label="QR code"><rect width="100%%" height="100%%" fill="%s"/><path d="%s" fill="%s"/></svg>'
            % (size + 2 * border, size + 2 * border, full, full, light, "".join(parts), dark))


def to_terminal(modules, border=2):
    """Half-block rendering with explicit black-on-white colours, so dark terminals work."""
    size = len(modules)
    padded = [[False] * (size + 2 * border) for _ in range(border)]
    for row in modules:
        padded.append([False] * border + list(row) + [False] * border)
    padded += [[False] * (size + 2 * border) for _ in range(border)]
    if len(padded) % 2:
        padded.append([False] * (size + 2 * border))
    lines = []
    for y in range(0, len(padded), 2):
        top, bottom = padded[y], padded[y + 1]
        chars = []
        for upper, lower in zip(top, bottom):
            chars.append("█" if upper and lower else "▀" if upper else "▄" if lower else " ")
        lines.append("\x1b[38;5;16m\x1b[48;5;231m" + "".join(chars) + "\x1b[0m")
    return "\n".join(lines)
