#!/usr/bin/env python3
"""Rebuild wifiscout/data/oui.tsv.gz from an IEEE oui.txt registry file.

Usage:
    python3 tools/build_oui.py /path/to/oui.txt

The registry is public: https://standards-oui.ieee.org/oui/oui.txt
"""
import gzip
import os
import re
import sys


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rows = {}
    with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"^([0-9A-F]{6})\s+\(base 16\)\s+(.+?)\s*$", line)
            if match:
                name = re.sub(r"\s+", " ", match.group(2)).strip()[:60]
                if name and name.lower() != "private":
                    rows.setdefault(match.group(1).lower(), name)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wifiscout", "data", "oui.tsv.gz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    body = "".join("%s\t%s\n" % (prefix, rows[prefix]) for prefix in sorted(rows)).encode("utf-8")
    with open(out, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as zipped:
            zipped.write(body)
    print("wrote %d vendors to %s (%d KB)" % (len(rows), os.path.normpath(out), os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
