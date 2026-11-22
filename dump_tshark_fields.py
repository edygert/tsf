#!/usr/bin/env python3
"""Dump every tshark display-filter field, grouped by protocol, as JSON.

Data sources (both run once):
  tshark -G protocols  -> one line per protocol:
                          full name, short name, filter name (4.6.x appends
                          3 T/F flag columns; 4.0.x does not; both work)
  tshark -G fields     -> 'P' lines (protocols) and 'F' lines (fields):
                          F, description, field name, type, parent protocol
                          filter name, base, bitmask, blurb

Output structure:
  {
    "tshark_version": "TShark (Wireshark) 4.6.9.",
    "generated_utc":  "2026-11-20T17:00:00Z",
    "protocol_count": 3169,
    "field_count":    269374,
    "protocols": {
      "dns": {
        "name": "Domain Name System",
        "short_name": "DNS",
        "field_count": 330,
        "fields": [
          {"name": "dns.flags", "description": "Flags", "type": "FT_UINT16",
           "base": "BASE_HEX", "bitmask": "0x0", "blurb": ""},
          ...
        ]
      },
      ...
    }
  }

Protocols are keyed by display-filter name and sorted. Fields within a
protocol are sorted by field name. Duplicate field names (one name registered
more than once with a different type or description) are kept as separate
entries.

Usage:
  dump_tshark_fields.py [-o FILE] [--tshark PATH] [--indent N]
"""

import argparse
import datetime
import json
import subprocess
import sys


def run_tshark(tshark, *args):
    """Run tshark with the given arguments and return stdout as text."""
    result = subprocess.run(
        [tshark, *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
    )
    return result.stdout


def build_glossary(tshark):
    version = run_tshark(tshark, "-v").splitlines()[0].strip()

    protocols = {}

    # -G protocols supplies the short name, which -G fields does not.
    for line in run_tshark(tshark, "-G", "protocols").splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        full_name, short_name, filter_name = cols[0], cols[1], cols[2]
        if filter_name in protocols:
            # A few protocols share one filter name (4.6.9: tpkt, docsis_map,
            # ipv6.routing.type.mipv6). Keep the first registration and
            # record the others; their fields share the same parent key.
            protocols[filter_name].setdefault("also_registered_as", []).append(
                {"name": full_name, "short_name": short_name})
            continue
        protocols[filter_name] = {
            "name": full_name,
            "short_name": short_name,
            "field_count": 0,
            "fields": [],
        }

    field_count = 0
    for line in run_tshark(tshark, "-G", "fields").splitlines():
        cols = line.split("\t")
        if cols[0] == "P" and len(cols) >= 3:
            # Register any protocol that -G protocols did not list.
            protocols.setdefault(cols[2], {
                "name": cols[1],
                "short_name": "",
                "field_count": 0,
                "fields": [],
            })
        elif cols[0] == "F" and len(cols) >= 5:
            cols += [""] * (8 - len(cols))  # pad missing trailing columns
            _, description, name, ftype, parent, base, bitmask, blurb = cols[:8]
            proto = protocols.setdefault(parent, {
                "name": parent,
                "short_name": "",
                "field_count": 0,
                "fields": [],
            })
            proto["fields"].append({
                "name": name,
                "description": description,
                "type": ftype,
                "base": base,
                "bitmask": bitmask,
                "blurb": blurb,
            })
            field_count += 1

    for proto in protocols.values():
        proto["fields"].sort(key=lambda f: (f["name"], f["type"]))
        proto["field_count"] = len(proto["fields"])

    return {
        "tshark_version": version,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "protocol_count": len(protocols),
        "field_count": field_count,
        "protocols": dict(sorted(protocols.items())),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Dump all tshark fields grouped by protocol as JSON.")
    parser.add_argument("-o", "--output", default="-",
                        help="output file (default: stdout)")
    parser.add_argument("--tshark", default="tshark",
                        help="tshark executable (default: tshark on PATH)")
    parser.add_argument("--indent", type=int, default=None,
                        help="JSON indent width (default: compact, one line)")
    args = parser.parse_args()

    try:
        glossary = build_glossary(args.tshark)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"error: running {args.tshark} failed: {exc}")

    if args.output == "-":
        json.dump(glossary, sys.stdout, indent=args.indent, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(glossary, fh, indent=args.indent, ensure_ascii=False)
            fh.write("\n")
        print(f"{glossary['protocol_count']} protocols, "
              f"{glossary['field_count']} fields -> {args.output}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
