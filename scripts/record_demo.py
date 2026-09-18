"""Record the demo video against a running Precedent instance.

    uv run --env-file .env --with playwright --with imageio-ffmpeg python scripts/record_demo.py

Drives real Chrome (headless) through the story in docs/DEMO.md, with a visible cursor and
burned-in captions, narrated with Gemini text-to-speech. Each narration sentence starts when its
on-screen action starts, so audio and video cannot drift. Writes video/precedent-demo.mp4 and .srt.

Env: DEMO_URL (default the live instance), GEMINI_API_KEY, DEMO_VOICE (default Kore).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

import imageio_ffmpeg
from google import genai
from google.genai import types
from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "video"
TTS_DIR = OUT / "tts"
URL = os.getenv("DEMO_URL", "https://precedent-boss.duckdns.org").rstrip("/")
VOICE = os.getenv("DEMO_VOICE", "Kore")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
W, H, ZOOM = 1920, 1080, 1.25
GAP = 0.25  # silence between sentences
STYLE = "Say in a calm, clear, confident tone at a brisk, natural pace: "
TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]

TOP_CAPTION = {"limits", "cost"}  # these scenes show the proof tiles at the bottom of the screen

# (key, text). Actions are bound to keys in `drive`.
SCRIPT = [
    ("hook1", "Guardrails get sampled because checks are slow."),
    ("hook2", "An LLM judge takes seconds per call, so it only runs on calls that look risky, which is exactly what an attacker shapes."),
    ("hook3", "Precedent checks every action against your own past decisions, in milliseconds."),
    ("inbox", "Here, a support agent works an inbox. Ticket T-103 hides an instruction in white-on-white text."),
    ("run", "Every tool call is checked before it runs, and shows its check time."),
    ("moss", "Retrieval is Moss, in-process, with no vector database."),
    ("obey", "The agent obeys the hidden text and asks for a four hundred pound refund."),
    ("trace", "Precedent traced both arguments to the email body, found no order behind them, and matched blocked precedents of exactly this shape."),
    ("evidence", "Blocked. The evidence is the actual precedents, not a score."),
    ("novel", "Then, an action it has never seen."),
    ("escalate", "It doesn't guess. It escalates."),
    ("confirm", "I confirm the block, and that decision goes straight into the live Moss index."),
    ("rerun", "Re-run the action: now it is a block, citing my decision. No reindex, no redeploy."),
    ("limits", "To be clear, this is nearest-precedent reasoning on synthetic data, not a proof. On held-out actions, every attack was blocked and every novel one escalated."),
    ("cost", "A check costs roughly thirteen milliseconds here, against almost four seconds for a fast LLM judge."),
    ("close", "Precedent: cheap enough to check everything."),
]

INIT_JS = r"""
(() => {
  const css = document.createElement('style');
  css.textContent = `
    #fakecursor{position:fixed;z-index:2147483646;width:22px;height:22px;margin:-4px 0 0 -4px;pointer-events:none;
      transition:none;filter:drop-shadow(0 2px 3px rgba(0,0,0,.6))}
    .ripple{position:fixed;z-index:2147483645;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;
      border:3px solid #7c9cff;pointer-events:none;animation:rp .55s ease-out forwards}
    @keyframes rp{from{transform:scale(.4);opacity:1}to{transform:scale(3);opacity:0}}
    #cap{position:fixed;left:50%;bottom:34px;transform:translateX(-50%);z-index:2147483647;max-width:1500px;
      padding:14px 26px;border-radius:14px;background:rgba(8,10,15,.86);color:#fff;font:600 30px/1.3 ui-sans-serif,-apple-system,Segoe UI,Inter,sans-serif;
      text-align:center;letter-spacing:.005em;opacity:0;transition:opacity .2s;pointer-events:none;border:1px solid rgba(124,156,255,.45)}
    #cap.on{opacity:1}
    .pulse{outline:4px solid #fbbf24!important;outline-offset:5px;border-radius:8px;animation:pl .9s ease-in-out 3}
    @keyframes pl{50%{outline-color:transparent}}
    #endcard{position:fixed;inset:0;z-index:2147483644;background:rgba(8,10,15,.94);color:#fff;display:flex;flex-direction:column;
      align-items:center;justify-content:center;gap:16px;font-family:ui-sans-serif,-apple-system,Segoe UI,Inter,sans-serif;opacity:0;transition:opacity .6s;pointer-events:none}
    #endcard.on{opacity:1}
    #endcard h1{font-size:88px;margin:0;letter-spacing:-.02em}
    #endcard p{font-size:34px;margin:0;color:#b8c2d9}
    #endcard code{font-size:32px;color:#7c9cff;font-family:ui-monospace,Menlo,monospace}
  `;
  const cur = document.createElement('div');
  cur.id = 'fakecursor';
  cur.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M3 2l7.5 19 2.7-7.6L21 10.7z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  cur.style.left='-50px'; cur.style.top='-50px';
  const cap = document.createElement('div'); cap.id = 'cap';
  const end = document.createElement('div'); end.id = 'endcard';
  end.innerHTML = '<h1>Precedent</h1><p>Make the guardrail cheap enough to run on every action.</p><code>precedent-boss.duckdns.org</code><code>github.com/kirmada1509/precedent</code>';
  // Init scripts run before the document exists: build now, attach as soon as <html> is there.
  const mount = () => { if (cur.isConnected) return; document.documentElement.append(css, cur, cap, end); };
  if (document.documentElement) mount(); else document.addEventListener('readystatechange', mount);
  document.addEventListener('DOMContentLoaded', mount);
  window.addEventListener('mousemove', e => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
  window.addEventListener('mousedown', e => {
    const r = document.createElement('div'); r.className = 'ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700);
  }, true);
  window.__cap = (t, top) => { cap.style.top = top ? '34px' : 'auto'; cap.style.bottom = top ? 'auto' : '34px'; cap.textContent = t; cap.classList.toggle('on', !!t); };
  window.__end = on => end.classList.toggle('on', on);
})();
"""


def post(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(URL + path, data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=30))


def synth(text: str) -> Path:
    """Gemini TTS -> 24 kHz mono wav, cached by text so re-runs are free."""
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TTS_DIR / (hashlib.sha1((VOICE + text).encode()).hexdigest()[:12] + ".wav")
    if path.exists():
        return path
    key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=key)
    last: Exception | None = None
    for attempt in range(6):
        model = TTS_MODELS[attempt % len(TTS_MODELS)]
        try:
            r = client.models.generate_content(
                model=model, contents=STYLE + text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))),
                ),
            )
            pcm = r.candidates[0].content.parts[0].inline_data.data
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
            return path
        except Exception as e:  # noqa: BLE001 - retry on demand spikes and empty audio
            last = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"TTS failed for {text[:40]!r}: {last}")


def duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


async def glide(page: Page, x: float, y: float, seconds: float = 0.7) -> None:
    cur = await page.evaluate("[parseFloat(document.getElementById('fakecursor').style.left)||0, parseFloat(document.getElementById('fakecursor').style.top)||0]")
    x0, y0 = cur
    steps = max(8, int(seconds * 45))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # ease in-out
        await page.mouse.move(x0 + (x - x0) * t, y0 + (y - y0) * t)
        await asyncio.sleep(seconds / steps)


async def click(page: Page, selector: str, has_text: str | None = None, scroll: str = "center") -> None:
    loc = page.locator(selector, has_text=has_text) if has_text else page.locator(selector)
    loc = loc.first
    await loc.evaluate(f"e => e.scrollIntoView({{behavior:'smooth', block:'{scroll}'}})")
    await asyncio.sleep(0.6)
    box = await loc.bounding_box()
    assert box, f"no box for {selector} {has_text}"
    await glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await asyncio.sleep(0.15)
    await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()


async def scroll_to(page: Page, selector: str, block: str = "center") -> None:
    await page.evaluate(f"document.querySelector({selector!r}).scrollIntoView({{behavior:'smooth', block:'{block}'}})")


async def drive(page: Page, clips: dict[str, tuple[Path, float]], t0: float, timeline: list[dict]) -> float:
    now = lambda: time.monotonic() - t0  # noqa: E731

    async def say(key: str, action=None, hold: float = 0.0) -> None:
        wav, dur = clips[key]
        start = now()
        text = dict(SCRIPT)[key]
        await page.evaluate("([t, top]) => window.__cap(t, top)", [text, key in TOP_CAPTION])
        timeline.append({"key": key, "start": start, "dur": dur, "text": text, "wav": str(wav)})
        if action:
            await action()
        remain = start + dur + GAP - now()
        await asyncio.sleep(max(remain, hold))

    # scene 1: the problem, on the empty state; pulse the hidden text
    await say("hook1")
    await say("hook2")
    await say("hook3")
    await page.evaluate("document.querySelectorAll('.hidden-text')[0].classList.add('pulse')")

    async def to_run():
        box = await page.locator("#btn-run").bounding_box()
        await glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, 0.9)
    await say("inbox", to_run)

    async def do_run():
        await page.evaluate("document.querySelectorAll('.pulse').forEach(e => e.classList.remove('pulse'))")
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
    # pace 0.6 s per agent step puts the injected call about where the narration reaches it
    post("/v1/demo/reset")
    await asyncio.sleep(0.3)
    await page.route("**/v1/demo/run", lambda route: route.continue_(post_data=json.dumps({"scenario": "shift", "pace": 0.6})))
    await say("run", do_run)
    await say("moss")

    # scene 3: the injected call is blocked; wait until it actually is
    await page.wait_for_selector("#stream .row.v-block", timeout=40000)

    await say("obey")
    await say("trace")
    await say("evidence", lambda: scroll_to(page, "#evidence .prec", "center"))

    # scene 4: escalation, human decision, re-run
    await page.wait_for_selector("#queue .row.v-escalate", timeout=30000)
    await say("novel", lambda: click(page, "#queue .row", "rotate_api_key", "center"))
    await say("escalate")
    await say("confirm", lambda: click(page, "button[data-adj='block']", None, "center"))
    async def rerun():
        await click(page, "button[data-recheck]", None, "center")
        await asyncio.sleep(0.5)
        await scroll_to(page, "#evidence", "start")
    await say("rerun", rerun, hold=1.2)

    # scene 5: honesty and cost
    await say("limits", lambda: scroll_to(page, "#proof", "center"))
    await say("cost", lambda: page.evaluate("document.querySelector('.tile .bars').classList.add('pulse')"))
    await page.evaluate("window.__cap('')")
    await page.evaluate("window.__end(true)")
    await say("close", hold=3.0)
    return now()


def build_audio(timeline: list[dict], total: float, out: Path) -> None:
    inputs, filt, labels = [], [], []
    for i, ev in enumerate(timeline):
        inputs += ["-i", ev["wav"]]
        ms = int(ev["start"] * 1000)
        filt.append(f"[{i}:a]adelay={ms}|{ms}[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(filt) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0,volume=1.6[aout]"
    subprocess.run([FFMPEG, "-y", *inputs, "-filter_complex", graph, "-map", "[aout]", "-t", f"{total:.2f}", str(out)], check=True, capture_output=True)


def srt_time(t: float) -> str:
    ms = int(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    print("synthesizing narration…")
    clips = {}
    for key, text in SCRIPT:
        wav = synth(text)
        clips[key] = (wav, duration(wav))
        print(f"  {key:9} {clips[key][1]:5.1f}s")
    print(f"speech total {sum(d for _, d in clips.values()):.1f}s over {len(clips)} sentences")

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "raw"),
                                        record_video_size={"width": W, "height": H}, device_scale_factor=1)
        rec_start = time.monotonic()
        page = await ctx.new_page()
        await page.add_init_script(INIT_JS)
        post("/v1/demo/reset")
        await page.goto(URL, wait_until="networkidle")
        await page.evaluate(f"document.body.style.zoom = {ZOOM}")
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1.0)
        t0 = time.monotonic()
        offset = t0 - rec_start  # video runs from context creation; the story starts here
        timeline: list[dict] = []
        total = await drive(page, clips, t0, timeline)
        await asyncio.sleep(0.5)
        post("/v1/demo/reset")  # leave the public instance clean
        video_path = await page.video.path()
        await ctx.close()
        await browser.close()

    total += 0.5
    audio = OUT / "narration.wav"
    build_audio(timeline, total, audio)
    mp4 = OUT / "precedent-demo.mp4"
    subprocess.run([
        FFMPEG, "-y", "-ss", f"{offset:.3f}", "-i", str(video_path), "-i", str(audio), "-t", f"{total:.2f}",
        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(mp4)],
        check=True, capture_output=True)
    (OUT / "precedent-demo.srt").write_text("".join(
        f"{i}\n{srt_time(e['start'])} --> {srt_time(e['start'] + e['dur'])}\n{e['text']}\n\n" for i, e in enumerate(timeline, 1)))
    print(f"\nwrote {mp4} ({mp4.stat().st_size / 1e6:.1f} MB), {total:.1f}s total")


if __name__ == "__main__":
    asyncio.run(main())
