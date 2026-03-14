from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import httpx

from .config import PROVIDER
import os


@dataclass
class Slide:
    title: str
    bullets: List[str]
    image_prompt: str
    duration: int


class AIProvider:
    def transcribe(self, audio_path: Path) -> str:
        raise NotImplementedError

    def generate_script(self, transcript: str) -> str:
        raise NotImplementedError

    def generate_storyboard(self, script: str) -> List[Slide]:
        raise NotImplementedError

    def generate_image(self, prompt: str, output_path: Path) -> None:
        raise NotImplementedError


class MockProvider(AIProvider):
    def transcribe(self, audio_path: Path) -> str:
        return (
            "Welcome to Animato. Today we turn audio into a cinematic podcast video, "
            "mixing slides, titles, and motion graphics."
        )

    def generate_script(self, transcript: str) -> str:
        return (
            f"Intro: {transcript}\n"
            "Point 1: The audio is analyzed and summarized.\n"
            "Point 2: Slides and animations are generated.\n"
            "Point 3: A full HD video is rendered with music and visuals."
        )

    def generate_storyboard(self, script: str) -> List[Slide]:
        topics = [
            ("Hook", ["Cinematic intro", "Establish the theme"], "Neon waveform in a studio"),
            ("Summary", ["Key takeaways", "Audience value"], "Minimalist icons over gradient"),
            ("Process", ["ASR to text", "LLM to script"], "Flowchart with glowing nodes"),
            ("Visuals", ["Images", "Animated titles"], "Abstract motion shapes"),
            ("Impact", ["Engage viewers", "Boost retention"], "Podcast host silhouette"),
            ("Closing", ["Call to action", "Subscribe"], "Spotlight on microphone"),
        ]
        slides = []
        for title, bullets, prompt in topics:
            slides.append(Slide(title=title, bullets=bullets, image_prompt=prompt, duration=6))
        return slides

    def generate_image(self, prompt: str, output_path: Path) -> None:
        output_path.write_text(f"IMAGE PLACEHOLDER\nPrompt: {prompt}\n")


class RestProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def transcribe(self, audio_path: Path) -> str:
        with audio_path.open("rb") as f:
            files = {"file": (audio_path.name, f.read())}
        response = httpx.post(
            f"{self.base_url}/asr",
            files=files,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["text"]

    def generate_script(self, transcript: str) -> str:
        response = httpx.post(
            f"{self.base_url}/text",
            json={"transcript": transcript},
            headers=self._headers(),
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["script"]

    def generate_storyboard(self, script: str) -> List[Slide]:
        response = httpx.post(
            f"{self.base_url}/storyboard",
            json={"script": script},
            headers=self._headers(),
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        slides = []
        for item in data["slides"]:
            slides.append(
                Slide(
                    title=item["title"],
                    bullets=item.get("bullets", []),
                    image_prompt=item.get("image_prompt", ""),
                    duration=int(item.get("duration", 6)),
                )
            )
        return slides

    def generate_image(self, prompt: str, output_path: Path) -> None:
        response = httpx.post(
            f"{self.base_url}/image",
            json={"prompt": prompt},
            headers=self._headers(),
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        output_path.write_bytes(bytes.fromhex(data["image_hex"]))


def get_provider() -> AIProvider:
    if PROVIDER == "rest":
        base_url = os.getenv("REST_AI_BASE_URL", "http://localhost:8081")
        api_key = os.getenv("REST_AI_API_KEY", "")
        return RestProvider(base_url=base_url, api_key=api_key)
    return MockProvider()
