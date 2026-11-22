"""Command-line entry point.

Data source, in order of precedence:
  tsf FILE.json            read a file written by dump_tshark_fields.py
  tsf --tshark [PATH]      run tshark -G protocols / -G fields (PATH
                           defaults to "tshark" on PATH)
  tsf                      ./tshark_fields.json if it exists, otherwise
                           run tshark if it is on PATH
--save FILE (with tshark as the source) also writes the document as JSON,
the same output dump_tshark_fields.py produces.
"""

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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="tsf",
        description="Browse and search every tshark display-filter field.",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("json", nargs="?", help=f"field JSON file (default: ./{DEFAULT_JSON})")
    src.add_argument(
        "--tshark",
        nargs="?",
        const="tshark",
        metavar="PATH",
        help="read the fields from tshark instead of a JSON file (default PATH: tshark)",
    )
    ap.add_argument("--save", metavar="FILE", help="with tshark as the source: also write the data to FILE as JSON")
    ap.add_argument("--no-mouse", action="store_true", help="leave the mouse to the terminal (native text selection)")
    args = ap.parse_args(argv)
    if args.save and args.json:
        ap.error("--save applies only when the data comes from tshark (--tshark)")

    t0 = time.perf_counter()
    try:
        if args.tshark:
            db, doc = _load_tshark(args.tshark)
        elif args.json:
            db, doc = _load_json(args.json)
        elif os.path.exists(DEFAULT_JSON):
            db, doc = _load_json(DEFAULT_JSON)
        elif shutil.which("tshark"):
            print(f"./{DEFAULT_JSON} not found; ", end="", file=sys.stderr)
            db, doc = _load_tshark("tshark")
        else:
            sys.exit(f"error: ./{DEFAULT_JSON} not found and tshark is not on PATH; pass FILE.json or --tshark PATH")
    except KeyboardInterrupt:
        sys.exit(130)
    print(
        f"{len(db.protocols):,} protocols, {db.field_total:,} fields in {time.perf_counter() - t0:.2f} s",
        file=sys.stderr,
    )

    if args.save:
        if doc is None:
            sys.exit("error: --save applies only when the data comes from tshark (--tshark)")
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
