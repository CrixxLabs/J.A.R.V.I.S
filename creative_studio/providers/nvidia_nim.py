from __future__ import annotations
import base64, json, mimetypes, os, socket
from pathlib import Path
from urllib import request, error

class CreativeProviderError(RuntimeError):
    pass

def _load_repo_env() -> None:
    """Load simple KEY=VALUE entries from repo .env without overriding real env vars."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if value[:1] == value[-1:] and value[:1] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass

_load_repo_env()

class NvidiaNimProvider:
    # Ordered: current/new fast model first, then proven older endpoints.
    IMAGE_ENDPOINTS = (
        ("flux.2-klein-4b", "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"),
        ("flux.1-schnell", "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell"),
        ("flux.1-dev", "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"),
    )
    IMAGE_TO_VIDEO_URL = "https://ai.api.nvidia.com/v1/genai/stabilityai/stable-video-diffusion"

    def __init__(self, api_key: str | None = None, timeout: int = 45):
        _load_repo_env()
        self.api_key = ((os.getenv("NVIDIA_API_KEY") or "") if api_key is None else api_key).strip()
        self.timeout = int(os.getenv("NVIDIA_CREATIVE_TIMEOUT", timeout))
        self.last_image_model = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _post(self, url: str, payload: dict, timeout: int | None = None) -> tuple[dict, dict]:
        if not self.configured:
            raise CreativeProviderError("NVIDIA_API_KEY is not configured.")
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype or body[:1] in (b"{", b"["):
                    return json.loads(body.decode("utf-8")), dict(resp.headers)
                return {"_raw_b64": base64.b64encode(body).decode("ascii"),
                        "_content_type": ctype}, dict(resp.headers)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise CreativeProviderError(f"HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise CreativeProviderError(f"timed out after {timeout or self.timeout}s") from exc
        except Exception as exc:
            raise CreativeProviderError(str(exc)) from exc

    @staticmethod
    def _extract_b64(obj):
        if isinstance(obj, str):
            if obj.startswith("data:") and ";base64," in obj:
                return obj.split(";base64,", 1)[1]
            return None
        if isinstance(obj, dict):
            for key in ("base64", "b64_json"):
                value = obj.get(key)
                if isinstance(value, str) and value:
                    return value.split(";base64,", 1)[-1] if ";base64," in value else value
            for key in ("image", "artifacts", "data", "output", "images"):
                if key in obj:
                    found = NvidiaNimProvider._extract_b64(obj[key])
                    if found:
                        return found
            for value in obj.values():
                found = NvidiaNimProvider._extract_b64(value)
                if found:
                    return found
        if isinstance(obj, list):
            for value in obj:
                found = NvidiaNimProvider._extract_b64(value)
                if found:
                    return found
        return None

    @staticmethod
    def _payload(model: str, prompt: str, seed: int) -> dict:
        if model == "flux.1-dev":
            return {"prompt": prompt, "height": 1024, "width": 1024, "cfg_scale": 5,
                    "mode": "base", "samples": 1, "seed": seed, "steps": 20}
        if model == "flux.2-klein-4b":
            return {"mode": "Image Generation", "prompt": prompt, "height": 1024,
                    "width": 1024, "cfg_scale": 0, "samples": 1, "seed": seed, "steps": 4}
        return {"prompt": prompt, "height": 1024, "width": 1024, "cfg_scale": 0,
                "mode": "base", "samples": 1, "seed": seed, "steps": 4}

    def generate_image(self, prompt: str, output: Path, *, seed: int = 0) -> Path:
        failures = []
        for model, url in self.IMAGE_ENDPOINTS:
            try:
                result, _ = self._post(url, self._payload(model, prompt, seed))
                b64 = self._extract_b64(result)
                if not b64:
                    raise CreativeProviderError("response contained no image data")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(base64.b64decode(b64))
                self.last_image_model = model
                return output
            except CreativeProviderError as exc:
                failures.append(f"{model}: {exc}")
        raise CreativeProviderError("All NVIDIA image endpoints failed | " + " | ".join(failures))

    @staticmethod
    def _image_data_uri(path: Path) -> str:
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    def animate_image(self, image_path: Path, output: Path, *, seed: int = 0,
                      cfg_scale: float = 1.8, motion_bucket_id: int = 127) -> Path:
        payload = {"image": self._image_data_uri(image_path), "seed": int(seed),
                   "cfg_scale": float(cfg_scale), "motion_bucket_id": int(motion_bucket_id)}
        result, _ = self._post(self.IMAGE_TO_VIDEO_URL, payload, timeout=max(self.timeout, 120))
        raw_b64 = result.get("_raw_b64") or self._extract_b64(result)
        if not raw_b64:
            raise CreativeProviderError("Video endpoint returned no inline video data.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(raw_b64))
        return output
