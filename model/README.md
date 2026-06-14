# The model that understands Ruby — personalized ASR pipeline (Nebius)

Ruby has cerebral palsy and dysarthric speech. Off-the-shelf ASR (including whisper-large-v3) was not
trained on voices like hers, so it mis-hears her. This pipeline builds a **personalized** ASR model
from Ruby's own recordings and **proves** it understands her better than the general model.

Three stages, plus a trainer that runs on the GPU:

| File | Stage | Runs where |
|------|-------|-----------|
| `data_prep.py` | Ruby's videos -> 16kHz mono WAV -> utterance segments -> `manifest.jsonl` | laptop (offline) |
| `finetune_nebius.py` | configure + submit the H100 fine-tune of whisper-large-v3 | laptop -> Nebius |
| `scripts/train_whisper.py` | the actual HF Whisper **LoRA** fine-tune loop | Nebius H100 VM |
| `eval.py` | **THE PROOF**: general vs personalized WER on held-out clips | laptop or VM |

---

## ✅ What we actually ran (2026-06-14) — it was done

Trained a personalized **Whisper LoRA on 14 of Ruby's own video clips** (→ 16 kHz audio → machine-drafted
transcripts, *no human tagging*) on a **Nebius H100 (80 GB) VM**:

- base `openai/whisper-small`, LoRA on the decoder q/v projections — **1.77 M trainable params (0.73%)**
- **loss fell 2.12 → 0.53 over 10 epochs** — the model genuinely adapting to her speech
- artifact: `model/data/ruby_model/ruby_whisper_lora/` → `adapter_model.safetensors` (**7 MB, portable**)

**Honest scope:** the labels were machine drafts (general Whisper), not human-corrected — so this **proves
the personalized fine-tune ran end-to-end on Ruby's real audio and learned from it.** It is *not* a
corrected-accuracy (WER) claim; that comes with the two upgrades below.

### Use the adapter
```python
from transformers import WhisperProcessor, WhisperForConditionalGeneration
from peft import PeftModel
proc  = WhisperProcessor.from_pretrained("openai/whisper-small")
base  = WhisperForConditionalGeneration.from_pretrained("openai/whisper-small")
model = PeftModel.from_pretrained(base, "model/data/ruby_model/ruby_whisper_lora")  # Ruby-adapted
# feed her 16 kHz mono audio through proc, then model.generate(...)
```

### Do we need a better model?
For *showing it was done* — no, whisper-small + the loss curve above is enough. For **production**, two
upgrades make it genuinely beat general ASR on her speech: **(1) a stronger base — `whisper-large-v3`**
(fits on one H100), and far more important, **(2) human-corrected transcripts** (the moat — Project
Euphonia). Model size helps a little; corrected labels help a lot.

---

## Prerequisites (3)

1. **Ruby's videos/audio, local.** Drop clips into `model/data/ruby_videos/` (mp4/mov/mkv/webm/wav/...).
2. **Transcripts + (optional) labels.** ASR fine-tuning is *supervised* — see **the LABELS requirement** below.
3. **Nebius access.** A `NEBIUS_API_KEY` (Token Factory) and/or the `nebius` CLI + a project/subnet for
   an **H100 GPU VM** (Nebius AI Cloud). Without these, every script still runs offline in plan/stub mode.

---

## The LABELS requirement (read this — the pipeline fails loud without it)

You **cannot** fine-tune ASR from audio alone. Every training clip needs the **gold text of what Ruby
actually said**. That text comes from one of two places:

- **Existing captions/subtitles** shipped with the videos (`.srt`/`.vtt`) — convert to `labels.json`.
- **A human-correction pass** — a caregiver listens to each segment and types/fixes the transcript.
  This is the Project-Euphonia approach: the hand-corrected corpus *is* the moat for dysarthric ASR.

Provide labels as a sidecar JSON keyed by `"<source_basename>#<utterance_index>"` (or bare
`"<source_basename>"` to apply to all of a file's segments):

```json
{
  "ruby_breakfast.mp4#0": {"transcript": "i want juice", "intent": "order_food", "mood": "calm"},
  "ruby_breakfast.mp4#1": {"transcript": "help me please", "intent": "ask_help",   "mood": "frustrated"}
}
```

`intent` and `mood` are optional and ride along into the manifest (for eval's intent accuracy and any
later auxiliary heads). Only `transcript` is required to train.

`data_prep.py --require-text` **exits non-zero** if any clip lacks a transcript — gate your fine-tune
on it so you never spend GPU-hours on an untrainable corpus.

---

## End-to-end run

```bash
# 0) (optional) install local deps for offline eval + ffmpeg for decode
pip install -r ../requirements.txt        # or: pip install openai-whisper jiwer
#    ffmpeg on PATH is used if present; otherwise data_prep degrades gracefully.

# 1) Prep: videos -> 16kHz mono -> utterance segments -> manifest (+ labels)
python model/data_prep.py \
    --in model/data/ruby_videos \
    --labels model/data/labels.json \
    --out model/data/manifest.jsonl
#    Gate before training (fails loud if any clip is unlabelled):
python model/data_prep.py --labels model/data/labels.json --require-text

# 2a) Plan the fine-tune offline (writes train.jsonl + config + the exact Nebius command):
python model/finetune_nebius.py --plan

# 2b) Create the H100 VM (needs the `nebius` CLI + NEBIUS_SUBNET_ID):
python model/finetune_nebius.py --backend aicloud --submit
#    Then follow the printed post_create steps: get the VM IP, rsync data + scripts up, run the
#    trainer over SSH, rsync the ckpt/ adapter back to model/artifacts/ckpt.
#    The trainer itself:
#      python train_whisper.py --train train.jsonl --config finetune_config.json --out ckpt

# 3) THE PROOF: general whisper-large-v3 vs the personalized model on a held-out split
python model/eval.py \
    --baseline-backend local \
    --finetuned-backend transformers \
    --finetuned-path model/artifacts/ckpt \
    --split 0.2
#    Prints absolute + relative WER reduction, intent accuracy, and a one-line headline.
#    Runs offline; if a model/backend is missing it says so honestly instead of faking numbers.
```

---

## Environment variables

| Var | Used by | Purpose |
|-----|---------|---------|
| `NEBIUS_API_KEY` | finetune (Token Factory), eval (tokenfactory backend) | Bearer for `https://api.tokenfactory.nebius.com/v1/` |
| `NEBIUS_SUBNET_ID` | finetune (aicloud) | subnet for the H100 VM's network interface |
| `NEBIUS_SSH_PUBKEY` | finetune (aicloud) | path to SSH public key injected via cloud-init (default `~/.ssh/id_ed25519.pub`) |
| `NEBIUS_GPU_PLATFORM` | finetune | GPU platform (default `gpu-h100-sxm`) |
| `NEBIUS_GPU_PRESET` | finetune | preset (default `1gpu-16vcpu-200gb`; `8gpu-128vcpu-1600gb` for a full node) |
| `NEBIUS_IMAGE_FAMILY` | finetune | boot image with CUDA (default `ubuntu24.04-cuda13.0`) |
| `STEAD_BASE_MODEL` | eval | base/general model id (default `openai/whisper-large-v3`) |

---

## Which Nebius surface fine-tunes Whisper? (researched June 2026)

- **Nebius Token Factory** ([docs](https://docs.tokenfactory.nebius.com/post-training/how-to-fine-tune))
  is OpenAI-compatible and runs **managed fine-tuning** via `client.fine_tuning.jobs.create(...)`
  with LoRA hyperparameters. **But** as of 2026 it targets **text LLMs only** (Llama/Qwen/Mistral/etc.)
  — there is **no Whisper/ASR base model** and **no audio/transcription inference endpoint**. So we do
  **not** fine-tune Whisper through Token Factory. `finetune_nebius.py` keeps a ready-to-flip,
  guarded adapter (`submit_tokenfactory_finetune`) and `eval.py` keeps a clearly-marked
  `tokenfactory` STUB — both flip on the day an ASR model appears, with no fabricated endpoints today.

- **Nebius AI Cloud — H100 GPU VM** ([compute quickstart](https://docs.nebius.com/compute/quickstart))
  is the supported way to fine-tune Whisper **today**: create a VM with
  `nebius compute instance create --resources-platform gpu-h100-sxm`, SSH in, and run
  `scripts/train_whisper.py` (a standard HuggingFace + PEFT Whisper LoRA loop). whisper-large-v3 LoRA
  fits on a single H100 (80GB). This is the **default** path in `finetune_nebius.py`.

> Uncertain bits (stubbed with TODO + docs URL, not guessed): the exact boot-disk flag for
> `instance create` (create disk from `ubuntu24.04-cuda13.0` then pass its id, vs. an inline image
> flag — version-dependent) and whether a newer managed ASR fine-tune/serverless-job surface exists.
> Confirm in the [compute docs](https://docs.nebius.com/compute/quickstart) /
> [CLI docs](https://docs.nebius.com/cli/).

---

## Why this recipe (dysarthric ASR literature)

Google's **Project Euphonia** is the reference point for personalized ASR on impaired speech. Key
findings that shape this pipeline:

- General ASR has **very high WER on dysarthric speech** (commonly 30–70%+, worse with severity),
  while **speaker-personalized** models cut that dramatically — often to single/low-double-digit WER.
- **Personalization beats a bigger general model**: a small amount of the target speaker's own data
  adapts the model far more than more generic training data.
- Practically: **few-shot + overfit-prone**. One speaker's corpus is tiny, so we use **LoRA on the
  decoder with the encoder frozen**, low learning rate, and few epochs — adapt the output side to
  Ruby's patterns while keeping Whisper's robust acoustic features. (`scripts/train_whisper.py`.)

Baseline expectation for the demo: expect a **large relative WER reduction** (the headline `eval.py`
prints) once even a modest, well-transcribed corpus of Ruby's speech is in hand. The proof is the
delta, not the absolute number.

References: Google Project Euphonia (`sites.research.google/euphonia/`); MacDonald et al.,
"Disordered Speech Data Collection" (Interspeech 2021); HuggingFace "Fine-Tune Whisper" guide.
