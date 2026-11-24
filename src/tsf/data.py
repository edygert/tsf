"""Load tshark_fields.json into flat, column-oriented lists.

Every field becomes one row index into parallel lists (one list per
attribute). Fields of one protocol occupy one contiguous index range, so a
protocol filter is a slice instead of a scan.
"""

import json
from dataclasses import dataclass, field

# Display columns, in the order Database.to_columns() stores them.
COLUMNS = ("f_proto", "f_name", "f_desc", "f_type", "f_base", "f_mask", "f_blurb")


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

        # Parallel per-field columns (original case, for display).
        self.f_proto: list[str] = []  # protocol key
        self.f_name: list[str] = []
        self.f_desc: list[str] = []
        self.f_type: list[str] = []
        self.f_base: list[str] = []
        self.f_mask: list[str] = []
        self.f_blurb: list[str] = []

        for key, p in doc.get("protocols", {}).items():
            start = len(self.f_name)
            for f in p.get("fields", ()):
                self.f_proto.append(key)
                self.f_name.append(f.get("name", ""))
                self.f_desc.append(f.get("description", ""))
                self.f_type.append(f.get("type", ""))
                self.f_base.append(f.get("base", ""))
                self.f_mask.append(f.get("bitmask", ""))
                self.f_blurb.append(f.get("blurb", ""))
            self.protocols.append(
                Protocol(
                    key=key,
                    name=p.get("name", ""),
                    short_name=p.get("short_name", ""),
                    field_count=len(self.f_name) - start,
                    start=start,
                    end=len(self.f_name),
                    also_registered_as=list(p.get("also_registered_as", ())),
                )
            )
        self._build_search_columns()

    @classmethod
    def from_columns(cls, state: dict) -> "Database":
        """Rebuild a Database from the output of to_columns()."""
        db = cls.__new__(cls)
        db.source = state["source"]
        db.tshark_version = state["tshark_version"]
        db.generated_utc = state["generated_utc"]
        for name, col in zip(COLUMNS, state["columns"]):
            setattr(db, name, col)
        db.protocols = [Protocol(*p) for p in state["protocols"]]
        db._build_search_columns()
        return db

    def to_columns(self) -> dict:
        """Plain lists, tuples, dicts and strings only, so marshal can store it."""
        return {
            "source": self.source,
            "tshark_version": self.tshark_version,
            "generated_utc": self.generated_utc,
            "columns": [getattr(self, name) for name in COLUMNS],
            "protocols": [
                (p.key, p.name, p.short_name, p.field_count, p.start, p.end, p.also_registered_as)
                for p in self.protocols
            ],
        }

    def _build_search_columns(self) -> None:
        """Derive the lowercase search columns from the display columns."""
        self.proto_by_key: dict[str, Protocol] = {p.key.lower(): p for p in self.protocols}
        self.l_name: list[str] = [s.lower() for s in self.f_name]
        self.l_desc: list[str] = [s.lower() for s in self.f_desc]
        self.l_blurb: list[str] = [s.lower() for s in self.f_blurb]
        self.l_type: list[str] = [s.lower() for s in self.f_type]
        # name + "\n" + description + "\n" + blurb, lowercase: one
        # substring test per field per search term (query words never
        # contain whitespace, so a word cannot span two attributes), and one
        # re.MULTILINE search per field per regex (^ and $ anchor to each
        # attribute). The data contains no newline characters.
        self.hay: list[str] = [f"{n}\n{d}\n{b}" for n, d, b in zip(self.l_name, self.l_desc, self.l_blurb)]
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
