"""Test the TV shortlist on a past broadcast instead of waiting for the next live morning window.

For each channel this finds the YouTube recording that covers the window (default 08:30-09:15 IST today), downloads
only that part, transcribes it (base model for all channels, also the small model for Hindi channels, with timings),
writes transcripts where `python -m stock_finder tv-rebuild` reads them, and grabs one video frame every 10 seconds
from the Hindi channels and reads their on-screen text if rapidocr is installed.

    python scripts/tv_recording_test.py [--date 2026-11-02] [--start 08:30] [--end 09:15] [--out tv_vod]
    python scripts/tv_recording_test.py --ocr-only      # re-read on-screen text from frames already downloaded

Then:  TV_DIR=tv_vod/base  python -m stock_finder tv-rebuild <date>
       TV_DIR=tv_vod/small python -m stock_finder tv-rebuild <date>
Needs the `tv` extra and ffmpeg; nothing is sent to Telegram and no database is written by this script.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from stock_finder.engine import IST
from stock_finder.tv import Segment, write_transcript
from stock_finder.tv_capture import CHUNK_SECONDS, SAMPLE_RATE, Channel, Transcriber, _slug, channels_from_env

FRAME_EVERY = 10  # seconds
MIN_OVERLAP = timedelta(minutes=5)
# No trailing word boundary: on-screen OCR often drops spaces ("BUYGRASIM", "SL2950").
CALL_WORDS = re.compile(r"\b(BUY|SELL|TARGET|TGT|STOP ?LOSS|SL(?=\s*\d)|INTRADAY|POSITIONAL|LONG ?TERM|SHORT ?TERM)",
                        re.I)


def pick_recording(infos: list[dict], w0: datetime, w1: datetime) -> tuple[dict, datetime, datetime, datetime] | None:
    """The finished recording with the largest overlap with [w0, w1]: (info, its start, overlap start, overlap end)."""
    best = None
    for info in infos:
        start_ts = info.get("release_timestamp") or info.get("timestamp")
        duration = info.get("duration")
        if info.get("live_status") == "is_live" or not start_ts or not duration:
            continue
        start = datetime.fromtimestamp(start_ts, IST)
        a, b = max(start, w0), min(start + timedelta(seconds=duration), w1)
        if b - a >= MIN_OVERLAP and (best is None or b - a > best[3] - best[2]):
            best = (info, start, a, b)
    return best


def find_recording(ch: Channel, w0: datetime, w1: datetime):
    from yt_dlp import YoutubeDL

    tab_url = ch.url.rsplit("/live", 1)[0] + "/streams"
    with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": 10}) as y:
        tab = y.extract_info(tab_url, download=False)
    infos = []
    with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        for entry in tab.get("entries") or []:
            if not entry.get("id"):
                continue
            try:
                info = y.extract_info(f"https://www.youtube.com/watch?v={entry['id']}", download=False)
            except Exception as exc:
                print(f"   skip {entry['id']}: {exc}")
                continue
            ts = info.get("release_timestamp") or info.get("timestamp")
            when = datetime.fromtimestamp(ts, IST).strftime("%d %b %H:%M") if ts else "?"
            mins = f"{info['duration'] / 60:.0f} min" if info.get("duration") else "live/unknown length"
            print(f"   candidate {entry['id']} | {when} | {mins} | {info.get('live_status')} | {info.get('title')}")
            infos.append(info)
    return pick_recording(infos, w0, w1)


def download_section(url: str, a: float, b: float, fmt: str, out_base: str) -> str:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func

    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "format": fmt, "outtmpl": out_base + ".%(ext)s",
            "download_ranges": download_range_func(None, [(a, b)]), "force_keyframes_at_cuts": False}
    with YoutubeDL(opts) as y:
        y.download([url])
    files = [f for f in glob.glob(out_base + ".*") if not f.endswith(".part")]
    if not files:
        raise RuntimeError("download produced no file")
    return files[0]


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def transcribe_dir(transcriber: Transcriber, chunk_dir: str, language: str, w0: datetime,
                   channel: str) -> tuple[list[Segment], list[float]]:
    segments, took = [], []
    for k, path in enumerate(sorted(glob.glob(os.path.join(chunk_dir, "chunk_*.wav")))):
        t = time.time()
        for offset, text in transcriber.transcribe(path, language):
            segments.append(Segment(channel, w0 + timedelta(seconds=k * CHUNK_SECONDS + offset), text))
        took.append(time.time() - t)
    return segments, took


def ocr_frames(frame_dir: str, w0: datetime) -> tuple[int, int, float]:
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    files = sorted(glob.glob(os.path.join(frame_dir, "f_*.jpg")))
    hits, t0 = 0, time.time()
    for i, f in enumerate(files):
        result, _ = ocr(f)
        lines = [str(r[1]) for r in (result or []) if float(r[2]) > 0.6]  # rapidocr returns the score as a string
        if any(CALL_WORDS.search(x) for x in lines):
            hits += 1
            at = w0 + timedelta(seconds=i * FRAME_EVERY)
            print(f"   {at:%H:%M:%S} {os.path.basename(f)}: " + " | ".join(lines)[:400])
    return hits, len(files), (time.time() - t0) / max(1, len(files))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=datetime.now(IST).date().isoformat())
    p.add_argument("--start", default="08:30")
    p.add_argument("--end", default="09:15")
    p.add_argument("--out", default="tv_vod")
    p.add_argument("--ocr-only", action="store_true", help="only read on-screen text from frames already in OUT/frames")
    args = p.parse_args()
    day = date.fromisoformat(args.date)
    w0 = datetime.combine(day, datetime.strptime(args.start, "%H:%M").time(), tzinfo=IST)
    w1 = datetime.combine(day, datetime.strptime(args.end, "%H:%M").time(), tzinfo=IST)
    if args.ocr_only:
        for frame_dir in sorted(glob.glob(os.path.join(args.out, "frames", "*"))):
            print(f"# {os.path.basename(frame_dir)} (times assume the frames start at {w0:%H:%M})")
            hits, total, per = ocr_frames(frame_dir, w0)
            print(f"   {hits} call-like frame(s) of {total}, {per:.1f}s per frame")
        return
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")
    raw_dir = os.path.join(args.out, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    found: dict[str, tuple[Channel, datetime, str]] = {}  # name -> (channel, start of audio, chunk dir)
    frames: dict[str, tuple[str, datetime]] = {}
    for ch in channels_from_env():
        print(f"\n## {ch.name}: looking for a recording covering {w0:%d %b %H:%M}-{w1:%H:%M} IST")
        try:
            pick = find_recording(ch, w0, w1)
        except Exception as exc:
            print(f"   FAILED to list recordings: {exc}")
            continue
        if pick is None:
            print("   NO finished recording covers this window (24x7 live streams are not kept as recordings)")
            continue
        info, vstart, a, b = pick
        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
        rel_a, rel_b = (a - vstart).total_seconds(), (b - vstart).total_seconds()
        print(f"   USING {url} ({info.get('title')}) for {a:%H:%M}-{b:%H:%M}")
        slug = _slug(ch.name)
        try:
            audio = download_section(url, rel_a, rel_b, "bestaudio/best", os.path.join(raw_dir, f"{slug}_audio"))
            chunk_dir = os.path.join(args.out, "audio", slug)
            os.makedirs(chunk_dir, exist_ok=True)
            ffmpeg("-i", audio, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "segment",
                   "-segment_time", str(CHUNK_SECONDS), os.path.join(chunk_dir, "chunk_%03d.wav"))
            found[ch.name] = (ch, a, chunk_dir)
            if ch.language != "en":
                video = download_section(url, rel_a, rel_b, "best[height<=480]/bestvideo[height<=480]",
                                         os.path.join(raw_dir, f"{slug}_video"))
                frame_dir = os.path.join(args.out, "frames", slug)
                os.makedirs(frame_dir, exist_ok=True)
                ffmpeg("-i", video, "-vf", f"fps=1/{FRAME_EVERY}", os.path.join(frame_dir, "f_%04d.jpg"))
                frames[ch.name] = (frame_dir, a)
        except Exception as exc:
            print(f"   FAILED to download/convert: {exc}")

    for model in ("base", "small"):
        todo = [v for v in found.values() if model == "base" or v[0].language != "en"]
        if not todo:
            continue
        print(f"\n## Transcribing with the {model} model")
        transcriber = Transcriber(model)
        out_dir = os.path.join(args.out, model, day.isoformat())
        os.makedirs(out_dir, exist_ok=True)
        for ch, a, chunk_dir in todo:
            segments, took = transcribe_dir(transcriber, chunk_dir, ch.language, a, ch.name)
            write_transcript(os.path.join(out_dir, f"{ch.name}.txt"), segments)
            if took:
                print(f"   {ch.name}: {len(took)} chunk(s), {len(segments)} line(s), "
                      f"avg {sum(took) / len(took):.0f}s / max {max(took):.0f}s per {CHUNK_SECONDS}s chunk")
        if model == "small":  # English channels keep their base transcripts so both shortlists cover all channels
            for ch, _, _ in found.values():
                src = os.path.join(args.out, "base", day.isoformat(), f"{ch.name}.txt")
                if ch.language == "en" and os.path.exists(src):
                    shutil.copy(src, out_dir)

    print("\n## On-screen text (frames that look like a stock call)")
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        print("   rapidocr-onnxruntime not installed: skipped")
        frames = {}
    for name, (frame_dir, a) in frames.items():
        print(f"# {name}")
        hits, total, per = ocr_frames(frame_dir, a)
        print(f"   {name}: {hits} call-like frame(s) of {total}, {per:.1f}s per frame")

    print(f"\nDone. Transcripts: {args.out}/base/{day} and {args.out}/small/{day}")


if __name__ == "__main__":
    main()
