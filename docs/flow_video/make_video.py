"""Render the BoardCast flow explainer video (MP4, 1280x720, voice-over + captions).

    python docs/flow_video/make_video.py          -> docs/flow_video/boardcast-flow.mp4

Windows only for the voice (built-in Microsoft voice via System.Speech). Edit SEGMENTS to change
what is said or shown; each segment lasts as long as its narration.
"""
from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
OUT = HERE / "boardcast-flow.mp4"
W, H, FPS = 1280, 720, 30
VOICE = "Microsoft Zira Desktop"
PAD = 0.7                      # silence after each line, seconds

FONT = "C:/Windows/Fonts/segoeui.ttf"
BOLD = "C:/Windows/Fonts/segoeuib.ttf"
_fonts: dict = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(BOLD if bold else FONT, size)
    return _fonts[key]


BG = (250, 250, 247)
INK = (31, 36, 48)
MUTED = (95, 94, 90)
STYLE = {   # fill, stroke, title, subtitle (same palette as the diagrams)
    "ai": ((238, 237, 254), (83, 74, 183), (60, 52, 137), (83, 74, 183)),
    "people": ((225, 245, 238), (15, 110, 86), (8, 80, 65), (15, 110, 86)),
    "code": ((241, 239, 232), (95, 94, 90), (68, 68, 65), (95, 94, 90)),
}

FLOW1 = {
    "header": "Part 1 · Submitting a poster",
    "boxes": [("Club rep sends poster", "Teams or Gemini Enterprise", "people"),
              ("Coordinator agent", "Routes to the intake agent", "ai"),
              ("Intake agent reads poster", "Asks only for missing fields", "ai"),
              ("API checks (code)", "Fields, links, dates, consent", "code"),
              ("Student Affairs approves", "In chat or on /admin", "people"),
              ("Approved item stored", "Waits for next Monday's issue", "code")],
    "branches": {"draft": (3, ("Draft", "Rep fixes in chat", "code"), 2)},   # from box, box, loops back to
}
FLOW2 = {
    "header": "Part 2 · Every week, automatically",
    "boxes": [("Sunday 6pm: editor agent", "Spotlight, headline, teaser", "ai"),
              ("Grounding check (code)", "Replaces invented numbers", "code"),
              ("Student Affairs preview", "Do nothing, or hold", "people"),
              ("Monday 8am: send (code)", "Same edition for everyone", "code"),
              ("Student reads", "Email and web magazine", "people"),
              ("Taps Register here", "Saves event, counts click", "code")],
    "branches": {"held": (2, ("Held", "Not sent Monday", "code"), None),
                 "reminder": (5, ("Reminder email", "1 hour before", "code"), None)},
}

# Each segment: what is said (say), the caption shown (caption, defaults to say) and what is on screen.
SEGMENTS = [
    {"scene": "title", "say": "BoardCast. How a club poster becomes Campus Weekly, the Monday newsletter for every A P U student."},
    {"scene": "problem", "step": 0,
     "say": "Today, club events live on lift lobby posters and on each club's own social media.",
     "caption": "Today, club events live on lift-lobby posters and on each club's own social media."},
    {"scene": "problem", "step": 1,
     "say": "A P Space notifications only carry official announcements, so students miss events they would have loved.",
     "caption": "APSpace Notifications only carry official announcements, so students miss events they would have loved."},
    {"scene": "flow1", "step": -1, "say": "Part one. Submitting a poster."},
    {"scene": "flow1", "step": 0, "say": "A club rep sends the poster in a chat, in Microsoft Teams or Gemini Enterprise."},
    {"scene": "flow1", "step": 1, "say": "The coordinator agent sees it is a submission, and hands it to the intake agent."},
    {"scene": "flow1", "step": 2, "say": "The intake agent reads the poster with Gemini. It asks only for what is missing, like the fee, and never guesses.",
     "caption": "The intake agent reads the poster with Gemini. It asks only for what's missing, like the fee, and never guesses."},
    {"scene": "flow1", "step": 3, "say": "The campus system has already handled event and venue approval. BoardCast checks the poster: required fields, past dates, and unsafe links. Q R codes are decoded in code too.",
     "caption": "Campus handles event and venue approval. BoardCast checks required fields, past dates and unsafe links. QR codes are decoded in code too."},
    {"scene": "flow1", "step": 3, "branch": "draft", "say": "If something is wrong, the item stays a draft, and the rep fixes it in the same chat.",
     "caption": "If something is wrong, the item stays a draft, and the rep fixes it in the same chat."},
    {"scene": "flow1", "step": 4, "say": "Clean items wait for Student Affairs, who approve them in chat or on the review page. A student without that role is refused by the server.",
     "caption": "Clean items wait for Student Affairs, who approve them in chat or on the review page. A student without that role is refused by the server."},
    {"scene": "flow1", "step": 5, "say": "Approved items wait for next Monday's issue, whether the event is next week or a month away."},
    {"scene": "flow2", "step": -1, "say": "Part two. Every week, automatically."},
    {"scene": "flow2", "step": 0, "say": "On Sunday at 6 P M, the editor agent picks a spotlight event and writes the headline, a one line teaser, and the subject. Everything else is in the clubs' own words.",
     "caption": "On Sunday at 6pm, the editor agent picks a spotlight event and writes the headline, teaser and subject. Everything else is in the clubs' own words."},
    {"scene": "flow2", "step": 1, "say": "Code then replaces any headline or teaser with a number that is not in the clubs' data, so nothing is invented.",
     "caption": "Code then replaces any headline or teaser with a number that isn't in the clubs' data, so nothing is invented."},
    {"scene": "flow2", "step": 2, "branch": "held", "say": "Student Affairs gets a preview. If they do nothing, it goes out. If something is wrong, they hold it.",
     "caption": "Student Affairs gets a preview. If they do nothing, it goes out. If something's wrong, they hold it."},
    {"scene": "flow2", "step": 3, "say": "On Monday at 8 A M, code re-checks every item is still approved, then sends the same edition to every subscriber.",
     "caption": "On Monday at 8am, code re-checks every item is still approved, then sends the same edition to every subscriber."},
    {"scene": "flow2", "step": 4, "say": "Students read it in their inbox, or on the web magazine."},
    {"scene": "flow2", "step": 5, "branch": "reminder", "say": "When they tap Register here, the click is counted for the club, and they get a reminder one hour before the event.",
     "caption": "When they tap Register here, the click is counted for the club, and they get a reminder one hour before the event."},
    {"scene": "rule", "say": "The rule throughout: A I reads and writes words. Code does everything that must be exact. People make the decisions.",
     "caption": "The rule throughout: AI reads and writes words. Code does everything that must be exact. People make the decisions."},
    {"scene": "end", "say": "BoardCast. One poster in. The right students reached."},
]


# ---------------------------------------------------------------- voice

def synthesise() -> list[float]:
    """One WAV per segment with the built-in Windows voice; returns each duration in seconds."""
    BUILD.mkdir(exist_ok=True)
    jobs = [{"text": s["say"], "path": str(BUILD / f"seg{i:02d}.wav")} for i, s in enumerate(SEGMENTS)]
    (BUILD / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$jobs = Get-Content -Raw -Encoding UTF8 '{BUILD / "jobs.json"}' | ConvertFrom-Json
foreach ($j in $jobs) {{
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice('{VOICE}'); $s.Rate = 0
  $s.SetOutputToWaveFile($j.path, $fmt); $s.Speak($j.text); $s.Dispose()
}}"""
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True)
    durations = []
    for j in jobs:
        with wave.open(j["path"]) as w:
            durations.append(w.getnframes() / w.getframerate())
    return durations


def join_audio(durations: list[float]) -> Path:
    out = BUILD / "voice.wav"
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(1); dst.setsampwidth(2); dst.setframerate(22050)
        for i in range(len(SEGMENTS)):
            with wave.open(str(BUILD / f"seg{i:02d}.wav")) as src:
                dst.writeframes(src.readframes(src.getnframes()))
            dst.writeframes(b"\x00\x00" * int(22050 * PAD))
    return out


# ---------------------------------------------------------------- drawing helpers

def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def mix(c1, c2, a: float):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * a) for i in range(3))


def text_c(d: ImageDraw.ImageDraw, xy, s: str, f, fill, anchor="mm"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def wrap(s: str, f, width: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if f.getlength(trial) <= width:
            cur = trial
        else:
            lines.append(cur); cur = w_
    return lines + [cur] if cur else lines


def arrow(d, x1, y1, x2, y2, progress: float, color=(136, 135, 128), width=3):
    if progress <= 0:
        return
    xe, ye = x1 + (x2 - x1) * progress, y1 + (y2 - y1) * progress
    d.line([(x1, y1), (xe, ye)], fill=color, width=width)
    if progress > 0.85:
        ang = math.atan2(ye - y1, xe - x1)
        for side in (-1, 1):
            a = ang + math.pi + side * 0.5
            d.line([(xe, ye), (xe + 12 * math.cos(a), ye + 12 * math.sin(a))], fill=color, width=width)


def polyline_arrow(d, pts, progress: float, color=(136, 135, 128), width=3):
    """Arrow along a path of points, drawn up to `progress` of its length."""
    segs = list(zip(pts, pts[1:]))
    total = sum(math.dist(a, b) for a, b in segs)
    left = total * progress
    for a, b in segs:
        L = math.dist(a, b)
        if left <= 0:
            break
        p = min(1.0, left / L)
        last = left <= L
        if last and progress >= 0.99:
            arrow(d, *a, *b, 1.0, color, width)
        else:
            d.line([a, (a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p)], fill=color, width=width)
        left -= L


def node(img, x, y, w, h, title, sub, kind, alpha: float, active: bool):
    if alpha <= 0:
        return
    fill, stroke, tcol, scol = STYLE[kind]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    dy = int((1 - alpha) * 14)
    if active:
        d.rounded_rectangle([x - 6, y - 6 + dy, x + w + 6, y + h + 6 + dy], radius=16, outline=stroke + (int(90 * alpha),), width=4)
    d.rounded_rectangle([x, y + dy, x + w, y + h + dy], radius=12, fill=fill + (a,), outline=stroke + (a,), width=3 if active else 2)
    d.text((x + w / 2, y + h / 2 - 11 + dy), title, font=font(23, True), fill=tcol + (a,), anchor="mm")
    d.text((x + w / 2, y + h / 2 + 14 + dy), sub, font=font(18), fill=scol + (a,), anchor="mm")
    img.alpha_composite(layer)


def caption(img, s: str, alpha: float):
    if not s:
        return
    d = ImageDraw.Draw(img)
    f = font(23)
    lines = wrap(s, f, W - 200)
    top = H - 30 - 34 * len(lines)
    d.rounded_rectangle([60, top - 14, W - 60, H - 22], radius=12, fill=(31, 36, 48, int(225 * alpha)))
    for i, line in enumerate(lines):
        d.text((W / 2, top + 16 + 34 * i), line, font=f, fill=(255, 255, 255, int(255 * alpha)), anchor="mm")


def legend(d):
    x = 60
    for kind, label in (("ai", "AI (Gemini)"), ("people", "People"), ("code", "Code")):
        fill, stroke, _, _ = STYLE[kind]
        d.rounded_rectangle([x, 92, x + 22, 114], radius=5, fill=fill, outline=stroke, width=2)
        d.text((x + 32, 103), label, font=font(19), fill=MUTED, anchor="lm")
        x += 70 + font(19).getlength(label)


# ---------------------------------------------------------------- scenes

COL_X, COL_W, BOX_H, GAP, TOP = 300, 460, 60, 16, 132   # last box ends at y=572, above a 2-line caption
BR_X, BR_W = 830, 300


def box_y(i: int) -> int:
    return TOP + i * (BOX_H + GAP)


def draw_flow(img, flow, seg, t):
    d = ImageDraw.Draw(img)
    text_c(d, (60, 52), flow["header"], font(34, True), INK, anchor="lm")
    legend(d)
    step = seg.get("step", -1)
    shown_branches = set()
    for s in SEGMENTS[: SEGMENTS.index(seg) + 1]:
        if s["scene"] == seg["scene"] and s.get("branch"):
            shown_branches.add(s["branch"])
    for i, (title, sub, kind) in enumerate(flow["boxes"]):
        if i > step:
            break
        is_new = i == step and not seg.get("branch")
        appear = ease((t - 0.25) / 0.45) if is_new else 1.0
        if i > 0:
            prog = ease(t / 0.3) if is_new else 1.0
            arrow(d, COL_X + COL_W / 2, box_y(i - 1) + BOX_H + 2, COL_X + COL_W / 2, box_y(i) - 4, prog)
        node(img, COL_X, box_y(i), COL_W, BOX_H, title, sub, kind, appear, active=(i == step))
        d = ImageDraw.Draw(img)
    for name in shown_branches:
        src, (title, sub, kind), back = flow["branches"][name]
        is_new = seg.get("branch") == name
        prog = ease(t / 0.3) if is_new else 1.0
        y = box_y(src) + BOX_H / 2
        arrow(d, COL_X + COL_W + 2, y, BR_X - 4, y, prog)
        node(img, BR_X, box_y(src), BR_W, BOX_H, title, sub, kind, ease((t - 0.25) / 0.45) if is_new else 1.0, active=is_new)
        d = ImageDraw.Draw(img)
        if back is not None:
            bp = ease((t - 0.8) / 0.6) if is_new else 1.0
            ty = box_y(back) + BOX_H / 2
            polyline_arrow(d, [(BR_X + BR_W / 2, box_y(src) - 2), (BR_X + BR_W / 2, ty), (COL_X + COL_W + 6, ty)], bp)


def draw_title(img, t, title, sub):
    d = ImageDraw.Draw(img)
    a = ease(t / 0.6)
    d.text((W / 2, H / 2 - 50), title, font=font(84, True), fill=mix(BG, INK, a), anchor="mm")
    d.text((W / 2, H / 2 + 30), sub, font=font(30), fill=mix(BG, MUTED, a), anchor="mm")
    d.rounded_rectangle([W / 2 - 60, H / 2 + 80, W / 2 + 60, H / 2 + 86], radius=3, fill=mix(BG, (83, 74, 183), a))


def draw_problem(img, seg, t):
    d = ImageDraw.Draw(img)
    text_c(d, (60, 52), "The problem", font(34, True), INK, anchor="lm")
    cards = [("Lift-lobby posters", "Seen only if you walk past"),
             ("Each club's own socials", "Only if you follow that club"),
             ("APSpace Notifications", "Official notices only")]
    step = seg["step"]
    for i, (title, sub) in enumerate(cards):
        visible = i <= 1 if step == 0 else True
        if not visible:
            continue
        new = (step == 0 and i <= 1) or (step == 1 and i == 2)
        a = ease((t - 0.3 * i) / 0.5) if new and step == 0 else (ease(t / 0.5) if new else 1.0)
        x = 100 + i * 370
        node(img, x, 230, 330, 110, title, sub, "code", a, active=False)
    if step == 1:
        d = ImageDraw.Draw(img)
        a = ease((t - 1.2) / 0.6)
        d.text((W / 2, 440), "Students miss events they would have loved.", font=font(36, True),
               fill=mix(BG, (153, 60, 29), a), anchor="mm")


def draw_rule(img, t):
    d = ImageDraw.Draw(img)
    text_c(d, (60, 52), "The rule throughout", font(34, True), INK, anchor="lm")
    cards = [("AI", "Reads and writes words", "ai"), ("Code", "Everything that must be exact", "code"),
             ("People", "Make the decisions", "people")]
    for i, (title, sub, kind) in enumerate(cards):
        node(img, 100 + i * 370, 250, 330, 110, title, sub, kind, ease((t - 0.9 * i) / 0.5), active=False)


def render(durations: list[float], audio: Path) -> None:
    seg_len = [dd + PAD for dd in durations]
    starts = np.cumsum([0] + seg_len[:-1])
    total = sum(seg_len)
    n = int(total * FPS)
    writer = imageio_ffmpeg.write_frames(str(OUT), (W, H), fps=FPS, codec="libx264", pix_fmt_out="yuv420p",
                                         quality=8, audio_path=str(audio), audio_codec="aac",
                                         output_params=["-shortest"])
    writer.send(None)
    for f in range(n):
        now = f / FPS
        k = int(np.searchsorted(starts, now, side="right") - 1)
        seg, t = SEGMENTS[k], now - starts[k]
        img = Image.new("RGBA", (W, H), BG + (255,))
        sc = seg["scene"]
        if sc == "title":
            draw_title(img, t, "BoardCast", "How a club poster becomes Campus Weekly")
        elif sc == "end":
            draw_title(img, t, "BoardCast", "One poster in. The right students reached.")
        elif sc == "problem":
            draw_problem(img, seg, t)
        elif sc == "rule":
            draw_rule(img, t)
        else:
            draw_flow(img, FLOW1 if sc == "flow1" else FLOW2, seg, t)
        if sc not in ("title", "end"):
            caption(img, seg.get("caption", seg["say"]), ease(t / 0.3))
        writer.send(np.asarray(img.convert("RGB")).tobytes())
    writer.close()
    print(f"{OUT}  ({total:.0f}s, {n} frames)")


if __name__ == "__main__":
    durs = synthesise()
    render(durs, join_audio(durs))
