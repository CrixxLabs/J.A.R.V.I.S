# J.A.R.V.I.S. — Synaptic Plasticity Overnight Google Colab Training Worker
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

BASE_MODEL_NAME = "unsloth/mistral-7b-instruct-v0.3-bnb-4bit"
MAX_SEQ_LENGTH = 512

# 2. Embedded Distilled Dataset
TRAINING_SAMPLES = [
  {
    "instruction": "Solve math problem #0",
    "input": "x = 0",
    "output": "Answer is 0"
  },
  {
    "instruction": "Solve math problem #1",
    "input": "x = 1",
    "output": "Answer is 2"
  },
  {
    "instruction": "Solve math problem #2",
    "input": "x = 2",
    "output": "Answer is 4"
  },
  {
    "instruction": "Solve math problem #3",
    "input": "x = 3",
    "output": "Answer is 6"
  },
  {
    "instruction": "Solve math problem #4",
    "input": "x = 4",
    "output": "Answer is 8"
  },
  {
    "instruction": "Solve math problem #5",
    "input": "x = 5",
    "output": "Answer is 10"
  },
  {
    "instruction": "Solve math problem #6",
    "input": "x = 6",
    "output": "Answer is 12"
  },
  {
    "instruction": "Solve math problem #7",
    "input": "x = 7",
    "output": "Answer is 14"
  },
  {
    "instruction": "Solve math problem #8",
    "input": "x = 8",
    "output": "Answer is 16"
  },
  {
    "instruction": "Solve math problem #9",
    "input": "x = 9",
    "output": "Answer is 18"
  },
  {
    "instruction": "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant.",
    "input": "Query #10",
    "output": "Response #10"
  },
  {
    "instruction": "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant.",
    "input": "Query #11",
    "output": "Response #11"
  },
  {
    "instruction": "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant.",
    "input": "Query #12",
    "output": "Response #12"
  },
  {
    "instruction": "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant.",
    "input": "Query #13",
    "output": "Response #13"
  },
  {
    "instruction": "You are J.A.R.V.I.S., a sharp, loyal, and highly capable AI assistant.",
    "input": "Query #14",
    "output": "Response #14"
  }
]

print(f"Loaded {len(TRAINING_SAMPLES)} distilled experiential samples.")

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
        return f"### Instruction:\n{inst}\n\n### Input:\n{inp}\n\n### Response:\n{out}"
    return f"### Instruction:\n{inst}\n\n### Response:\n{out}"

texts = [format_prompt(s) for s in TRAINING_SAMPLES]
dataset = Dataset.from_dict({"text": texts})

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
