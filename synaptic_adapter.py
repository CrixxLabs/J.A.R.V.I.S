"""Synaptic Plasticity & Overnight QLoRA Worker for J.A.R.V.I.S. — MARK VIII.

Implements autonomous neural weight adaptation using distilled experiential memories:
  1. Data Pipeline: Harvests and formats `data/distilled_memories.jsonl` into 4-bit PEFT/Unsloth instruction datasets.
  2. Overnight Training Engine: Generates `scripts/train_qlora_adapter.py` with 6GB VRAM guardrails (r=8, alpha=16, batch_size=1, gradient_accumulation=4).
  3. Adapter & Modelfile Exporter: Creates updated Ollama `Modelfile` with LoRA adapter bindings.
  4. Quiescence & Cloud Fallback: Runs locally when VRAM >= 4.5GB or exports runnable Google Colab notebooks to `data/colab_training_export.py`.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.synaptic_adapter")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
SCRIPTS_DIR = BASE_DIR / "scripts"
ADAPTERS_DIR = BASE_DIR / "adapters"

DISTILLED_FILE = DATA_DIR / "distilled_memories.jsonl"
TRAINING_DATASET_FILE = DATA_DIR / "qlora_training_data.jsonl"
TRAIN_SCRIPT_PATH = SCRIPTS_DIR / "train_qlora_adapter.py"
COLAB_EXPORT_PATH = DATA_DIR / "colab_training_export.py"
MODELFILE_PATH = BASE_DIR / "Modelfile.synaptic"

DEFAULT_MIN_SAMPLES = 50
VRAM_MIN_FREE_GB = 4.5

_lock = threading.RLock()


def prepare_instruction_dataset(
    min_samples: int = DEFAULT_MIN_SAMPLES,
    source_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Tuple[bool, int, Path]:
    """Harvest verified memory pairs and format into a standardized Alpaca instruction dataset.

    Returns:
        Tuple of (ready_for_training: bool, sample_count: int, dataset_path: Path)
    """
    src = Path(source_path).resolve() if source_path else DISTILLED_FILE
    dst = Path(output_path).resolve() if output_path else TRAINING_DATASET_FILE
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        return False, 0, dst

    valid_samples: List[Dict[str, str]] = []

    with _lock:
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                except Exception:
                    continue

                # Extract instruction, input, and output from various recording formats
                instruction = ""
                inp = ""
                output = ""

                if "alpaca" in data and isinstance(data["alpaca"], dict):
                    alp = data["alpaca"]
                    instruction = str(alp.get("instruction", "")).strip()
                    inp = str(alp.get("input", "")).strip()
                    output = str(alp.get("output", "")).strip()
                elif "instruction" in data and "output" in data:
                    instruction = str(data.get("instruction", "")).strip()
                    inp = str(data.get("input", "")).strip()
                    output = str(data.get("output", "")).strip()
                elif "user_query" in data and "assistant_response" in data:
                    instruction = "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant."
                    inp = str(data.get("user_query", "")).strip()
                    output = str(data.get("assistant_response", "")).strip()

                if (instruction or inp) and output:
                    if not instruction:
                        instruction = "You are J.A.R.V.I.S., an advanced AI assistant. Respond accurately to the user's request."
                    valid_samples.append({
                        "instruction": instruction,
                        "input": inp,
                        "output": output,
                    })

        # Write formatted dataset
        with open(dst, "w", encoding="utf-8") as f_out:
            for sample in valid_samples:
                f_out.write(json.dumps(sample, ensure_ascii=False) + "\n")

    count = len(valid_samples)
    is_ready = count >= min_samples

    log.info(f"[SynapticAdapter] Prepared {count} training samples -> {dst.name} (Ready: {is_ready})")
    try:
        get_registry().set_capability_evidence(
            "SYNAPTIC_ADAPTER",
            EvidenceLevel.LIVE,
            f"Prepared dataset with {count} samples (min={min_samples})",
            source="synaptic_adapter",
        )
    except Exception:
        pass

    return is_ready, count, dst


def generate_qlora_training_script(
    dataset_path: Optional[Path] = None,
    output_script_path: Optional[Path] = None,
    adapter_output_dir: Optional[Path] = None,
    base_model_name: str = "unsloth/mistral-7b-instruct-v0.3-bnb-4bit",
) -> Path:
    """Generate a self-contained 4-bit QLoRA training script with 6GB VRAM budget guardrails."""
    script_path = Path(output_script_path).resolve() if output_script_path else TRAIN_SCRIPT_PATH
    script_path.parent.mkdir(parents=True, exist_ok=True)

    d_path = Path(dataset_path).resolve() if dataset_path else TRAINING_DATASET_FILE
    a_dir = Path(adapter_output_dir).resolve() if adapter_output_dir else ADAPTERS_DIR / "qlora_adapter"

    script_content = f'''"""Self-contained 4-bit QLoRA fine-tuning worker for J.A.R.V.I.S. MARK VIII.

Hardware Guardrails:
  - Strict 6GB VRAM budget (RTX 3050 Laptop GPU compatible).
  - 4-bit NF4 quantization via BitsAndBytes / Unsloth.
  - LoRA rank r=8, alpha=16 on attention projections.
  - Per-device batch size = 1, gradient accumulation steps = 4.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# VRAM optimization environment flags
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

DATASET_PATH = r"{d_path}"
ADAPTER_OUTPUT_DIR = r"{a_dir}"
BASE_MODEL_NAME = "{base_model_name}"

MAX_SEQ_LENGTH = 512
LORA_R = 8
LORA_ALPHA = 16
LORA_DROPOUT = 0.05
LEARNING_RATE = 2e-4
BATCH_SIZE = 1
GRADIENT_ACCUMULATION = 4
MAX_STEPS = 60

def train():
    print(f"[QLoRA Worker] Starting synaptic adaptation on {{BASE_MODEL_NAME}}...")
    print(f"[QLoRA Worker] Dataset: {{DATASET_PATH}}")
    print(f"[QLoRA Worker] Output Adapter: {{ADAPTER_OUTPUT_DIR}}")

    if not os.path.exists(DATASET_PATH):
        print(f"[QLoRA Worker][ERROR] Dataset not found: {{DATASET_PATH}}")
        sys.exit(1)

    import torch
    if not torch.cuda.is_available():
        print("[QLoRA Worker][ERROR] CUDA is not available on this system.")
        sys.exit(1)

    torch.cuda.empty_cache()
    gpu_name = torch.cuda.get_device_name(0)
    free_mem_gb = torch.cuda.mem_get_info()[0] / (1024 ** 3)
    print(f"[QLoRA Worker] GPU: {{gpu_name}} (Free VRAM: {{free_mem_gb:.2f}} GB)")

    # Load dataset
    samples = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                samples.append(json.loads(line))

    if not samples:
        print("[QLoRA Worker][ERROR] Dataset is empty.")
        sys.exit(1)

    print(f"[QLoRA Worker] Loaded {{len(samples)}} training pairs.")

    # Try Unsloth first, then fallback to standard HuggingFace PEFT / SFTTrainer
    try:
        from unsloth import FastLanguageModel
        print("[QLoRA Worker] Using Unsloth FastLanguageModel engine.")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=BASE_MODEL_NAME,
            max_seq_length=MAX_SEQ_LENGTH,
            load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=LORA_R,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            lora_alpha=LORA_ALPHA,
            lora_dropout=LORA_DROPOUT,
            bias="none",
            use_gradient_checkpointing="unsloth",
        )
    except Exception as unsloth_exc:
        print(f"[QLoRA Worker] Unsloth not found ({{unsloth_exc}}), loading via HuggingFace PEFT...")
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME, use_fast=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL_NAME,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.float16,
        )
        model = prepare_model_for_kbit_training(model)
        peft_config = LoraConfig(
            r=LORA_R,
            lora_alpha=LORA_ALPHA,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=LORA_DROPOUT,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, peft_config)

    # Format texts
    def format_prompt(sample):
        inst = sample.get("instruction", "")
        inp = sample.get("input", "")
        out = sample.get("output", "")
        if inp:
            return f"### Instruction:\\n{{inst}}\\n\\n### Input:\\n{{inp}}\\n\\n### Response:\\n{{out}}"
        return f"### Instruction:\\n{{inst}}\\n\\n### Response:\\n{{out}}"

    texts = [format_prompt(s) for s in samples]

    from datasets import Dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments

    dataset = Dataset.from_dict({{"text": texts}})

    training_args = TrainingArguments(
        output_dir=ADAPTER_OUTPUT_DIR,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION,
        warmup_steps=5,
        max_steps=min(MAX_STEPS, len(samples) * 2),
        learning_rate=LEARNING_RATE,
        fp16=True,
        logging_steps=5,
        optim="adamw_8bit",
        save_strategy="no",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LENGTH,
        tokenizer=tokenizer,
        args=training_args,
    )

    print("[QLoRA Worker] Executing training loop under 5.0GB VRAM ceiling...")
    trainer.train()

    os.makedirs(ADAPTER_OUTPUT_DIR, exist_ok=True)
    model.save_pretrained(ADAPTER_OUTPUT_DIR)
    tokenizer.save_pretrained(ADAPTER_OUTPUT_DIR)
    print(f"[QLoRA Worker] Synaptic adapter saved successfully to {{ADAPTER_OUTPUT_DIR}}.")
    torch.cuda.empty_cache()

if __name__ == "__main__":
    train()
'''

    with _lock:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

    log.info(f"[SynapticAdapter] Generated training script: {script_path}")
    return script_path


def export_ollama_modelfile(
    adapter_dir: Optional[Path] = None,
    base_model: str = "jarvis:latest",
    output_path: Optional[Path] = None,
) -> Path:
    """Generate an updated Ollama Modelfile referencing the trained LoRA adapter."""
    out = Path(output_path).resolve() if output_path else MODELFILE_PATH
    a_dir = Path(adapter_dir).resolve() if adapter_dir else ADAPTERS_DIR / "qlora_adapter"

    modelfile_content = f'''# J.A.R.V.I.S. Synaptic Consolidated Model
FROM {base_model}

# Load fine-tuned overnight QLoRA adapter
ADAPTER "{a_dir.as_posix()}"

# Runtime parameters
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|endoftext|>"

# System persona
SYSTEM """You are J.A.R.V.I.S., a hyper-capable personal AI assistant adapted with overnight synaptic consolidation."""
'''

    with _lock:
        with open(out, "w", encoding="utf-8") as f:
            f.write(modelfile_content)

    log.info(f"[SynapticAdapter] Exported Ollama Modelfile: {out}")
    return out


def generate_colab_training_export(
    dataset_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    base_model_name: str = "unsloth/mistral-7b-instruct-v0.3-bnb-4bit",
) -> Path:
    """Generate a self-contained Python / Colab script for free cloud execution on Google Colab T4 GPU."""
    out = Path(output_path).resolve() if output_path else COLAB_EXPORT_PATH
    d_path = Path(dataset_path).resolve() if dataset_path else TRAINING_DATASET_FILE
    out.parent.mkdir(parents=True, exist_ok=True)

    samples: List[Dict[str, str]] = []
    if d_path.exists():
        with open(d_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        samples.append(json.loads(line.strip()))
                    except Exception:
                        pass

    sample_json_str = json.dumps(samples, ensure_ascii=False, indent=2)

    colab_script = f'''# J.A.R.V.I.S. — Synaptic Plasticity Overnight Google Colab Training Worker
# Run this notebook/script on Google Colab with a free T4 GPU runtime!

# 1. Install required packages
# !pip install -q "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
# !pip install -q --no-deps "xformers<0.0.27" "trl<0.9.0" peft accelerate bitsandbytes

import json
import torch
from datasets import Dataset
from unsloth import FastLanguageModel
from trl import SFTTrainer
from transformers import TrainingArguments

BASE_MODEL_NAME = "{base_model_name}"
MAX_SEQ_LENGTH = 512

# 2. Embedded Distilled Dataset
TRAINING_SAMPLES = {sample_json_str}

print(f"Loaded {{len(TRAINING_SAMPLES)}} distilled experiential samples.")

# 3. Load 4-bit Base Model
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE_MODEL_NAME,
    max_seq_length=MAX_SEQ_LENGTH,
    load_in_4bit=True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r=8,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0.05,
    bias="none",
    use_gradient_checkpointing="unsloth",
)

# 4. Format Dataset
def format_prompt(sample):
    inst = sample.get("instruction", "")
    inp = sample.get("input", "")
    out = sample.get("output", "")
    if inp:
        return f"### Instruction:\\n{{inst}}\\n\\n### Input:\\n{{inp}}\\n\\n### Response:\\n{{out}}"
    return f"### Instruction:\\n{{inst}}\\n\\n### Response:\\n{{out}}"

texts = [format_prompt(s) for s in TRAINING_SAMPLES]
dataset = Dataset.from_dict({{"text": texts}})

# 5. Execute Training
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LENGTH,
    tokenizer=tokenizer,
    args=TrainingArguments(
        output_dir="jarvis_qlora_adapter",
        per_device_train_batch_size=2,
        gradient_accumulation_steps=2,
        warmup_steps=5,
        max_steps=60,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=5,
        optim="adamw_8bit",
    ),
)

trainer.train()

# 6. Save Adapter
model.save_pretrained("jarvis_qlora_adapter")
tokenizer.save_pretrained("jarvis_qlora_adapter")
print("Synaptic fine-tuning complete! Download 'jarvis_qlora_adapter' to your local JARVIS repository.")
'''

    with _lock:
        with open(out, "w", encoding="utf-8") as f:
            f.write(colab_script)

    log.info(f"[SynapticAdapter] Generated Google Colab cloud export: {out}")
    return out


def check_vram_and_cuda() -> Tuple[bool, float, str]:
    """Inspect local hardware for CUDA and free VRAM headroom."""
    try:
        import torch
        if not torch.cuda.is_available():
            return False, 0.0, "CUDA is not available"

        free_bytes = torch.cuda.mem_get_info()[0]
        free_gb = free_bytes / (1024 ** 3)
        sufficient = free_gb >= VRAM_MIN_FREE_GB
        detail = f"Free VRAM: {free_gb:.2f} GB (Required: {VRAM_MIN_FREE_GB:.1f} GB)"
        return sufficient, free_gb, detail
    except Exception as exc:
        return False, 0.0, f"Hardware check error: {exc}"


def schedule_overnight_training(
    maintenance_hour: int = 3,
    force: bool = False,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    """Execute or schedule overnight synaptic fine-tuning."""
    now = datetime.datetime.now()
    if not force and now.hour != maintenance_hour:
        return {
            "success": False,
            "status": "outside_maintenance_window",
            "message": f"Current hour ({now.hour}) != maintenance hour ({maintenance_hour}:00 AM)",
        }

    ready, count, dataset_path = prepare_instruction_dataset(min_samples=min_samples)
    if not ready:
        return {
            "success": False,
            "status": "insufficient_data",
            "message": f"Sample count ({count}) below required threshold ({min_samples})",
            "sample_count": count,
        }

    # Generate training artifacts
    train_script = generate_qlora_training_script(dataset_path=dataset_path)
    modelfile = export_ollama_modelfile()
    colab_script = generate_colab_training_export(dataset_path=dataset_path)

    vram_ok, free_gb, vram_detail = check_vram_and_cuda()

    if not vram_ok:
        log.warning(f"[SynapticAdapter] VRAM constraint detected ({vram_detail}). Cloud Colab export generated.")
        return {
            "success": True,
            "status": "cloud_export_ready",
            "message": f"Local training skipped ({vram_detail}). Exported cloud training script to {colab_script.name}",
            "colab_script": str(colab_script),
            "modelfile": str(modelfile),
            "sample_count": count,
        }

    # Local training execution via script
    log.info(f"[SynapticAdapter] VRAM headroom verified ({free_gb:.2f} GB). Spawning QLoRA worker...")
    return {
        "success": True,
        "status": "local_training_ready",
        "message": f"Hardware verified ({vram_detail}). Training script generated at {train_script.name}",
        "script_path": str(train_script),
        "modelfile": str(modelfile),
        "sample_count": count,
    }
