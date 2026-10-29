"""Tests for chapter XML generator."""

import re

from auto_chapter.chapter.xml import to_xml
from auto_chapter.models import Chapter, DetectionMethod


def test_basic_structure() -> None:
    chapters = [
        Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START),
        Chapter(name="Auto Chapter 2", timestamp=300.5, method=DetectionMethod.BLACK),
    ]
    xml = to_xml(chapters)

    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<Chapters>" in xml
    assert "<EditionEntry>" in xml
    assert xml.endswith("</Chapters>\n")


def test_mandatory_flags_present() -> None:
    chapters = [Chapter(name="A", timestamp=0.0, method=DetectionMethod.START)]
    xml = to_xml(chapters)

    assert "<EditionFlagHidden>0</EditionFlagHidden>" in xml
    assert "<EditionFlagDefault>0</EditionFlagDefault>" in xml
    assert "<EditionFlagOrdered>0</EditionFlagOrdered>" in xml
    assert "<ChapterFlagHidden>0</ChapterFlagHidden>" in xml
    assert "<ChapterFlagEnabled>1</ChapterFlagEnabled>" in xml


def test_timestamp_nanosecond_precision() -> None:
    chapters = [Chapter(name="A", timestamp=125.5, method=DetectionMethod.START)]
    xml = to_xml(chapters)

    # 125.5 = 0:02:05.500000000
    assert "<ChapterTimeStart>00:02:05.500000000</ChapterTimeStart>" in xml


def test_chapter_name_escaped() -> None:
    chapters = [
        Chapter(name="Bob & Alice <test>", timestamp=0.0, method=DetectionMethod.START),
    ]
    xml = to_xml(chapters)

    assert "<ChapterString>Bob &amp; Alice &lt;test&gt;</ChapterString>" in xml


def test_language_in_xml() -> None:
    chapters = [Chapter(name="A", timestamp=0.0, method=DetectionMethod.START)]
    xml = to_xml(chapters, language="eng")

    assert "<ChapterLanguage>eng</ChapterLanguage>" in xml
    assert "<ChapLanguageIETF>eng</ChapLanguageIETF>" in xml


def test_random_uids_differ() -> None:
    chapters = [
        Chapter(name="A", timestamp=0.0, method=DetectionMethod.START),
        Chapter(name="B", timestamp=300.0, method=DetectionMethod.BLACK),
    ]
    xml = to_xml(chapters)

    uids = re.findall(r"<ChapterUID>(\d+)</ChapterUID>", xml)
    assert len(uids) == 2
    assert uids[0] != uids[1]


def test_chapter_count_matches() -> None:
    chapters = [
        Chapter(name=f"Auto Chapter {i}", timestamp=float(i * 300), method=DetectionMethod.START)
        for i in range(1, 11)
    ]
    xml = to_xml(chapters)
    assert xml.count("<ChapterAtom>") == 10
