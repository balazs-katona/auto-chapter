# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""The version shown by the app, the installer and the README must agree."""

import re
import tomllib
from pathlib import Path

from auto_chapter import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_app_version_matches_pyproject() -> None:
    """The installer reads pyproject.toml, the app shows __version__. The
    1.0.0 bump once updated only one of them."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == pyproject["project"]["version"]


def test_readme_shows_the_same_version() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert re.search(rf"^\*\*v{re.escape(__version__)}\*\*$", readme, re.MULTILINE)
