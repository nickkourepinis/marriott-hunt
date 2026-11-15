#!/usr/bin/env python3
"""Pack WiFi Scout into one runnable file: dist/wifiscout.pyz

Usage:
    python3 tools/build_pyz.py
    python3 dist/wifiscout.pyz
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "wifiscout")
OUT = os.path.join(ROOT, "dist", "wifiscout.pyz")
MAIN = "import sys\nfrom wifiscout.cli import main\nsys.exit(main())\n"


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    count = 0
    with open(OUT, "wb") as handle:
        handle.write(b"#!/usr/bin/env python3\n")
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("__main__.py", MAIN)
            for folder, dirs, files in os.walk(PACKAGE):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in sorted(files):
                    if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                        continue
                    path = os.path.join(folder, name)
                    archive.write(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
                    count += 1
    try:
        os.chmod(OUT, 0o755)
    except OSError:
        pass
    print("wrote %s (%d files, %d KB)" % (os.path.relpath(OUT, ROOT), count, os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
