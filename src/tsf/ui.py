"""curses user interface.

Screen rows, top to bottom:
  0              title bar: view tabs, tshark version, generation time
  1              search line: query text, match count, search time
  2              column header
  3 .. list_end  result rows (only the visible rows are drawn)
  separator      detail pane title
  detail rows    attributes of the highlighted row
  last           key help or status message
"""

import base64
import curses
import os
import re
import sys
import textwrap
import time

from .data import Database
from .search import FieldSearcher, Query, match_protocols, parse

FIELDS, PROTOS = 0, 1
VIEW_NAMES = ("Fields", "Protocols")

COMPACT_DETAIL_ROWS = 5
LABEL_W = 13  # width of the label column in the detail pane

# Column width caps (characters).
CAP_PROTO, CAP_NAME, CAP_TYPE = 16, 40, 16
CAP_PKEY, CAP_PSHORT = 24, 24
# Above this many results, column widths use the database-wide maxima
# instead of a scan of the result list.
WIDTH_SCAN_LIMIT = 20_000

HELP_TEXT = """\
SEARCH  (type anywhere; every token must match)
  word        substring of field name, description or blurb
  p:dns       only fields of protocol dns (several p: are OR-ed)
  t:ipv4      field type contains "ipv4"
  n:qry       field name contains "qry"
  /^dns\\.a/   regular expression on name, description, blurb
Ranking: exact name, name prefix, name substring,
         description match, blurb match.

KEYS
  Up Down PgUp PgDn Home End   move (also Ctrl-P / Ctrl-N, wheel)
  Enter        Fields: expand/collapse detail pane
               Protocols: list that protocol's fields
  Tab          switch Fields / Protocols view
  Esc          clear query; on empty query return to Protocols
  Backspace    delete character    Ctrl-W delete word
  Ctrl-U       clear query
  Ctrl-Y       copy field name / protocol key to clipboard (OSC 52)
  F1           this help           Ctrl-Q, Ctrl-C quit

Press any key to close."""


def fit(text: str, width: int) -> str:
    """Truncate to `width` characters (ending in an ellipsis) and pad."""
    if width <= 0:
        return ""
    if len(text) > width:
        return text[: width - 1] + "…"
    return text.ljust(width)


def dash(value: str) -> str:
    return value if value and value != "0x0" else "—"


class View:
    def __init__(self, kind: int):
        self.kind = kind
        self.query = ""
        self.parsed = Query()
        self.results: list[int] = []
        self.cursor = 0
        self.top = 0
        self.ms = 0.0
        self.error = ""
        self.dirty = True  # query changed since the last search
        self.widths: tuple[int, ...] | None = None


class App:
    def __init__(self, scr, db: Database, mouse: bool = True):
        self.scr = scr
        self.db = db
        self.searcher = FieldSearcher(db)
        self.views = [View(FIELDS), View(PROTOS)]
        self.active = FIELDS
        self.detail_expanded = False
        self.came_from_protos = False
        self.show_help = False
        self.status = ""
        self.list_y = 3
        self.list_h = 1

        # Database-wide column maxima, used when the result list is large.
        self.max_proto = max(map(len, (p.key for p in db.protocols)), default=8)
        self.max_name = max(map(len, db.f_name), default=10)
        self.max_type = max(map(len, db.f_type), default=4)

        curses.raw()  # deliver Ctrl-C, Ctrl-Q, Ctrl-Y as keys
        curses.noecho()
        scr.keypad(True)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        self.colors = curses.has_colors()
        if self.colors:
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_YELLOW, bg)  # match highlight
            curses.init_pair(2, curses.COLOR_RED, bg)  # error text
            curses.init_pair(3, curses.COLOR_CYAN, bg)  # labels
        self.a_hl = (curses.color_pair(1) if self.colors else 0) | curses.A_BOLD
        self.a_err = (curses.color_pair(2) if self.colors else 0) | curses.A_BOLD
        self.a_label = curses.color_pair(3) if self.colors else curses.A_BOLD
        self.wheel_up = self.wheel_down = 0
        if mouse:
            self.wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
            self.wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
            curses.mousemask(curses.ALL_MOUSE_EVENTS)

        for v in self.views:
            self.run_search(v)

    # ------------------------------------------------------------ state

    @property
    def view(self) -> View:
        return self.views[self.active]

    def run_search(self, v: View) -> None:
        v.dirty = False
        try:
            q = parse(v.query)
        except re.error as exc:
            v.error = f"regex error: {exc}"
            return  # keep the previous results on screen
        v.error = ""
        t0 = time.perf_counter()
        if v.kind == FIELDS:
            v.results = self.searcher.search(q)
        else:
            v.results = match_protocols(self.db, q)
        v.ms = (time.perf_counter() - t0) * 1000
        v.parsed = q
        v.cursor = v.top = 0
        v.widths = None

    def set_query(self, v: View, text: str) -> None:
        if text != v.query:
            v.query = text
            v.dirty = True

    def move(self, delta: int) -> None:
        v = self.view
        if v.results:
            v.cursor = max(0, min(len(v.results) - 1, v.cursor + delta))

    def switch_view(self) -> None:
        self.active = PROTOS if self.active == FIELDS else FIELDS
        self.came_from_protos = False

    def enter(self) -> None:
        v = self.view
        if v.kind == FIELDS:
            self.detail_expanded = not self.detail_expanded
        elif v.results:
            p = self.db.protocols[v.results[v.cursor]]
            fv = self.views[FIELDS]
            self.set_query(fv, f"p:{p.key} ")
            self.active = FIELDS
            self.came_from_protos = True

    def escape(self) -> None:
        v = self.view
        if v.query:
            self.set_query(v, "")
        elif v.kind == FIELDS and self.came_from_protos:
            self.active = PROTOS
            self.came_from_protos = False

    def copy(self) -> None:
        v = self.view
        if not v.results:
            return
        i = v.results[v.cursor]
        text = self.db.f_name[i] if v.kind == FIELDS else self.db.protocols[i].key
        seq = "\033]52;c;" + base64.b64encode(text.encode()).decode() + "\a"
        if os.environ.get("TMUX"):
            seq = "\033Ptmux;" + seq.replace("\033", "\033\033") + "\033\\"
        try:
            with open("/dev/tty", "w") as tty:
                tty.write(seq)
        except OSError:
            sys.stdout.write(seq)
            sys.stdout.flush()
        self.status = f"Copied {text!r} to the clipboard (OSC 52 escape sequence)"

    # ------------------------------------------------------------- keys

    def handle(self, k) -> bool:
        """Process one key. Returns False to quit."""
        if self.show_help:
            self.show_help = False
            return True
        if k != curses.KEY_RESIZE:
            self.status = ""
        v = self.view
        if isinstance(k, str):
            if k in ("\x11", "\x03"):  # Ctrl-Q, Ctrl-C
                return False
            if k in ("\n", "\r"):
                self.enter()
            elif k == "\t":
                self.switch_view()
            elif k == "\x1b":
                self.escape()
            elif k in ("\x7f", "\x08"):
                self.set_query(v, v.query[:-1])
            elif k == "\x15":  # Ctrl-U
                self.set_query(v, "")
            elif k == "\x17":  # Ctrl-W
                self.set_query(v, re.sub(r"\S+\s*$", "", v.query))
            elif k == "\x19":  # Ctrl-Y
                self.copy()
            elif k == "\x0e":  # Ctrl-N
                self.move(1)
            elif k == "\x10":  # Ctrl-P
                self.move(-1)
            elif k.isprintable():
                self.set_query(v, v.query + k)
            return True

        page = max(1, self.list_h - 1)
        if k == curses.KEY_UP:
            self.move(-1)
        elif k == curses.KEY_DOWN:
            self.move(1)
        elif k == curses.KEY_PPAGE:
            self.move(-page)
        elif k == curses.KEY_NPAGE:
            self.move(page)
        elif k == curses.KEY_HOME:
            self.move(-len(v.results))
        elif k == curses.KEY_END:
            self.move(len(v.results))
        elif k == curses.KEY_BACKSPACE:
            self.set_query(v, v.query[:-1])
        elif k == curses.KEY_ENTER:
            self.enter()
        elif k == curses.KEY_BTAB:
            self.switch_view()
        elif k == curses.KEY_F1:
            self.show_help = True
        elif k == curses.KEY_RESIZE:
            curses.update_lines_cols()
        elif k == curses.KEY_MOUSE:
            self.mouse()
        return True

    def mouse(self) -> None:
        try:
            _, _, y, _, bstate = curses.getmouse()
        except curses.error:
            return
        if self.wheel_up and bstate & self.wheel_up:
            self.move(-3)
        elif self.wheel_down and bstate & self.wheel_down:
            self.move(3)
        elif self.list_y <= y < self.list_y + self.list_h:
            v = self.view
            row = v.top + (y - self.list_y)
            if row < len(v.results):
                v.cursor = row
                if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                    self.enter()

    # ---------------------------------------------------------- drawing

    def put(self, y: int, x: int, s: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            try:
                self.scr.addnstr(y, x, s, w - x, attr)
            except curses.error:
                pass  # writing the bottom-right cell raises after output

    def cell(self, y: int, x: int, text: str, width: int, attr: int, hl: int, terms) -> None:
        s = fit(text, width)
        self.put(y, x, s, attr)
        if not terms:
            return
        low = s.lower()
        if len(low) != len(s):
            return
        for t in terms:
            start = 0
            while (j := low.find(t, start)) != -1:
                self.put(y, x + j, s[j : j + len(t)], hl)
                start = j + len(t)

    def field_widths(self, v: View) -> tuple[int, int, int]:
        if v.widths is None:
            db, res = self.db, v.results
            if len(res) > WIDTH_SCAN_LIMIT:
                mp, mn, mt = self.max_proto, self.max_name, self.max_type
            elif res:
                mp = max(len(db.f_proto[i]) for i in res)
                mn = max(len(db.f_name[i]) for i in res)
                mt = max(len(db.f_type[i]) for i in res)
            else:
                mp, mn, mt = 8, 10, 4
            v.widths = (
                max(8, min(mp, CAP_PROTO)),
                max(10, min(mn, CAP_NAME)),
                max(4, min(mt, CAP_TYPE)),
            )
        return v.widths

    def proto_widths(self, v: View) -> tuple[int, int]:
        if v.widths is None:
            prot = self.db.protocols
            res = v.results
            mk = max((len(prot[i].key) for i in res), default=6)
            ms = max((len(prot[i].short_name) for i in res), default=10)
            v.widths = (max(11, min(mk, CAP_PKEY)), max(10, min(ms, CAP_PSHORT)))
        return v.widths

    def field_detail(self, i: int, w: int) -> list[tuple[str, str]]:
        db = self.db
        p = db.proto_by_key[db.f_proto[i].lower()]
        rows = [
            ("Field", db.f_name[i]),
            ("Description", db.f_desc[i] or "—"),
            ("Type", f"{db.f_type[i]}    Base: {dash(db.f_base[i])}    Bitmask: {dash(db.f_mask[i])}"),
            ("Protocol", f"{p.key} · {p.short_name} · {p.name}"),
            ("Blurb", db.f_blurb[i] or "—"),
        ]
        return self.wrap_rows(rows, w)

    def proto_detail(self, i: int, w: int) -> list[tuple[str, str]]:
        p = self.db.protocols[i]
        rows = [
            ("Protocol", p.key),
            ("Short name", p.short_name),
            ("Full name", p.name),
            ("Fields", f"{p.field_count:,}" + ("    (Enter lists them)" if p.field_count else "")),
        ]
        for a in p.also_registered_as:
            rows.append(("Also as", f"{a.get('short_name', '')} · {a.get('name', '')}"))
        return self.wrap_rows(rows, w)

    @staticmethod
    def wrap_rows(rows, w: int) -> list[tuple[str, str]]:
        """Wrap each value to the width right of the label column."""
        out = []
        vw = max(10, w - LABEL_W - 2)
        for label, value in rows:
            lines = textwrap.wrap(value, vw, break_long_words=True, break_on_hyphens=False) or [""]
            out.append((label, lines[0]))
            out.extend(("", ln) for ln in lines[1:])
        return out

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        v = self.view
        db = self.db
        if h < 8 or w < 30:
            self.put(0, 0, "Terminal too small (minimum 30x8)")
            scr.refresh()
            return

        # Row 0: title bar.
        title = ""
        for n, name in enumerate(VIEW_NAMES):
            title += f" [{name}] " if n == self.active else f"  {name}  "
        info = f"{db.tshark_version}  generated {db.generated_utc}  source: {db.source} "
        self.put(0, 0, fit(title + "  " + info, w), curses.A_REVERSE)
        x = 0
        for n, name in enumerate(VIEW_NAMES):
            label = f" [{name}] " if n == self.active else f"  {name}  "
            if n == self.active:
                self.put(0, x, label, curses.A_REVERSE | curses.A_BOLD)
            x += len(label)

        # Row 1: search line.
        noun = "fields" if v.kind == FIELDS else "protocols"
        total = db.field_total if v.kind == FIELDS else len(db.protocols)
        if v.error:
            right, rattr = v.error, self.a_err
        else:
            right, rattr = f"{len(v.results):,} / {total:,} {noun} · {v.ms:.1f} ms", curses.A_DIM
        prompt = "Search: "
        room = max(1, w - len(prompt) - len(right) - 3)
        shown = v.query[-room:]
        self.put(1, 0, prompt, curses.A_BOLD)
        self.put(1, len(prompt), shown)
        self.put(1, max(len(prompt) + len(shown) + 2, w - len(right) - 1), right, rattr)

        # Detail pane content.
        if v.results:
            i = v.results[v.cursor]
            detail = self.field_detail(i, w) if v.kind == FIELDS else self.proto_detail(i, w)
        else:
            detail = [("", "No matches.")]
        if self.detail_expanded:
            dh = min(max(COMPACT_DETAIL_ROWS, len(detail)), max(COMPACT_DETAIL_ROWS, (h - 6) // 2))
        else:
            dh = COMPACT_DETAIL_ROWS
        dh = min(dh, h - 6)
        self.list_y = 3
        self.list_h = h - 3 - 1 - dh - 1
        sep_y = self.list_y + self.list_h

        # Keep the cursor row visible.
        if v.cursor < v.top:
            v.top = v.cursor
        elif v.cursor >= v.top + self.list_h:
            v.top = v.cursor - self.list_h + 1
        v.top = max(0, min(v.top, max(0, len(v.results) - self.list_h)))

        terms = v.parsed.highlight_terms()
        rows = v.results[v.top : v.top + self.list_h]
        if v.kind == FIELDS:
            wp, wn, wt = self.field_widths(v)
            show_proto = w >= 80
            cols = []
            x = 0
            if show_proto:
                cols.append((x, wp, "PROTOCOL"))
                x += wp + 1
            cols += [(x, wn, "FIELD NAME"), (x + wn + 1, wt, "TYPE")]
            xd = x + wn + 1 + wt + 1
            cols.append((xd, max(0, w - xd), "DESCRIPTION"))
            self.put(2, 0, " " * w, curses.A_UNDERLINE)
            for cx, cw, label in cols:
                self.put(2, cx, fit(label, cw), curses.A_UNDERLINE | curses.A_BOLD)
            for r, i in enumerate(rows):
                y = self.list_y + r
                sel = v.top + r == v.cursor
                attr = curses.A_REVERSE if sel else 0
                hl = (curses.A_REVERSE | curses.A_BOLD | curses.A_UNDERLINE) if sel else self.a_hl
                if sel:
                    self.put(y, 0, " " * w, attr)
                values = ([db.f_proto[i]] if show_proto else []) + [db.f_name[i], db.f_type[i], db.f_desc[i]]
                for (cx, cw, _), val in zip(cols, values):
                    self.cell(y, cx, val, cw, attr, hl, terms)
        else:
            wk, ws = self.proto_widths(v)
            cols = [(0, wk, "FILTER NAME"), (wk + 1, ws, "SHORT NAME"), (wk + ws + 2, 7, " FIELDS")]
            xn = wk + ws + 2 + 7 + 1
            cols.append((xn, max(0, w - xn), "FULL NAME"))
            self.put(2, 0, " " * w, curses.A_UNDERLINE)
            for cx, cw, label in cols:
                self.put(2, cx, fit(label, cw), curses.A_UNDERLINE | curses.A_BOLD)
            for r, i in enumerate(rows):
                p = db.protocols[i]
                y = self.list_y + r
                sel = v.top + r == v.cursor
                attr = curses.A_REVERSE if sel else 0
                hl = (curses.A_REVERSE | curses.A_BOLD | curses.A_UNDERLINE) if sel else self.a_hl
                if sel:
                    self.put(y, 0, " " * w, attr)
                dim = 0 if p.field_count or sel else curses.A_DIM
                values = [p.key, p.short_name, f"{p.field_count:>7,}", p.name]
                for (cx, cw, _), val in zip(cols, values):
                    self.cell(y, cx, val, cw, attr | dim, hl, terms)
        if not v.results:
            self.put(self.list_y, 1, "No matches." if not v.error else "Fix the regular expression.", curses.A_DIM)

        # Detail pane.
        more = len(detail) - dh
        if more > 0:
            sep = f"─ Detail ── Enter: show {more} more line{'s' if more != 1 else ''} "
        elif self.detail_expanded and v.kind == FIELDS:
            sep = "─ Detail ── Enter: collapse "
        else:
            sep = "─ Detail "
        if v.kind == PROTOS:
            sep = "─ Detail " if more <= 0 else f"─ Detail ({more} more line{'s' if more != 1 else ''}) "
        self.put(sep_y, 0, (sep + "─" * w)[:w], curses.A_DIM)
        for r, (label, text) in enumerate(detail[:dh]):
            y = sep_y + 1 + r
            if label:
                self.put(y, 1, f"{label}:", self.a_label)
            self.put(y, LABEL_W + 1, text[: max(0, w - LABEL_W - 1)])

        # Last row: status or key help.
        if self.status:
            footer, fattr = self.status, curses.A_BOLD
        elif v.kind == FIELDS:
            back = "  Esc back" if self.came_from_protos and not v.query else "  Esc clear"
            footer, fattr = f" Tab protocols  ↑↓ PgUp PgDn move  Enter detail  ^Y copy{back}  F1 help  ^Q quit", curses.A_REVERSE
        else:
            footer, fattr = " Tab fields  ↑↓ PgUp PgDn move  Enter list fields  ^Y copy  Esc clear  F1 help  ^Q quit", curses.A_REVERSE
        self.put(h - 1, 0, fit(footer, w - 1), fattr)

        if self.show_help:
            curses.curs_set(0)
            scr.noutrefresh()
            self.draw_help(h, w)
        else:
            curses.curs_set(1)
            scr.move(1, min(w - 1, len(prompt) + len(shown)))
            scr.noutrefresh()
        curses.doupdate()

    def draw_help(self, h: int, w: int) -> None:
        lines = HELP_TEXT.splitlines()
        bw = min(w - 2, max(map(len, lines)) + 4)
        bh = min(h - 2, len(lines) + 2)
        y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
        try:
            win = curses.newwin(bh, bw, y0, x0)
        except curses.error:
            return
        win.erase()
        win.box()
        for r, ln in enumerate(lines[: bh - 2]):
            try:
                win.addnstr(r + 1, 2, ln, bw - 4)
            except curses.error:
                pass
        try:
            win.addnstr(0, 2, " tsf help ", bw - 4, curses.A_BOLD)
        except curses.error:
            pass
        win.noutrefresh()

    # ------------------------------------------------------------- loop

    def run(self) -> None:
        scr = self.scr
        self.draw()
        while True:
            try:
                keys = [scr.get_wch()]
            except curses.error:
                continue
            # Process every key already queued (fast typing, paste) before
            # running one search and one redraw.
            scr.nodelay(True)
            try:
                while True:
                    try:
                        keys.append(scr.get_wch())
                    except curses.error:
                        break
            finally:
                scr.nodelay(False)
            for k in keys:
                if not self.handle(k):
                    return
            for v in self.views:
                if v.dirty and v is self.view:
                    self.run_search(v)
            self.draw()
