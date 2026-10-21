#!/usr/bin/env bash
# Mac check for the MoneyPrinterTurbo Arabic subtitle shaping branch.
#
#   bash mac_check.sh            # full: everything (about 25-35 min)
#   bash mac_check.sh quick      # skips the two full test-suite runs (about 10 min)
#
# Writes $WORK/report/REPORT.md (plus PNG evidence) and prints the verdict.
# Read-only for GitHub: it clones and runs things locally, and never pushes.
#
# Optional environment variables:
#   WORK=~/mpt-mac-check     working folder
#   PYVER=3.11               Python version to test (3.11 or 3.13)
#   BRANCH_DIR=/path         reuse an existing checkout of the branch
#   MAIN_DIR=/path           reuse an existing checkout of the upstream base commit

set -u
MODE="${1:-full}"
WORK="${WORK:-$HOME/mpt-mac-check}"
PYVER="${PYVER:-3.11}"
FORK="https://github.com/DrEssam97/MoneyPrinterTurbo.git"
UPSTREAM="https://github.com/harry0703/MoneyPrinterTurbo.git"
BRANCH="fix/arabic-subtitle-shaping"
REPORT="$WORK/report"
mkdir -p "$WORK" "$REPORT"
export PYTHONUTF8=1

log() { echo "[$(date +%H:%M:%S)] $*"; }
venv_python() {
  if [ -x "$1/.venv/bin/python" ]; then echo "$1/.venv/bin/python"; else echo "$1/.venv/Scripts/python.exe"; fi
}

# ---------------------------------------------------------------- prerequisites
command -v git >/dev/null || { echo "git is missing. Run: xcode-select --install"; exit 1; }
if ! command -v uv >/dev/null; then
  log "installing uv (https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null || { echo "uv is still missing; install it from https://docs.astral.sh/uv/ and rerun"; exit 1; }

# ---------------------------------------------------------------- checkouts
BR="${BRANCH_DIR:-$WORK/branch}"
if [ ! -d "$BR/.git" ] && [ ! -f "$BR/.git" ]; then
  log "cloning $BRANCH from the fork (blobless)"
  git clone -q --filter=blob:none --branch "$BRANCH" "$FORK" "$BR" || exit 1
fi
git -C "$BR" fetch -q "$UPSTREAM" main || exit 1
BASE="$(git -C "$BR" merge-base HEAD FETCH_HEAD)"
MAIN="${MAIN_DIR:-$WORK/main}"
if [ ! -d "$MAIN/.git" ] && [ ! -f "$MAIN/.git" ]; then
  log "creating a checkout of the upstream base commit ${BASE:0:7}"
  git -C "$BR" worktree add -f -q "$MAIN" "$BASE" || exit 1
fi
log "branch: $(git -C "$BR" log --oneline -1 | cut -c1-70)"
log "base:   $(git -C "$MAIN" log --oneline -1 | cut -c1-70)"

# ---------------------------------------------------------------- environment
log "installing the branch's locked dependencies (uv sync --frozen, Python $PYVER)"
(cd "$BR" && uv sync --frozen --python "$PYVER") > "$REPORT/uv_sync.txt" 2>&1 || { echo "uv sync failed, see $REPORT/uv_sync.txt"; exit 1; }
PY="$(venv_python "$BR")"
{
  if command -v sw_vers >/dev/null; then echo "os: $(sw_vers | tr '\n' ' ')"; else echo "os: $(uname -a)"; fi
  echo "machine: $(uname -m)"
  "$PY" -c "import sys, platform, PIL, khatt, moviepy; from PIL import features; print('python:', sys.version.split()[0]); print('pillow:', PIL.__version__); print('raqm:', features.check('raqm')); print('khatt:', khatt.__version__); print('moviepy:', getattr(moviepy, '__version__', '?'))"
  echo "system ffmpeg: $( (ffmpeg -version 2>/dev/null | head -1) || echo none)"
  echo "branch commit: $(git -C "$BR" rev-parse --short HEAD)"
  echo "base commit: $(git -C "$MAIN" rev-parse --short HEAD)"
} > "$REPORT/environment.txt" 2>&1
cat "$REPORT/environment.txt"

# ---------------------------------------------------------------- tests
log "running the Arabic subtitle tests"
(cd "$BR" && "$PY" -X utf8 -m pytest -v -p no:cacheprovider test/services/test_subtitle_arabic_shaping.py) > "$REPORT/arabic_tests.txt" 2>&1
tail -1 "$REPORT/arabic_tests.txt"

if [ "$MODE" != "quick" ]; then
  log "running the full suite on the branch (several minutes)"
  (cd "$BR" && "$PY" -X utf8 -m pytest -q -p no:cacheprovider -rf test) > "$REPORT/full_branch.txt" 2>&1
  tail -1 "$REPORT/full_branch.txt"
  log "running the full suite on the upstream base commit (several minutes)"
  (cd "$MAIN" && "$PY" -X utf8 -m pytest -q -p no:cacheprovider -rf test) > "$REPORT/full_main.txt" 2>&1
  tail -1 "$REPORT/full_main.txt"
fi

# ---------------------------------------------------------------- end-to-end videos
cat > "$WORK/mpt_e2e.py" <<'PYEOF'
"""Drive MoneyPrinterTurbo's real generate_video(): python mpt_e2e.py REPO MODE OUT_DIR"""
import json, os, subprocess, sys
from pathlib import Path

repo, mode, out_dir = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve()
sys.path.insert(0, str(repo))
os.chdir(repo)
import numpy as np
from moviepy import AudioClip, ColorClip, CompositeVideoClip, VideoFileClip
from PIL import Image
from app.models.schema import VideoParams
from app.services import video as vd

W, H, DURATION = 1080, 1920, 8.0
CUE_TIMES = (1.2, 3.7, 6.5)
SRT = {
    "arabic": ["مَرْحَبًا بِكُمْ فِي قَنَاتِنَا، نَتَمَنَّى لَكُمْ مُشَاهَدَةً مُمْتِعَةً وَمُفِيدَةً",
               "Breaking news: عاجل من القاهرة 2026\nالسطر الثاني من الخبر",
               "سلام دوستان، این یک آزمایش است — ٹھیک ہے"],
    "english": ["Welcome to our channel, we hope you enjoy watching",
                "Breaking news: a second line\nwith a manual break", "Thanks for watching, see you next time!"],
    "chinese": ["欢迎来到我们的频道，希望您观看愉快，并且有所收获。", "突发新闻：第二条字幕\n手动换行的第二行", "感谢观看，我们下次再见！"],
}
STYLES = {
    "plain_bottom_stroke2": dict(stroke_width=2, subtitle_position="bottom"),
    "plain_top_stroke0": dict(stroke_width=0, subtitle_position="top"),
    "rect_bg_bottom_stroke5": dict(text_background_color="#000000", stroke_width=5),
    "rounded_bg_custom40": dict(text_background_color="#1e1e1e", rounded_subtitle_background=True,
                                subtitle_position="custom", custom_position=40.0),
    "rounded_bg_spring_two_thirds": dict(text_background_color="#1e1e1e", rounded_subtitle_background=True,
                                         subtitle_animation="pop_spring", subtitle_position="two_thirds"),
}

def srt_time(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

def fixtures(folder):
    folder.mkdir(parents=True, exist_ok=True)
    base, voice = folder / "base.mp4", folder / "voice.mp3"
    if not base.exists():
        bg = ColorClip((W, H), color=(30, 70, 110), duration=DURATION)
        bar = ColorClip((W, 240), color=(200, 140, 60), duration=DURATION).with_position(lambda t: (0, int(200 + t * 180)))
        CompositeVideoClip([bg, bar], size=(W, H)).write_videofile(str(base), fps=30, codec="libx264", audio=False, logger=None)
    if not voice.exists():
        AudioClip(lambda t: 0.1 * np.sin(2 * np.pi * 220 * t), duration=DURATION, fps=44100).write_audiofile(str(voice), fps=44100, logger=None)
    bounds = (0.0, 2.5, 5.0, DURATION)
    for name, lines in SRT.items():
        cues = [f"{i + 1}\n{srt_time(bounds[i])} --> {srt_time(bounds[i + 1])}\n{t}\n" for i, t in enumerate(lines)]
        (folder / f"{name}.srt").write_text("\n".join(cues), encoding="utf-8")
    return base, voice

def params(style):
    return VideoParams(video_subject="e2e", subtitle_enabled=True, bgm_type="", font_name="STHeitiMedium.ttc",
                       font_size=60, text_fore_color="#FFFFFF", stroke_color="#000000", n_threads=2, **STYLES[style])

def streams(path):
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line.strip() for line in r.stderr.splitlines() if "Stream #" in line]

fx = out_dir.parent / "fixtures"
base, voice = fixtures(fx)
out_dir.mkdir(parents=True, exist_ok=True)
report = {}
if mode == "arabic":
    for style in STYLES:
        out = out_dir / f"arabic_{style}.mp4"
        try:
            vd.generate_video(str(base), str(voice), str(fx / "arabic.srt"), str(out), params(style))
            with VideoFileClip(str(out)) as clip:
                for t in CUE_TIMES:
                    Image.fromarray(clip.get_frame(t)).save(out_dir / f"arabic_{style}_{t:.1f}.png")
            report[style] = {"ok": True, "streams": streams(out)}
        except Exception as exc:  # report, don't stop
            report[style] = {"ok": False, "error": repr(exc)}
elif mode == "parity":
    captured = {}
    def capture(clip, output_file, codec, atomic_output=False, **kwargs):
        captured["frames"] = [np.asarray(clip.get_frame(t)) for t in CUE_TIMES]
        return codec
    vd._write_videofile_with_codec_fallback = capture
    for language in ("english", "chinese"):
        for style in STYLES:
            vd.generate_video(str(base), str(voice), str(fx / f"{language}.srt"), str(out_dir / "unused.mp4"), params(style))
            np.save(out_dir / f"{language}_{style}.npy", np.stack(captured["frames"]))
            report[f"{language}_{style}"] = "captured"
(out_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
PYEOF

E2E="$WORK/e2e"
log "end-to-end: Arabic videos through generate_video() on the branch"
"$PY" -X utf8 "$WORK/mpt_e2e.py" "$BR" arabic "$E2E/arabic-branch" > "$REPORT/e2e_branch_log.txt" 2>&1
log "end-to-end: the same Arabic videos on the upstream base (for before/after)"
"$PY" -X utf8 "$WORK/mpt_e2e.py" "$MAIN" arabic "$E2E/arabic-main" > "$REPORT/e2e_main_log.txt" 2>&1
log "parity: English/Chinese frames on the branch and on the upstream base"
"$PY" -X utf8 "$WORK/mpt_e2e.py" "$BR" parity "$E2E/parity-branch" > "$REPORT/parity_branch_log.txt" 2>&1
"$PY" -X utf8 "$WORK/mpt_e2e.py" "$MAIN" parity "$E2E/parity-main" > "$REPORT/parity_main_log.txt" 2>&1

# ---------------------------------------------------------------- report
cat > "$WORK/make_report.py" <<'PYEOF'
import json, re, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

report, e2e, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
lines = ["# MoneyPrinterTurbo Arabic subtitle shaping: Mac check", ""]
problems = []

def read(name):
    p = report / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

def counts(text):
    found = dict((k, int(v)) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?)", text.splitlines()[-1] if text.strip() else ""))
    return found

def failed(text):
    return sorted(set(re.findall(r"^FAILED (\S+)", text, re.M)))

lines += ["## Environment", "", "```", read("environment.txt").strip(), "```", ""]

arabic = read("arabic_tests.txt")
c = counts(arabic)
ok = c.get("passed", 0) >= 23 and not c.get("failed") and not c.get("error") and not c.get("errors")
lines += ["## Arabic subtitle tests", "", f"- {c.get('passed', 0)} passed, {c.get('failed', 0)} failed  ->  {'OK' if ok else 'PROBLEM'}", ""]
if not ok:
    problems.append("Arabic subtitle tests did not all pass (see arabic_tests.txt)")
    lines += ["```", *failed(arabic), "```", ""]

if mode != "quick":
    br, mn = read("full_branch.txt"), read("full_main.txt")
    cb, cm = counts(br), counts(mn)
    fb, fm = failed(br), failed(mn)
    new = [t for t in fb if t not in fm]
    lines += ["## Full test suite", "",
              f"- branch: {cb.get('passed', 0)} passed, {cb.get('failed', 0)} failed, {cb.get('skipped', 0)} skipped",
              f"- upstream base: {cm.get('passed', 0)} passed, {cm.get('failed', 0)} failed, {cm.get('skipped', 0)} skipped",
              f"- failures on the branch that do NOT also fail on the upstream base: {len(new)}  ->  {'OK' if not new else 'PROBLEM'}", ""]
    if fb:
        lines += ["Failures on the branch:", "", "```", *fb, "```", ""]
    if new:
        problems.append("new test failures on the branch: " + ", ".join(new))

lines += ["## End-to-end videos (generate_video, Arabic, 5 styles)", ""]
result = json.loads((e2e / "arabic-branch" / "result.json").read_text(encoding="utf-8")) if (e2e / "arabic-branch" / "result.json").exists() else {}
if not result:
    problems.append("end-to-end run produced no result (see e2e_branch_log.txt)")
for style, r in result.items():
    good = r.get("ok") and any("Video: h264" in s for s in r.get("streams", [])) and any("Audio: aac" in s for s in r.get("streams", []))
    lines.append(f"- {style}: {'OK (H.264 + AAC)' if good else 'PROBLEM ' + r.get('error', str(r.get('streams')))}")
    if not good:
        problems.append(f"end-to-end video failed: {style}")
lines.append("")

same, total = 0, 0
for f in sorted((e2e / "parity-branch").glob("*.npy")):
    other = e2e / "parity-main" / f.name
    total += 1
    if other.exists() and np.array_equal(np.load(f), np.load(other)):
        same += 1
lines += ["## English/Chinese parity (branch vs upstream base)", "",
          f"- {same}/{total} renders pixel-identical (3 frames each)  ->  {'OK' if total and same == total else 'PROBLEM'}", ""]
if not total or same != total:
    problems.append(f"English/Chinese parity: only {same}/{total} identical")

# images: before/after and a contact sheet of the branch's decoded frames
def crop_pair(style, t):
    b, a = e2e / "arabic-main" / f"arabic_{style}_{t}.png", e2e / "arabic-branch" / f"arabic_{style}_{t}.png"
    if not (b.exists() and a.exists()):
        return None
    ims = [Image.open(p).convert("RGB") for p in (b, a)]
    rows = set()
    for im in ims:
        arr = np.asarray(im).astype(int)
        mask = (arr.min(axis=-1) > 200) | (arr.max(axis=-1) < 40)
        rows.update(np.nonzero(mask.any(axis=1))[0].tolist())
    if not rows:
        return None
    box = (0, max(0, min(rows) - 25), 1080, min(1920, max(rows) + 25))
    left, right = (im.crop(box) for im in ims)
    pair = Image.new("RGB", (left.width * 2 + 30, left.height + 44), "white")
    d = ImageDraw.Draw(pair)
    d.text((10, 8), "upstream base", fill=(170, 30, 30), font=ImageFont.load_default())
    d.text((left.width + 30, 8), "branch (khatt)", fill=(20, 120, 40), font=ImageFont.load_default())
    pair.paste(left, (10, 36)); pair.paste(right, (left.width + 20, 36))
    return pair

pairs = [p for p in (crop_pair("plain_bottom_stroke2", "1.2"), crop_pair("plain_bottom_stroke2", "3.7"),
                     crop_pair("rounded_bg_custom40", "6.5")) if p]
if pairs:
    sheet = Image.new("RGB", (pairs[0].width, sum(p.height for p in pairs)), "white")
    y = 0
    for p in pairs:
        sheet.paste(p, (0, y)); y += p.height
    sheet.resize((sheet.width // 2, sheet.height // 2)).save(report / "before_after.png")
    lines += ["## Images to look at", "", "- `before_after.png`: same SRT and style on the upstream base (left) and the branch (right)"]
frames = sorted((e2e / "arabic-branch").glob("*.png"))
if frames:
    thumbs = [Image.open(f).convert("RGB").resize((270, 480)) for f in frames]
    cols = 3
    grid = Image.new("RGB", (cols * 280, ((len(thumbs) + cols - 1) // cols) * 490), "white")
    for i, im in enumerate(thumbs):
        grid.paste(im, ((i % cols) * 280, (i // cols) * 490))
    grid.save(report / "arabic_frames.png")
    lines += ["- `arabic_frames.png`: every decoded Arabic frame from the branch's videos (5 styles × 3 cues)", ""]

verdict = "PASS" if not problems else "NEEDS A LOOK"
lines[1:1] = ["", f"**Verdict: {verdict}**", ""] + [f"- {p}" for p in problems] + [""]
(report / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Verdict: {verdict}")
for p in problems:
    print(" -", p)
PYEOF
"$PY" -X utf8 "$WORK/make_report.py" "$REPORT" "$E2E" "$MODE"
log "report: $REPORT/REPORT.md"
