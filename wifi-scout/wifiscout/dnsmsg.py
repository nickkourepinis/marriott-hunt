"""Minimal DNS wire format: enough for mDNS discovery and resolver timing."""
from __future__ import annotations

import struct

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33
CLASS_IN = 1


def encode_name(name):
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("utf-8")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(questions, qid=0, recursion=True, unicast_response=False):
    """questions: list of (name, qtype). Returns the packet bytes."""
    flags = 0x0100 if recursion else 0
    packet = struct.pack("!HHHHHH", qid, flags, len(questions), 0, 0, 0)
    for name, qtype in questions:
        qclass = CLASS_IN | (0x8000 if unicast_response else 0)
        packet += encode_name(name) + struct.pack("!HH", qtype, qclass)
    return packet


def _read_name(data, offset, depth=0):
    """Returns (name, next_offset). Follows compression pointers safely."""
    labels = []
    jumped = False
    next_offset = offset
    hops = 0
    while True:
        if offset >= len(data):
            raise ValueError("name runs past end of packet")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                next_offset = offset + 2
            jumped = True
            offset = pointer
            hops += 1
            if hops > 32:
                raise ValueError("compression loop")
            continue
        if length & 0xC0:
            raise ValueError("unsupported label type")
        offset += 1
        labels.append(data[offset:offset + length].decode("utf-8", errors="replace"))
        offset += length
    if not jumped:
        next_offset = offset
    return ".".join(labels), next_offset


def _parse_txt(rdata):
    out = {}
    pos = 0
    while pos < len(rdata):
        length = rdata[pos]
        pos += 1
        chunk = rdata[pos:pos + length]
        pos += length
        if not chunk:
            continue
        text = chunk.decode("utf-8", errors="replace")
        if "=" in text:
            key, value = text.split("=", 1)
        else:
            key, value = text, ""
        if key and key.lower() not in out:
            out[key.lower()] = value[:200]
    return out


def parse_message(data):
    """Parse a DNS message. Returns a dict; raises ValueError on garbage."""
    if len(data) < 12:
        raise ValueError("short packet")
    qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    offset = 12
    questions = []
    for _ in range(qd):
        name, offset = _read_name(data, offset)
        if offset + 4 > len(data):
            raise ValueError("truncated question")
        qtype, qclass = struct.unpack("!HH", data[offset:offset + 4])
        offset += 4
        questions.append({"name": name, "type": qtype})
    records = []
    for _ in range(an + ns + ar):
        if offset >= len(data):
            break
        name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        start = offset
        offset += rdlen
        record = {"name": name, "type": rtype, "ttl": ttl}
        try:
            if rtype == TYPE_A and rdlen == 4:
                record["address"] = ".".join(str(b) for b in rdata)
            elif rtype == TYPE_PTR:
                record["target"] = _read_name(data, start)[0]
            elif rtype == TYPE_SRV and rdlen >= 6:
                priority, weight, port = struct.unpack("!HHH", rdata[:6])
                record["port"] = port
                record["target"] = _read_name(data, start + 6)[0]
            elif rtype == TYPE_TXT:
                record["txt"] = _parse_txt(rdata)
        except ValueError:
            continue
        records.append(record)
    return {
        "id": qid,
        "response": bool(flags & 0x8000),
        "rcode": flags & 0x000F,
        "questions": questions,
        "records": records,
    }
