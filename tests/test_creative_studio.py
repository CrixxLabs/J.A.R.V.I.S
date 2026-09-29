from pathlib import Path
from creative_studio import CreativeStudio
from creative_studio.providers.nvidia_nim import NvidiaNimProvider, CreativeProviderError

class FakeProvider:
    configured = True
    def generate_image(self, prompt, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"fake"); return output
    def animate_image(self, image_path, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"fake-video"); return output

def test_status_truthful(tmp_path):
    s = CreativeStudio(tmp_path, provider=FakeProvider())
    st = s.status()
    assert st["text_to_image"] is True
    assert st["image_to_video"] is True
    assert st["text_to_video"] is False
    assert st["image_edit"] is False

def test_image_generation_contract(tmp_path):
    r = CreativeStudio(tmp_path, FakeProvider()).generate_image("JARVIS")
    assert r.ok and Path(r.path).exists()

def test_animate_contract(tmp_path):
    source = tmp_path/"source.png"; source.write_bytes(b"x")
    r = CreativeStudio(tmp_path, FakeProvider()).animate_image(source)
    assert r.ok and Path(r.path).suffix == ".mp4"

def test_missing_key_is_not_ready(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert NvidiaNimProvider(api_key="").configured is False

def test_unimplemented_features_are_truthful(tmp_path):
    s = CreativeStudio(tmp_path, FakeProvider())
    assert not s.generate_video("test").ok
    assert not s.edit_image("x.png", "change it").ok
