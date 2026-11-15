"""Self-signed certificate for the phone survey page.

iPhone Safari only lets a page use the motion sensors, compass and location
over HTTPS. The survey server therefore needs a certificate; it is generated
locally, used only inside the home network, and the phone shows a one-time
warning for it. Uses the `openssl` program when present, otherwise a small
pure-Python RSA/X.509 writer so Windows works with nothing installed.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import json
import os
import secrets
import tempfile
import time

from . import osutil

VALID_DAYS = 397
_SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % d for d in range(3, int(p ** 0.5) + 1, 2))]


# ------------------------------------------------------------ DER helpers

def _length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der(tag, content):
    return bytes([tag]) + _length(len(content)) + content


def _integer(value):
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    return _der(0x02, raw)


def _sequence(*items):
    return _der(0x30, b"".join(items))


def _oid(dotted):
    parts = [int(p) for p in dotted.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7F))
            part >>= 7
        body += bytes(reversed(chunk))
    return _der(0x06, body)


def _utc_time(moment):
    return _der(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def _bit_string(content):
    return _der(0x03, b"\x00" + content)


def _octet_string(content):
    return _der(0x04, content)


def _name(common_name):
    attribute = _sequence(_oid("2.5.4.3"), _der(0x0C, common_name.encode("utf-8")))
    return _sequence(_der(0x31, attribute))


def _extension(oid, value, critical=False):
    items = [_oid(oid)]
    if critical:
        items.append(_der(0x01, b"\xff"))
    items.append(_octet_string(value))
    return _sequence(*items)


def _san(hosts):
    names = b""
    for host in hosts:
        try:
            names += _der(0x87, ipaddress.ip_address(host).packed)
        except ValueError:
            names += _der(0x82, host.encode("ascii", errors="ignore"))
    return _sequence(names) if names else _sequence()


# ------------------------------------------------------------------- RSA

def _is_probable_prime(n, rounds=24):
    if n < 2:
        return False
    for prime in _SMALL_PRIMES:
        if n % prime == 0:
            return n == prime
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = 2 + secrets.randbelow(n - 3)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits):
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if candidate % 65537 != 1 and _is_probable_prime(candidate):
            return candidate


def generate_rsa(bits=2048):
    """Returns the key as a dict of integers: n, e, d, p, q, dp, dq, qi."""
    e = 65537
    while True:
        p = _random_prime(bits // 2)
        q = _random_prime(bits // 2)
        if p == q:
            continue
        if p < q:
            p, q = q, p
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        d = _inverse(e, phi)
        if d is None:
            continue
        return {"n": n, "e": e, "d": d, "p": p, "q": q, "dp": d % (p - 1), "dq": d % (q - 1), "qi": _inverse(q, p)}


def _inverse(a, m):
    """Modular inverse by the extended Euclid algorithm (works on Python 3.7 too)."""
    g, x, y = m, 0, 1
    r, s = a % m, 1
    old_r, old_s = m, 0
    while r:
        quotient = old_r // r
        old_r, r = r, old_r - quotient * r
        old_s, s = s, old_s - quotient * s
    if old_r != 1:
        return None
    return old_s % m


def _sign_sha256(key, message):
    size = (key["n"].bit_length() + 7) // 8
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(message).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    value = int.from_bytes(padded, "big")
    # Chinese remainder theorem: about four times faster than a plain pow().
    m1 = pow(value % key["p"], key["dp"], key["p"])
    m2 = pow(value % key["q"], key["dq"], key["q"])
    h = (key["qi"] * (m1 - m2)) % key["p"]
    signature = m2 + h * key["q"]
    if pow(signature, key["e"], key["n"]) != value:
        raise ValueError("RSA self-check failed")
    return signature.to_bytes(size, "big")


def _pem(label, der_bytes):
    body = base64.encodebytes(der_bytes).decode("ascii")
    return "-----BEGIN %s-----\n%s-----END %s-----\n" % (label, body, label)


def build_certificate(key, hosts, common_name="WiFi Scout", now=None):
    """Self-signed X.509 v3 certificate with the hosts as subject alternative names. Returns PEM text."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    not_before = now - datetime.timedelta(days=1)
    not_after = now + datetime.timedelta(days=VALID_DAYS)
    algorithm = _sequence(_oid("1.2.840.113549.1.1.11"), _der(0x05, b""))
    public_key = _sequence(_integer(key["n"]), _integer(key["e"]))
    spki = _sequence(_sequence(_oid("1.2.840.113549.1.1.1"), _der(0x05, b"")), _bit_string(public_key))
    key_id = hashlib.sha1(public_key).digest()
    extensions = _sequence(
        _extension("2.5.29.14", _octet_string(key_id)),
        _extension("2.5.29.35", _sequence(_der(0x80, key_id))),
        _extension("2.5.29.19", _sequence(_der(0x01, b"\xff")), critical=True),
        _extension("2.5.29.17", _san(hosts)),
        _extension("2.5.29.37", _sequence(_oid("1.3.6.1.5.5.7.3.1"))),
    )
    serial = secrets.randbits(159) | (1 << 158)
    tbs = _sequence(
        _der(0xA0, _integer(2)),
        _integer(serial),
        algorithm,
        _name(common_name),
        _sequence(_utc_time(not_before), _utc_time(not_after)),
        _name(common_name),
        spki,
        _der(0xA3, extensions),
    )
    certificate = _sequence(tbs, algorithm, _bit_string(_sign_sha256(key, tbs)))
    return _pem("CERTIFICATE", certificate)


def private_key_pem(key):
    body = _sequence(_integer(0), _integer(key["n"]), _integer(key["e"]), _integer(key["d"]), _integer(key["p"]),
                     _integer(key["q"]), _integer(key["dp"]), _integer(key["dq"]), _integer(key["qi"]))
    return _pem("RSA PRIVATE KEY", body)


def generate_pure_python(cert_path, key_path, hosts):
    key = generate_rsa(2048)
    _write_private(key_path, private_key_pem(key))
    with open(cert_path, "w", encoding="ascii") as handle:
        handle.write(build_certificate(key, hosts))
    return True


def _write_private(path, text):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(text)


def generate_with_openssl(cert_path, key_path, hosts):
    if not osutil.which("openssl"):
        return False
    names = []
    for host in hosts:
        try:
            ipaddress.ip_address(host)
            names.append("IP:%s" % host)
        except ValueError:
            names.append("DNS:%s" % host)
    config = (
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
        "[dn]\nCN=WiFi Scout\n"
        "[ext]\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n"
        "basicConstraints=critical,CA:TRUE\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n" % ",".join(names)
    )
    handle = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False)
    try:
        handle.write(config)
        handle.close()
        rc, _ = osutil.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", str(VALID_DAYS),
            "-keyout", key_path, "-out", cert_path, "-config", handle.name], timeout=60)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    if rc != 0 or not os.path.exists(cert_path) or not os.path.exists(key_path):
        return False
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return True


def ensure(folder, hosts, prefer_openssl=True):
    """Make sure a usable certificate exists for these hosts.

    Returns (cert_path, key_path), or None when one could not be made.
    """
    os.makedirs(folder, exist_ok=True)
    cert_path = os.path.join(folder, "cert.pem")
    key_path = os.path.join(folder, "key.pem")
    meta_path = os.path.join(folder, "cert.json")
    hosts = sorted(set(h for h in hosts if h))
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        meta = {}
    fresh = (
        os.path.exists(cert_path) and os.path.exists(key_path)
        and meta.get("hosts") == hosts
        and time.time() - meta.get("created", 0) < (VALID_DAYS - 60) * 86400
    )
    if fresh:
        return cert_path, key_path
    made = False
    method = None
    if prefer_openssl:
        try:
            made = generate_with_openssl(cert_path, key_path, hosts)
            method = "openssl"
        except OSError:
            made = False
    if not made:
        try:
            made = generate_pure_python(cert_path, key_path, hosts)
            method = "python"
        except (OSError, ValueError):
            made = False
    if not made:
        return None
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump({"hosts": hosts, "created": int(time.time()), "method": method}, handle)
    return cert_path, key_path


def fingerprint(cert_path):
    """SHA-256 fingerprint of the certificate, to compare with what the phone shows."""
    try:
        with open(cert_path, "r", encoding="ascii") as handle:
            text = handle.read()
        body = "".join(line for line in text.splitlines() if not line.startswith("-----"))
        digest = hashlib.sha256(base64.b64decode(body)).hexdigest().upper()
        return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    except (OSError, ValueError):
        return None
