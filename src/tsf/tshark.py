"""Build the field document directly from tshark, without a JSON file.

Produces the same structure that dump_tshark_fields.py writes:
  tshark -G protocols  -> full name, short name, filter name per protocol
                          (4.6.x appends 3 T/F flag columns; 4.0.x does not)
  tshark -G fields     -> 'P' lines (protocols) and 'F' lines (fields):
                          F, description, field name, type, parent protocol
                          filter name, base, bitmask, blurb
"""

import datetime
import subprocess


class TsharkError(Exception):
    pass


def _run(tshark: str, *args: str) -> str:
    try:
        result = subprocess.run(
            [tshark, *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TsharkError(f"running {tshark} {' '.join(args)} failed: {exc}") from exc
    return result.stdout


def _new_proto(name: str, short_name: str) -> dict:
    return {"name": name, "short_name": short_name, "field_count": 0, "fields": []}


def build_document(tshark: str = "tshark") -> dict:
    version_lines = _run(tshark, "-v").splitlines()
    version = version_lines[0].strip() if version_lines else ""

    protocols: dict[str, dict] = {}
    # -G protocols supplies the short name, which -G fields does not.
    for line in _run(tshark, "-G", "protocols").splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        full_name, short_name, filter_name = cols[0], cols[1], cols[2]
        if filter_name in protocols:
            # Several protocols can share one filter name (4.0.17: tpkt,
            # docsis_map, ipv6.routing.type.mipv6). Keep the first
            # registration and record the others.
            protocols[filter_name].setdefault("also_registered_as", []).append(
                {"name": full_name, "short_name": short_name}
            )
            continue
        protocols[filter_name] = _new_proto(full_name, short_name)

    field_count = 0
    for line in _run(tshark, "-G", "fields").splitlines():
        cols = line.split("\t")
        if cols[0] == "P" and len(cols) >= 3:
            protocols.setdefault(cols[2], _new_proto(cols[1], ""))
        elif cols[0] == "F" and len(cols) >= 5:
            cols += [""] * (8 - len(cols))  # pad missing trailing columns
            _, description, name, ftype, parent, base, bitmask, blurb = cols[:8]
            proto = protocols.setdefault(parent, _new_proto(parent, ""))
            proto["fields"].append(
                {
                    "name": name,
                    "description": description,
                    "type": ftype,
                    "base": base,
                    "bitmask": bitmask,
                    "blurb": blurb,
                }
            )
            field_count += 1

    for proto in protocols.values():
        proto["fields"].sort(key=lambda f: (f["name"], f["type"]))
        proto["field_count"] = len(proto["fields"])

    return {
        "tshark_version": version,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "protocol_count": len(protocols),
        "field_count": field_count,
        "protocols": dict(sorted(protocols.items())),
    }
