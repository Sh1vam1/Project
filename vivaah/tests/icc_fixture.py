"""Build a minimal matrix/TRC ICC v2 profile (used to test wide-gamut inputs)."""
import struct


def _s15(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536)))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)


def _desc(text: str) -> bytes:
    t = text.encode("ascii") + b"\0"
    body = b"desc" + b"\0" * 4 + struct.pack(">I", len(t)) + t
    body += struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + b"\0" * 67
    return body


def _curv(gamma: float) -> bytes:
    return b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(gamma * 256))) + b"\0\0"


def matrix_profile(name: str, red, green, blue, gamma: float = 2.2) -> bytes:
    """Colorants are D50-adapted XYZ (as stored in ICC rXYZ/gXYZ/bXYZ tags)."""
    tags = [
        (b"desc", _desc(name)),
        (b"wtpt", _xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", _xyz(*red)), (b"gXYZ", _xyz(*green)), (b"bXYZ", _xyz(*blue)),
        (b"rTRC", _curv(gamma)), (b"gTRC", _curv(gamma)), (b"bTRC", _curv(gamma)),
        (b"cprt", b"text" + b"\0" * 4 + b"test\0\0\0\0"),
    ]
    table_len = 4 + 12 * len(tags)
    offset = 128 + table_len
    entries, data = b"", b""
    for sig, body in tags:
        while (offset + len(data)) % 4:
            data += b"\0"
        entries += sig + struct.pack(">II", offset + len(data), len(body))
        data += body
    while len(data) % 4:
        data += b"\0"
    size = 128 + table_len + len(data)
    header = struct.pack(">I", size) + b"lcms" + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ "
    header += struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acspAPPL" + b"\0" * 4 + b"\0" * 8 + b"\0" * 8
    header += struct.pack(">I", 0) + _s15(0.9642) + _s15(1.0) + _s15(0.8249) + b"lcms" + b"\0" * 16 + b"\0" * 28
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + entries + data


DISPLAY_P3 = matrix_profile("Display P3 test", (0.5151, 0.2412, -0.0011), (0.2920, 0.6922, 0.0419),
                            (0.1571, 0.0666, 0.7841), gamma=2.2)
