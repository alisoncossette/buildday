"""Stead trainer — the standard HuggingFace Whisper LoRA fine-tune loop, run on a Nebius H100 VM.

This is the script `finetune_nebius.py` ships to the GPU VM and launches over SSH. It personalizes
whisper-large-v3 to Ruby's dysarthric speech from (audio, text) pairs.

It is a thin, conventional HF + PEFT pipeline so it is auditable and easy to swap:
  load manifest -> Whisper processor -> log-mel features + tokenized labels -> LoRA decoder fine-tune
  -> save adapter to --out (eval.py loads it as the fine-tuned model).

Why LoRA + frozen encoder for ONE dysarthric speaker:
  The corpus is tiny (minutes-to-hours of one voice). Full fine-tuning overfits and is expensive;
  LoRA on the decoder (keeping the robust acoustic encoder frozen) is the Project-Euphonia-style
  recipe — adapt the language/output side to the speaker's patterns while keeping general acoustics.

This file is meant to run ON the GPU VM (where torch + CUDA exist), NOT on the laptop. It imports the
heavy ML stack at call time so the rest of the repo (data_prep, eval planning) stays importable
without torch installed.

Run (on the VM):
    python train_whisper.py --train train.jsonl --config finetune_config.json --out ckpt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any


def _load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_config(path: str | None) -> dict:
    cfg = {
        "base_model": "openai/whisper-large-v3",
        "method": "lora",
        "lora_r": 16,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "learning_rate": 1e-5,
        "n_epochs": 5,
        "batch_size": 8,
        "warmup_ratio": 0.05,
        "language": "en",
        "task": "transcribe",
        "freeze_encoder": True,
    }
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


@dataclass
class _Collator:
    """Pads Whisper input features and label ids into a batch (the canonical HF Whisper collator)."""

    processor: Any
    decoder_start_token_id: int

    def __call__(self, features: list[dict]) -> dict:
        import torch  # local import: VM-only dependency

        input_features = [{"input_features": f["input_features"]} for f in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")
        label_features = [{"input_ids": f["labels"]} for f in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(
            labels_batch.attention_mask.ne(1), -100
        )
        # Strip the leading decoder-start token if the tokenizer prepended it.
        if (labels[:, 0] == self.decoder_start_token_id).all().cpu().item():
            labels = labels[:, 1:]
        batch["labels"] = labels
        return batch


def train(train_path: str, config_path: str | None, out_dir: str) -> str:
    """Run the LoRA fine-tune and save the adapter (+ processor) to `out_dir`. Returns `out_dir`.

    Heavy imports are local so `--help` and import of this module never require torch.
    """
    # ---- VM-only ML stack. If missing, fail with the exact install line (do not pretend to train).
    try:
        import torch
        import librosa
        from datasets import Dataset, Audio
        from transformers import (
            WhisperForConditionalGeneration,
            WhisperProcessor,
            Seq2SeqTrainer,
            Seq2SeqTrainingArguments,
        )
        from peft import LoraConfig, get_peft_model
    except ImportError as e:  # noqa: BLE001
        print(
            "[train_whisper] missing ML dependency: %s\n"
            "Install on the GPU VM:\n"
            "  pip install 'transformers>=4.44' datasets accelerate peft evaluate jiwer "
            "soundfile librosa torch" % e,
            file=sys.stderr,
        )
        return ""

    cfg = _load_config(config_path)
    rows = _load_jsonl(train_path)
    rows = [r for r in rows if r.get("text") and r.get("audio_path")]
    if not rows:
        print("[train_whisper] FATAL: no (audio_path, text) pairs in training file.", file=sys.stderr)
        return ""
    print(f"[train_whisper] {len(rows)} training pairs | base={cfg['base_model']} method={cfg['method']}")

    processor = WhisperProcessor.from_pretrained(
        cfg["base_model"], language=cfg["language"], task=cfg["task"]
    )

    def _prepare(batch: dict) -> dict:
        audio = batch["audio"]
        batch["input_features"] = processor.feature_extractor(
            audio["array"], sampling_rate=audio["sampling_rate"]
        ).input_features[0]
        batch["labels"] = processor.tokenizer(batch["text"]).input_ids
        return batch

    ds = Dataset.from_list([{"audio_path": r["audio_path"], "text": r["text"]} for r in rows])
    ds = ds.rename_column("audio_path", "audio").cast_column("audio", Audio(sampling_rate=16_000))
    ds = ds.map(_prepare, remove_columns=ds.column_names)

    model = WhisperForConditionalGeneration.from_pretrained(cfg["base_model"])
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []
    if cfg.get("freeze_encoder"):
        model.freeze_encoder()  # personalize the decoder; keep robust acoustic features

    if cfg["method"] == "lora":
        lora = LoraConfig(
            r=cfg["lora_r"],
            lora_alpha=cfg["lora_alpha"],
            lora_dropout=cfg["lora_dropout"],
            target_modules=["q_proj", "v_proj"],  # attention projections (standard Whisper LoRA)
            bias="none",
        )
        model = get_peft_model(model, lora)
        model.print_trainable_parameters()

    collator = _Collator(processor=processor, decoder_start_token_id=model.config.decoder_start_token_id)

    args = Seq2SeqTrainingArguments(
        output_dir=out_dir,
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=1,
        learning_rate=cfg["learning_rate"],
        warmup_ratio=cfg["warmup_ratio"],
        num_train_epochs=cfg["n_epochs"],
        fp16=torch.cuda.is_available(),
        logging_steps=10,
        save_strategy="epoch",
        report_to=[],  # wire Langfuse/W&B here if desired
        remove_unused_columns=False,  # LoRA + custom features need this off
        label_names=["labels"],
    )
    trainer = Seq2SeqTrainer(
        model=model,
        args=args,
        train_dataset=ds,
        data_collator=collator,
        tokenizer=processor.feature_extractor,
    )
    trainer.train()

    os.makedirs(out_dir, exist_ok=True)
    model.save_pretrained(out_dir)        # LoRA adapter (or full model)
    processor.save_pretrained(out_dir)
    with open(os.path.join(out_dir, "stead_train_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"base_model": cfg["base_model"], "method": cfg["method"], "n_pairs": len(rows)}, f)
    print(f"[train_whisper] saved adapter + processor -> {out_dir}")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HF Whisper LoRA fine-tune (run on a Nebius H100 VM)")
    ap.add_argument("--train", required=True, help="training JSONL of {audio_path, text}")
    ap.add_argument("--config", default=None, help="finetune_config.json from finetune_nebius.py")
    ap.add_argument("--out", default="ckpt", help="output dir for the adapter + processor")
    args = ap.parse_args(argv)
    ok = train(args.train, args.config, args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
