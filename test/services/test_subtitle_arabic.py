"""阿拉伯字母文字字幕（阿拉伯语、波斯语、乌尔都语）通过 khatt 塑形渲染。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import khatt
import pytest
from loguru import logger
from moviepy import ColorClip, TextClip
from PIL import ImageFont

from app.services import video as vd
from app.utils import utils
from khatt.video import TextClip as KhattTextClip

FONT_DIR = Path(utils.font_dir())
DEFAULT_FONT = str(FONT_DIR / "STHeitiMedium.ttc")


@pytest.mark.parametrize(
    "text",
    [
        "مرحبا بالعالم",
        "بِسْمِ اللَّهِ",
        "سلام دنیا پچگ",  # 波斯语
        "پاکستان ٹیکسی ے",  # 乌尔都语
        "Khatt 2026 خط",  # 混排
        "ﻻ",  # 表现形式
    ],
)
def test_detects_arabic_script(text):
    assert vd.is_arabic_script(text)


@pytest.mark.parametrize("text", ["Hello world", "你好世界", "Xin chào", "", None, "﻿BOM"])
def test_other_scripts_are_not_arabic(text):
    assert not vd.is_arabic_script(text)


def test_font_without_arabic_glyphs_falls_back_to_amiri():
    assert vd.arabic_subtitle_font(DEFAULT_FONT, "مرحبا") == vd.ARABIC_FALLBACK_FONT
    assert khatt.missing_characters("مرحبا پچگ ے", vd.ARABIC_FALLBACK_FONT) == []


def test_font_with_arabic_glyphs_is_kept():
    assert vd.arabic_subtitle_font(vd.ARABIC_FALLBACK_FONT, "مرحبا") == vd.ARABIC_FALLBACK_FONT
    # 非阿拉伯文字不触发回退。
    assert vd.arabic_subtitle_font(DEFAULT_FONT, "你好") == DEFAULT_FONT


def test_arabic_lines_wrap_by_shaped_width():
    text = "تُمنح هذه الشهادة إلى الطالب المتميز تقديرا لجهوده الكبيرة في العام الدراسي"
    font = vd.ARABIC_FALLBACK_FONT
    max_width = 420
    wrapped, height = vd.wrap_text(text, max_width, font=font, fontsize=48)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    assert " ".join(lines) == text  # 只在空格处换行，不拆词
    assert all(vd.shaped_text_width(line, font, 48) <= max_width for line in lines)
    assert height == len(lines) * sum(ImageFont.truetype(font, 48).getmetrics())


def test_bundled_font_no_longer_reports_arabic_as_unsupported():
    # Amiri 会自动补上阿拉伯字形，因此 WebUI 不再误报字体不支持。
    assert vd.subtitle_font_supports_text(DEFAULT_FONT, "مرحبا بالعالم")
    # 其他语言的检测保持不变。
    vietnamese_only = str(FONT_DIR / "BeVietnamPro-Medium.ttf")
    assert not vd.subtitle_font_supports_text(vietnamese_only, "你好世界")


class _FakeAudio:
    duration = 3

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        pass

    def with_effects(self, _effects):
        return self


def _render_subtitled_video(subtitles, frame_at=None, **param_overrides):
    """用真实的 ColorClip 走完 generate_video 的字幕流程，返回指定时刻的画面和渲染器调用。"""
    params = vd.VideoParams(
        video_subject="test",
        video_aspect="9:16",
        subtitle_enabled=True,
        font_name="STHeitiMedium.ttc",
        font_size=60,
        stroke_width=2,
        bgm_type="",
        **param_overrides,
    )
    srt = "".join(
        f"{index}\n00:00:0{index - 1},000 --> 00:00:0{index},000\n{text}\n\n"
        for index, text in enumerate(subtitles, start=1)
    )
    frames = []

    def write(clip, *_args, **_kwargs):
        # generate_video 返回后会关闭合成视频，所以在写出时截取画面。
        if frame_at is not None:
            frames.append(clip.get_frame(frame_at))

    with tempfile.TemporaryDirectory() as tmp_dir:
        subtitle_path = Path(tmp_dir) / "subtitle.srt"
        subtitle_path.write_text(srt, encoding="utf-8")
        source = ColorClip((1080, 1920), color=(20, 30, 60), duration=len(subtitles))
        with (
            patch.object(vd, "_open_video_clip_quietly", return_value=source),
            patch.object(vd, "AudioFileClip", return_value=_FakeAudio()),
            patch.object(vd, "TextClip", wraps=TextClip) as moviepy_renderer,
            patch.object(vd, "KhattTextClip", wraps=KhattTextClip) as khatt_renderer,
            patch.object(vd, "_write_videofile_with_codec_fallback", side_effect=write),
            patch.object(vd, "_get_configured_video_codec", return_value="libx264"),
        ):
            vd.generate_video(
                video_path="combined.mp4",
                audio_path="voice.mp3",
                subtitle_path=str(subtitle_path),
                output_file=str(Path(tmp_dir) / "final.mp4"),
                params=params,
            )
    return (frames[0] if frames else None), moviepy_renderer, khatt_renderer


def _rendered_texts(renderer):
    return [call.kwargs.get("text") for call in renderer.call_args_list]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"text_background_color": "#000000"},
        {"text_background_color": "#000000", "rounded_subtitle_background": True},
    ],
    ids=["no-background", "background", "rounded-background"],
)
def test_only_arabic_subtitles_go_through_khatt(overrides):
    subtitles = ["Hello world", "مرحبا بالعالم", "你好世界"]
    frame, moviepy_renderer, khatt_renderer = _render_subtitled_video(
        subtitles, frame_at=1.5, **overrides
    )
    khatt_texts = _rendered_texts(khatt_renderer)
    moviepy_texts = _rendered_texts(moviepy_renderer)
    assert khatt_texts and all(vd.is_arabic_script(text) for text in khatt_texts)
    assert moviepy_texts and not any(vd.is_arabic_script(text) for text in moviepy_texts)
    assert all(
        call.kwargs["font"] == vd.ARABIC_FALLBACK_FONT for call in khatt_renderer.call_args_list
    )
    # 阿拉伯字幕所在的那一秒确实画出了字幕（白色文字）。
    assert (frame >= 250).all(axis=2).any()


def test_font_fallback_is_logged_once_per_video():
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        _render_subtitled_video(["مرحبا", "بالعالم", "سلام"])
    finally:
        logger.remove(sink)
    assert sum("using Amiri" in message for message in messages) == 1
