# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the questions of install.sh: with no input at all (started with a
double click, from a script, or Ctrl+D), every question used to print its
"Please type ..." line forever instead of stopping."""

import re
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
STOPPED = "No input received -- installation stopped."


def _run_ask(stdin: str) -> subprocess.CompletedProcess[str]:
    """Run install.sh's own ask() (and the warn() it uses) on one question."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    warn = re.search(r"^warn\(\).*$", text, re.MULTILINE)
    ask = re.search(r"^ask\(\) \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL)
    assert warn and ask, "install.sh has no ask() / warn() function"
    script = f'{warn.group(0)}\n{ask.group(0)}\nask "Question: " answer\necho "answer=[$answer]"\n'
    return subprocess.run(
        ["bash", "-c", script], input=stdin, capture_output=True, text=True, timeout=5, check=False
    )


def test_no_input_stops_instead_of_asking_forever() -> None:
    done = _run_ask("")

    assert done.returncode == 1
    assert STOPPED in done.stdout
    assert "answer=" not in done.stdout  # nothing after the question ran


def test_a_normal_answer_is_read() -> None:
    done = _run_ask("  192.168.1.10:32400\n")

    assert done.returncode == 0
    assert "answer=[192.168.1.10:32400]" in done.stdout  # read itself drops the spaces
    assert STOPPED not in done.stdout


def test_a_last_answer_without_a_final_newline_still_counts() -> None:
    done = _run_ask("n")

    assert done.returncode == 0
    assert "answer=[n]" in done.stdout


def test_every_question_goes_through_ask() -> None:
    """A new question with a bare `read` would bring the endless loop back.
    The only other `read`s: the one inside ask() and "Start it now?", which
    treats no input as 'n' (the installation is already complete there)."""
    allowed = ('read -e -rp "$1" "$2"', 'read -e -rp "Start it now? (y/n): " start_now')
    reads = [
        line.strip()
        for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if re.search(r"(^|\s|!)read\s", line) and not line.lstrip().startswith("#")
    ]

    assert reads  # the pattern still finds them
    assert [r for r in reads if not any(a in r for a in allowed)] == []


def _code_lines() -> list[str]:
    return [
        line.strip()
        for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_users_get_no_dev_tools() -> None:
    """pytest, ruff and mypy are for development only: the installer syncs
    the app's own dependencies and does not run the test suite."""
    syncs = [line for line in _code_lines() if "uv sync" in line]

    assert syncs  # the dependency step is still there
    assert [line for line in syncs if "dev" in line] == []
    assert [line for line in _code_lines() if "pytest" in line] == []


def test_quit_installer_says_that_nothing_was_saved() -> None:
    """"3) Quit installer" used to end without a word."""
    quits = [line for line in _code_lines() if line.startswith("3)")]

    assert len(quits) == 2  # after a failed connection and after a bad token
    for line in quits:
        assert "Installation stopped -- nothing was saved." in line, line


def _bash_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    found = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert found, f"install.sh has no {name}() function"
    return found.group(0)


# What /etc/os-release says on these systems (the lines that matter).
OS_RELEASE = {
    "EndeavourOS": 'ID=endeavouros\nID_LIKE=arch\n',
    "Arch": "ID=arch\n",
    "Ubuntu": "ID=ubuntu\nID_LIKE=debian\n",
    "Linux Mint": 'ID=linuxmint\nID_LIKE="ubuntu debian"\n',
    "Debian": "ID=debian\n",
    "Fedora": "ID=fedora\n",
    "openSUSE Tumbleweed": 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
    "Rocky Linux": 'ID="rocky"\nID_LIKE="rhel centos fedora"\n',
    "Gentoo": "ID=gentoo\n",
}
FAMILY = {
    "EndeavourOS": "arch",
    "Arch": "arch",
    "Ubuntu": "debian",
    "Linux Mint": "debian",
    "Debian": "debian",
    "Fedora": "fedora",
    "openSUSE Tumbleweed": "suse",
    "Rocky Linux": "",  # RHEL-like: no ffmpeg in its own repositories
    "Gentoo": "",  # package names not known here
}


@pytest.mark.parametrize("system", list(OS_RELEASE))
def test_the_installer_knows_which_system_it_is_on(system: str, tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(OS_RELEASE[system], encoding="utf-8")
    script = f'{_bash_function("distro_family")}\ndistro_family "{os_release}"\n'

    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)

    assert done.stdout.strip() == FAMILY[system]


def test_no_os_release_means_an_unknown_system(tmp_path: Path) -> None:
    script = f'{_bash_function("distro_family")}\ndistro_family "{tmp_path / "none"}"\n'

    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)

    assert done.returncode == 0
    assert done.stdout == ""


def _commands(family: str, *packages: str, sudo: str = "sudo ", download: str = "curl") -> list[str]:
    script = (
        f'{_bash_function("install_commands")}\nSUDO="{sudo}"\nDOWNLOAD="{download}"\n'
        f'install_commands {family} {" ".join(packages)}\n'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    return done.stdout.splitlines()


UV_SCRIPT = "curl -LsSf https://astral.sh/uv/install.sh | sh"


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("arch", ["sudo pacman -S --needed --noconfirm uv ffmpeg mkvtoolnix-cli"]),
        (
            "debian",
            ["sudo apt-get update", "sudo apt-get install -y ffmpeg mkvtoolnix", UV_SCRIPT],
        ),
        ("fedora", ["sudo dnf install -y ffmpeg-free mkvtoolnix", UV_SCRIPT]),
        ("suse", ["sudo zypper --non-interactive install ffmpeg mkvtoolnix", UV_SCRIPT]),
    ],
)
def test_commands_for_everything_missing(family: str, expected: list[str]) -> None:
    assert _commands(family, "uv", "ffmpeg", "mkvtoolnix") == expected


def test_only_uv_missing_needs_no_package_manager() -> None:
    assert _commands("debian", "uv") == [UV_SCRIPT]


def test_without_curl_or_wget_curl_is_installed_for_uv() -> None:
    assert _commands("debian", "uv", "ffmpeg", download="") == [
        "sudo apt-get update",
        "sudo apt-get install -y ffmpeg curl",
        UV_SCRIPT,
    ]


def test_with_only_wget_uv_comes_through_wget() -> None:
    assert _commands("debian", "uv", download="wget") == [
        "wget -qO- https://astral.sh/uv/install.sh | sh"
    ]


def test_as_root_no_sudo() -> None:
    assert _commands("arch", "ffmpeg", sudo="") == ["pacman -S --needed --noconfirm ffmpeg"]


def test_keeping_a_missing_path_says_where_to_change_it_later() -> None:
    """A wrong path kept with "Use it anyway? y" was easy to fix only for
    someone who already knew Settings > Folder mappings."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    use_anyway = text[text.index('ask "  Use it anyway? (y/n): " yn') :]
    yes_branch = use_anyway[: use_anyway.index("n|N|no|NO)")]

    assert "You can change it later in the app: Settings → Folder mappings." in yes_branch


# --- Folder mapping: are Plex's movies in the folder typed? -----------------


def _movies_are_here(
    tmp_path: Path, counts: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run movies_are_here with a fake `uv` that answers `counts` (what
    `python -m auto_chapter.folder_match` would print) and notes how it was called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f'#!/bin/bash\necho "$PLEX_URL $PLEX_TOKEN $*" > "{tmp_path}/called"\n'
        f'printf "%s" "{counts}"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    text = INSTALL_SH.read_text(encoding="utf-8")
    helpers = "\n".join(m.group(0) for m in re.finditer(r"^(ok|warn)\(\).*$", text, re.MULTILINE))
    script = (
        f"{helpers}\n{_bash_function('ask')}\n{_bash_function('movies_are_here')}\n"
        f'PATH="{bin_dir}:$PATH"\nplex_url="http://plex:32400"\nplex_token="abc"\n'
        'movies_are_here "/plex/Filmek/" "/mnt/Filmek/"\necho "rc=$?"\n'
    )
    return subprocess.run(
        ["bash", "-c", script], input=stdin, capture_output=True, text=True, timeout=10, check=False
    )


def test_installer_shows_how_many_of_plexs_movies_are_in_the_folder(tmp_path: Path) -> None:
    done = _movies_are_here(tmp_path, "650 653")

    assert "Found 650 of 653 Plex movies in this folder." in done.stdout
    assert "Is the path right?" not in done.stdout
    assert done.stdout.rstrip().endswith("rc=0")
    called = (tmp_path / "called").read_text(encoding="utf-8").split()
    assert called == [
        "http://plex:32400", "abc", "run", "python", "-m", "auto_chapter.folder_match",
        "/plex/Filmek/", "/mnt/Filmek/",
    ]  # fmt: skip


@pytest.mark.parametrize(("answer", "rc"), [("1", "rc=1"), ("2", "rc=0")])
def test_installer_asks_when_none_of_plexs_movies_are_there(
    tmp_path: Path, answer: str, rc: str
) -> None:
    """1) Enter another path (rc 1: the path question again), 2) Use it anyway."""
    done = _movies_are_here(tmp_path, "0 653", stdin=f"x\n{answer}\n")

    assert "Found 0 of 653 Plex movies in this folder. Is the path right?" in done.stdout
    assert "1) Enter another path" in done.stdout
    assert "2) Use it anyway" in done.stdout
    assert "Please type 1 or 2." in done.stdout  # the "x"
    assert done.stdout.rstrip().endswith(rc)


@pytest.mark.parametrize("counts", ["", "0 0", "garbage"])
def test_installer_says_nothing_when_it_cannot_compare(tmp_path: Path, counts: str) -> None:
    done = _movies_are_here(tmp_path, counts)

    assert "Found" not in done.stdout
    assert done.stdout.rstrip().endswith("rc=0")


def test_installer_compares_only_a_folder_that_exists() -> None:
    """A path that isn't there gets the "Use it anyway?" question instead."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    call = text.index('movies_are_here "$p_path" "$l_path"')
    guard = text.rindex('if [ -d "$l_path" ]; then', 0, call)

    assert text[guard:call].count("\n") == 2  # right inside that if


# --- End message: judged by the user's own PATH -----------------------------


def test_the_path_message_uses_the_users_own_path() -> None:
    """After installing uv the installer adds ~/.local/bin to its own PATH, so
    judging by that PATH always said "works from any shell" (0.4.4 Ubuntu test)."""
    lines = _code_lines()
    saved = lines.index('USER_PATH="$PATH"')
    first_export = next(i for i, line in enumerate(lines) if line.startswith("export PATH="))

    assert saved < first_export
    assert 'case ":$USER_PATH:" in' in lines
    assert 'case ":$PATH:" in' not in lines


# --- An address typed instead of a folder path --------------------------------


def _path_question(answers: str) -> str:
    """The installer's text for these answers to the mounted-path question:
    the path loop cut out of install.sh and run on its own."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('                    ask "  Where is this folder mounted on this computer?')
    end = text.index('                    [[ "$l_path" != */ ]] && l_path="${l_path}/"', start)
    loop = text[start:end]
    script = (
        "\n".join(m.group(0) for m in re.finditer(r"^(ok|warn)\(\).*$", text, re.MULTILINE))
        + f"\n{_bash_function('ask')}\n"
        + 'trim() { echo "$1"; }\n'
        + f"while true; do\n{loop}\n"
        + 'echo "path=[$l_path]"; break\ndone\n'
    )
    done = subprocess.run(
        ["bash", "-c", script], input=answers, capture_output=True, text=True, timeout=10, check=False
    )
    return done.stdout


def test_installer_says_a_network_address_has_to_be_mounted() -> None:
    out = _path_question("smb://nas.local/media/Filmek\n/mnt/nas/Filmek\n")

    assert "That's a network address (smb://...), not a folder path." in out
    assert "mounted as a folder on this computer first" in out
    assert '"Mounting a network share"' in out
    assert "doesn't look like a folder path" not in out
    assert "path=[/mnt/nas/Filmek]" in out  # asked again


def test_installer_uses_a_local_folders_address_as_its_path() -> None:
    out = _path_question("file:///home/user/%C3%81rv%C3%A1cska%20filmek\n")

    assert "network address" not in out
    assert "path=[/home/user/Árvácska filmek]" in out
