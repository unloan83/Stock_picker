"""Record the business channels' YouTube live streams and transcribe them while they record.

Needs the `tv` extra (yt-dlp), `ffmpeg`, and either GROQ_API_KEY (cloud) or the `tv-local` extra (faster-whisper).
Each channel is recorded in 2-minute audio
chunks; finished chunks are transcribed straight away, so the shortlist is ready soon after recording stops.
Hindi channels are translated to English so company names come out in Latin script.
"""

import glob
import logging
import os
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta

from .tv import Segment

log = logging.getLogger(__name__)

CHUNK_SECONDS = 120  # short chunks keep the backlog small when recording stops at 09:15
SAMPLE_RATE = 16000  # what Whisper expects
# TV_KEEP_AUDIO=1 keeps the WAV chunks (about 2 MB per channel-minute) so another model can be tried on them later.
KEEP_AUDIO = os.environ.get("TV_KEEP_AUDIO", "") not in ("", "0")


@dataclass(frozen=True)
class Channel:
    name: str
    url: str
    language: str  # "hi" (translated to English) or "en"


# YouTube live pages of each channel. Override with TV_CHANNELS="Name|url|lang;Name|url|lang".
DEFAULT_CHANNELS = [
    Channel("Zee Business", "https://www.youtube.com/@ZeeBusiness/live", "hi"),
    Channel("CNBC Awaaz", "https://www.youtube.com/@CNBCAwaaz/live", "hi"),
    Channel("NDTV Profit", "https://www.youtube.com/@NDTVProfitIndia/live", "en"),
    Channel("CNBC-TV18", "https://www.youtube.com/@CNBC-TV18/live", "en"),
]


def channels_from_env() -> list[Channel]:
    raw = os.environ.get("TV_CHANNELS", "").strip()
    if not raw:
        return DEFAULT_CHANNELS
    out = []
    for part in raw.split(";"):
        name, url, lang = (p.strip() for p in part.split("|"))
        out.append(Channel(name, url, lang))
    return out


def use_groq() -> bool:
    """Cloud transcription when TV_TRANSCRIBER=groq, or by default whenever GROQ_API_KEY is set."""
    choice = os.environ.get("TV_TRANSCRIBER", "auto").strip().lower()
    return choice == "groq" or (choice == "auto" and bool(os.environ.get("GROQ_API_KEY")))


def check_tools() -> list[str]:
    """Missing prerequisites, as readable messages (empty when everything is installed)."""
    missing = [f"{tool} not found on PATH" for tool in ("ffmpeg", "yt-dlp") if shutil.which(tool) is None]
    if use_groq():
        if not os.environ.get("GROQ_API_KEY"):
            missing.append("TV_TRANSCRIBER=groq but GROQ_API_KEY is not set")
        return missing
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append('no GROQ_API_KEY, and faster-whisper not installed (pip install -e ".[tv-local]")')
    return missing


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _record(channel: Channel, seconds: int, out_dir: str) -> subprocess.Popen:
    """Start yt-dlp | ffmpeg writing 16 kHz mono WAV chunks into out_dir; returns the ffmpeg process."""
    ytdlp = subprocess.Popen(["yt-dlp", "--quiet", "--no-warnings", "-f", "bestaudio/best", "-o", "-", channel.url],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-t", str(seconds), "-vn", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
         "-f", "segment", "-segment_time", str(CHUNK_SECONDS), os.path.join(out_dir, "chunk_%03d.wav")],
        stdin=ytdlp.stdout, stderr=subprocess.PIPE)
    assert ytdlp.stdout is not None
    ytdlp.stdout.close()  # ffmpeg owns the pipe now; yt-dlp gets SIGPIPE when ffmpeg stops
    ffmpeg.ytdlp = ytdlp  # type: ignore[attr-defined]
    return ffmpeg


def read_wav(path: str):  # -> numpy float32 array
    """16 kHz mono 16-bit WAV as float32 samples in [-1, 1].

    Reading the chunk ourselves keeps faster-whisper away from its PyAV file decoder, which breaks on newer PyAV
    releases (`open() got an unexpected keyword argument 'metadata_errors'`).
    """
    import numpy as np

    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path}: expected {SAMPLE_RATE} Hz mono 16-bit WAV, got "
                             f"{w.getframerate()} Hz, {w.getnchannels()} ch, {8 * w.getsampwidth()}-bit")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class Transcriber:
    def __init__(self, model_size: str) -> None:
        import numpy as np
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._lock = threading.Lock()  # one chunk at a time: the CPU is the limit anyway
        # Fail now, not after a whole recording, if the speech model cannot run on this machine.
        segments, _ = self._model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en", beam_size=1)
        list(segments)

    def transcribe(self, path: str, language: str) -> list[tuple[float, str]]:
        task = "translate" if language != "en" else "transcribe"
        audio = read_wav(path)
        with self._lock:
            segments, _ = self._model.transcribe(audio, language=language, task=task, vad_filter=True,
                                                 beam_size=1)
            return [(s.start, s.text) for s in segments]


GROQ_URL = "https://api.groq.com/openai/v1/audio/{endpoint}"
GROQ_TRANSLATE_MODEL = "whisper-large-v3"  # the only Groq Whisper model that translates to English
GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
# Whisper's prompt nudges spelling; market vocabulary helps it hear names like "ICICI Bank" and "Cipla" correctly.
GROQ_PROMPT = ("Indian stock market TV. Nifty, Bank Nifty, Sensex, buy, sell, target, stop loss, intraday, positional. "
               "Reliance, HDFC Bank, ICICI Bank, SBI, Infosys, TCS, Cipla, Bosch, Grasim, Tata Motors, Bajaj Finance.")


class GroqTranscriber:
    """Groq's hosted Whisper: a 2-minute chunk takes seconds, so all channels keep up with the broadcast.

    Hindi is translated to English (as the local model does); English is transcribed. The audio is sent to Groq.
    """

    def __init__(self, api_key: str | None = None, session=None, max_retries: int = 3) -> None:
        import requests

        self._key = api_key or os.environ["GROQ_API_KEY"]
        self._http = session or requests.Session()
        self._retries = max_retries
        self._limits_logged = False

    def transcribe(self, path: str, language: str) -> list[tuple[float, str]]:
        translate = language != "en"
        data = {"model": GROQ_TRANSLATE_MODEL if translate else GROQ_TRANSCRIBE_MODEL,
                "response_format": "verbose_json", "temperature": "0", "prompt": GROQ_PROMPT}
        if not translate:
            data["language"] = language
        url = GROQ_URL.format(endpoint="translations" if translate else "transcriptions")
        for attempt in range(self._retries + 1):
            with open(path, "rb") as fh:
                resp = self._http.post(url, headers={"Authorization": f"Bearer {self._key}"}, data=data,
                                       files={"file": (os.path.basename(path), fh, "audio/wav")}, timeout=120)
            if not self._limits_logged:
                limits = {k: v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")}
                if limits:
                    log.info("Groq rate limits: %s", limits)
                    self._limits_logged = True
            if resp.status_code == 200:
                body = resp.json()
                segments = body.get("segments") or []
                if segments:
                    return [(float(seg.get("start", 0.0)), seg.get("text", "")) for seg in segments]
                return [(0.0, body["text"])] if body.get("text") else []
            if resp.status_code in (429, 500, 502, 503) and attempt < self._retries:
                wait = min(60.0, float(resp.headers.get("retry-after") or 2 ** (attempt + 1)))
                log.warning("Groq HTTP %s for %s; retrying in %.0fs", resp.status_code, os.path.basename(path), wait)
                time.sleep(wait)
                continue
            # Never include the request (it carries the key); the response body is Groq's error message.
            raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")
        raise RuntimeError("Groq: retries exhausted")


def make_transcriber(model_size: str):
    if use_groq():
        log.info("transcribing with Groq (%s / %s)", GROQ_TRANSLATE_MODEL, GROQ_TRANSCRIBE_MODEL)
        return GroqTranscriber()
    log.info("transcribing locally with faster-whisper %s", model_size)
    return Transcriber(model_size)


def capture_channel(channel: Channel, start: datetime, seconds: int, work_dir: str, transcriber,
                    deadline: datetime | None = None) -> tuple[list[Segment], int]:
    """Record one channel for `seconds`, transcribing each finished chunk.

    Returns (timed transcript segments, chunks skipped because `deadline` passed before they were transcribed).
    """
    out_dir = os.path.join(work_dir, _slug(channel.name))
    os.makedirs(out_dir, exist_ok=True)
    proc = _record(channel, seconds, out_dir)
    done: set[str] = set()
    segments: list[Segment] = []
    skipped = 0

    def transcribe_ready(final: bool) -> None:
        nonlocal skipped
        chunks = sorted(glob.glob(os.path.join(out_dir, "chunk_*.wav")))
        ready = chunks if final else chunks[:-1]  # the newest chunk is still being written
        for path in ready:
            if path in done:
                continue
            done.add(path)
            if deadline is not None and datetime.now(deadline.tzinfo) >= deadline:
                skipped += 1
                continue
            k = int(os.path.basename(path)[6:9])
            try:
                for offset, text in transcriber.transcribe(path, channel.language):
                    segments.append(Segment(channel.name, start + timedelta(seconds=k * CHUNK_SECONDS + offset),
                                            text))
            except Exception:
                log.exception("%s: transcription failed for %s", channel.name, path)
            if not KEEP_AUDIO:
                os.remove(path)

    while proc.poll() is None:
        transcribe_ready(final=False)
        time.sleep(10)
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    proc.ytdlp.kill()  # type: ignore[attr-defined]
    if proc.returncode != 0:
        log.warning("%s: recorder exited %s: %s", channel.name, proc.returncode, err.strip()[-300:])
    transcribe_ready(final=True)
    if not segments:
        log.warning("%s: no speech captured (stream offline or URL changed?)", channel.name)
    if skipped:
        log.warning("%s: %d chunk(s) not transcribed before the deadline", channel.name, skipped)
    return sorted(segments, key=lambda s: s.at), skipped


def capture_all(channels: list[Channel], start: datetime, seconds: int, work_dir: str, model_size: str,
                deadline: datetime | None = None) -> dict[str, tuple[list[Segment], int]]:
    """Record and transcribe all channels in parallel; per channel (segments, chunks skipped at the deadline)."""
    transcriber = make_transcriber(model_size)
    results: dict[str, tuple[list[Segment], int]] = {}

    def run(ch: Channel) -> None:
        try:
            results[ch.name] = capture_channel(ch, start, seconds, work_dir, transcriber, deadline)
        except Exception:
            log.exception("%s: capture failed", ch.name)
            results[ch.name] = ([], 0)

    threads = [threading.Thread(target=run, args=(ch,), name=_slug(ch.name)) for ch in channels]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results
