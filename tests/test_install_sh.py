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
