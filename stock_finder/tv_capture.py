"""Record the business channels' YouTube live streams and transcribe them while they record.

Needs the `tv` extra (yt-dlp, faster-whisper) and the system `ffmpeg`. Each channel is recorded in 2-minute audio
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
from dataclasses import dataclass
from datetime import datetime, timedelta

from .tv import Segment

log = logging.getLogger(__name__)

CHUNK_SECONDS = 120  # short chunks keep the backlog small when recording stops at 09:15


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


def check_tools() -> list[str]:
    """Missing prerequisites, as readable messages (empty when everything is installed)."""
    missing = [f"{tool} not found on PATH" for tool in ("ffmpeg", "yt-dlp") if shutil.which(tool) is None]
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append('faster-whisper not installed (pip install -e ".[tv]")')
    return missing


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _record(channel: Channel, seconds: int, out_dir: str) -> subprocess.Popen:
    """Start yt-dlp | ffmpeg writing 16 kHz mono WAV chunks into out_dir; returns the ffmpeg process."""
    ytdlp = subprocess.Popen(["yt-dlp", "--quiet", "--no-warnings", "-f", "bestaudio/best", "-o", "-", channel.url],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-t", str(seconds), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "segment", "-segment_time", str(CHUNK_SECONDS), os.path.join(out_dir, "chunk_%03d.wav")],
        stdin=ytdlp.stdout, stderr=subprocess.PIPE)
    assert ytdlp.stdout is not None
    ytdlp.stdout.close()  # ffmpeg owns the pipe now; yt-dlp gets SIGPIPE when ffmpeg stops
    ffmpeg.ytdlp = ytdlp  # type: ignore[attr-defined]
    return ffmpeg


class Transcriber:
    def __init__(self, model_size: str) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._lock = threading.Lock()  # one chunk at a time: the CPU is the limit anyway

    def transcribe(self, path: str, language: str) -> list[tuple[float, str]]:
        task = "translate" if language != "en" else "transcribe"
        with self._lock:
            segments, _ = self._model.transcribe(path, language=language, task=task, vad_filter=True,
                                                 beam_size=1)
            return [(s.start, s.text) for s in segments]


def capture_channel(channel: Channel, start: datetime, seconds: int, work_dir: str, transcriber: Transcriber,
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
    transcriber = Transcriber(model_size)
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
