"""Load tshark_fields.json into flat, column-oriented lists.

Every field becomes one row index into parallel lists (one list per
attribute). Fields of one protocol occupy one contiguous index range, so a
protocol filter is a slice instead of a scan.
"""

import json
from dataclasses import dataclass, field


@dataclass(slots=True)
class Protocol:
    key: str  # display-filter name, e.g. "dns"
    name: str  # full name, e.g. "Domain Name System"
    short_name: str  # e.g. "DNS"
    field_count: int
    start: int  # first field row index of this protocol
    end: int  # one past the last field row index
    also_registered_as: list = field(default_factory=list)


class Database:
    """All protocols and fields from one tshark_fields.json document."""

    def __init__(self, doc: dict, source: str = ""):
        self.source = source  # where the data came from, shown in the title bar
        self.tshark_version: str = doc.get("tshark_version", "")
        self.generated_utc: str = doc.get("generated_utc", "")

        self.protocols: list[Protocol] = []
        self.proto_by_key: dict[str, Protocol] = {}

        # Parallel per-field columns (original case, for display).
        self.f_proto: list[str] = []  # protocol key
        self.f_name: list[str] = []
        self.f_desc: list[str] = []
        self.f_type: list[str] = []
        self.f_base: list[str] = []
        self.f_mask: list[str] = []
        self.f_blurb: list[str] = []
        # Lowercase columns used by search.
        self.l_name: list[str] = []
        self.l_desc: list[str] = []
        self.l_blurb: list[str] = []
        self.l_type: list[str] = []
        # name + "\n" + description + "\n" + blurb, lowercase: one
        # substring test per field per search term (query words never
        # contain whitespace, so a word cannot span two attributes), and one
        # re.MULTILINE search per field per regex (^ and $ anchor to each
        # attribute). The data contains no newline characters.
        self.hay: list[str] = []

        for key, p in doc.get("protocols", {}).items():
            start = len(self.f_name)
            for f in p.get("fields", ()):
                name = f.get("name", "")
                desc = f.get("description", "")
                blurb = f.get("blurb", "")
                ftype = f.get("type", "")
                self.f_proto.append(key)
                self.f_name.append(name)
                self.f_desc.append(desc)
                self.f_type.append(ftype)
                self.f_base.append(f.get("base", ""))
                self.f_mask.append(f.get("bitmask", ""))
                self.f_blurb.append(blurb)
                ln, ld, lb = name.lower(), desc.lower(), blurb.lower()
                self.l_name.append(ln)
                self.l_desc.append(ld)
                self.l_blurb.append(lb)
                self.l_type.append(ftype.lower())
                self.hay.append(f"{ln}\n{ld}\n{lb}")
            proto = Protocol(
                key=key,
                name=p.get("name", ""),
                short_name=p.get("short_name", ""),
                field_count=len(self.f_name) - start,
                start=start,
                end=len(self.f_name),
                also_registered_as=list(p.get("also_registered_as", ())),
            )
            self.protocols.append(proto)
            self.proto_by_key[key.lower()] = proto

        # Lowercase search string per protocol: key, short name, full name,
        # and the names it is also registered under.
        self.p_hay: list[str] = [
            "\0".join(
                [p.key, p.short_name, p.name]
                + [a.get("short_name", "") + "\0" + a.get("name", "") for a in p.also_registered_as]
            ).lower()
            for p in self.protocols
        ]
        self.l_pkey: list[str] = [p.key.lower() for p in self.protocols]
        self.l_pshort: list[str] = [p.short_name.lower() for p in self.protocols]

    @property
    def field_total(self) -> int:
        return len(self.f_name)


def load(path: str) -> Database:
    with open(path, encoding="utf-8") as fh:
        return Database(json.load(fh), source=path)
