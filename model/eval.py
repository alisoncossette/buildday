"""Stead eval — THE PROOF: general Whisper vs Ruby-personalized Whisper, on a held-out split.

Stage three. Runs a STRONG GENERAL baseline (off-the-shelf whisper-large-v3) and the FINE-TUNED model
over the same held-out clips, computes Word Error Rate (and intent accuracy if intent labels exist),
and prints the delta + a one-line headline. Lower WER = the model understands Ruby better.

Why this matters: dysarthric speech is exactly where general ASR fails. Google's Project Euphonia
showed personalized models cut WER dramatically vs general ASR for impaired speech (often from
~30-70%+ down to single/low-double digits). This script is how we SHOW that for Ruby, not claim it.

Pluggable backends (pick with --baseline-backend / --finetuned-backend):
  - "local"        : openai-whisper or transformers running locally (works offline if installed).
  - "tokenfactory" : Nebius Token Factory hosted inference (NEBIUS_API_KEY). NOTE: as of 2026 Token
                     Factory serves text LLMs and exposes NO audio transcription endpoint — this
                     backend is a clearly-marked STUB with the docs URL, NOT a fabricated call.
  - "transformers" : load a local fine-tuned LoRA checkpoint (model/artifacts/ckpt) on top of the base.

OFFLINE-SAFE: with no models installed and no API key, eval still RUNS — it reports which clips it
could score and prints honest "backend unavailable" lines instead of crashing or faking numbers.

Run:
    python model/eval.py --manifest model/data/manifest.jsonl --split 0.2
    python model/eval.py --baseline-backend local --finetuned-backend transformers \
        --finetuned-path model/artifacts/ckpt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(_HERE, "data", "manifest.jsonl")
DEFAULT_FT_PATH = os.path.join(_HERE, "artifacts", "ckpt")

BASE_MODEL = os.environ.get("STEAD_BASE_MODEL", "openai/whisper-large-v3")
TOKENFACTORY_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"


# --------------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------------
def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the standard WER normalization."""
    text = text.lower()
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def wer(ref: str, hyp: str) -> float:
    """Word Error Rate via Levenshtein on word tokens. Prefers `jiwer` if installed for parity with
    the literature; falls back to a self-contained DP implementation (no deps, offline-safe)."""
    try:
        import jiwer  # type: ignore
        return float(jiwer.wer(normalize(ref), normalize(hyp)))
    except ImportError:
        pass
    r, h = normalize(ref).split(), normalize(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    # Levenshtein distance over word lists.
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / len(r)


def corpus_wer(pairs: list[tuple[str, str]]) -> float:
    """Aggregate WER weighted by reference length (the conventional corpus-level WER)."""
    tot_err = 0.0
    tot_words = 0
    for ref, hyp in pairs:
        n = max(1, len(normalize(ref).split()))
        tot_err += wer(ref, hyp) * n
        tot_words += n
    return tot_err / max(1, tot_words)


# --------------------------------------------------------------------------------------------------
# Held-out split — deterministic so baseline and fine-tuned see the SAME eval clips.
# --------------------------------------------------------------------------------------------------
def load_eval_rows(manifest_path: str, split: float, seed: int) -> list[dict]:
    """Return the held-out, transcript-bearing rows (the last `split` fraction, seeded shuffle)."""
    if not os.path.exists(manifest_path):
        print(f"[eval] manifest not found: {manifest_path} — run data_prep.py.", file=sys.stderr)
        return []
    rows = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("transcript") and r.get("audio_path"):
                    rows.append(r)
    if not rows:
        print("[eval] no rows with both audio_path and transcript — cannot score WER.", file=sys.stderr)
        return []
    import random
    random.Random(seed).shuffle(rows)
    n_hold = max(1, int(len(rows) * split))
    return rows[-n_hold:]


# --------------------------------------------------------------------------------------------------
# Transcriber backends — each returns a callable(audio_path) -> str, or None if unavailable.
# --------------------------------------------------------------------------------------------------
def make_local_whisper(model_name: str) -> Callable[[str], str] | None:
    """openai-whisper (preferred for offline) then transformers pipeline. None if neither installed."""
    try:
        import whisper  # type: ignore  (openai-whisper)
        size = "large-v3" if "large-v3" in model_name else model_name.split("/")[-1]
        try:
            model = whisper.load_model(size)
        except Exception:  # noqa: BLE001 - unknown size name -> fall back to base
            model = whisper.load_model("base")

        def _t(path: str) -> str:
            return model.transcribe(path, language="en")["text"].strip()
        return _t
    except ImportError:
        pass
    try:
        from transformers import pipeline  # type: ignore
        pipe = pipeline("automatic-speech-recognition", model=model_name)

        def _t2(path: str) -> str:
            return pipe(path)["text"].strip()
        return _t2
    except ImportError:
        return None


def make_finetuned_transformers(ckpt_path: str, base_model: str) -> Callable[[str], str] | None:
    """Load the LoRA adapter from `ckpt_path` on top of `base_model` (transformers + peft).

    None if the stack or checkpoint is missing — eval then reports the fine-tuned column as
    unavailable rather than scoring the base model twice (which would hide the real delta).
    """
    if not os.path.isdir(ckpt_path):
        print(f"[eval] fine-tuned checkpoint not found: {ckpt_path} — run finetune_nebius.py first.",
              file=sys.stderr)
        return None
    try:
        import torch  # noqa: F401
        import librosa
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        from peft import PeftModel
    except ImportError as e:  # noqa: BLE001
        print(f"[eval] fine-tuned backend needs transformers+peft+librosa ({e}); skipping.",
              file=sys.stderr)
        return None

    processor = WhisperProcessor.from_pretrained(ckpt_path)
    base = WhisperForConditionalGeneration.from_pretrained(base_model)
    model = PeftModel.from_pretrained(base, ckpt_path)
    model.eval()

    def _t(path: str) -> str:
        import torch
        audio, _ = librosa.load(path, sr=16_000)
        feats = processor(audio, sampling_rate=16_000, return_tensors="pt").input_features
        with torch.no_grad():
            ids = model.generate(feats, language="en", task="transcribe")
        return processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
    return _t


def make_tokenfactory(model_name: str) -> Callable[[str], str] | None:
    """STUB — Nebius Token Factory hosted ASR.

    TODO(2026): Token Factory is OpenAI-compatible but, per
    https://docs.tokenfactory.nebius.com/api-reference/introduction , it currently exposes
    /chat/completions for TEXT LLMs and NO /audio/transcriptions (speech-to-text) endpoint. The
    moment it ships one, this becomes:

        from openai import OpenAI
        client = OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/",
                        api_key=os.environ["NEBIUS_API_KEY"])
        with open(path, "rb") as f:
            return client.audio.transcriptions.create(model=<asr-model>, file=f).text

    Until then we do NOT fabricate the call — return None so eval reports this backend unavailable.
    """
    if not os.environ.get("NEBIUS_API_KEY"):
        print("[eval] tokenfactory backend: NEBIUS_API_KEY unset.", file=sys.stderr)
    print("[eval] tokenfactory backend is a STUB — no hosted ASR/transcription endpoint as of 2026. "
          "See docs: https://docs.tokenfactory.nebius.com/api-reference/introduction", file=sys.stderr)
    return None


def get_backend(kind: str, model_or_path: str) -> Callable[[str], str] | None:
    if kind == "local":
        return make_local_whisper(model_or_path)
    if kind == "transformers":
        return make_finetuned_transformers(model_or_path, BASE_MODEL)
    if kind == "tokenfactory":
        return make_tokenfactory(model_or_path)
    print(f"[eval] unknown backend '{kind}'.", file=sys.stderr)
    return None


# --------------------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------------------
def score(rows: list[dict], transcribe: Callable[[str], str]) -> dict:
    """Transcribe every held-out clip; return WER + per-clip hyps + (optional) intent accuracy.

    Intent accuracy is a naive proxy: if a clip has a gold `intent`, we count it "correct" when the
    transcription is exact-normalized-match to the reference (a stand-in until a real intent head /
    classifier is wired). Swap `_intent_correct` for your downstream NLU when available.
    """
    pairs: list[tuple[str, str]] = []
    hyps = []
    intent_total = intent_ok = 0
    for r in rows:
        ref = r["transcript"]
        try:
            hyp = transcribe(r["audio_path"])
        except Exception as e:  # noqa: BLE001 - one bad clip shouldn't sink the eval
            hyp = ""
            print(f"[eval] transcribe failed for {r['audio_path']}: {e}", file=sys.stderr)
        pairs.append((ref, hyp))
        hyps.append({"audio_path": r["audio_path"], "ref": ref, "hyp": hyp,
                     "wer": round(wer(ref, hyp), 4)})
        if r.get("intent"):
            intent_total += 1
            intent_ok += int(normalize(ref) == normalize(hyp))  # proxy; see docstring
    out = {"n": len(rows), "wer": round(corpus_wer(pairs), 4), "hyps": hyps}
    if intent_total:
        out["intent_accuracy"] = round(intent_ok / intent_total, 4)
        out["intent_n"] = intent_total
    return out


def run(manifest: str, split: float, seed: int, baseline_backend: str, finetuned_backend: str,
        finetuned_path: str) -> dict:
    rows = load_eval_rows(manifest, split, seed)
    if not rows:
        print("[eval] nothing to score. Need a labelled (transcript) held-out split.", file=sys.stderr)
        return {"error": "no_eval_rows"}
    print(f"[eval] held-out clips: {len(rows)} (split={split}, seed={seed})")

    base_fn = get_backend(baseline_backend, BASE_MODEL)
    ft_fn = get_backend(finetuned_backend, finetuned_path)

    report: dict = {"n_eval": len(rows), "base_model": BASE_MODEL,
                    "baseline_backend": baseline_backend, "finetuned_backend": finetuned_backend}

    if base_fn:
        report["baseline"] = score(rows, base_fn)
    else:
        report["baseline"] = {"unavailable": True,
                              "hint": "install openai-whisper or transformers (pip install) to score the baseline"}
    if ft_fn:
        report["finetuned"] = score(rows, ft_fn)
    else:
        report["finetuned"] = {"unavailable": True,
                               "hint": f"need fine-tuned checkpoint at {finetuned_path} (run finetune_nebius.py)"}

    _print_headline(report)
    out_path = os.path.join(_HERE, "artifacts", "eval_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[eval] report -> {out_path}")
    return report


def _print_headline(report: dict) -> None:
    base = report.get("baseline", {})
    ft = report.get("finetuned", {})
    print("\n" + "=" * 72)
    if base.get("unavailable") or ft.get("unavailable"):
        print("RESULT: partial — could not run both models offline.")
        if not base.get("unavailable"):
            print(f"  general whisper-large-v3 WER = {base['wer']:.1%}")
        if not ft.get("unavailable"):
            print(f"  Ruby-personalized WER       = {ft['wer']:.1%}")
        print("  Install the missing backend (README > Eval) to see the delta.")
        print("=" * 72)
        return
    b, f = base["wer"], ft["wer"]
    delta = b - f
    rel = (delta / b * 100) if b else 0.0
    print(f"  General  whisper-large-v3 WER : {b:.1%}")
    print(f"  Ruby-personalized model   WER : {f:.1%}")
    print(f"  Absolute WER reduction        : {delta*100:+.1f} pts   (relative {rel:+.1f}%)")
    if "intent_accuracy" in base and "intent_accuracy" in ft:
        print(f"  Intent accuracy   base -> ft  : {base['intent_accuracy']:.1%} -> {ft['intent_accuracy']:.1%}")
    headline = (f"HEADLINE: personalization cut WER {rel:.0f}% "
                f"({b:.0%} -> {f:.0%}) — the model now understands Ruby."
                if delta > 0 else
                "HEADLINE: no improvement yet — need more labelled clips or more epochs.")
    print("\n  " + headline)
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Baseline vs fine-tuned Whisper WER on Ruby's held-out clips")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--split", type=float, default=0.2, help="held-out fraction")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--baseline-backend", default="local", choices=["local", "tokenfactory"])
    ap.add_argument("--finetuned-backend", default="transformers",
                    choices=["transformers", "local", "tokenfactory"])
    ap.add_argument("--finetuned-path", default=DEFAULT_FT_PATH, help="LoRA checkpoint dir")
    args = ap.parse_args(argv)
    run(args.manifest, args.split, args.seed, args.baseline_backend, args.finetuned_backend,
        args.finetuned_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
