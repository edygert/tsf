"""Command-line entry point. See USAGE below or run: tsf --help"""

import argparse
import curses
import json
import locale
import os
import shutil
import sys
import time

from .data import Database, load
from .tshark import TsharkError, build_document
from .ui import App

DEFAULT_JSON = "tshark_fields.json"


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

Inside the program, press F1 or Ctrl-G for keys and search syntax.
"""


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("tsf")
    except Exception:
        return "unknown"


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
    ap.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    args = ap.parse_args(argv)
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
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(lambda scr: App(scr, db, mouse=not args.no_mouse).run())


if __name__ == "__main__":
    main()
