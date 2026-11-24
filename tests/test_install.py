"""tsf --install-shell / --uninstall-shell, run against a temporary HOME."""

import pytest

from tsf.__main__ import BLOCK_END, BLOCK_START, main


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setenv("SHELL", "/bin/bash")
    return tmp_path


def test_install_and_uninstall_bash(home, capsys):
    rc = home / ".bashrc"
    rc.write_text("alias ll='ls -l'")  # no final newline
    main(["--install-shell", "--yes"])
    script = home / ".local/share/tsf/tsf.bash"
    assert script.read_text().startswith("# tsf picker key for bash.")
    text = rc.read_text()
    assert text == f"alias ll='ls -l'\n{BLOCK_START}\n[ -f {script} ] && . {script}\n{BLOCK_END}\n"

    main(["--install-shell", "bash", "--yes"])  # second run: rc unchanged
    assert rc.read_text() == text
    assert "already loads it" in capsys.readouterr().out

    main(["--uninstall-shell", "--yes"])
    assert rc.read_text() == "alias ll='ls -l'\n"
    assert not script.exists()
    main(["--uninstall-shell", "--yes"])
    assert "Not installed" in capsys.readouterr().out


def test_install_zsh_uses_zdotdir_and_keeps_symlink(home, monkeypatch):
    zdot = home / "zdot"
    zdot.mkdir()
    target = home / "dotfiles-zshrc"
    target.write_text("setopt autocd\n")
    (zdot / ".zshrc").symlink_to(target)
    monkeypatch.setenv("ZDOTDIR", str(zdot))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    main(["--install-shell", "zsh", "--yes"])
    assert (zdot / ".zshrc").is_symlink()
    assert BLOCK_START in target.read_text()
    assert (home / "data/tsf/tsf.zsh").exists()
    main(["--uninstall-shell", "zsh", "--yes"])
    assert (zdot / ".zshrc").is_symlink() and target.read_text() == "setopt autocd\n"


def test_install_asks_and_no_changes_without_yes(home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit) as exc:
        main(["--install-shell"])
    assert exc.value.code == "Nothing changed."
    assert not (home / ".bashrc").exists()
    assert not (home / ".local/share/tsf").exists()


def test_install_unknown_shell(home, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    with pytest.raises(SystemExit) as exc:
        main(["--install-shell", "--yes"])
    assert "give bash or zsh" in str(exc.value.code)


def test_shell_prints_script(capsys):
    main(["--shell", "zsh"])
    assert "zle -N _tsf_pick" in capsys.readouterr().out
