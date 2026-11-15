"""OS helpers: platform detection and safe command execution."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading

if sys.platform == "darwin":
    PLATFORM = "mac"
elif sys.platform.startswith("win"):
    PLATFORM = "win"
else:
    PLATFORM = "linux"

_debug_dir = None
_debug_lock = threading.Lock()
_debug_seq = [0]


def set_debug_dir(path):
    """Save the raw output of every external command into this folder."""
    global _debug_dir
    _debug_dir = path
    if path:
        os.makedirs(path, exist_ok=True)


def which(name):
    return shutil.which(name)


def _save_debug(cmd, rc, out):
    if not _debug_dir:
        return
    with _debug_lock:
        _debug_seq[0] += 1
        seq = _debug_seq[0]
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", " ".join(str(c) for c in cmd))[:80]
    try:
        with open(os.path.join(_debug_dir, "%03d_%s.txt" % (seq, label)), "w", encoding="utf-8") as fh:
            fh.write("$ %s\n[exit %s]\n%s" % (" ".join(str(c) for c in cmd), rc, out))
    except OSError:
        pass


def run(cmd, timeout=10.0):
    """Run a command without a shell.

    Returns (exit_code, text). The exit code is None when the program is
    missing, could not start, or timed out; text is then whatever was
    captured (often empty). Never raises.
    """
    if not cmd or not (os.path.isabs(cmd[0]) and os.path.exists(cmd[0])) and not which(cmd[0]):
        return None, ""
    kwargs = {}
    if PLATFORM == "win":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        encoding = "oem"
    else:
        encoding = "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            **kwargs
        )
        rc = proc.returncode
        raw = proc.stdout or b""
    except subprocess.TimeoutExpired as exc:
        rc = None
        raw = exc.stdout or b""
    except (OSError, ValueError):
        return None, ""
    try:
        out = raw.decode(encoding, errors="replace")
    except LookupError:
        out = raw.decode("utf-8", errors="replace")
    _save_debug(cmd, rc, out)
    return rc, out


def read_file(path, limit=1 << 20):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def enable_ansi():
    """Turn on ANSI escape handling in the Windows console. Returns True if colours can be used."""
    if not sys.stdout.isatty():
        return False
    if PLATFORM != "win":
        return os.environ.get("TERM", "") != "dumb"
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False
