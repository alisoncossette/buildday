"""Fine-tune Whisper (LoRA) on Ruby's speech — runs on the Nebius GPU VM.

Self-contained: reads manifest_vm.jsonl ({audio_path, text}) + the WAVs in ./audio/, fine-tunes a
LoRA adapter on openai/whisper-small, saves the adapter, and prints BEFORE/AFTER transcriptions so you
can SEE the model adapt to her speech. Uses scipy for WAV (no libsndfile system dep).

Note: labels here are machine DRAFTS (no human correction), so this PROVES the personalized fine-tune
ran end-to-end on Ruby's real audio — it is not a corrected-accuracy claim.
"""
import json
import os

import numpy as np
import torch
from scipy.io import wavfile
from transformers import WhisperProcessor, WhisperForConditionalGeneration
from peft import LoraConfig, get_peft_model

BASE = os.environ.get("BASE_MODEL", "openai/whisper-small")
EPOCHS = int(os.environ.get("EPOCHS", "10"))
DEV = "cuda" if torch.cuda.is_available() else "cpu"
print(f"device={DEV}  base={BASE}  epochs={EPOCHS}", flush=True)

data = [json.loads(l) for l in open("manifest_vm.jsonl", encoding="utf-8") if l.strip()]
print(f"clips={len(data)}", flush=True)


def load_wav(path):
    sr, a = wavfile.read(path)
    a = a.astype(np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if a.dtype != np.float32 or a.max() > 1.5:
        a = a / 32768.0
    return a, sr


proc = WhisperProcessor.from_pretrained(BASE, language="en", task="transcribe")
model = WhisperForConditionalGeneration.from_pretrained(BASE).to(DEV)


def transcribe(path):
    a, _ = load_wav(path)
    feats = proc(a, sampling_rate=16000, return_tensors="pt").input_features.to(DEV)
    with torch.no_grad():
        ids = model.generate(feats, language="en", task="transcribe", max_new_tokens=180)
    return proc.batch_decode(ids, skip_special_tokens=True)[0].strip()


print("\n=== BEFORE (general whisper-small) ===", flush=True)
before = {}
for ex in data[:3]:
    before[ex["audio_path"]] = transcribe(ex["audio_path"])
    print(" ", os.path.basename(ex["audio_path"]), "->", before[ex["audio_path"]][:100], flush=True)

lora = LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj", "v_proj"], lora_dropout=0.05, bias="none")
model = get_peft_model(model, lora)
model.print_trainable_parameters()
opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-4)

model.train()
for ep in range(EPOCHS):
    tot, nb = 0.0, 0
    for ex in data:
        a, _ = load_wav(ex["audio_path"])
        feats = proc(a, sampling_rate=16000, return_tensors="pt").input_features.to(DEV)
        labels = proc.tokenizer(ex["text"], return_tensors="pt").input_ids.to(DEV)
        out = model(input_features=feats, labels=labels)
        out.loss.backward()
        opt.step()
        opt.zero_grad()
        tot += float(out.loss.item())
        nb += 1
    print(f"epoch {ep+1}/{EPOCHS}  loss {tot/max(nb,1):.4f}", flush=True)

model.save_pretrained("ruby_whisper_lora")
model.eval()

print("\n=== AFTER (Ruby-adapted LoRA) ===", flush=True)
samples = []
for ex in data[:3]:
    aft = transcribe(ex["audio_path"])
    print(" ", os.path.basename(ex["audio_path"]), "->", aft[:100], flush=True)
    samples.append({"clip": os.path.basename(ex["audio_path"]),
                    "before": before.get(ex["audio_path"], ""), "after": aft})

json.dump({"base": BASE, "epochs": EPOCHS, "clips": len(data), "samples": samples},
          open("ruby_finetune_result.json", "w"), indent=2)
print("\nDONE — fine-tuned a Whisper LoRA on Ruby's real speech.")
print("artifacts: ruby_whisper_lora/  +  ruby_finetune_result.json", flush=True)
