"""Tests for ffmpeg_decoder_problem: most movies are H.264 or HEVC, and some
ffmpeg builds (Fedora's ffmpeg-free) can't decode them -- every detection
would fail."""

import subprocess
from types import SimpleNamespace

import pytest

from auto_chapter.detector import ffmpeg as ffmpeg_module
from auto_chapter.detector.ffmpeg import ffmpeg_decoder_problem

# Shape of the real `ffmpeg -codecs` output (FFmpeg 9.0.2, shortened).
_HEADER = """Codecs:
 D..... = Decoding supported
 .E.... = Encoding supported
 ..V... = Video codec
 ..A... = Audio codec
 ..S... = Subtitle codec
 ..D... = Data codec
 ..T... = Attachment codec
 ...I.. = Intra frame-only codec
 ....L. = Lossy compression
 .....S = Lossless compression
 -------
 D.VI.S 012v                 Uncompressed 4:2:2 10-bit
 DEV.LS av1                  Alliance for Open Media AV1 (decoders: libdav1d av1 av1_cuvid) (encoders: libaom-av1)
"""
_H264 = (
    " DEV.LS h264                 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10"
    " (decoders: h264 h264_v4l2m2m h264_qsv h264_cuvid) (encoders: libx264 h264_vaapi)\n"
)
_HEVC = (
    " DEV.LS hevc                 H.265 / HEVC (High Efficiency Video Coding)"
    " (decoders: hevc hevc_qsv hevc_cuvid) (encoders: libx265 hevc_vaapi)\n"
)


def _fake_run(stdout: str, returncode: int = 0):
    def run(cmd, **kwargs):
        assert cmd == ["ffmpeg", "-hide_banner", "-codecs"]
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return run


def test_a_full_ffmpeg_has_no_problem(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER + _H264 + _HEVC))

    assert ffmpeg_decoder_problem() is None


def test_missing_codecs_are_named(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER))

    problem = ffmpeg_decoder_problem()

    assert problem is not None
    assert "can't decode H.264 / HEVC video" in problem
    assert "RPM Fusion" in problem and "Packman" in problem


def test_a_codec_it_can_only_encode_counts_as_missing(monkeypatch) -> None:
    """ffmpeg-free style: HEVC listed, but only with an encoder (no D flag)."""
    hevc_encode_only = " .EV.L. hevc                 H.265 / HEVC (encoders: hevc_vaapi)\n"
    monkeypatch.setattr(
        ffmpeg_module.subprocess, "run", _fake_run(_HEADER + _H264 + hevc_encode_only)
    )

    problem = ffmpeg_decoder_problem()

    assert problem is not None
    assert "can't decode HEVC video" in problem
    assert "H.264" not in problem


def test_h264_through_openh264_is_enough(monkeypatch) -> None:
    """Fedora can decode H.264 with Cisco's openh264: a software decoder."""
    openh264 = " DEV.L. h264                 H.264 (decoders: libopenh264) (encoders: libopenh264)\n"
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER + openh264 + _HEVC))

    assert ffmpeg_decoder_problem() is None


def test_only_graphics_card_decoders_count_as_missing(monkeypatch) -> None:
    """h264_cuvid / h264_qsv need particular hardware: no use on most computers."""
    hardware_only = " DEV.LS h264                 H.264 (decoders: h264_cuvid h264_qsv h264_v4l2m2m)\n"
    monkeypatch.setattr(
        ffmpeg_module.subprocess, "run", _fake_run(_HEADER + hardware_only + _HEVC)
    )

    problem = ffmpeg_decoder_problem()

    assert problem is not None
    assert "can't decode H.264 video" in problem


def test_a_decoder_named_like_its_codec_needs_no_decoder_list(monkeypatch) -> None:
    plain = " DEV.LS h264                 H.264 / AVC\n DEV.LS hevc                 H.265 / HEVC\n"
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER + plain))

    assert ffmpeg_decoder_problem() is None


@pytest.mark.parametrize(
    "run",
    [
        _fake_run("", returncode=1),
        _fake_run("something else entirely\n"),
    ],
    ids=["ffmpeg fails", "unknown output"],
)
def test_when_ffmpeg_cant_tell_nothing_is_claimed(monkeypatch, run) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", run)

    assert ffmpeg_decoder_problem() is None


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("ffmpeg"), subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15)],
)
def test_ffmpeg_not_runnable_claims_nothing(monkeypatch, error) -> None:
    def run(cmd, **kwargs):
        raise error

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", run)

    assert ffmpeg_decoder_problem() is None
