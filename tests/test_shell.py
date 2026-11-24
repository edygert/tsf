"""Drive the picker key scripts in real interactive bash and zsh shells.

A stub `tsf` on $PATH records its arguments and prints a fixed name, so
these tests check only the command-line editing. Each shell runs in a pty,
reads no startup files, and writes no history file.

Standard library only, Python 3.9 syntax: runs under pytest, or directly
with `python3 tests/test_shell.py` on a machine without pytest.
"""

import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time

SHELL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src", "tsf", "shell")
KEY = "\x18\x14"  # Ctrl-X Ctrl-T, the default picker key
LEFT = "\x02"  # Ctrl-B: cursor one character left (emacs keymap in both shells)
UTF8 = "C.UTF-8" if sys.platform.startswith("linux") else "en_US.UTF-8"

STUB = """#!/bin/sh
printf '%s\\n' "$@" > "$STUB_ARGS"
[ -n "$STUB_OUT" ] && printf '%s\\n' "$STUB_OUT"
exit "${STUB_RC:-0}"
"""

# typed line, cursor moves left, stub output, stub exit status,
# expected --query value, expected line after the key
CASES = [
    ("tshark -Y 'ip.src == 1 && dns.qr", 0, "dns.qry.name", 0, "dns.qr", "tshark -Y 'ip.src == 1 && dns.qry.name"),
    ('tshark -Y "dns.qr', 0, "dns.qry.name", 0, "dns.qr", 'tshark -Y "dns.qry.name'),
    ("tshark -e dns.qry.na", 3, "dns.qry.name", 0, "dns.qry.na", "tshark -e dns.qry.name"),
    ("tshark -e dns.q", 5, "dns.qry.name", 0, "dns.q", "tshark -e dns.qry.name"),
    ("a  b", 2, "ip.src", 0, "", "a ip.src b"),
    ("", 0, "ip.src", 0, "", "ip.src"),
    ("é·x dns.qr", 0, "dns.qry.name", 0, "dns.qr", "é·x dns.qry.name"),
    ("tshark -Y '!(dns.qr", 0, "dns.qry.name", 0, "dns.qr", "tshark -Y '!(dns.qry.name"),
    ("tshark -Ydns.qr", 0, "-Ydns.qry.name", 0, "-Ydns.qr", "tshark -Ydns.qry.name"),
    ("tshark -e dns.q -c 5", 5, "dns.qry.name", 0, "dns.q", "tshark -e dns.qry.name -c 5"),
    ("tshark -e dns.qr", 0, "", 1, "dns.qr", "tshark -e dns.qr"),  # cancelled
]

BASH_DUMP = r"""_dump() { printf 'LINE=[%s] POINT=%s\n' "$READLINE_LINE" "$READLINE_POINT" >> "$LOG"; }
bind -x '"\C-t": _dump'
"""
ZSH_DUMP = r"""_dump() { print -r -- "LINE=[$BUFFER] POINT=$CURSOR" >> $LOG }
zle -N _dump
bindkey '^T' _dump
print -r -- "BEFORE=$(bindkey '^X^T')" >> $LOG
"""


class Shell:
    """An interactive shell in a pty."""

    def __init__(self, argv, env):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe(argv[0], argv, env)

    def send(self, text, wait=0.3):
        os.write(self.fd, text.encode())
        end = time.time() + wait
        while time.time() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    os.read(self.fd, 65536)
                except OSError:
                    return

    def close(self):
        self.send("\x05\x15exit\n", 0.3)
        try:
            os.close(self.fd)
        except OSError:
            pass
        os.waitpid(self.pid, 0)


def run_cases(shell, tmp):
    """Run every case in `shell` ("bash" or "zsh"); return the log lines and stub arguments."""
    stub_dir = os.path.join(tmp, "bin")
    os.makedirs(stub_dir)
    stub = os.path.join(stub_dir, "tsf")
    with open(stub, "w") as fh:
        fh.write(STUB)
    os.chmod(stub, 0o755)
    log = os.path.join(tmp, "log")
    open(log, "w").close()
    dump = os.path.join(tmp, "dump")
    with open(dump, "w") as fh:
        fh.write(BASH_DUMP if shell == "bash" else ZSH_DUMP)

    env = {k: v for k, v in os.environ.items() if k not in ("HISTFILE", "EDITOR", "VISUAL", "TSF_PICK_KEY")}
    env.update(PATH=stub_dir + os.pathsep + env.get("PATH", ""), PS1="$ ", TERM="dumb", LOG=log, LC_ALL=UTF8, LANG=UTF8)
    if shell == "bash":
        env.update(HISTFILE="/dev/null", INPUTRC="/dev/null")
        argv = [shutil.which("bash"), "--norc", "--noprofile", "-i"]
    else:
        env["ZDOTDIR"] = tmp  # no startup files there; -f also skips them
        argv = [shutil.which("zsh"), "-f", "-i"]
    sh = Shell(argv, env)
    try:
        sh.send("", 0.5)
        if shell == "zsh":
            sh.send("unset HISTFILE; SAVEHIST=0\n")
        sh.send(f". {dump}\n")
        sh.send(f". {os.path.join(SHELL_DIR, 'tsf.' + shell)}\n", 0.5)
        args = []
        for i, (line, left, out, rc, _query, _expected) in enumerate(CASES):
            argfile = os.path.join(tmp, f"args{i}")
            sh.send(f"export STUB_ARGS={argfile} STUB_OUT='{out}' STUB_RC={rc}\n")
            sh.send(line + LEFT * left, 0.2)
            sh.send(KEY, 0.6)
            sh.send("\x14", 0.3)  # Ctrl-T: dump the line and the cursor position
            sh.send("\x05\x15", 0.2)  # end of line, delete the line
            try:
                with open(argfile) as fh:
                    args.append(fh.read().splitlines())
            except FileNotFoundError:
                args.append(None)
    finally:
        sh.close()
    with open(log) as fh:
        return fh.read().splitlines(), args


def expected_dumps():
    out = []
    for line, left, stub_out, rc, query, expected in CASES:
        if rc != 0:
            point = len(line) - left
        else:
            before = line[: len(line) - left]
            run_left = len(before) - len(before.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"))
            point = len(before) - run_left + len(stub_out)
        out.append(f"LINE=[{expected}] POINT={point}")
    return out


def check(shell):
    with tempfile.TemporaryDirectory() as tmp:
        log, args = run_cases(shell, tmp)
    dumps = [ln for ln in log if ln.startswith("LINE=")]
    assert dumps == expected_dumps(), "\n".join(dumps)
    for (_line, _l, _o, _rc, query, _e), a in zip(CASES, args):
        assert a == ["--pick", f"--query={query}"], a
    return log


def need(prog):
    if shutil.which(prog) is None:
        try:
            import pytest

            pytest.skip(f"{prog} is not on PATH")
        except ImportError:
            print(f"skip: {prog} is not on PATH")
            return False
    return True


def test_bash():
    if need("bash"):
        out = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout
        if int(out.strip() or 0) < 4:
            return  # covered by test_old_bash_warns
        check("bash")


def test_zsh():
    if need("zsh"):
        log = check("zsh")
        print("\n".join(ln for ln in log if ln.startswith("BEFORE=")))


def test_old_bash_warns():
    """bash 3.2 (macOS /bin/bash) prints one warning and binds nothing."""
    if not os.path.exists("/bin/bash"):
        return
    ver = subprocess.run(["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout
    if int(ver.strip() or 0) >= 4:
        return
    script = os.path.join(SHELL_DIR, "tsf.bash")
    p = subprocess.run(
        ["/bin/bash", "--norc", "--noprofile", "-i", "-c", f". {script}; bind -X 2>/dev/null | grep -c _tsf_pick"],
        capture_output=True,
        text=True,
        env=dict(os.environ, HISTFILE="/dev/null"),
    )
    assert "needs bash 4.0 or later" in p.stderr, p.stderr
    assert p.stdout.strip() in ("", "0"), p.stdout



# ------------------------------------------------------------- zsh Tab

TAB = "\t"

# name, typed line, cursor moves left, stub output, stub exit status,
# expect the stub to run, expected line (None: not checked)
TAB_CASES = [
    ("fallback: files", "ls /us", 0, "", 0, False, "ls /usr/"),
    ("fallback: tshark -r file", "tshark -r /tm", 0, "", 0, False, "tshark -r /tmp/"),
    ("quoted filter", "tshark -Y 'ip.src == 1 && dns.qr", 0, "dns.qry.name", 0, True, "tshark -Y 'ip.src == 1 && dns.qry.name"),
    ("double-quoted, mid-name", 'tshark -Y "dns.q && ip', 7, "dns.qry.name", 0, True, 'tshark -Y "dns.qry.name && ip'),
    ("-e argument", "tshark -T fields -e dns.q", 0, "dns.qry.name", 0, True, "tshark -T fields -e dns.qry.name"),
    ("-e empty argument", "tshark -T fields -e ", 0, "ip.src", 0, True, "tshark -T fields -e ip.src"),
    ("-Y attached", "tshark -Ydns.qr", 0, "-Ydns.qry.name", 0, True, "tshark -Ydns.qry.name"),
    ("-O protocol", "tshark -O dn", 0, "dns", 0, True, "tshark -O dns"),
    ("-O comma list", "tshark -O tcp,dn", 0, "dns", 0, True, "tshark -O tcp,dns"),
    ("-2 -R", "tshark -2 -R dn", 0, "dns", 0, True, "tshark -2 -R dns"),
    ("on the option itself", "tshark -e", 0, "", 0, False, None),
    ("after a closing quote", "tshark -Y 'dns.a'", 0, "", 0, False, None),
    ("other option's argument", "tshark -r a.pcap -Y dn -w out", 0, "", 0, False, None),
    ("sudo", "sudo tshark -e dns.q", 0, "dns.qry.name", 0, True, "sudo tshark -e dns.qry.name"),
    ("assignment", "TZ=UTC tshark -e dns.q", 0, "dns.qry.name", 0, True, "TZ=UTC tshark -e dns.qry.name"),
    ("pipe", "cat x | tshark -e dns.q", 0, "dns.qry.name", 0, True, "cat x | tshark -e dns.qry.name"),
    ("full path", "/opt/x/bin/tshark -e dns.q", 0, "dns.qry.name", 0, True, "/opt/x/bin/tshark -e dns.qry.name"),
    ("not tshark", "echo -e dns.q", 0, "", 0, False, None),
    ("second command", "tshark -e dns.a; ls /us", 0, "", 0, False, "tshark -e dns.a; ls /usr/"),
    ("cancel", "tshark -e dns.qr", 0, "", 1, True, "tshark -e dns.qr"),
]
# Cases repeated with fzf's Tab widget loaded before and after tsf.zsh.
FZF_CASES = [c for c in TAB_CASES if c[0] in ("fallback: files", "quoted filter", "-e empty argument", "second command")]

TAB_STUB = """#!/bin/sh
echo "called $2" >> "$LOG"
[ -n "$STUB_OUT" ] && printf '%s\\n' "$STUB_OUT"
exit "${STUB_RC:-0}"
"""


class ZshTab:
    """An interactive zsh with compinit, a stub tsf and a temporary ZDOTDIR."""

    def __init__(self, tmp, before=(), after=(), tsf=True):
        self.log = os.path.join(tmp, "log")
        open(self.log, "w").close()
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        stub = os.path.join(bindir, "tsf")
        with open(stub, "w") as fh:
            fh.write(TAB_STUB)
        os.chmod(stub, 0o755)
        lines = ["unset HISTFILE; SAVEHIST=0", f"autoload -Uz compinit; compinit -d {tmp}/.zcompdump", *before]
        if tsf:
            lines.append(f". {os.path.join(SHELL_DIR, 'tsf.zsh')}")
        lines += [*after, '_dump() { print -r -- "LINE=[$BUFFER]" >> $LOG }; zle -N _dump; bindkey "^T" _dump']
        lines.append('print -r -- "TAB=$(bindkey "^I")" >> $LOG')
        with open(os.path.join(tmp, ".zshrc"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        env = {k: v for k, v in os.environ.items() if k not in ("HISTFILE", "EDITOR", "VISUAL", "TSF_TAB", "TSF_PICK_KEY")}
        env.update(ZDOTDIR=tmp, PS1="$ ", TERM="xterm-256color", LOG=self.log, LC_ALL=UTF8, LANG=UTF8)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        self.sh = Shell([shutil.which("zsh"), "-i"], env)
        self.sh.send("", 1.5)
        with open(self.log) as fh:
            self.tab_binding = fh.read().strip()

    def press(self, line, keys, left=0, out="", rc=0):
        """Type `line`, move left, send `keys`; return (stub ran, --query value, line)."""
        self.sh.send(f"export STUB_OUT='{out}' STUB_RC={rc}\n")
        open(self.log, "w").close()
        self.sh.send(line + LEFT * left, 0.2)
        for k in keys:
            self.sh.send(k, 0.8)
        self.sh.send("\x14", 0.3)  # Ctrl-T: dump the line
        self.sh.send("\x07\x05\x15", 0.3)  # Ctrl-G ends a completion menu; end of line; delete the line
        with open(self.log) as fh:
            text = fh.read().splitlines()
        called = [ln[len("called "):] for ln in text if ln.startswith("called ")]
        got = [ln[len("LINE=["):-1] for ln in text if ln.startswith("LINE=[")]
        return bool(called), (called[0] if called else None), (got[0] if got else None)

    def close(self):
        self.sh.close()


def check_tab_cases(cases, before=(), after=()):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        z = ZshTab(tmp, before, after)
        try:
            for name, line, left, out, rc, want_called, want_line in cases:
                called, query, got = z.press(line, [TAB], left, out, rc)
                if called != want_called or (want_line is not None and got != want_line):
                    failures.append(f"{name}: stub ran={called} {query} line=[{got}]")
        finally:
            z.close()
    assert not failures, "\n".join(failures)


def test_zsh_tab():
    if need("zsh"):
        check_tab_cases(TAB_CASES)


def test_zsh_tab_menu_completion_unchanged():
    """Repeated Tab outside tshark gives the same lines with and without tsf.zsh."""
    if not need("zsh"):
        return
    results = {}
    for with_tsf in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            z = ZshTab(tmp, tsf=with_tsf)
            try:
                results[with_tsf] = [z.press("ls /usr/l", [TAB] * n)[2] for n in (1, 2, 3, 4)]
            finally:
                z.close()
    assert results[True] == results[False], results


def test_zsh_tab_with_fzf():
    """fzf's Tab widget loaded before or after tsf.zsh: both keep working."""
    if not (need("zsh") and need("fzf")):
        return
    fzf = "source <(fzf --zsh)"
    check_tab_cases(FZF_CASES, before=[fzf])
    check_tab_cases(FZF_CASES, after=[fzf])


def test_zsh_tab_vi_mode():
    """Tab in the viins keymap: the picker in tshark arguments, completion elsewhere."""
    if need("zsh"):
        vi = ["bindkey -v", "bindkey -M viins '^E' end-of-line '^U' kill-whole-line"]
        check_tab_cases(FZF_CASES, before=vi)


def test_zsh_tab_off():
    """TSF_TAB=0 leaves Tab as it was; the picker key is still bound."""
    if not need("zsh"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        z = ZshTab(tmp, before=["TSF_TAB=0"])
        try:
            called, _, _ = z.press("tshark -e dns.q", [TAB])
            assert not called
            assert z.tab_binding == 'TAB="^I" expand-or-complete', z.tab_binding
            called, query, got = z.press("tshark -e dns.q", [KEY], out="dns.qry.name")
            assert called and got == "tshark -e dns.qry.name", (called, query, got)
        finally:
            z.close()


if __name__ == "__main__":
    for name in (
        "test_bash",
        "test_zsh",
        "test_old_bash_warns",
        "test_zsh_tab",
        "test_zsh_tab_menu_completion_unchanged",
        "test_zsh_tab_with_fzf",
        "test_zsh_tab_vi_mode",
        "test_zsh_tab_off",
    ):
        globals()[name]()
        print(f"{name}: ok")
