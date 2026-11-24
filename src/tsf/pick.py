"""Picker mode: `tsf --pick` for the shell key binding (no curses here).

The shell passes the run of name characters around the cursor
(`--query RUN`). split_run() decides the starting search and whether Enter
replaces the run or keeps it; replacement() builds the text the shell puts
in place of the run. run_on_tty() runs the curses UI on /dev/tty, because
the shell captures stdout with $(...).

Exit status of `tsf --pick`: 0 = a name was chosen (one line on stdout),
1 = cancelled, 2 = error (message on stderr).
"""

import glob
import hashlib
import marshal
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass

from .data import Database, load
from .tshark import TsharkError, build_document

REPLACE, INSERT = "replace", "insert"

# Words of the display filter language: operators, functions and boolean
# literals, from wireshark-filter(4) of TShark 4.0.17 and 4.6.9. A run equal
# to one of these (any case) is kept, never replaced.
KEYWORDS = frozenset(
    """
    and or xor not in
    eq ne gt lt ge le any_eq all_ne all_eq any_ne
    contains matches bitand bitwise_and any all true false
    upper lower len count string vals dec hex float double max min abs
    ip_special_name ip_special_mask ip_linklocal ip_multicast ip_rfc1918 ip_ula
    """.split()
)
# Decimal, dotted decimal (IPv4 address, version, float) or hex value. No
# field or protocol name of TShark 4.0.17 or 4.6.9 matches this in full.
_VALUE = re.compile(r"[0-9]+(?:\.[0-9]+)*|0[xX][0-9A-Fa-f]+")
# An option letter with its argument attached, e.g. -Ydns.qry.
_OPTION = re.compile(r"-[A-Za-z]")
RUN_CHARS = re.compile(r"[A-Za-z0-9._-]*")

# Increment when the layout written by save_cache() changes.
CACHE_FORMAT = 1


class PickError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    """What the key binding found at the cursor, after split_run()."""

    prefix: str  # kept in every case, e.g. "-Y" of "-Ydns.qr"
    rest: str  # the run after the prefix
    search: str  # starting search text
    action: str  # default action for Enter: REPLACE or INSERT


def has_name_prefix(db: Database, text: str) -> bool:
    """True if some field or protocol name starts with `text` (any case).

    A linear scan: one test per key press, and on 236,478 names (TShark
    4.0.17) a scan without a match measured 11.1 ms, against 116.0 ms to
    sort the names for a binary search.
    """
    t = text.lower()
    return any(n.startswith(t) for n in db.l_name) or any(n.startswith(t) for n in db.l_pkey)


def split_run(run: str, has_prefix) -> Target:
    """Decide the starting search and the default action for `run`.

    `has_prefix(text)` tells whether some loaded name starts with `text`.
    """
    if not RUN_CHARS.fullmatch(run):
        raise ValueError(f"not a run of name characters: {run!r}")
    if run.startswith("--"):  # a long option: never part of a name
        return Target("", run, "", INSERT)
    prefix = ""
    if _OPTION.match(run):
        prefix, run = run[:2], run[2:]
    if not run:
        return Target(prefix, "", "", REPLACE)  # REPLACE of nothing inserts at the cursor
    if _VALUE.fullmatch(run) or run.lower() in KEYWORDS:
        return Target(prefix, run, "", INSERT)
    if has_prefix(run):
        return Target(prefix, run, run, REPLACE)
    return Target(prefix, run, run, INSERT)


def replacement(t: Target, name: str, action: str) -> str:
    """The text that replaces the whole run on the command line."""
    if action == REPLACE or not t.rest:
        return t.prefix + name
    return f"{t.prefix}{t.rest} {name}"


def describe(t: Target, action: str) -> str:
    """One line for the search row: what Enter will do."""
    shown = t.rest if len(t.rest) <= 24 else t.rest[:23] + "…"
    if not t.rest:
        return "Enter inserts at the cursor"
    if action == REPLACE:
        return f"Enter replaces '{shown}'"
    return f"Enter inserts after '{t.prefix}{shown}'"


# ---------------------------------------------------------------- data


def cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "tsf")


def resolve_source(file: str | None, tshark: str | None) -> tuple[str, str]:
    """Return (kind, path): kind is "json" or "tshark", path is absolute.

    Order: FILE, --tshark PATH, $TSF_FIELDS, $TSF_TSHARK, tshark on $PATH.
    ./tshark_fields.json is not used, so the result does not depend on the
    shell's current directory.
    """
    if file:
        return "json", os.path.realpath(file)
    if not tshark and os.environ.get("TSF_FIELDS"):
        return "json", os.path.realpath(os.path.expanduser(os.environ["TSF_FIELDS"]))
    exe = tshark or os.environ.get("TSF_TSHARK") or "tshark"
    found = shutil.which(os.path.expanduser(exe))
    if not found:
        raise PickError(f"tshark not found ({exe}); set TSF_FIELDS or TSF_TSHARK")
    return "tshark", os.path.realpath(found)


def cache_path(kind: str, path: str, version: str) -> str:
    try:
        st = os.stat(path)
    except OSError as exc:
        raise PickError(f"cannot read {path}: {exc.strerror}") from exc
    key = "\0".join(
        map(str, (CACHE_FORMAT, kind, path, st.st_size, st.st_mtime_ns, sys.version_info[:3], marshal.version, version))
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return os.path.join(cache_dir(), f"pick-{digest}.marshal")


def read_cache(path: str) -> Database | None:
    try:
        with open(path, "rb") as fh:
            state = marshal.load(fh)
        return Database.from_columns(state)
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        return None  # missing or unreadable: rebuilt by the caller


def save_cache(path: str, db: Database) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".pick-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:  # mkstemp creates the file with mode 0600
            marshal.dump(db.to_columns(), fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_caches() -> int:
    """Delete every picker cache file. Returns the number deleted."""
    n = 0
    for p in glob.glob(os.path.join(cache_dir(), "pick-*.marshal")):
        try:
            os.unlink(p)
            n += 1
        except OSError:
            pass
    return n


def load_database(file: str | None, tshark: str | None, version: str) -> tuple[Database, str, bool]:
    """Load the picker's data. Returns (database, cache path, cache was used)."""
    kind, path = resolve_source(file, tshark)
    cpath = cache_path(kind, path, version)
    db = read_cache(cpath)
    if db is not None:
        return db, cpath, True
    try:
        if kind == "json":
            db = load(path)
        else:
            db = Database(build_document(path), source=f"{path} (live)")
    except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        raise PickError(f"cannot load {path}: {exc}") from exc
    except TsharkError as exc:
        raise PickError(str(exc)) from exc
    try:
        save_cache(cpath, db)
    except OSError as exc:
        print(f"tsf: cannot write cache {cpath}: {exc.strerror}", file=sys.stderr)
    return db, cpath, False


# ---------------------------------------------------------------- tty


def run_on_tty(run_ui):
    """Call run_ui() with fds 0 and 1 on /dev/tty; restore them afterwards.

    The shell captures stdout with $(...), but curses draws on fd 1 and
    reads fd 0, and Python's curses module has no newterm() to choose
    another terminal. Returns what run_ui() returns.
    """
    try:
        tty = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        raise PickError(f"cannot open /dev/tty: {exc.strerror}") from exc
    sys.stdout.flush()
    saved_in, saved_out = os.dup(0), os.dup(1)
    try:
        os.dup2(tty, 0)
        os.dup2(tty, 1)
        return run_ui()
    finally:
        os.dup2(saved_in, 0)
        os.dup2(saved_out, 1)
        for fd in (saved_in, saved_out, tty):
            os.close(fd)
