"""Unit tests for Synaptic Plasticity & Overnight QLoRA Worker."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest

import synaptic_adapter
from synaptic_adapter import (
    prepare_instruction_dataset,
    generate_qlora_training_script,
    export_ollama_modelfile,
    generate_colab_training_export,
    schedule_overnight_training,
)


@pytest.fixture
def temp_memory_file(tmp_path):
    """Create a temporary distilled_memories.jsonl for test isolation."""
    src = tmp_path / "test_memories.jsonl"
    samples = [
        {
            "format": "dual_standard",
            "alpaca": {
                "instruction": f"Solve math problem #{i}",
                "input": f"x = {i}",
                "output": f"Answer is {i*2}",
            },
        }
        for i in range(10)
    ]
    # Add some user_query / assistant_response formatted entries
    for i in range(10, 15):
        samples.append({
            "user_query": f"Query #{i}",
            "assistant_response": f"Response #{i}",
        })

    with open(src, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    return src


def test_prepare_instruction_dataset(temp_memory_file, tmp_path):
    dst = tmp_path / "test_output.jsonl"

    # Test with min_samples=20 (we only have 15) -> not ready
    ready, count, out_path = prepare_instruction_dataset(
        min_samples=20,
        source_path=temp_memory_file,
        output_path=dst,
    )
    assert ready is False
    assert count == 15
    assert out_path == dst
    assert dst.exists()

    # Test with min_samples=10 -> ready
    ready, count, out_path = prepare_instruction_dataset(
        min_samples=10,
        source_path=temp_memory_file,
        output_path=dst,
    )
    assert ready is True
    assert count == 15

    # Verify content of dst
    with open(dst, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 15
    assert lines[0]["instruction"] == "Solve math problem #0"
    assert lines[0]["input"] == "x = 0"
    assert lines[0]["output"] == "Answer is 0"
    assert lines[10]["input"] == "Query #10"
    assert lines[10]["output"] == "Response #10"


def test_generate_qlora_training_script(tmp_path):
    script_path = tmp_path / "train_test.py"
    d_path = tmp_path / "dataset.jsonl"
    a_dir = tmp_path / "adapters" / "test_lora"

    res = generate_qlora_training_script(
        dataset_path=d_path,
        output_script_path=script_path,
        adapter_output_dir=a_dir,
        base_model_name="unsloth/mistral-7b-instruct-v0.3-bnb-4bit",
    )

    assert res == script_path
    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    assert "BATCH_SIZE = 1" in content
    assert "GRADIENT_ACCUMULATION = 4" in content
    assert "LORA_R = 8" in content
    assert "LORA_ALPHA = 16" in content
    assert "MAX_SEQ_LENGTH = 512" in content
    assert "unsloth/mistral-7b-instruct-v0.3-bnb-4bit" in content


def test_export_ollama_modelfile(tmp_path):
    modelfile_path = tmp_path / "Modelfile.test"
    a_dir = tmp_path / "adapters" / "qlora_adapter"

    res = export_ollama_modelfile(
        adapter_dir=a_dir,
        base_model="jarvis:latest",
        output_path=modelfile_path,
    )

    assert res == modelfile_path
    assert modelfile_path.exists()

    content = modelfile_path.read_text(encoding="utf-8")
    assert "FROM jarvis:latest" in content
    assert f'ADAPTER "{a_dir.as_posix()}"' in content
    assert "SYSTEM" in content


def test_generate_colab_training_export(temp_memory_file, tmp_path):
    colab_path = tmp_path / "colab_test.py"
    res = generate_colab_training_export(
        dataset_path=temp_memory_file,
        output_path=colab_path,
    )

    assert res == colab_path
    assert colab_path.exists()

    content = colab_path.read_text(encoding="utf-8")
    assert "TRAINING_SAMPLES =" in content
    assert "FastLanguageModel" in content
    assert "SFTTrainer" in content


def test_schedule_overnight_training(temp_memory_file, tmp_path):
    # Outside maintenance window without force
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 14  # 2 PM
        res = schedule_overnight_training(maintenance_hour=3, force=False)
        assert res["success"] is False
        assert res["status"] == "outside_maintenance_window"

    # Forced run with insufficient data
    with patch("synaptic_adapter.DISTILLED_FILE", temp_memory_file):
        res = schedule_overnight_training(force=True, min_samples=50)
        assert res["success"] is False
        assert res["status"] == "insufficient_data"

    # Forced run with sufficient data and mock hardware check
    with patch("synaptic_adapter.DISTILLED_FILE", temp_memory_file):
        with patch("synaptic_adapter.check_vram_and_cuda", return_value=(True, 5.2, "Free VRAM: 5.2 GB")):
            res = schedule_overnight_training(force=True, min_samples=5)
            assert res["success"] is True
            assert res["status"] == "local_training_ready"

    # Forced run with constrained VRAM -> Cloud Colab fallback
    with patch("synaptic_adapter.DISTILLED_FILE", temp_memory_file):
        with patch("synaptic_adapter.check_vram_and_cuda", return_value=(False, 2.1, "Free VRAM: 2.1 GB < 4.5 GB")):
            res = schedule_overnight_training(force=True, min_samples=5)
            assert res["success"] is True
            assert res["status"] == "cloud_export_ready"
            assert "colab_script" in res
