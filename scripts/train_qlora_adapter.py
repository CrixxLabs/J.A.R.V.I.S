"""Self-contained 4-bit QLoRA fine-tuning worker for J.A.R.V.I.S. MARK VIII.

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

DATASET_PATH = r"D:\J.A.R.V.I.S\Data\qlora_training_data.jsonl"
ADAPTER_OUTPUT_DIR = r"D:\J.A.R.V.I.S\adapters\qlora_adapter"
BASE_MODEL_NAME = "unsloth/mistral-7b-instruct-v0.3-bnb-4bit"

MAX_SEQ_LENGTH = 512
LORA_R = 8
LORA_ALPHA = 16
LORA_DROPOUT = 0.05
LEARNING_RATE = 2e-4
BATCH_SIZE = 1
GRADIENT_ACCUMULATION = 4
MAX_STEPS = 60

def train():
    print(f"[QLoRA Worker] Starting synaptic adaptation on {BASE_MODEL_NAME}...")
    print(f"[QLoRA Worker] Dataset: {DATASET_PATH}")
    print(f"[QLoRA Worker] Output Adapter: {ADAPTER_OUTPUT_DIR}")

    if not os.path.exists(DATASET_PATH):
        print(f"[QLoRA Worker][ERROR] Dataset not found: {DATASET_PATH}")
        sys.exit(1)

    import torch
    if not torch.cuda.is_available():
        print("[QLoRA Worker][ERROR] CUDA is not available on this system.")
        sys.exit(1)

    torch.cuda.empty_cache()
    gpu_name = torch.cuda.get_device_name(0)
    free_mem_gb = torch.cuda.mem_get_info()[0] / (1024 ** 3)
    print(f"[QLoRA Worker] GPU: {gpu_name} (Free VRAM: {free_mem_gb:.2f} GB)")

    # Load dataset
    samples = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                samples.append(json.loads(line))

    if not samples:
        print("[QLoRA Worker][ERROR] Dataset is empty.")
        sys.exit(1)

    print(f"[QLoRA Worker] Loaded {len(samples)} training pairs.")

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
        print(f"[QLoRA Worker] Unsloth not found ({unsloth_exc}), loading via HuggingFace PEFT...")
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
            return f"### Instruction:\n{inst}\n\n### Input:\n{inp}\n\n### Response:\n{out}"
        return f"### Instruction:\n{inst}\n\n### Response:\n{out}"

    texts = [format_prompt(s) for s in samples]

    from datasets import Dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments

    dataset = Dataset.from_dict({"text": texts})

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
    print(f"[QLoRA Worker] Synaptic adapter saved successfully to {ADAPTER_OUTPUT_DIR}.")
    torch.cuda.empty_cache()

if __name__ == "__main__":
    train()
