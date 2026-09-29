from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
from .providers.nvidia_nim import NvidiaNimProvider, CreativeProviderError

@dataclass
class CreativeResult:
    ok: bool
    kind: str
    provider: str
    path: str | None = None
    message: str = ""

class CreativeStudio:
    def __init__(self, output_dir: str | Path = "generated_media", provider=None):
        self.output_dir = Path(output_dir)
        self.provider = provider or NvidiaNimProvider()

    def status(self) -> dict:
        return {
            "provider": "nvidia_nim",
            "configured": self.provider.configured,
            "text_to_image": True,
            "image_to_video": True,
            "text_to_video": False,
            "image_edit": False,
        }

    def _name(self, prefix: str, suffix: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self.output_dir / f"{prefix}_{stamp}{suffix}"

    def generate_image(self, prompt: str) -> CreativeResult:
        try:
            path = self.provider.generate_image(prompt, self._name("image", ".png"))
            return CreativeResult(True, "image", "nvidia_nim", str(path), "Image generated.")
        except CreativeProviderError as exc:
            return CreativeResult(False, "image", "nvidia_nim", message=str(exc))

    def animate_image(self, image_path: str | Path) -> CreativeResult:
        try:
            path = self.provider.animate_image(Path(image_path), self._name("video", ".mp4"))
            return CreativeResult(True, "video", "nvidia_nim", str(path), "Video generated.")
        except (CreativeProviderError, OSError) as exc:
            return CreativeResult(False, "video", "nvidia_nim", message=str(exc))

    def generate_video(self, prompt: str) -> CreativeResult:
        return CreativeResult(False, "video", "nvidia_nim",
            message="Text-to-video is not enabled in NIM Creative Studio 01; no endpoint is being invented.")

    def edit_image(self, image_path: str | Path, prompt: str) -> CreativeResult:
        return CreativeResult(False, "image_edit", "nvidia_nim",
            message="Arbitrary image editing is not enabled in NIM Creative Studio 01; FLUX.1-schnell does not support it.")
