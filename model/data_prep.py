"""Stead data prep — Ruby's videos -> 16kHz mono audio -> utterance segments -> manifest.

The first stage of the Nebius pipeline that trains "the model that understands Ruby". Ruby has
cerebral palsy (dysarthric speech); off-the-shelf ASR fails her, so we build a *personalized*
corpus from her own recordings. This module turns raw clips into the manifest the fine-tune and
eval stages consume.

Pipeline:
  video/audio  ->  16kHz mono WAV  ->  voice-activity utterance segments  ->  manifest.jsonl

Each manifest row is one utterance:
  {"audio_path": "...wav", "transcript": "...", "intent": "...", "mood": "...", "speaker": "ruby"}
`transcript` / `intent` / `mood` are OPTIONAL — present when a human (or a sidecar label file) has
annotated them, absent for raw segments awaiting labels. The fine-tune needs transcripts; eval needs
a held-out labelled slice.

OFFLINE-FIRST + degrade gracefully:
  - Uses ffmpeg for decode/resample when it is on PATH; otherwise falls back to the stdlib `wave`
    module (already-WAV inputs only) and, failing that, emits manifest rows that point at the
    ORIGINAL files with a clear `needs_ffmpeg` note rather than crashing.
  - Segmentation prefers `webrtcvad` (pip install webrtcvad) for real voice-activity detection;
    without it, falls back to a simple energy gate, and without numpy to whole-file segments.
Nothing here needs the network. Thin + swappable: each stage is a function you can replace.

Run:
    python model/data_prep.py
    python model/data_prep.py --in model/data/ruby_videos --out model/data/manifest.jsonl
    python model/data_prep.py --labels model/data/labels.json   # sidecar transcripts/intent/mood
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.join(_HERE, "data", "ruby_videos")
DEFAULT_AUDIO = os.path.join(_HERE, "data", "audio")
DEFAULT_MANIFEST = os.path.join(_HERE, "data", "manifest.jsonl")

TARGET_RATE = 16_000  # Whisper-family models expect 16kHz mono
MEDIA_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4a", ".mp3", ".wav", ".flac", ".ogg")


@dataclass
class Utterance:
    """One labelled-or-unlabelled speech segment — a single manifest row."""

    audio_path: str
    speaker: str = "ruby"
    start: float = 0.0
    end: float = 0.0
    transcript: str | None = None  # gold text, when annotated
    intent: str | None = None      # e.g. "order_food", "ask_help", "social" — when annotated
    mood: str | None = None        # e.g. "calm", "frustrated", "tired" — when annotated
    source: str = ""               # original media file this came from
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        row = asdict(self)
        # Keep the manifest tidy: drop empty optional fields.
        return {k: v for k, v in row.items() if v not in (None, "", [], 0.0) or k == "audio_path"}


def have_ffmpeg() -> bool:
    """True iff ffmpeg is callable on PATH."""
    return shutil.which("ffmpeg") is not None


def list_media(in_dir: str) -> list[str]:
    """All recognised media files directly under `in_dir` (sorted, recursive)."""
    found: list[str] = []
    for root, _dirs, files in os.walk(in_dir):
        for name in sorted(files):
            if name.lower().endswith(MEDIA_EXTS):
                found.append(os.path.join(root, name))
    return found


def to_wav_16k_mono(src: str, dst: str) -> bool:
    """Decode `src` to a 16kHz mono PCM WAV at `dst`.

    Tries ffmpeg first (handles any container/codec); falls back to the stdlib `wave` module for
    inputs that are already WAV at the right format. Returns True on success, False if neither path
    can produce a clean 16kHz mono WAV (caller then degrades to pointing at the original).
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if have_ffmpeg():
        cmd = ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(TARGET_RATE),
               "-vn", "-acodec", "pcm_s16le", dst]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.exists(dst)
        except (subprocess.CalledProcessError, OSError):
            return False
    # No ffmpeg: only already-WAV inputs can be handled, and only if already 16k mono.
    if not src.lower().endswith(".wav"):
        return False
    try:
        with wave.open(src, "rb") as w:
            if w.getnchannels() == 1 and w.getframerate() == TARGET_RATE and w.getsampwidth() == 2:
                shutil.copyfile(src, dst)
                return True
    except (wave.Error, OSError):
        return False
    return False  # WAV but wrong format and no ffmpeg to transcode — degrade upstream.


def _wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or TARGET_RATE)
    except (wave.Error, OSError):
        return 0.0


def segment_utterances(wav_path: str, out_dir: str, min_sec: float = 0.6,
                       max_sec: float = 20.0) -> list[tuple[str, float, float]]:
    """Split a 16kHz mono WAV into utterance segments by voice activity.

    Returns a list of (segment_wav_path, start_sec, end_sec). Prefers `webrtcvad`; falls back to a
    numpy energy gate; falls back again to one whole-file "segment". Dysarthric speech has long,
    uneven pauses — we keep min/max bounds generous so we don't chop mid-word.
    """
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(wav_path))[0]
    try:
        spans = _vad_spans(wav_path, min_sec, max_sec)
    except Exception:  # noqa: BLE001 - any VAD failure degrades to whole-file
        spans = []
    if not spans:
        dur = _wav_duration(wav_path)
        return [(wav_path, 0.0, dur)]  # whole file is the single utterance

    segs: list[tuple[str, float, float]] = []
    for i, (start, end) in enumerate(spans):
        seg_path = os.path.join(out_dir, f"{base}_utt{i:03d}.wav")
        if _write_wav_slice(wav_path, seg_path, start, end):
            segs.append((seg_path, start, end))
        else:
            segs.append((wav_path, start, end))  # couldn't slice; reference parent + offsets
    return segs


def _vad_spans(wav_path: str, min_sec: float, max_sec: float) -> list[tuple[float, float]]:
    """Voice-activity spans as (start_sec, end_sec). Raises if neither webrtcvad nor numpy works."""
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        pcm = w.readframes(n)
    try:
        import webrtcvad  # type: ignore
    except ImportError:
        return _energy_spans(pcm, rate, min_sec, max_sec)

    vad = webrtcvad.Vad(2)  # 0..3, higher = more aggressive filtering
    frame_ms = 30
    bytes_per_frame = int(rate * (frame_ms / 1000.0)) * 2  # 16-bit mono
    spans: list[tuple[float, float]] = []
    cur_start: float | None = None
    t = 0.0
    step = frame_ms / 1000.0
    for off in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame):
        frame = pcm[off:off + bytes_per_frame]
        try:
            voiced = vad.is_speech(frame, rate)
        except Exception:  # noqa: BLE001
            voiced = True
        if voiced and cur_start is None:
            cur_start = t
        elif not voiced and cur_start is not None:
            if t - cur_start >= min_sec:
                spans.append((cur_start, min(t, cur_start + max_sec)))
            cur_start = None
        t += step
    if cur_start is not None and t - cur_start >= min_sec:
        spans.append((cur_start, min(t, cur_start + max_sec)))
    return spans


def _energy_spans(pcm: bytes, rate: int, min_sec: float, max_sec: float) -> list[tuple[float, float]]:
    """numpy energy-gate fallback. Raises ImportError if numpy is missing (caller degrades)."""
    import numpy as np  # type: ignore

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return []
    win = int(rate * 0.03) or 1
    n_win = samples.size // win
    if n_win == 0:
        return []
    frames = samples[: n_win * win].reshape(n_win, win)
    energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
    thresh = max(energy.mean() * 0.5, np.percentile(energy, 40))
    voiced = energy > thresh
    spans: list[tuple[float, float]] = []
    cur_start: int | None = None
    for i, v in enumerate(voiced):
        if v and cur_start is None:
            cur_start = i
        elif not v and cur_start is not None:
            s, e = cur_start * 0.03, i * 0.03
            if e - s >= min_sec:
                spans.append((s, min(e, s + max_sec)))
            cur_start = None
    if cur_start is not None:
        s, e = cur_start * 0.03, n_win * 0.03
        if e - s >= min_sec:
            spans.append((s, min(e, s + max_sec)))
    return spans


def _write_wav_slice(src_wav: str, dst_wav: str, start: float, end: float) -> bool:
    """Write [start, end) of a 16kHz mono WAV to a new WAV. Returns False on any failure."""
    try:
        with wave.open(src_wav, "rb") as w:
            rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
            w.setpos(int(start * rate))
            frames = w.readframes(max(0, int((end - start) * rate)))
        with wave.open(dst_wav, "wb") as out:
            out.setnchannels(ch)
            out.setsampwidth(width)
            out.setframerate(rate)
            out.writeframes(frames)
        return True
    except (wave.Error, OSError, ValueError):
        return False


def load_labels(path: str | None) -> dict:
    """Optional sidecar labels: {source_basename or "source#utt_index": {transcript, intent, mood}}.

    Lets a human annotate transcripts/intent/mood out-of-band (a JSON file) without touching media.
    Returns {} when no label file is given or it can't be read.
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _apply_labels(utt: Utterance, idx: int, labels: dict) -> None:
    """Attach transcript/intent/mood from the sidecar by 'source#idx' then bare 'source'."""
    src_key = os.path.basename(utt.source)
    rec = labels.get(f"{src_key}#{idx}") or labels.get(src_key) or {}
    if isinstance(rec, dict):
        utt.transcript = rec.get("transcript", utt.transcript)
        utt.intent = rec.get("intent", utt.intent)
        utt.mood = rec.get("mood", utt.mood)


class MissingTranscriptsError(RuntimeError):
    """Raised when --require-text is set but the manifest has no (audio, text) pairs.

    ASR fine-tuning is *supervised*: every training clip needs the gold `text` of what Ruby
    actually said. Those transcripts come from one of two places, documented in the README:
      1. Existing captions/subtitles shipped alongside Ruby's videos (.srt/.vtt -> labels.json), or
      2. A human-correction pass: a caregiver listens to each segment and types/fixes the text
         (this is the Project-Euphonia approach — the gold corpus IS the moat for dysarthric ASR).
    We FAIL LOUD here rather than silently producing an untrainable manifest.
    """


def build_manifest(in_dir: str = DEFAULT_IN, audio_dir: str = DEFAULT_AUDIO,
                   manifest_path: str = DEFAULT_MANIFEST, labels_path: str | None = None,
                   speaker: str = "ruby", require_text: bool = False) -> list[Utterance]:
    """Run the full pipeline and write `manifest_path` (JSONL). Returns the utterance list.

    Degrades gracefully at every step: missing ffmpeg, unsegmentable audio, or zero inputs all
    yield a valid (possibly empty / annotated-with-notes) manifest instead of an exception.

    If `require_text` is True, raises MissingTranscriptsError unless EVERY emitted utterance has a
    transcript — use this gate before kicking off a fine-tune so you never spend GPU-hours on an
    unlabelled corpus. See class docstring for where transcripts come from.
    """
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    labels = load_labels(labels_path)

    media = list_media(in_dir)
    utterances: list[Utterance] = []

    if not media:
        print(f"[data_prep] No media in {in_dir}. Drop Ruby's clips there and re-run.", file=sys.stderr)
    if not have_ffmpeg():
        print("[data_prep] ffmpeg not found — only 16kHz-mono WAVs convert cleanly; others are "
              "referenced as-is with a 'needs_ffmpeg' note. Install ffmpeg for full decode.",
              file=sys.stderr)

    for src in media:
        base = os.path.splitext(os.path.basename(src))[0]
        wav = os.path.join(audio_dir, base + ".16k.wav")
        if to_wav_16k_mono(src, wav):
            segs = segment_utterances(wav, os.path.join(audio_dir, "segments"))
            for i, (seg_path, start, end) in enumerate(segs):
                u = Utterance(audio_path=os.path.abspath(seg_path), speaker=speaker,
                              start=round(start, 3), end=round(end, 3), source=os.path.abspath(src))
                _apply_labels(u, i, labels)
                utterances.append(u)
        else:
            # Degrade: keep the original in the manifest so nothing is silently lost.
            u = Utterance(audio_path=os.path.abspath(src), speaker=speaker, source=os.path.abspath(src),
                          notes=["needs_ffmpeg: could not transcode to 16kHz mono WAV"])
            _apply_labels(u, 0, labels)
            utterances.append(u)

    with open(manifest_path, "w", encoding="utf-8") as f:
        for u in utterances:
            f.write(json.dumps(u.to_row(), ensure_ascii=False) + "\n")

    labelled = sum(1 for u in utterances if u.transcript)
    print(f"[data_prep] {len(utterances)} utterances from {len(media)} files "
          f"({labelled} with transcripts) -> {manifest_path}")

    if require_text:
        missing = [u for u in utterances if not u.transcript]
        if not utterances or missing:
            raise MissingTranscriptsError(
                f"--require-text: {len(missing)}/{len(utterances)} utterances have NO transcript. "
                "ASR fine-tuning needs the gold text of what Ruby said for EVERY clip. Provide a "
                "sidecar labels file (--labels labels.json mapping 'source#idx' -> {{transcript, "
                "intent, mood}}) from captions or a human-correction pass, then re-run. "
                "See model/README.md > Transcripts (the LABELS requirement)."
            )
    return utterances


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ruby videos -> 16kHz audio -> utterance manifest")
    ap.add_argument("--in", dest="in_dir", default=DEFAULT_IN, help="input media dir")
    ap.add_argument("--audio", dest="audio_dir", default=DEFAULT_AUDIO, help="output audio dir")
    ap.add_argument("--out", dest="manifest", default=DEFAULT_MANIFEST, help="output manifest.jsonl")
    ap.add_argument("--labels", dest="labels", default=None, help="optional sidecar labels JSON")
    ap.add_argument("--speaker", default="ruby")
    ap.add_argument("--require-text", action="store_true",
                    help="fail loudly unless every utterance has a transcript (gate before fine-tune)")
    args = ap.parse_args(argv)
    try:
        build_manifest(args.in_dir, args.audio_dir, args.manifest, args.labels, args.speaker,
                       require_text=args.require_text)
    except MissingTranscriptsError as e:
        print(f"[data_prep] FATAL: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
