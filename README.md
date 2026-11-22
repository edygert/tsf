# tsf — tshark field browser

A terminal UI to browse and search every tshark / Wireshark display-filter
field: 233,550 fields in 2,928 protocols for TShark 4.0.17. It reads the
field list straight from tshark, or from a JSON dump, and holds it in memory.
Search runs on every keystroke.

Python standard library only (`curses`) on Linux and macOS. On Windows the
`windows-curses` package is installed automatically.

## Install

Requires Python 3.10 or later. tshark is needed only to read live data.

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/OWNER/tsf
```

Or with pipx / pip:

```sh
pipx install git+https://github.com/OWNER/tsf
pip install git+https://github.com/OWNER/tsf
```

Run it once without installing:

```sh
uvx --from git+https://github.com/OWNER/tsf tsf --tshark
```

Upgrade with `uv tool upgrade tsf`, remove with `uv tool uninstall tsf`.
If the shell does not find `tsf` after installing, run `uv tool update-shell`
and open a new terminal.

## Data sources

| Command | Source |
|---|---|
| `tsf --tshark [PATH]` | runs `tshark -v`, `tshark -G protocols` and `tshark -G fields` (PATH defaults to `tshark` on `$PATH`) |
| `tsf --tshark --save FILE.json` | as above, and also writes the JSON document |
| `tsf FILE.json` | a JSON file written by `tsf --save` or `dump_tshark_fields.py` |
| `tsf` | `./tshark_fields.json` if it exists, otherwise tshark if it is on `$PATH` |

A JSON file lets you browse the fields of a tshark version on a machine that
does not have that version installed. Loading the 35.9 MB file for 4.0.17
takes 0.6–0.8 s; running tshark takes 0.8–1.0 s.

`--no-mouse` leaves mouse events to the terminal so its native text
selection works.

## Search syntax

Typing always edits the query; every whitespace-separated token must match.

| Token | Matches |
|---|---|
| `word` | case-insensitive substring of field name, description or blurb |
| `p:dns` | fields of protocol `dns` (exact filter name; several `p:` tokens are OR-ed) |
| `t:ipv4` | field type contains `ipv4` |
| `n:qry` | field name contains `qry` |
| `/^dns\.a/` | case-insensitive regular expression on name, description and blurb; `^` and `$` anchor to each of the three (closing `/` optional) |

Results are ranked by the worst-matching word: exact name, name prefix or
dotted-component prefix, name substring, description, blurb. Ties keep
protocol order, then field-name order.

In the Protocols view, words match the filter name, short name, full name and
alternate registrations.

## Keys

| Key | Action |
|---|---|
| Up Down PgUp PgDn Home End, Ctrl-P Ctrl-N, mouse wheel | move |
| Enter | Fields: expand/collapse the detail pane. Protocols: list that protocol's fields |
| Tab | switch Fields / Protocols view |
| Esc | clear the query; on an empty query, return to the Protocols view |
| Backspace, Ctrl-W, Ctrl-U | delete character, word, whole query |
| Ctrl-Y | copy the field name (or protocol filter name) to the clipboard via the OSC 52 escape sequence |
| F1 | help |
| Ctrl-Q, Ctrl-C | quit |

Ctrl-Y needs a terminal that supports OSC 52 (xterm, kitty, WezTerm, iTerm2,
Windows Terminal; tmux with `set -g set-clipboard on`).

## Standalone scripts

These are independent of the `tsf` package and are not installed by it:

- `dump_tshark_fields.py`: writes the JSON document (same output as
  `tsf --tshark --save FILE`).
- `query_tshark_fields.py`: non-interactive queries of the JSON document
  (table, TSV, JSON or name-only output).

## Development

```sh
git clone https://github.com/OWNER/tsf && cd tsf
uv run tsf --tshark       # run from the working tree
uv run pytest             # tests
```

On a filesystem that does not permit symlinks, uv cannot create `.venv` in
the project. Point `UV_PROJECT_ENVIRONMENT` at a directory on another
filesystem before running uv.

Layout:

```
src/tsf/data.py      load the document into column lists
src/tsf/tshark.py    build the document by running tshark
src/tsf/search.py    query parsing, matching, ranking, result cache (no curses)
src/tsf/ui.py        curses interface
src/tsf/__main__.py  command line
tests/               pytest
```
