#!/usr/bin/env python3
"""Query the JSON file written by dump_tshark_fields.py.

Subcommands:
  protocols [-g TEXT]       List every protocol: filter name, short name,
                            field count, full name. -g keeps only protocols
                            whose filter name, short name, or full name
                            contains TEXT (case-insensitive).
  fields PROTOCOL [-g TEXT] List the fields of one protocol. PROTOCOL is
                            matched case-insensitively against the filter
                            name (e.g. "dns"), then the short name (e.g.
                            "DNS"). -g keeps only fields whose name,
                            description, or blurb contains TEXT.

Output formats (-f, applies to both subcommands):
  table  aligned columns for reading in a terminal (default)
  tsv    tab-separated with a header row, for awk/cut/spreadsheets
  json   the matching JSON objects
  names  filter names only, one per line (for tshark -e)

Usage examples:
  query_tshark_fields.py -j fields.json protocols
  query_tshark_fields.py -j fields.json protocols -g smb
  query_tshark_fields.py -j fields.json fields dns
  query_tshark_fields.py -j fields.json fields http -g agent
  query_tshark_fields.py -j fields.json fields dns -f names
"""

import argparse
import difflib
import json
import sys

DEFAULT_JSON = "tshark_fields.json"  # resolved against the current directory


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        sys.exit(f"error: cannot read {path}: {exc}\n"
                 f"Generate it with: dump_tshark_fields.py -o {path}")


def contains(needle, *haystacks):
    needle = needle.lower()
    return any(needle in (h or "").lower() for h in haystacks)


MAX_PAD = 32  # columns are padded to at most 32 characters; longer cells overflow


def print_table(header, rows):
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row[:-1]):  # last column is not padded
            widths[i] = min(max(widths[i], len(cell)), max(MAX_PAD, len(header[i])))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths[:-1]) + "  {}"
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths[:-1]), "-" * len(header[-1])))
    for row in rows:
        print(fmt.format(*row))


def print_tsv(header, rows):
    print("\t".join(header))
    for row in rows:
        print("\t".join(cell.replace("\t", " ") for cell in row))


def find_protocol(protocols, query):
    if query in protocols:
        return query
    q = query.lower()
    for key, proto in protocols.items():
        if key.lower() == q:
            return key
    for key, proto in protocols.items():
        if proto.get("short_name", "").lower() == q:
            return key
    return None


def cmd_protocols(data, args):
    selected = [
        (key, proto) for key, proto in data["protocols"].items()
        if not args.grep
        or contains(args.grep, key, proto.get("short_name"), proto.get("name"))
    ]
    if args.format == "json":
        json.dump({k: {kk: vv for kk, vv in p.items() if kk != "fields"}
                   for k, p in selected}, sys.stdout, indent=2,
                  ensure_ascii=False)
        print()
        return
    if args.format == "names":
        for key, _ in selected:
            print(key)
        return
    header = ["filter_name", "short_name", "fields", "name"]
    rows = [[key, proto.get("short_name", ""), str(proto["field_count"]),
             proto.get("name", "")] for key, proto in selected]
    (print_tsv if args.format == "tsv" else print_table)(header, rows)
    if args.format == "table":
        print(f"\n{len(rows)} of {data['protocol_count']} protocols "
              f"({data['tshark_version']})", file=sys.stderr)


def cmd_fields(data, args):
    protocols = data["protocols"]
    key = find_protocol(protocols, args.protocol)
    if key is None:
        close = difflib.get_close_matches(args.protocol.lower(),
                                          list(protocols), n=8, cutoff=0.6)
        msg = f"error: no protocol named '{args.protocol}'"
        if close:
            msg += "\nclosest filter names: " + ", ".join(close)
        msg += "\nlist protocols with: protocols -g " + args.protocol
        sys.exit(msg)

    proto = protocols[key]
    fields = [f for f in proto["fields"]
              if not args.grep
              or contains(args.grep, f["name"], f["description"], f["blurb"])]

    if args.format == "json":
        json.dump({"filter_name": key, "name": proto.get("name", ""),
                   "short_name": proto.get("short_name", ""),
                   "fields": fields}, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    if args.format == "names":
        for name in dict.fromkeys(f["name"] for f in fields):  # de-duplicate
            print(name)
        return
    header = ["field", "type", "description", "blurb"]
    rows = [[f["name"], f["type"], f["description"], f["blurb"]]
            for f in fields]
    if args.format == "tsv":
        print_tsv(header, rows)
    else:
        print_table(header, rows)
        print(f"\n{len(rows)} of {proto['field_count']} fields in {key} "
              f"({proto.get('name', '')})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="List tshark protocols, or the fields of one protocol, "
                    "from the JSON written by dump_tshark_fields.py.")
    parser.add_argument("-j", "--json", default=DEFAULT_JSON,
                        help=f"glossary JSON file (default: {DEFAULT_JSON})")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-f", "--format", default="table",
                        choices=["table", "tsv", "json", "names"],
                        help="output format (default: table)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("protocols", parents=[common],
                            help="list all protocols")
    p_list.add_argument("-g", "--grep", help="substring filter")

    p_fields = sub.add_parser("fields", parents=[common],
                              help="list fields of one protocol")
    p_fields.add_argument("protocol", help="filter name or short name")
    p_fields.add_argument("-g", "--grep", help="substring filter")

    args = parser.parse_args()
    data = load(args.json)
    try:
        if args.command == "protocols":
            cmd_protocols(data, args)
        else:
            cmd_fields(data, args)
    except BrokenPipeError:  # e.g. piped into head
        sys.stderr.close()


if __name__ == "__main__":
    main()
