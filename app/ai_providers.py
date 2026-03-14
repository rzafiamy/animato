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


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str, base_url: str):
        from openai import OpenAI
        from .config import OPENAI_MODEL, WHISPER_MODEL, DALLE_MODEL
        self.client = OpenAI(api_key=api_key, base_url=base_url)

        self.model = OPENAI_MODEL
        self.whisper_model = WHISPER_MODEL
        self.dalle_model = DALLE_MODEL

    def transcribe(self, audio_path: Path) -> str:
        try:
            with audio_path.open("rb") as f:
                # Use SRT format as requested to get timestamps
                transcript = self.client.audio.transcriptions.create(
                    model=self.whisper_model, 
                    file=f,
                    response_format="srt"
                )
            return transcript
        except Exception as e:
            raise PipelineError(f"Transcription failed: {str(e)}")

    def generate_script(self, transcript: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a podcast video script writer. Summarize the transcript into a compelling narrative. The input is in SRT format, use the timestamps to gauge the flow but output clean text."},
                    {"role": "user", "content": transcript}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            raise PipelineError(f"Script generation failed: {str(e)}")

    def generate_storyboard(self, script: str) -> List[Slide]:
        import json
        prompt = (
            "Create a storyboard for a podcast video based on this script. "
            "Return a JSON list (wrapped in a { 'slides': [...] } object) with objects containing 'title', "
            "'bullets' (list of 2-3 strings), 'image_prompt' (descriptive), and 'duration' (seconds). "
            "\n\nScript:\n" + script
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            slides_data = data.get("slides") or data if isinstance(data, list) else []
            if not slides_data and isinstance(data, dict):
                 for key in ["slides", "storyboard", "content"]:
                     if isinstance(data.get(key), list):
                         slides_data = data[key]
                         break
            
            if not slides_data and isinstance(data, dict):
                vals = list(data.values())
                if len(vals) == 1 and isinstance(vals[0], list):
                    slides_data = vals[0]

            if not slides_data:
                raise PipelineError("AI returned an empty storyboard. Check the script complexity.")

            slides = []
            for item in slides_data:
                slides.append(Slide(
                    title=item.get("title", "Untitled"),
                    bullets=item.get("bullets", []),
                    image_prompt=item.get("image_prompt", ""),
                    duration=int(item.get("duration", 6))
                ))
            return slides
        except Exception as e:
            raise PipelineError(f"Storyboard generation failed: {str(e)}")

    def generate_image(self, prompt: str, output_path: Path) -> None:
        try:
            response = self.client.images.generate(
                model=self.dalle_model,
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            image_url = response.data[0].url
            import httpx
            with httpx.Client() as client:
                resp = client.get(image_url)
                resp.raise_for_status()
                output_path.write_bytes(resp.content)
        except Exception as e:
            raise PipelineError(f"Image generation failed for prompt '{prompt[:50]}...': {str(e)}")


def get_provider() -> AIProvider:
    from .config import OPENAI_API_KEY, OPENAI_BASE_URL
    return OpenAIProvider(OPENAI_API_KEY or "no-key-required", OPENAI_BASE_URL)




