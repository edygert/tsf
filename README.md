# tsf

Browse and search every tshark display-filter field in the terminal.

tsf gets the field list from tshark (or from a saved JSON file) and searches
it as you type. TShark 4.0.17 has 233,550 fields in 2,928 protocols.

Requires Python 3.10 or later and nothing else on Linux and macOS. On
Windows, the installer adds `windows-curses`; Windows has not been tested.
tshark is needed only when reading the fields from tshark instead of a file.

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/OWNER/tsf
```

With pipx or pip:

```sh
pipx install git+https://github.com/OWNER/tsf
pip install git+https://github.com/OWNER/tsf
```

To try it without installing:

```sh
uvx --from git+https://github.com/OWNER/tsf tsf --tshark
```

## Usage

```
tsf                  read ./tshark_fields.json if it exists, otherwise run tshark
tsf FILE             read FILE
tsf --tshark         run the tshark found on $PATH
tsf --tshark PATH    run the tshark executable at PATH
tsf --save FILE      run tshark and also write the fields to FILE
```

`--save` runs the tshark on `$PATH`; add `--tshark PATH` to use a different one.
A saved file lets you look up the fields of one tshark version on a machine
that has a different version, or none.

`--no-mouse` stops tsf from capturing the mouse, so the terminal can select
text again.

## Searching

Type to search. A field is shown only if it matches every word.

| Search | Finds fields where |
|---|---|
| `agent` | the name, description or blurb contains "agent" |
| `p:dns` | the protocol is `dns` (exact name; `p:tcp p:udp` finds either) |
| `t:ipv4` | the type contains "ipv4", e.g. `FT_IPv4` |
| `n:qry` | the name contains "qry" |
| `/^dns\.a/` | the name, description or blurb matches the regular expression |

Case is ignored everywhere. In a regular expression, `^` and `$` match at the
start and end of the name, the description and the blurb.

Results are ordered by how well the words match. With several words, the
word that matches worst decides:

1. the name is the word (`dns.a`)
2. the name, or a part between dots, starts with the word (`qry` in `dns.qry.name`)
3. the name contains the word
4. the description contains the word
5. the blurb contains the word

In the Protocols view (Tab), words match the protocol's filter name, short
name and full name.

## Keys

| Key | Action |
|---|---|
| ↑ ↓ PgUp PgDn Home End, mouse wheel, click | move |
| Enter | show the full detail of a field / list the fields of a protocol |
| Tab | switch between the Fields and Protocols views |
| Esc | clear the search; if you came from the Protocols view, press again to go back |
| Backspace, Ctrl-W, Ctrl-U | delete a character, a word, the whole search |
| Ctrl-Y | copy the field name (or, in the Protocols view, the protocol name) to the clipboard |
| F1 | help |
| Ctrl-Q, Ctrl-C | quit |

Ctrl-Y sends an OSC 52 escape sequence, which xterm, kitty, WezTerm, iTerm2
and Windows Terminal support. In tmux, it also needs `set -g set-clipboard on`.

## Other scripts

`dump_tshark_fields.py` writes the same JSON file as `tsf --save`.
`query_tshark_fields.py` searches that file from the command line and prints
a table, TSV, JSON or a list of field names. Neither is installed with tsf.
Run them with `python3`.

## Development

```sh
git clone https://github.com/OWNER/tsf
cd tsf
uv run tsf --tshark
uv run pytest
```

uv creates `.venv` with symlinks. If the filesystem does not allow them, set
`UV_PROJECT_ENVIRONMENT` to a directory on another filesystem.

| File | Contents |
|---|---|
| `src/tsf/tshark.py` | runs tshark and builds the field list |
| `src/tsf/data.py` | loads the field list into memory |
| `src/tsf/search.py` | search syntax, matching and ordering |
| `src/tsf/ui.py` | the terminal interface |
| `src/tsf/__main__.py` | command-line options |
