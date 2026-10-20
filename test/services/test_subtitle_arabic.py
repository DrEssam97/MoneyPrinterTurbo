"""阿拉伯字母文字字幕（阿拉伯语、波斯语、乌尔都语）通过 khatt 塑形渲染。"""

import functools
import tempfile
from pathlib import Path
from unittest.mock import patch

import khatt
import pytest
from khatt.fonts import font_path as khatt_font_path
from loguru import logger
from moviepy import ColorClip, TextClip
from PIL import ImageFont

from app.services import video as vd
from app.utils import utils
from khatt.video import TextClip as KhattTextClip

FONT_DIR = Path(utils.font_dir())
DEFAULT_FONT = str(FONT_DIR / "STHeitiMedium.ttc")
AMIRI = vd.ARABIC_FALLBACK_FONT
# 另一款带阿拉伯字形的字体（khatt 自带），用来确认用户字体可用时不会被替换。
NOTO_NASKH = str(khatt_font_path("noto-naskh"))
RLM, LRM = "‏", "‎"

# khatt 在文字超出画布、内容被裁掉时发出 TextOverflowWarning：这里一律视为失败。
pytestmark = pytest.mark.filterwarnings("error::khatt.TextOverflowWarning")


def _strip_marks(line):
    return line.lstrip(RLM + LRM)


@pytest.mark.parametrize(
    "text",
    [
        "مرحبا بالعالم",
        "بِسْمِ اللَّهِ",
        "سلام دنیا پچگ",  # 波斯语
        "پاکستان ٹیکسی ے",  # 乌尔都语
        "Khatt 2026 خط",  # 混排
        "ﻻ",  # 表现形式（lam-alef 连字）
        "﷽",  # 巴斯马拉连字（符号类）
    ],
)
def test_detects_arabic_script(text):
    assert vd.is_arabic_script(text)


@pytest.mark.parametrize(
    "text",
    [
        "Hello world",
        "你好世界",
        "Xin chào",
        "",
        None,
        "﻿BOM",
        "שלום עולם",  # 希伯来文
        "ދިވެހި، ބަސް",  # 迪维希语（Thaana）使用阿拉伯逗号
        "ܫܠܡܐ؟",  # 叙利亚文使用阿拉伯问号
        "ߒߞߏ؛",  # N'Ko 使用阿拉伯分号
        "١٢٣ ٤٥٦",  # 只有阿拉伯-印度数字
    ],
)
def test_other_scripts_are_not_arabic(text):
    assert not vd.is_arabic_script(text)


def test_font_without_arabic_glyphs_falls_back_to_amiri():
    assert vd.arabic_subtitle_font(DEFAULT_FONT, "مرحبا") == AMIRI
    assert khatt.missing_characters("مرحبا پچگ ے", AMIRI) == []


def test_font_with_arabic_glyphs_is_kept():
    assert khatt.missing_characters("مرحبا", NOTO_NASKH) == []
    assert vd.arabic_subtitle_font(NOTO_NASKH, "مرحبا") == NOTO_NASKH
    # 非阿拉伯文字不触发回退。
    assert vd.arabic_subtitle_font(DEFAULT_FONT, "你好") == DEFAULT_FONT


ARABIC_PARAGRAPH = "تُمنح هذه الشهادة إلى الطالب المتميز تقديرا لجهوده الكبيرة في العام الدراسي"


def test_arabic_wraps_by_shaped_width_even_without_raqm(monkeypatch):
    # Windows 版 Pillow 没有 Raqm：按 Pillow 测量的阿拉伯文宽度是断开、倒序的
    # 字母宽度。换行必须仍然按塑形后的宽度进行：每行不超宽，且贪心地尽量填满。
    basic = functools.partial(ImageFont.truetype, layout_engine=ImageFont.Layout.BASIC)
    monkeypatch.setattr(vd.ImageFont, "truetype", basic)
    max_width = 420
    wrapped, height = vd.wrap_text(ARABIC_PARAGRAPH, max_width, font=AMIRI, fontsize=48)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    assert " ".join(_strip_marks(line) for line in lines) == ARABIC_PARAGRAPH  # 只在空格处换行
    for line, following in zip(lines, lines[1:] + [None]):
        assert vd.shaped_text_width(line, AMIRI, 48) <= max_width
        if following is not None:
            longer = f"{line} {_strip_marks(following).split()[0]}"
            assert vd.shaped_text_width(longer, AMIRI, 48) > max_width
    assert height == len(lines) * sum(ImageFont.truetype(AMIRI, 48).getmetrics())


def test_wrapped_lines_keep_their_paragraphs_direction():
    # 自动折出的行沿用原段落方向：从右到左段落的每行以 RLM 开头，
    # 夹有阿拉伯文的从左到右段落的每行以 LRM 开头。
    rtl, _ = vd.wrap_text("مرحبا بالعالم Khatt 2026 جديد " * 4, 300, font=AMIRI, fontsize=48)
    ltr, _ = vd.wrap_text("Hello مرحبا world بالعالم " * 4, 300, font=AMIRI, fontsize=48)
    assert len(rtl.split("\n")) > 1 and len(ltr.split("\n")) > 1
    assert all(line.startswith(RLM) and vd.is_rtl_paragraph(line) for line in rtl.split("\n"))
    assert all(line.startswith(LRM) and not vd.is_rtl_paragraph(line) for line in ltr.split("\n"))


def test_hand_broken_lines_keep_their_own_direction():
    # 双语字幕：阿拉伯语一行、英文翻译一行，各自保持自己的方向。
    wrapped, _ = vd.wrap_text("مرحبا بالعالم\nHello, world!", 900, font=AMIRI, fontsize=48)
    arabic_line, english_line = wrapped.split("\n")
    assert arabic_line == RLM + "مرحبا بالعالم"
    assert english_line == "Hello, world!"  # 纯英文行本身就是从左到右
    assert not vd.is_rtl_paragraph(english_line)


def test_lines_are_measured_in_the_direction_they_render():
    # 混排行的宽度随方向不同而不同：测量必须用渲染时的方向（从右到左）。
    text = "مرحبا 1.5 Khatt"
    rtl_width = vd.shaped_text_width(RLM + text, AMIRI, 48)
    ltr_width = vd.shaped_text_width(LRM + text, AMIRI, 48)
    assert rtl_width < ltr_width  # 前提：按从左到右测量会更宽
    wrapped, _ = vd.wrap_text(text, rtl_width, font=AMIRI, fontsize=48)
    assert wrapped == RLM + text  # 按渲染方向恰好放得下，不应折行


def test_arabic_words_are_never_split_across_lines():
    # 中日文的行首标点调整会把上一行最后一个字移到下一行；阿拉伯单词
    # 不能这样拆开，否则字母连写会断开。
    text = "مرحبا بالعالم الجميل ! نعم , هذا اختبار . للتأكد ؟ من النتيجة"
    words = set(text.split())
    for max_width in range(120, 600, 15):
        wrapped, _ = vd.wrap_text(text, max_width, font=AMIRI, fontsize=48)
        assert {w for line in wrapped.split("\n") for w in _strip_marks(line).split()} <= words


def test_non_arabic_wrapping_gets_no_direction_marks():
    for text in ("Hello world from MoneyPrinterTurbo " * 4, "你好世界，这是一个很长的中文字幕句子。" * 3):
        wrapped, _ = vd.wrap_text(text, 300, font=DEFAULT_FONT, fontsize=48)
        assert RLM not in wrapped and LRM not in wrapped


def test_webui_font_check_judges_the_font_that_draws_arabic():
    # 纯阿拉伯文：Amiri 会补上字形，不再误报字体不支持。
    assert vd.subtitle_font_supports_text(DEFAULT_FONT, "مرحبا بالعالم")
    # 阿拉伯文夹中文：实际由 Amiri 绘制，Amiri 没有中文，应当提示。
    assert not vd.subtitle_font_supports_text(DEFAULT_FONT, "مرحبا 你好")
    # 其他语言的检测保持不变。
    vietnamese_only = str(FONT_DIR / "BeVietnamPro-Medium.ttf")
    assert not vd.subtitle_font_supports_text(vietnamese_only, "你好世界")
    assert vd.subtitle_font_supports_text(DEFAULT_FONT, "你好世界")


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


def _render_subtitled_video(subtitles, frame_at=None, font_dir=None, **param_overrides):
    """用真实的 ColorClip 走完 generate_video 的字幕流程，返回指定时刻的画面和渲染器调用。"""
    params = vd.VideoParams(
        video_subject="test",
        video_aspect="9:16",
        subtitle_enabled=True,
        font_name=param_overrides.pop("font_name", "STHeitiMedium.ttc"),
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
            patch.object(vd.utils, "font_dir", return_value=font_dir or str(FONT_DIR)),
        ):
            vd.generate_video(
                video_path="combined.mp4",
                audio_path="voice.mp3",
                subtitle_path=str(subtitle_path),
                output_file=str(Path(tmp_dir) / "final.mp4"),
                params=params,
            )
    return (frames[0] if frames else None), moviepy_renderer, khatt_renderer


def _subtitle_calls(renderer):
    """create_text_clip 实际绘制字幕的调用（带颜色和尺寸）。

    不含 SubtitlesClip 通过 make_textclip 的探测调用，也不含无背景样式
    为量出阿拉伯字幕自然高度而做的测量调用。
    """
    return [
        call for call in renderer.call_args_list if "size" in call.kwargs and "color" in call.kwargs
    ]


STYLES = [
    {},
    {"text_background_color": "#000000"},
    {"text_background_color": "#000000", "rounded_subtitle_background": True},
]
STYLE_IDS = ["no-background", "background", "rounded-background"]


@pytest.mark.parametrize("overrides", STYLES, ids=STYLE_IDS)
def test_only_arabic_subtitles_go_through_khatt(overrides):
    subtitles = ["Hello world", "مرحبا بالعالم", "你好世界"]
    frame, moviepy_renderer, khatt_renderer = _render_subtitled_video(
        subtitles, frame_at=1.5, **overrides
    )
    khatt_calls = _subtitle_calls(khatt_renderer)
    moviepy_calls = _subtitle_calls(moviepy_renderer)
    assert [call.kwargs["text"] for call in khatt_calls] == [RLM + "مرحبا بالعالم"]
    assert all(call.kwargs["font"] == AMIRI for call in khatt_calls)
    # 其他语言的调用与修改前完全相同：原字体、原文本、没有方向标记。
    assert [call.kwargs["text"] for call in moviepy_calls] == ["Hello world", "你好世界"]
    assert all(call.kwargs["font"] != AMIRI for call in moviepy_calls)
    assert (frame >= 250).all(axis=2).any()  # 阿拉伯字幕那一秒画出了白色文字


def test_rounded_background_fits_the_shaped_arabic_text():
    _, _, khatt_renderer = _render_subtitled_video(
        ["مرحبا بالعالم"], text_background_color="#000000", rounded_subtitle_background=True
    )
    (call,) = _subtitle_calls(khatt_renderer)
    pad_x = int(60 * 0.4)
    expected = vd.shaped_text_width(call.kwargs["text"], AMIRI, 60) + 2 * pad_x
    assert call.kwargs["size"][0] == expected  # 按塑形宽度，而不是 STHeiti 的缺字宽度


@pytest.mark.parametrize("overrides", STYLES, ids=STYLE_IDS)
def test_quranic_pause_marks_are_not_clipped(overrides):
    # Amiri 的停顿符号高出字体上沿；无背景样式的固定高度不能把它们裁掉
    # （裁掉时 khatt 会发出 TextOverflowWarning，本文件将其视为失败）。
    # 这节经文在 60px、2px 描边时需要 152px 高，按公式只有 147px。
    _render_subtitled_video(["وَٱلضُّحَىٰ ۖ وَٱلَّيْلِ إِذَا سَجَىٰ ۗ"], **overrides)


def test_make_textclip_routes_arabic_through_khatt():
    # SubtitlesClip 会用 make_textclip 探测第一条字幕。
    _, moviepy_renderer, khatt_renderer = _render_subtitled_video(["مرحبا", "Hello"])
    probes = [call for call in khatt_renderer.call_args_list if "size" not in call.kwargs]
    assert probes and all(call.kwargs["font"] == AMIRI for call in probes)
    # MoviePy 自己用 "T" 探测是否有透明蒙版；除此之外不应有阿拉伯文交给 MoviePy。
    moviepy_probes = [c for c in moviepy_renderer.call_args_list if "size" not in c.kwargs]
    assert {call.kwargs["text"] for call in moviepy_probes} <= {"T"}


def test_one_arabic_font_for_the_whole_video():
    # Rubik 有阿拉伯字母但没有乌尔都语的 ے：整条视频的阿拉伯字幕都改用
    # Amiri，而不是逐条在两种字体之间切换。
    rubik_dir = str(khatt_font_path("rubik").parent)
    _, _, khatt_renderer = _render_subtitled_video(
        ["مرحبا", "پاکستان ے"], font_dir=rubik_dir, font_name="Rubik-Regular.ttf"
    )
    calls = _subtitle_calls(khatt_renderer)
    assert len(calls) == 2
    assert {call.kwargs["font"] for call in calls} == {AMIRI}


def test_user_font_with_arabic_glyphs_is_used():
    rubik_dir = str(khatt_font_path("rubik").parent)
    _, _, khatt_renderer = _render_subtitled_video(
        ["مرحبا", "سلام"], font_dir=rubik_dir, font_name="Rubik-Regular.ttf"
    )
    assert {call.kwargs["font"] for call in _subtitle_calls(khatt_renderer)} == {
        str(Path(rubik_dir) / "Rubik-Regular.ttf")
    }


def _warnings_while(render):
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        render()
    finally:
        logger.remove(sink)
    return messages


def test_font_fallback_is_logged_once_per_video():
    messages = _warnings_while(
        lambda: [_render_subtitled_video(["مرحبا", "بالعالم", "سلام"]) for _ in range(2)]
    )
    assert sum("using Amiri" in message for message in messages) == 2  # 每条视频一次


def test_characters_the_font_cannot_draw_are_reported():
    # 阿拉伯字幕中夹杂的中文 Amiri 画不出来，会被跳过：日志要说明。
    messages = _warnings_while(lambda: _render_subtitled_video(["مرحبا 你好"]))
    assert any("skipped" in message and "你好" in message for message in messages)
