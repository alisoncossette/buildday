"""Ruby's personalized speech-to-text — loads her sovereign Whisper LoRA adapter and transcribes HER voice.

This is the inference half of the sovereign stack: train on Nebius (GPU), serve here (a GPU host / the
DGX Spark). The lightweight web app calls `transcribe()` as its STT when the model is present; otherwise
it returns None and the caller falls back to general STT — so the app never hard-depends on torch.

PRIVACY / SOVEREIGNTY: the adapter WEIGHTS are NOT committed to the public repo — they're Ruby's model,
trained on her private voice. Place them at  model/data/ruby_model/ruby_whisper_lora/  (gitignored) or
point RUBY_LORA_PATH at wherever they live (e.g. on the DGX Spark).
"""
import os

_BASE = os.environ.get("RUBY_STT_BASE", "openai/whisper-small")
_LORA = os.environ.get(
    "RUBY_LORA_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "model", "data", "ruby_model", "ruby_whisper_lora"),
)
_model = None
_proc = None


def available() -> bool:
    """True iff Ruby's adapter is present on this machine (so the app can choose her STT vs. general)."""
    return os.path.isdir(_LORA)


def _load():
    global _model, _proc
    if _model is not None:
        return
    import torch
    from transformers import WhisperProcessor, WhisperForConditionalGeneration
    from peft import PeftModel
    _proc = WhisperProcessor.from_pretrained(_BASE, language="en", task="transcribe")
    base = WhisperForConditionalGeneration.from_pretrained(_BASE)
    _model = PeftModel.from_pretrained(base, _LORA)
    _model.eval()
    if torch.cuda.is_available():
        _model.to("cuda")


def transcribe(audio, sr: int = 16000):
    """Transcribe Ruby's 16 kHz mono audio (float array in [-1, 1]) with HER fine-tuned model.

    Returns the transcript string, or None if the model / libraries aren't available on this host
    (the caller then falls back to a general ASR). Never raises.
    """
    if not available():
        return None
    try:
        import torch
        _load()
        feats = _proc(audio, sampling_rate=sr, return_tensors="pt").input_features
        if torch.cuda.is_available():
            feats = feats.to("cuda")
        with torch.no_grad():
            ids = _model.generate(feats, language="en", task="transcribe", max_new_tokens=200)
        return _proc.batch_decode(ids, skip_special_tokens=True)[0].strip()
    except Exception:
        return None


if __name__ == "__main__":
    print("Ruby's model present:", available(), "(", _LORA, ")")
