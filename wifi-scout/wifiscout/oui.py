"""Who made a device, from the first half of its MAC address (IEEE registry)."""
from __future__ import annotations

import gzip
import os
import pkgutil
import threading

_table = None
_lock = threading.Lock()


def _parse(blob):
    table = {}
    for line in gzip.decompress(blob).decode("utf-8", errors="replace").splitlines():
        prefix, _, name = line.partition("\t")
        if len(prefix) == 6 and name:
            table[prefix] = name
    return table


def _load():
    global _table
    with _lock:
        if _table is not None:
            return _table
        table = {}
        try:
            blob = pkgutil.get_data("wifiscout", "data/oui.tsv.gz")
            if blob:
                table = _parse(blob)
        except (OSError, ValueError, EOFError):
            table = {}
        override = os.environ.get("WIFISCOUT_OUI")
        if override and os.path.exists(override):
            try:
                with open(override, "rb") as handle:
                    table.update(_parse(handle.read()))
            except (OSError, ValueError, EOFError):
                pass
        _table = table
        return _table


def is_random(mac):
    """True for a private (randomised) address, as phones use by default."""
    try:
        return bool(int(mac[:2], 16) & 0x02)
    except (TypeError, ValueError):
        return False


def lookup(mac):
    """Vendor name for a MAC like 'a4:11:62:aa:bb:cc', or None."""
    if not mac or is_random(mac):
        return None
    prefix = mac.replace(":", "").replace("-", "").lower()[:6]
    return _load().get(prefix)


def size():
    return len(_load())
