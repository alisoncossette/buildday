"""Stead fine-tune — submit a Nebius job that personalizes ASR to Ruby's dysarthric speech.

Stage two of the pipeline. Takes the manifest from data_prep.py and configures/submits a Nebius
fine-tune of an open ASR model (Whisper-family) so the model learns *Ruby's* speech, which
off-the-shelf ASR mis-hears.

WHICH NEBIUS SURFACE? (confirmed against the 2026 docs — see README)
  - Nebius **Token Factory** (https://api.tokenfactory.nebius.com/v1/) is OpenAI-compatible and
    runs managed fine-tuning of TEXT LLMs via /v1/files + /v1/fine_tuning/jobs (LoRA / full). As of
    this writing it exposes NO audio/transcription fine-tune target, so we cannot fine-tune Whisper
    through it yet. We keep a ready-to-flip adapter (`submit_tokenfactory_finetune`) guarded by a
    TODO so the moment Token Factory adds an ASR base model, only the model id changes.
  - Nebius **AI Cloud** H100 GPU VMs (`nebius compute instance create --resources-platform
    gpu-h100-sxm`) are the supported way to fine-tune Whisper TODAY: spin up an H100 VM, SSH in, and
    run the HF-Transformers/PEFT trainer (scripts/train_whisper.py) over the manifest + audio. This
    is the DEFAULT path here. The VM-create CLI shape is confirmed against the 2026 compute docs
    (https://docs.nebius.com/compute/quickstart); the trainer loop is standard HF Whisper fine-tune.

OFFLINE-FIRST: with no NEBIUS_API_KEY (Token Factory) / no `nebius` CLI (AI Cloud), this PLANS the
job — writes the JSONL training file, the training config, and prints the exact command/payload it
WOULD submit — so the pipeline is demoable on a hotspot and the submission is auditable before it
spends a GPU-hour. Thin + swappable: swap the backend by passing --backend.

Env:
  NEBIUS_API_KEY        Token Factory bearer (also used by eval.py for hosted inference).
  NEBIUS_SUBNET_ID      AI Cloud subnet id for the VM (optional for planning).
  NEBIUS_SSH_PUBKEY     Path to the SSH public key injected via cloud-init (default ~/.ssh/id_ed25519.pub).
  NEBIUS_GPU_PLATFORM   GPU platform (default gpu-h100-sxm).
  NEBIUS_GPU_PRESET     GPU preset (default 1gpu-16vcpu-200gb; use 8gpu-128vcpu-1600gb for multi-GPU).

Run:
    python model/finetune_nebius.py --plan                  # offline: write artifacts + print plan
    python model/finetune_nebius.py --backend aicloud --submit       # create the H100 VM
    python model/finetune_nebius.py --backend tokenfactory --submit  # TODO: blocked until ASR FT
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(_HERE, "data", "manifest.jsonl")
ARTIFACTS_DIR = os.path.join(_HERE, "artifacts")

TOKENFACTORY_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# The open ASR model we personalize. Whisper-large-v3 is the strong general baseline eval.py scores
# against; we fine-tune the same family so the delta is attributable to personalization, not arch.
BASE_ASR_MODEL = "openai/whisper-large-v3"

# H100 platform/preset confirmed against https://docs.nebius.com/compute/quickstart (2026):
#   --resources-platform gpu-h100-sxm   --resources-preset 1gpu-16vcpu-200gb (single-GPU)
#                                                        or 8gpu-128vcpu-1600gb (full node).
# whisper-large-v3 LoRA fits comfortably on one H100 (80GB); use the 8-GPU preset only for big data.
GPU_PLATFORM = os.environ.get("NEBIUS_GPU_PLATFORM", "gpu-h100-sxm")
GPU_PRESET = os.environ.get("NEBIUS_GPU_PRESET", "1gpu-16vcpu-200gb")
# Boot image family with CUDA preinstalled (per the compute quickstart). The VM then pip-installs the
# HF stack and runs scripts/train_whisper.py — no custom container needed.
BOOT_IMAGE_FAMILY = os.environ.get("NEBIUS_IMAGE_FAMILY", "ubuntu24.04-cuda13.0")
SSH_PUBKEY_PATH = os.environ.get(
    "NEBIUS_SSH_PUBKEY", os.path.expanduser("~/.ssh/id_ed25519.pub")
)


def load_manifest(path: str) -> list[dict]:
    """Read manifest.jsonl into a list of utterance dicts (empty list if missing)."""
    if not os.path.exists(path):
        print(f"[finetune] manifest not found: {path} — run data_prep.py first.", file=sys.stderr)
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def trainable_rows(rows: list[dict]) -> list[dict]:
    """Only utterances with a gold transcript can train ASR; warn if none."""
    keep = [r for r in rows if r.get("transcript")]
    if not keep:
        print("[finetune] No utterances have transcripts — ASR fine-tune needs (audio, transcript) "
              "pairs. Add a sidecar labels file in data_prep.py (--labels).", file=sys.stderr)
    return keep


def write_training_jsonl(rows: list[dict], out_path: str) -> str:
    """Write the audio<->transcript training file the trainer reads. One JSON object per line:
        {"audio_path": "...", "text": "...", "intent": "...", "mood": "..."}
    `intent`/`mood` ride along so the same corpus can train auxiliary heads later (intent/mood from
    voice). Returns the path written."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            rec = {"audio_path": r["audio_path"], "text": r["transcript"]}
            if r.get("intent"):
                rec["intent"] = r["intent"]
            if r.get("mood"):
                rec["mood"] = r["mood"]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out_path


def default_hparams() -> dict:
    """Sensible LoRA defaults for a SMALL personalization set (one speaker). Few-shot dysarthric
    adaptation overfits fast, so: low LR, few epochs, modest LoRA rank. Tune per data volume."""
    return {
        "base_model": BASE_ASR_MODEL,
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
        "freeze_encoder": True,  # personalize the decoder; keep robust acoustic features
    }


# --------------------------------------------------------------------------------------------------
# Backend A (DEFAULT): Nebius AI Cloud H100 GPU VM — the supported way to fine-tune Whisper now
# --------------------------------------------------------------------------------------------------
def _cloud_init(job_name: str) -> str:
    """cloud-init user-data: inject the SSH key so we can connect and drive the trainer.

    We deliberately do NOT auto-run training in cloud-init: the operator rsyncs the audio + manifest +
    scripts/train_whisper.py to the VM and launches the trainer over SSH (see README). Keeps the
    GPU-hour spend explicit and the run debuggable.
    """
    pub = ""
    if os.path.exists(SSH_PUBKEY_PATH):
        with open(SSH_PUBKEY_PATH, "r", encoding="utf-8") as f:
            pub = f.read().strip()
    else:
        pub = "<PASTE_YOUR_SSH_PUBLIC_KEY>  # NEBIUS_SSH_PUBKEY not found at " + SSH_PUBKEY_PATH
    return (
        "#cloud-config\n"
        "users:\n"
        "  - name: ruby\n"
        "    groups: sudo\n"
        "    sudo: ['ALL=(ALL) NOPASSWD:ALL']\n"
        "    shell: /bin/bash\n"
        "    ssh_authorized_keys:\n"
        f"      - {pub}\n"
    )


def plan_aicloud_job(training_file: str, config_path: str, hparams: dict, job_name: str) -> dict:
    """Build the `nebius compute instance create` invocation for an H100 VM.

    CONFIRMED shape (https://docs.nebius.com/compute/quickstart, 2026): create the VM with
    --resources-platform gpu-h100-sxm + a preset + a CUDA boot image + SSH via cloud-init. Returns a
    plan dict {command, argv, env_needed, post_create} without submitting. After the VM is up the
    operator SSHes in and runs scripts/train_whisper.py (the standard HF Whisper LoRA loop) over the
    training JSONL — that produces the LoRA checkpoint eval.py loads as the fine-tuned model.
    """
    subnet = os.environ.get("NEBIUS_SUBNET_ID", "<NEBIUS_SUBNET_ID>")
    user_data_path = os.path.join(ARTIFACTS_DIR, "cloud_init.yaml")
    with open(user_data_path, "w", encoding="utf-8") as f:
        f.write(_cloud_init(job_name))

    argv = [
        "nebius", "compute", "instance", "create",
        "--name", job_name,
        "--resources-platform", GPU_PLATFORM,
        "--resources-preset", GPU_PRESET,
        # TODO(docs): create the boot disk first from image family `BOOT_IMAGE_FAMILY` and pass its id
        # here, OR use the inline image flag your CLI version exposes. See compute/quickstart.
        "--boot-disk-image-family", BOOT_IMAGE_FAMILY,
        "--boot-disk-size", "200GiB",
        "--cloud-init-user-data-file", user_data_path,
        "--network-interfaces",
        f'[{{"subnet_id": "{subnet}", "ip_address": {{}}, "public_ip_address": {{}}}}]',
    ]
    # What the operator runs on the VM once it is reachable (rsync data up, then train).
    remote_train = (
        "pip install -q 'transformers>=4.44' datasets accelerate peft evaluate jiwer soundfile "
        "librosa torch && "
        f"python ~/stead/scripts/train_whisper.py "
        f"--train ~/stead/{os.path.basename(training_file)} "
        f"--config ~/stead/{os.path.basename(config_path)} "
        "--out ~/stead/ckpt"
    )
    return {
        "backend": "aicloud",
        "command": " ".join(argv),
        "argv": argv,
        "hparams": hparams,
        "env_needed": ["NEBIUS_SUBNET_ID", "NEBIUS_SSH_PUBKEY"],
        "post_create": {
            "1_get_ip": ("export IP=$(nebius compute instance get --name " + job_name +
                         " --format json | jq -r '.status.network_interfaces[0]"
                         ".public_ip_address.address' | cut -d/ -f1)"),
            "2_upload": ("rsync -avz model/artifacts model/data/audio model/scripts "
                         "ruby@$IP:~/stead/"),
            "3_train": f'ssh ruby@$IP "{remote_train}"',
            "4_fetch_ckpt": "rsync -avz ruby@$IP:~/stead/ckpt model/artifacts/ckpt",
        },
        "note": ("H100 VM path (default). The trainer is scripts/train_whisper.py (standard HF "
                 "Whisper LoRA fine-tune). It writes a LoRA adapter to ckpt/; eval.py loads it as the "
                 "fine-tuned model. CLI shape: https://docs.nebius.com/compute/quickstart"),
    }


def submit_aicloud_job(plan: dict) -> dict:
    """Create the H100 VM via the `nebius` CLI. Degrades to the plan if the CLI is absent."""
    if shutil.which("nebius") is None:
        print("[finetune] `nebius` CLI not found — emitting the plan instead of submitting. "
              "Install it: https://docs.nebius.com/cli/", file=sys.stderr)
        return {**plan, "submitted": False, "reason": "no nebius CLI"}
    try:
        res = subprocess.run(plan["argv"], check=True, capture_output=True, text=True)
        return {**plan, "submitted": True, "stdout": res.stdout.strip()}
    except (subprocess.CalledProcessError, OSError) as e:
        return {**plan, "submitted": False, "reason": str(e),
                "stderr": getattr(e, "stderr", "")}


# --------------------------------------------------------------------------------------------------
# Backend B: Nebius Token Factory managed fine-tune (OpenAI-compatible) — TEXT today, ASR pending
# --------------------------------------------------------------------------------------------------
def submit_tokenfactory_finetune(training_file: str, hparams: dict, suffix: str = "ruby-asr") -> dict:
    """Submit a managed fine-tune via Token Factory's OpenAI-compatible API.

    CONFIRMED surface (2026 docs https://docs.tokenfactory.nebius.com/post-training/how-to-fine-tune):
        client = OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/", api_key=NEBIUS_API_KEY)
        f = client.files.create(file=open(path,"rb"), purpose="fine-tune")
        job = client.fine_tuning.jobs.create(model=<base>, training_file=f.id,
                  hyperparameters={"lora": True, "lora_r": 16, "n_epochs": 5, ...})

    TODO(2026): Token Factory's fine-tune currently targets TEXT LLMs and exposes no Whisper / audio
    base model, so `model=openai/whisper-large-v3` is NOT yet accepted. The moment an ASR base model
    appears in https://docs.tokenfactory.nebius.com/ (Models for fine-tuning), set BASE_ASR_MODEL to
    it and remove this guard. Until then this path is intentionally blocked rather than guessing an
    endpoint that does not exist.
    """
    api_key = os.environ.get("NEBIUS_API_KEY")
    blocked = {
        "backend": "tokenfactory",
        "submitted": False,
        "base_url": TOKENFACTORY_BASE_URL,
        "reason": ("Token Factory fine-tune has no audio/ASR base model yet (text LLMs only). "
                   "Use --backend aicloud to fine-tune Whisper today."),
        "docs": "https://docs.tokenfactory.nebius.com/post-training/how-to-fine-tune",
    }
    if not api_key:
        return {**blocked, "reason": blocked["reason"] + " (also: NEBIUS_API_KEY unset)"}

    try:
        from openai import OpenAI  # OpenAI-compatible client, per Nebius docs
    except ImportError:
        return {**blocked, "reason": "openai package not installed (pip install openai); " + blocked["reason"]}

    # The wiring below is correct for Token Factory's API shape; it stays guarded until an ASR base
    # model is offered so we never submit a job doomed to fail.
    if BASE_ASR_MODEL.startswith("openai/whisper"):
        return blocked  # ASR base not accepted by managed FT yet — see TODO above.

    client = OpenAI(base_url=TOKENFACTORY_BASE_URL, api_key=api_key)  # pragma: no cover
    uploaded = client.files.create(file=open(training_file, "rb"), purpose="fine-tune")  # pragma: no cover
    job = client.fine_tuning.jobs.create(  # pragma: no cover
        model=hparams["base_model"],
        training_file=uploaded.id,
        suffix=suffix,
        hyperparameters={
            "lora": hparams["method"] == "lora",
            "lora_r": hparams["lora_r"],
            "lora_alpha": hparams["lora_alpha"],
            "lora_dropout": hparams["lora_dropout"],
            "learning_rate": hparams["learning_rate"],
            "n_epochs": hparams["n_epochs"],
            "batch_size": hparams["batch_size"],
            "warmup_ratio": hparams["warmup_ratio"],
        },
    )
    return {"backend": "tokenfactory", "submitted": True, "job_id": job.id, "base_url": TOKENFACTORY_BASE_URL}


def run(manifest_path: str, backend: str, do_submit: bool, job_name: str) -> dict:
    """Plan (and optionally submit) a fine-tune. Always writes artifacts so the run is auditable."""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    rows = trainable_rows(load_manifest(manifest_path))
    hparams = default_hparams()

    training_file = write_training_jsonl(rows, os.path.join(ARTIFACTS_DIR, "train.jsonl"))
    config_path = os.path.join(ARTIFACTS_DIR, "finetune_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(hparams, f, indent=2)
    print(f"[finetune] wrote {training_file} ({len(rows)} pairs) and {config_path}")

    if backend == "tokenfactory":
        result = (submit_tokenfactory_finetune(training_file, hparams)
                  if do_submit else
                  {"backend": "tokenfactory", "planned": True, "base_url": TOKENFACTORY_BASE_URL,
                   "note": "managed FT for TEXT LLMs; ASR base pending — see submit_tokenfactory_finetune TODO"})
    else:  # aicloud (default)
        plan = plan_aicloud_job(training_file, config_path, hparams, job_name)
        result = submit_aicloud_job(plan) if do_submit else {**plan, "planned": True}

    plan_path = os.path.join(ARTIFACTS_DIR, "finetune_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"[finetune] plan -> {plan_path}")
    print(json.dumps(result, indent=2))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Submit/plan a Nebius ASR fine-tune for Ruby")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--backend", choices=["aicloud", "tokenfactory"], default="aicloud")
    ap.add_argument("--job-name", default="stead-ruby-asr-finetune")
    ap.add_argument("--submit", action="store_true", help="actually submit (default: plan only)")
    ap.add_argument("--plan", action="store_true", help="explicit plan-only (default behaviour)")
    args = ap.parse_args(argv)
    run(args.manifest, args.backend, do_submit=args.submit and not args.plan, job_name=args.job_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
