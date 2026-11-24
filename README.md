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
uv tool install git+https://github.com/edygert/tsf
```

If uv is not installed, on Ubuntu install it from the Snap Store
(Ubuntu's apt repositories do not include it):

```sh
sudo snap install astral-uv --classic
```

On other systems, see the
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

With pipx or pip:

```sh
pipx install git+https://github.com/edygert/tsf
pip install git+https://github.com/edygert/tsf
```

To try it without installing:

```sh
uvx --from git+https://github.com/edygert/tsf tsf --tshark
```

## Usage

```
tsf                  read ./tshark_fields.json if it exists, otherwise run tshark
tsf FILE             read FILE
tsf --tshark         run the tshark found on $PATH
tsf --tshark PATH    run the tshark executable at PATH
tsf --save FILE      run tshark and also write the fields to FILE
tsf --query TEXT     start with TEXT in the search line
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
2. the name starts with the word (`tcp.flags.sy` in `tcp.flags.syn`)
3. a part between dots starts with the word (`qry` in `dns.qry.name`,
   `tcp.flags.sy` in `stt.tcp.flags.syn`)
4. the name contains the word
5. the description contains the word
6. the blurb contains the word

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
| F1, Ctrl-G | help (Ctrl-G works on keyboards without F1, such as Chromebook keyboards) |
| Ctrl-Q, Ctrl-C | quit |

Ctrl-Y sends an OSC 52 escape sequence, which xterm, kitty, WezTerm, iTerm2
and Windows Terminal support. In tmux, it also needs `set -g set-clipboard on`.

## Picking a field from the command line

tsf can be opened from a bash or zsh command line with a key, and puts the
field or protocol name you choose at the cursor:

```
$ tshark -r a.pcap -Y 'ip.src == 10.0.0.1 && dns.qr█     press Ctrl-X Ctrl-T
  tsf opens with the search "dns.qr"; choose dns.qry.name and press Enter
$ tshark -r a.pcap -Y 'ip.src == 10.0.0.1 && dns.qry.name█
```

Install it with:

```sh
tsf --install-shell          # bash or zsh, from $SHELL
tsf --install-shell zsh      # or name the shell
```

This writes the key script to `~/.local/share/tsf/tsf.bash` (or `tsf.zsh`;
`$XDG_DATA_HOME` replaces `~/.local/share`) and adds three lines that load it
to `~/.bashrc` (or `${ZDOTDIR:-~}/.zshrc`). It shows both paths and the lines
and asks before writing; `--yes` skips the question. Open a new shell to get
the key. `tsf --uninstall-shell` removes the lines and the script.
`tsf --shell bash` prints the script, to load it some other way.

The key needs bash 4.0 or later; with macOS's `/bin/bash` (3.2) the script
prints a warning and binds nothing. zsh, the default macOS shell, works.
To use another key, set `TSF_PICK_KEY` before the script is loaded, in
readline syntax for bash (`'\C-x\C-t'`) or bindkey syntax for zsh (`'^X^T'`).

### What the key replaces

The key looks at the characters around the cursor that can be part of a name
(letters, digits, `.`, `_` and `-`):

| At the cursor | tsf starts with the search | Enter |
|---|---|---|
| nothing (a space, an operator, an empty line) | empty | inserts the name at the cursor |
| the start of a name, e.g. `dns.qr` | `dns.qr` | replaces it with the name |
| an option with its value attached, e.g. `-Ydns.qr` | `dns.qr` | keeps `-Y`, replaces the rest |
| a value: `10.0.0.1`, `0x1f`, `100` | empty | keeps it and inserts a space and the name after it |
| a filter word: `and`, `or`, `not`, `in`, `contains`, `len`, … | empty | keeps it and inserts a space and the name after it |
| anything else, e.g. `capture.pcap` or a mistyped name | the text | keeps it and inserts a space and the name after it |

The search line shows what Enter will do, e.g. `Enter replaces 'dns.qr'`.
Ctrl-R switches between replacing and keeping the text. Enter in the
Protocols view chooses the protocol name. In the picker, → does what Enter
does elsewhere (show the detail, list a protocol's fields), and Esc on an
empty search, or Ctrl-Q, closes tsf without changing the command line.

### Where the picker gets the fields

The picker does not read `./tshark_fields.json`, so it gives the same result
in every directory. It reads, in this order:

1. `$TSF_FIELDS`, a file written by `tsf --save`
2. `$TSF_TSHARK`, a tshark executable
3. the tshark on `$PATH`

The first time, it saves what it read in
`${XDG_CACHE_HOME:-~/.cache}/tsf/pick-*.marshal`, which loads in 0.149 s
instead of 0.450 s for a JSON file (269,527 fields of TShark 4.6.10).
The cache is rebuilt by itself when that file or tshark executable changes,
or when tsf or Python is upgraded. Lua dissectors and plugins add fields
without changing the tshark executable, so after adding or removing one run:

```sh
tsf --refresh
```

## Other scripts

`dump_tshark_fields.py` writes the same JSON file as `tsf --save`.
`query_tshark_fields.py` searches that file from the command line and prints
a table, TSV, JSON or a list of field names. Neither is installed with tsf.
Run them with `python3`.

## Development

```sh
git clone https://github.com/edygert/tsf
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
| `src/tsf/pick.py` | the picker: what the key replaces, the cache, /dev/tty |
| `src/tsf/shell/` | the bash and zsh key scripts |
| `src/tsf/__main__.py` | command-line options, `--install-shell` |

`tests/test_shell.py` runs the key scripts in real bash and zsh shells with a
stub tsf. It uses only the standard library, so it also runs as
`python3 tests/test_shell.py` on a machine without pytest.

## License

MIT. See `LICENSE`.
