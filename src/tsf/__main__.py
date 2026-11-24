"""Command-line entry point. See USAGE below or run: tsf --help"""

import argparse
import curses
import json
import locale
import os
import re
import shlex
import shutil
import sys
import time
from importlib import resources

from .data import Database, load
from .tshark import TsharkError, build_document
from .ui import App

DEFAULT_JSON = "tshark_fields.json"
SHELLS = ("bash", "zsh")
BLOCK_START, BLOCK_END = "# >>> tsf >>>", "# <<< tsf <<<"


def _load_json(path: str) -> tuple[Database, dict | None]:
    print(f"Loading {path} …", file=sys.stderr, flush=True)
    try:
        return load(path), None
    except OSError as exc:
        sys.exit(f"error: cannot read {path}: {exc}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        sys.exit(f"error: {path} is not valid JSON: {exc}")


def _load_tshark(tshark: str) -> tuple[Database, dict]:
    print(f"Running {tshark} -G protocols and -G fields …", file=sys.stderr, flush=True)
    try:
        doc = build_document(tshark)
    except TsharkError as exc:
        sys.exit(f"error: {exc}")
    resolved = shutil.which(tshark) or tshark
    return Database(doc, source=f"{resolved} (live)"), doc


USAGE = f"""\
Where the data comes from:
  tsf                  ./{DEFAULT_JSON} if it exists, otherwise tshark
  tsf FILE             FILE, written earlier by --save or dump_tshark_fields.py
  tsf --tshark         the tshark found on $PATH
  tsf --tshark PATH    the tshark executable at PATH

Examples:
  tsf --tshark                     browse the fields of the installed tshark
  tsf --save fields-4.0.17.json    the same, and keep a copy
  tsf fields-4.0.17.json           browse the copy later, or on another machine

Picker key for the shell (Ctrl-X Ctrl-T on a command line opens tsf and puts
the chosen name at the cursor; in zsh, Tab does too in the arguments of
tshark -Y, -R, -e, -j, -J and -O):
  tsf --install-shell              install it for bash or zsh ($SHELL)
  tsf --uninstall-shell            remove it
  tsf --refresh                    rebuild the picker's cache after a tshark
                                   upgrade or after adding Lua dissectors
  The picker reads $TSF_FIELDS (a file written by --save) if set, otherwise
  $TSF_TSHARK or the tshark on $PATH; it never reads ./{DEFAULT_JSON}.

Inside the program, press F1 or Ctrl-G for keys and search syntax.
"""


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("tsf")
    except Exception:
        return "unknown"


# ------------------------------------------------------------ picker


def _start_curses_env() -> None:
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")


def _pick_fail(msg: str) -> None:
    print(f"tsf: {msg}", file=sys.stderr)
    sys.exit(2)


def _pick_load(args):
    """Load the picker's data, rebuilding its cache on a miss."""
    from . import pick as pk

    on_tty = sys.stderr.isatty()
    if on_tty:
        sys.stderr.write("tsf: loading fields …")
        sys.stderr.flush()
    try:
        return pk.load_database(args.file, args.tshark, _version())
    except pk.PickError as exc:
        error = str(exc)
    finally:
        if on_tty:
            sys.stderr.write("\r\033[K")  # erase the loading message
            sys.stderr.flush()
    _pick_fail(error)  # reached only after a PickError


def _pick(args) -> None:
    """tsf --pick: exit 0 with the replacement on stdout, 1 if cancelled, 2 on error."""
    from . import pick as pk

    if args.refresh:
        pk.remove_caches()
    db, _, _ = _pick_load(args)
    try:
        target = pk.split_run(args.query or "", lambda t: pk.has_name_prefix(db, t))
    except ValueError as exc:
        _pick_fail(str(exc))
    _start_curses_env()
    try:
        result = pk.run_on_tty(
            lambda: curses.wrapper(lambda scr: App(scr, db, mouse=not args.no_mouse, pick=target).run())
        )
    except pk.PickError as exc:
        _pick_fail(str(exc))
    if result is None:
        sys.exit(1)
    print(result)


def _refresh(args) -> None:
    """tsf --refresh without --pick: rebuild the picker's cache and report."""
    from . import pick as pk

    n = pk.remove_caches()
    db, cpath, _ = _pick_load(args)
    print(f"Deleted {n} cache file{'s' if n != 1 else ''}; wrote {cpath}", file=sys.stderr)
    print(f"{db.tshark_version}: {len(db.protocols):,} protocols, {db.field_total:,} fields from {db.source}", file=sys.stderr)


# ------------------------------------------------------- shell setup


def _shell_script(shell: str) -> str:
    return resources.files("tsf").joinpath(f"shell/tsf.{shell}").read_text(encoding="utf-8")


def _shell_paths(shell: str) -> tuple[str, str]:
    """(script path, rc file path) for `shell`."""
    home = os.path.expanduser("~")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    script = os.path.join(data, "tsf", f"tsf.{shell}")
    if shell == "bash":
        rc = os.path.join(home, ".bashrc")
    else:
        rc = os.path.join(os.environ.get("ZDOTDIR") or home, ".zshrc")
    return script, rc


def _which_shell(name: str) -> str:
    shell = name or os.path.basename(os.environ.get("SHELL", ""))
    if shell not in SHELLS:
        sys.exit(f"error: cannot tell which shell to set up ({shell or '$SHELL is empty'}); give bash or zsh")
    return shell


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _confirm(yes: bool) -> None:
    if yes:
        return
    try:
        answer = input("Proceed? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit("Nothing changed.")


def _install_shell(name: str, yes: bool) -> None:
    shell = _which_shell(name)
    script, rc = _shell_paths(shell)
    text = _shell_script(shell)
    block = f"{BLOCK_START}\n[ -f {shlex.quote(script)} ] && . {shlex.quote(script)}\n{BLOCK_END}\n"
    rc_text = _read(rc)
    has_block = BLOCK_START in rc_text
    print(f"Write {script} ({len(text.encode())} bytes)")
    if has_block:
        print(f"{rc} already loads it; it is not changed")
    else:
        print(f"Append these 3 lines to {rc}:\n\n{block}")
    _confirm(yes)
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(text)
    if not has_block:
        # Append in place, so a symlinked rc file stays a symlink.
        with open(rc, "a", encoding="utf-8") as fh:
            fh.write(("\n" if rc_text and not rc_text.endswith("\n") else "") + block)
    keys = "Ctrl-X Ctrl-T, and Tab in tshark field arguments" if shell == "zsh" else "Ctrl-X Ctrl-T"
    print(f"Done. New {shell} shells have the key {keys}; in this shell run: . {shlex.quote(script)}")


def _uninstall_shell(name: str, yes: bool) -> None:
    shell = _which_shell(name)
    script, rc = _shell_paths(shell)
    rc_text = _read(rc)
    # The block as --install-shell appends it: whole lines, start to end marker.
    pattern = re.compile(rf"^{re.escape(BLOCK_START)}\n.*?^{re.escape(BLOCK_END)}\n?", re.S | re.M)
    has_block = bool(pattern.search(rc_text))
    has_script = os.path.exists(script)
    if not has_block and not has_script:
        print(f"Not installed for {shell}: no block in {rc} and no {script}")
        return
    if has_block:
        print(f"Remove the lines from {BLOCK_START!r} to {BLOCK_END!r} in {rc}")
    if has_script:
        print(f"Delete {script}")
    _confirm(yes)
    if has_block:
        with open(rc, "w", encoding="utf-8") as fh:  # in place: a symlinked rc stays a symlink
            fh.write(pattern.sub("", rc_text, count=1))
    if has_script:
        os.unlink(script)
    print(f"Done. Shells started from now on do not have the key; already running {shell} shells keep it until they exit.")


# -------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="tsf",
        description="Browse and search every tshark display-filter field.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("file", nargs="?", metavar="FILE", help=f"JSON file to read (default: ./{DEFAULT_JSON})")
    src.add_argument(
        "--tshark",
        nargs="?",
        const="tshark",
        metavar="PATH",
        help="read the fields from tshark instead of a file (default PATH: tshark)",
    )
    ap.add_argument(
        "--save",
        metavar="FILE",
        help="read the fields from tshark and also write them to FILE as JSON; combine with --tshark PATH to choose the executable",
    )
    ap.add_argument(
        "--no-mouse",
        action="store_true",
        help="ignore the mouse so the terminal can select text",
    )
    ap.add_argument("--query", metavar="TEXT", help="start with TEXT in the search line")
    ap.add_argument(
        "--pick",
        action="store_true",
        help="picker for the shell key: print the chosen name (exit 0), or exit 1 if cancelled; "
        "TEXT of --query is the text at the cursor",
    )
    ap.add_argument("--refresh", action="store_true", help="delete and rebuild the picker's cache")
    ap.add_argument("--shell", choices=SHELLS, help="print the picker key script for bash or zsh")
    ap.add_argument(
        "--install-shell",
        nargs="?",
        const="",
        metavar="SHELL",
        help="install the picker key for bash or zsh (default: from $SHELL)",
    )
    ap.add_argument(
        "--uninstall-shell",
        nargs="?",
        const="",
        metavar="SHELL",
        help="remove what --install-shell added",
    )
    ap.add_argument("--yes", action="store_true", help="do not ask before --install-shell or --uninstall-shell")
    ap.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    args = ap.parse_args(argv)

    if args.shell:
        sys.stdout.write(_shell_script(args.shell))
        return
    if args.install_shell is not None:
        _install_shell(args.install_shell, args.yes)
        return
    if args.uninstall_shell is not None:
        _uninstall_shell(args.uninstall_shell, args.yes)
        return
    if args.save and (args.pick or args.refresh):
        ap.error("--save cannot be used with --pick or --refresh")
    if args.pick:
        try:
            _pick(args)
        except KeyboardInterrupt:
            sys.exit(130)
        return
    if args.refresh:
        try:
            _refresh(args)
        except KeyboardInterrupt:
            sys.exit(130)
        return

    if args.save:
        if args.file:
            ap.error("--save cannot be used with FILE; it saves what tshark reports")
        args.tshark = args.tshark or "tshark"

    t0 = time.perf_counter()
    try:
        if args.tshark:
            db, doc = _load_tshark(args.tshark)
        elif args.file:
            db, doc = _load_json(args.file)
        elif os.path.exists(DEFAULT_JSON):
            db, doc = _load_json(DEFAULT_JSON)
        elif shutil.which("tshark"):
            print(f"./{DEFAULT_JSON} not found; ", end="", file=sys.stderr)
            db, doc = _load_tshark("tshark")
        else:
            sys.exit(f"error: ./{DEFAULT_JSON} not found and tshark is not on PATH; give a FILE or --tshark PATH")
    except KeyboardInterrupt:
        sys.exit(130)
    print(
        f"{len(db.protocols):,} protocols, {db.field_total:,} fields in {time.perf_counter() - t0:.2f} s",
        file=sys.stderr,
    )

    if args.save:
        try:
            with open(args.save, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc:
            sys.exit(f"error: cannot write {args.save}: {exc}")
        print(f"Saved {args.save}", file=sys.stderr)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.exit("error: tsf needs an interactive terminal")
    _start_curses_env()
    curses.wrapper(lambda scr: App(scr, db, mouse=not args.no_mouse, query=args.query or "").run())


if __name__ == "__main__":
    main()
