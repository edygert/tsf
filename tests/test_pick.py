import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import pytest

from tsf import pick as pk
from tsf.data import Database


def f(name):
    return {"name": name, "description": name.upper(), "type": "FT_UINT8", "base": "", "bitmask": "", "blurb": ""}


DOC = {
    "tshark_version": "TShark test",
    "generated_utc": "2026-11-24T00:00:00Z",
    "protocols": {
        "2dparityfec": {"name": "FEC", "short_name": "2dparityfec", "fields": [f("2dparityfec.d")]},
        "dns": {"name": "Domain Name System", "short_name": "DNS", "fields": [f("dns.a"), f("dns.qry.name"), f("dns.qry.type")]},
        "inap": {"name": "INAP", "short_name": "INAP", "fields": [f("inap.cause")]},
        "ip": {"name": "Internet Protocol Version 4", "short_name": "IPv4", "fields": [f("ip.src")]},
    },
}


@pytest.fixture(scope="module")
def db():
    return Database(DOC)


@pytest.fixture
def has(db):
    return lambda t: pk.has_name_prefix(db, t)


# run, prefix, rest, search, default action
SPLIT_CASES = [
    ("", "", "", "", pk.REPLACE),
    ("dns.qr", "", "dns.qr", "dns.qr", pk.REPLACE),
    ("DNS.QR", "", "DNS.QR", "DNS.QR", pk.REPLACE),  # prefix test ignores case
    ("dns.qry.name", "", "dns.qry.name", "dns.qry.name", pk.REPLACE),
    ("2", "", "2", "", pk.INSERT),  # a value, though 2dparityfec starts with it
    ("2d", "", "2d", "2d", pk.REPLACE),
    ("-Ydns.qr", "-Y", "dns.qr", "dns.qr", pk.REPLACE),
    ("-e", "-e", "", "", pk.REPLACE),
    ("-c100", "-c", "100", "", pk.INSERT),
    ("--foo", "", "--foo", "", pk.INSERT),
    ("10.0.0.1", "", "10.0.0.1", "", pk.INSERT),
    ("0x1f", "", "0x1f", "", pk.INSERT),
    ("100", "", "100", "", pk.INSERT),
    ("1.5", "", "1.5", "", pk.INSERT),
    ("and", "", "and", "", pk.INSERT),
    ("in", "", "in", "", pk.INSERT),  # a keyword, though inap starts with it
    ("OR", "", "OR", "", pk.INSERT),
    ("ip_rfc1918", "", "ip_rfc1918", "", pk.INSERT),
    ("capture-2024.pcap", "", "capture-2024.pcap", "capture-2024.pcap", pk.INSERT),
    ("dns.qyr", "", "dns.qyr", "dns.qyr", pk.INSERT),  # typo: no name starts with it
]


@pytest.mark.parametrize("run,prefix,rest,search,action", SPLIT_CASES)
def test_split_run(has, run, prefix, rest, search, action):
    assert pk.split_run(run, has) == pk.Target(prefix, rest, search, action)


def test_split_run_rejects_other_characters(has):
    with pytest.raises(ValueError):
        pk.split_run("a b", has)


@pytest.mark.parametrize(
    "run,action,expected",
    [
        ("", pk.REPLACE, "dns.qry.name"),
        ("", pk.INSERT, "dns.qry.name"),  # nothing to keep
        ("dns.qr", pk.REPLACE, "dns.qry.name"),
        ("dns.qr", pk.INSERT, "dns.qr dns.qry.name"),
        ("-Ydns.qr", pk.REPLACE, "-Ydns.qry.name"),
        ("-Ydns.qr", pk.INSERT, "-Ydns.qr dns.qry.name"),
        ("-e", pk.REPLACE, "-edns.qry.name"),
        ("10.0.0.1", pk.INSERT, "10.0.0.1 dns.qry.name"),
        ("10.0.0.1", pk.REPLACE, "dns.qry.name"),  # after Ctrl-R
        ("--foo", pk.INSERT, "--foo dns.qry.name"),
    ],
)
def test_replacement(has, run, action, expected):
    assert pk.replacement(pk.split_run(run, has), "dns.qry.name", action) == expected


def test_describe(has):
    assert pk.describe(pk.split_run("", has), pk.REPLACE) == "Enter inserts at the cursor"
    assert pk.describe(pk.split_run("dns.qr", has), pk.REPLACE) == "Enter replaces 'dns.qr'"
    assert pk.describe(pk.split_run("-Y10", has), pk.INSERT) == "Enter inserts after '-Y10'"


def test_keywords_and_values_are_not_names(db):
    names = [*db.l_name, *db.l_pkey]
    assert not any(n in pk.KEYWORDS for n in names)
    assert not any(pk._VALUE.fullmatch(n) for n in names)


# ------------------------------------------------------------ cache


@pytest.fixture
def json_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("TSF_FIELDS", raising=False)
    monkeypatch.delenv("TSF_TSHARK", raising=False)
    p = tmp_path / "fields.json"
    p.write_text(json.dumps(DOC))
    return p


def test_cache_written_then_used(json_file):
    db1, path1, cached1 = pk.load_database(str(json_file), None, "1")
    assert not cached1 and os.path.exists(path1)
    assert os.stat(path1).st_mode & 0o777 == 0o600
    db2, path2, cached2 = pk.load_database(str(json_file), None, "1")
    assert cached2 and path2 == path1
    for attr in ("f_name", "f_desc", "l_name", "hay", "p_hay", "l_pkey", "tshark_version", "source"):
        assert getattr(db2, attr) == getattr(db1, attr)
    assert [(p.key, p.start, p.end) for p in db2.protocols] == [(p.key, p.start, p.end) for p in db1.protocols]
    assert db2.proto_by_key["dns"].field_count == 3


def test_cache_key_changes(json_file):
    _, path1, _ = pk.load_database(str(json_file), None, "1")
    _, path2, cached = pk.load_database(str(json_file), None, "2")  # tsf version
    assert path2 != path1 and not cached
    st = os.stat(json_file)
    os.utime(json_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    _, path3, cached = pk.load_database(str(json_file), None, "1")
    assert path3 not in (path1, path2) and not cached


def test_corrupt_cache_is_rebuilt(json_file):
    _, path, _ = pk.load_database(str(json_file), None, "1")
    with open(path, "wb") as fh:
        fh.write(b"not marshal data")
    db, _, cached = pk.load_database(str(json_file), None, "1")
    assert not cached and db.field_total == 6
    assert pk.read_cache(path) is not None


def test_remove_caches(json_file):
    pk.load_database(str(json_file), None, "1")
    pk.load_database(str(json_file), None, "2")
    assert pk.remove_caches() == 2
    assert pk.remove_caches() == 0


def test_resolve_source(json_file, monkeypatch, tmp_path):
    assert pk.resolve_source(str(json_file), None) == ("json", str(json_file.resolve()))
    monkeypatch.setenv("TSF_FIELDS", str(json_file))
    assert pk.resolve_source(None, None) == ("json", str(json_file.resolve()))
    monkeypatch.delenv("TSF_FIELDS")
    monkeypatch.setenv("TSF_TSHARK", str(tmp_path / "no-such-tshark"))
    with pytest.raises(pk.PickError):
        pk.resolve_source(None, None)


# ------------------------------------------- tsf --pick end to end, in a pty


def run_picker(json_file, query, keys, extra=()):
    """Run tsf --pick with /dev/tty on a pty and stdout on a pipe.

    Returns (exit status, stdout). `keys` is a list of strings sent to the
    terminal one at a time, 0.3 s apart.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))

    def controlling_tty():
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    env = dict(os.environ, TERM="xterm", LC_ALL="C.UTF-8", TSF_FIELDS=str(json_file))
    proc = subprocess.Popen(
        # --query=RUN, as the shell scripts pass it: a run can start with "-".
        [sys.executable, "-m", "tsf", "--pick", "--no-mouse", f"--query={query}", *extra],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=slave,
        env=env,
        start_new_session=True,
        preexec_fn=controlling_tty,
    )
    os.close(slave)

    def drain(t):
        end = time.time() + t
        while time.time() < end:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    os.read(master, 65536)
                except OSError:
                    return

    drain(1.5)  # load and first draw
    for k in keys:
        os.write(master, k.encode())
        drain(0.3)
    try:
        out, _ = proc.communicate(timeout=10)
    finally:
        os.close(master)
    return proc.returncode, out.decode()


ESC_DOWN = "\x1bOB"  # xterm Down in keypad-transmit mode (curses sends smkx)
ESC_RIGHT = "\x1bOC"


@pytest.mark.parametrize(
    "query,keys,status,out",
    [
        ("dns.qr", ["\r"], 0, "dns.qry.name\n"),
        ("dns.qr", [ESC_DOWN, "\r"], 0, "dns.qry.type\n"),
        ("dns.qr", ["\x12", "\r"], 0, "dns.qr dns.qry.name\n"),  # Ctrl-R: keep the run
        ("-Ydns.qr", ["\r"], 0, "-Ydns.qry.name\n"),
        ("10.0.0.1", ["ip.s", "\r"], 0, "10.0.0.1 ip.src\n"),  # typed and Enter in one batch
        ("", ["\t", "dns", "\r"], 0, "dns\n"),  # Protocols view: the protocol name
        ("", ["\t", "dns", ESC_RIGHT, "\r"], 0, "dns.a\n"),  # Right lists fields, Enter picks
        ("dns.qr", ["\x1b", "\x1b"], 1, ""),  # first Esc clears, second cancels
        ("dns.qr", ["\x11"], 1, ""),  # Ctrl-Q
        ("zzzz", ["\r", "\x11"], 1, ""),  # Enter with no results does nothing
    ],
)
def test_picker_end_to_end(json_file, query, keys, status, out):
    assert run_picker(json_file, query, keys) == (status, out)


def test_picker_without_tty_exits_2(json_file):
    env = dict(os.environ, TSF_FIELDS=str(json_file))
    proc = subprocess.run(
        [sys.executable, "-m", "tsf", "--pick", "--query", "dns"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
        start_new_session=True,  # no controlling terminal, so /dev/tty cannot be opened
    )
    assert proc.returncode == 2 and proc.stdout == b"" and b"/dev/tty" in proc.stderr
