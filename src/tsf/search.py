"""Query parsing, matching and ranking. No curses dependency.

Query syntax (tokens separated by whitespace; all tokens must match):
  word      case-insensitive substring of field name, description or blurb
  p:KEY     field belongs to protocol KEY (exact filter name, any case);
            several p: tokens are OR-ed together
  t:TEXT    case-insensitive substring of the field type (e.g. t:ipv4)
  n:TEXT    case-insensitive substring of the field name only
  /REGEX/   case-insensitive regular expression searched in the name,
            description and blurb (^ and $ anchor to each one) (closing "/" is optional)

Ranking per plain word (lower is better):
  0 exact name, 1 name prefix, 2 dotted-component prefix, 3 name substring,
  4 description substring, 5 blurb substring. A regex is class 3 if it
  matches the name, otherwise 4.
Results are ordered by (worst word class, sum of word classes, row index).
"""

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from .data import Database


@dataclass(frozen=True)
class Query:
    terms: tuple[str, ...] = ()
    protos: frozenset[str] = frozenset()
    types: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    regex_src: tuple[str, ...] = ()
    regexes: tuple[re.Pattern, ...] = field(default=(), compare=False)

    @property
    def empty(self) -> bool:
        return not (self.terms or self.protos or self.types or self.names or self.regex_src)

    def highlight_terms(self) -> tuple[str, ...]:
        """Lowercase substrings worth highlighting in displayed rows."""
        return self.terms + self.names


def parse(text: str) -> Query:
    """Parse query text. Raises re.error for an invalid regular expression."""
    terms, protos, types, names, rsrc, rx = [], set(), [], [], [], []
    for tok in text.split():
        low = tok.lower()
        if tok.startswith("/"):
            pat = tok[1:-1] if len(tok) > 1 and tok.endswith("/") else tok[1:]
            if pat:
                rx.append(re.compile(pat, re.IGNORECASE | re.MULTILINE))
                rsrc.append(pat)
        elif low.startswith("p:"):
            if low[2:]:
                protos.add(low[2:])
        elif low.startswith("t:"):
            if low[2:]:
                types.append(low[2:])
        elif low.startswith("n:"):
            if low[2:]:
                names.append(low[2:])
        else:
            terms.append(low)
    return Query(tuple(terms), frozenset(protos), tuple(types), tuple(names), tuple(rsrc), tuple(rx))


def implies(new: Query, old: Query) -> bool:
    """True if every field matching `new` also matches `old`.

    When true, the result list of `old` is a valid candidate set for `new`.
    """
    if old.protos and not (new.protos and new.protos <= old.protos):
        return False
    for t in old.terms:  # new word or n: text containing t forces t into hay
        if not any(t in u for u in new.terms) and not any(t in u for u in new.names):
            return False
    for t in old.names:
        if not any(t in u for u in new.names):
            return False
    for t in old.types:
        if not any(t in u for u in new.types):
            return False
    return set(old.regex_src) <= set(new.regex_src)


def _rank(db: Database, idx: list[int], words: tuple[str, ...], regexes) -> list[int]:
    """Sort rows by (worst class, class sum, row index).

    Classes are computed per word in one list comprehension each, then
    packed into one integer sort key per row:
    (worst * (5 * nwords + 1) + total) * field_total + row.
    Sorting plain integers avoids one Python function call per row
    (measured: 36 ms instead of 95 ms for 124,302 rows).
    """
    ln, ld, fn = db.l_name, db.l_desc, db.f_name
    per_word: list[list[int]] = []
    for t in words:
        dt = "." + t
        per_word.append(
            [
                0 if (n := ln[i]) == t
                else 1 if n.startswith(t)
                else 2 if dt in n
                else 3 if t in n
                else 4 if t in ld[i]
                else 5
                for i in idx
            ]
        )
    for r in regexes:
        s = r.search
        per_word.append([3 if s(fn[i]) else 4 for i in idx])
    if len(per_word) == 1:
        worst = total = per_word[0]
    else:
        worst = [max(cs) for cs in zip(*per_word)]
        total = [sum(cs) for cs in zip(*per_word)]
    n = db.field_total
    k = 5 * len(per_word) + 1  # one more than the largest class sum
    keys = sorted([(w * k + s) * n + i for w, s, i in zip(worst, total, idx)])
    return [key % n for key in keys]


def match_fields(db: Database, q: Query, candidates: list[int] | None = None) -> list[int]:
    """Return ranked row indices of fields matching `q`.

    `candidates`, when given, must be a superset of the true result (see
    `implies`); only those rows are scanned.
    """
    if candidates is None:
        if q.protos:
            idx: list[int] = []
            for key in sorted(q.protos):
                p = db.proto_by_key.get(key)
                if p is not None:
                    idx.extend(range(p.start, p.end))
            idx.sort()
        else:
            idx = None  # full scan, using enumerate on the first filter
    else:
        idx = candidates
        if q.protos:
            fp = db.f_proto
            idx = [i for i in idx if fp[i].lower() in q.protos]

    hay = db.hay
    for t in q.terms:
        idx = [i for i, h in enumerate(hay) if t in h] if idx is None else [i for i in idx if t in hay[i]]
    ln = db.l_name
    for t in q.names:
        idx = [i for i, s in enumerate(ln) if t in s] if idx is None else [i for i in idx if t in ln[i]]
    lt = db.l_type
    for t in q.types:
        idx = [i for i, s in enumerate(lt) if t in s] if idx is None else [i for i in idx if t in lt[i]]
    if q.regexes:
        if idx is None:
            idx = range(db.field_total)
        for r in q.regexes:
            s = r.search
            idx = [i for i in idx if s(hay[i])]
    if idx is None:
        return list(range(db.field_total))

    rank_words = q.terms + q.names
    if rank_words or q.regexes:
        idx = _rank(db, idx, rank_words, q.regexes)
    elif candidates is not None:
        idx = sorted(idx)  # candidate order may come from a ranked query
    return idx


def match_protocols(db: Database, q: Query) -> list[int]:
    """Return ranked indices into db.protocols matching `q`.

    Words match key, short name, full name and alternate registrations;
    p: is an exact key match, n: a key substring; t: is ignored.
    """
    idx = list(range(len(db.protocols)))
    keys = db.l_pkey
    if q.protos:
        idx = [i for i in idx if keys[i] in q.protos]
    ph = db.p_hay
    for t in q.terms:
        idx = [i for i in idx if t in ph[i]]
    for t in q.names:
        idx = [i for i in idx if t in keys[i]]
    if q.regexes:
        prot = db.protocols
        for r in q.regexes:
            idx = [i for i in idx if r.search(prot[i].key) or r.search(prot[i].short_name) or r.search(prot[i].name)]
    words = q.terms + q.names
    if words:
        shorts = db.l_pshort

        def key(i: int):
            k = keys[i]
            worst = total = 0
            for t in words:
                c = 0 if (k == t or shorts[i] == t) else 1 if k.startswith(t) else 2 if t in k else 3
                total += c
                worst = max(worst, c)
            return (worst, total, i)

        idx.sort(key=key)
    return idx


class FieldSearcher:
    """Field search with a cache of recent results used as candidate sets.

    A new query scans only the smallest cached result whose query it
    implies, so typing more characters re-scans the previous result rather
    than all fields, and Backspace returns cached results without a scan.
    """

    CACHE_SIZE = 32

    def __init__(self, db: Database):
        self.db = db
        self.cache: OrderedDict[Query, list[int]] = OrderedDict()

    def search(self, q: Query) -> list[int]:
        if q.empty:
            return list(range(self.db.field_total))
        hit = self.cache.get(q)
        if hit is not None:
            self.cache.move_to_end(q)
            return hit
        best = None
        for old, res in self.cache.items():
            if implies(q, old) and (best is None or len(res) < len(best)):
                best = res
        res = match_fields(self.db, q, best)
        self.cache[q] = res
        if len(self.cache) > self.CACHE_SIZE:
            self.cache.popitem(last=False)
        return res
