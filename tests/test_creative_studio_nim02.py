import os
from pathlib import Path
from creative_studio.providers import nvidia_nim as nim

def test_repo_env_loader_does_not_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "already-set")
    nim._load_repo_env()
    assert os.environ["NVIDIA_API_KEY"] == "already-set"

def test_current_image_provider_order():
    assert nim.NvidiaNimProvider.IMAGE_ENDPOINTS[0][0] == "flux.2-klein-4b"
    assert nim.NvidiaNimProvider.IMAGE_ENDPOINTS[1][0] == "flux.1-schnell"

def test_flux2_payload_contract():
    p = nim.NvidiaNimProvider._payload("flux.2-klein-4b", "hello", 7)
    assert p["mode"] == "Image Generation"
    assert p["steps"] == 4 and p["cfg_scale"] == 0

def test_fallback_moves_to_second_endpoint(monkeypatch, tmp_path):
    provider = nim.NvidiaNimProvider(api_key="x", timeout=1)
    calls = []
    def fake_post(url, payload, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise nim.CreativeProviderError("simulated timeout")
        import base64
        return {"artifacts":[{"base64":base64.b64encode(b"img").decode()}]}, {}
    monkeypatch.setattr(provider, "_post", fake_post)
    out = provider.generate_image("x", tmp_path/"x.png")
    assert out.read_bytes() == b"img"
    assert provider.last_image_model == "flux.1-schnell"
    assert len(calls) == 2

def test_all_endpoint_errors_are_reported(monkeypatch, tmp_path):
    provider = nim.NvidiaNimProvider(api_key="x", timeout=1)
    monkeypatch.setattr(provider, "_post",
        lambda *a, **k: (_ for _ in ()).throw(nim.CreativeProviderError("nope")))
    try:
        provider.generate_image("x", tmp_path/"x.png")
    except nim.CreativeProviderError as exc:
        msg = str(exc)
        assert "flux.2-klein-4b" in msg and "flux.1-schnell" in msg and "flux.1-dev" in msg
    else:
        raise AssertionError("expected CreativeProviderError")
