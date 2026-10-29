"""Matroska chapter XML generator.

Produces RFC 9559-compliant chapter XML for mkvmerge --chapters.
All mandatory flags (EditionFlagHidden/Default/Ordered, ChapterFlagHidden/Enabled)
are included for full TV client compatibility.
"""

import secrets
from collections.abc import Iterable

from auto_chapter.models import Chapter


def _random_uid() -> int:
    """Generate a 64-bit unsigned random integer for UID fields."""
    return secrets.randbelow(2**63) + 1


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS.nnnnnnnnn (nanosecond precision)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:012.9f}"


def _escape(text: str) -> str:
    """Escape XML special characters in chapter strings."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def to_xml(chapters: Iterable[Chapter], *, language: str = "und") -> str:
    """Render chapters as a Matroska XML string."""
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<Chapters>",
        "  <EditionEntry>",
        "    <EditionFlagHidden>0</EditionFlagHidden>",
        "    <EditionFlagDefault>0</EditionFlagDefault>",
        "    <EditionFlagOrdered>0</EditionFlagOrdered>",
        f"    <EditionUID>{_random_uid()}</EditionUID>",
    ]

    for chapter in chapters:
        lines.extend(
            [
                "    <ChapterAtom>",
                f"      <ChapterUID>{_random_uid()}</ChapterUID>",
                f"      <ChapterTimeStart>{_format_timestamp(chapter.timestamp)}</ChapterTimeStart>",
                "      <ChapterFlagHidden>0</ChapterFlagHidden>",
                "      <ChapterFlagEnabled>1</ChapterFlagEnabled>",
                "      <ChapterDisplay>",
                f"        <ChapterString>{_escape(chapter.name)}</ChapterString>",
                f"        <ChapterLanguage>{language}</ChapterLanguage>",
                f"        <ChapLanguageIETF>{language}</ChapLanguageIETF>",
                "      </ChapterDisplay>",
                "    </ChapterAtom>",
            ]
        )

    lines.append("  </EditionEntry>")
    lines.append("</Chapters>")
    return "\n".join(lines) + "\n"
