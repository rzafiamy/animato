"""
ai_providers.py – AI backend layer (OpenAI / compatible endpoints).

Improvements:
  - Retry logic (exponential back-off) on all AI calls
  - Proper SRT timestamp parsing for audio-image sync
  - Robust JSON extraction (handles markdown code-fences from LLMs)
  - JPEG/PNG auto-detection and correct extension handling
  - Slide dataclass extended with start_time for sync
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx

from .config import MAX_RETRIES, PROVIDER


class PipelineError(RuntimeError):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Slide:
    title: str
    bullets: List[str]
    image_prompt: str
    duration: float          # seconds, float for precision
    start_time: float = 0.0  # audio offset (for sync)
    image_ext: str = "png"   # "png" or "jpg" – set after image gen


# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_image(path: Path) -> bool:
    """Check magic bytes: PNG or JPEG."""
    try:
        if path.stat().st_size < 8:
            return False
        header = path.read_bytes()[:8]
        return header[:8] == b"\x89PNG\r\n\x1a\n" or header[:3] == b"\xff\xd8\xff"
    except Exception:
        return False


def _srt_time_to_sec(t: str) -> float:
    """Convert SRT timestamp '00:01:23,456' → seconds."""
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def _extract_json(raw: str) -> str:
    """Strip markdown code fences that some LLMs wrap around JSON."""
    raw = raw.strip()
    # Remove ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if m:
        return m.group(1).strip()
    return raw


def _retry(fn, label: str, retries: int = MAX_RETRIES):
    """Call fn(), retrying up to `retries` times with exponential back-off."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except PipelineError:
            raise
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(f"[RETRY] {label} attempt {attempt}/{retries} failed: {exc}. Waiting {wait}s…")
            time.sleep(wait)
    raise PipelineError(f"{label} failed after {retries} retries: {last_exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Base class
# ──────────────────────────────────────────────────────────────────────────────

class AIProvider:
    def transcribe(self, audio_path: Path) -> str:
        raise NotImplementedError

    def generate_script(self, transcript: str, project_id: str | None = None) -> str:
        raise NotImplementedError

    def generate_storyboard(self, script: str, project_id: str | None = None) -> List[Slide]:
        raise NotImplementedError

    def generate_image(self, prompt: str, output_path: Path) -> str:
        """Generate image and save. Returns actual extension ('png' or 'jpg')."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI provider
# ──────────────────────────────────────────────────────────────────────────────

class OpenAIProvider(AIProvider):

    # Visual style palette – cycles across chunks for variety
    STYLES = [
        "Hyper-realistic cinematic photography, 8K ultra-sharp, dramatic Rembrandt lighting, bokeh depth of field",
        "Stylized 3-D isometric illustration, vibrant jewel-tone palette, soft global illumination, Octane render",
        "Conceptual digital art, abstract symbolic geometry, neon gradient aurora, dark background",
        "Minimalist flat-vector design, bold Swiss-typography-inspired shapes, professional muted palette",
        "Cyberpunk cityscape painting, rain-soaked neon reflections, high contrast, dramatic atmosphere",
        "Architectural technical blueprint, precise line-work, grid overlay, deep-blue ink on white",
        "Impressionist oil painting, expressive palate-knife texture, museum gallery lighting",
        "Retro synthwave poster art, warm magenta/cyan gradient sky, 80s grid, chrome typography",
    ]

    def __init__(self, api_key: str, base_url: str) -> None:
        from openai import OpenAI
        from .config import OPENAI_MODEL, WHISPER_MODEL, DALLE_MODEL, DALLE_QUALITY
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
        self.model = OPENAI_MODEL
        self.whisper_model = WHISPER_MODEL
        self.dalle_model = DALLE_MODEL
        self.dalle_quality = DALLE_QUALITY

    # ── ASR ─────────────────────────────────────────────────────────────────

    def transcribe(self, audio_path: Path) -> str:
        def _call():
            with audio_path.open("rb") as f:
                result = self.client.audio.transcriptions.create(
                    model=self.whisper_model,
                    file=f,
                    response_format="srt",
                )
            return str(result)

        return _retry(_call, "ASR transcription")

    # ── Script (scene planning) ─────────────────────────────────────────────

    @staticmethod
    def _parse_srt_blocks(srt: str) -> List[dict]:
        """Parse SRT into list of dicts: {text, start, end}."""
        blocks = re.split(r"\n\s*\n", srt.strip())
        result = []
        for block in blocks:
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            # line 0 = index, line 1 = timestamps, line 2+ = text
            m = re.match(
                r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
                lines[1],
            )
            if not m:
                continue
            start = _srt_time_to_sec(m.group(1))
            end = _srt_time_to_sec(m.group(2))
            text = " ".join(lines[2:])
            result.append({"text": text, "start": start, "end": end})
        return result

    def generate_script(self, transcript: str, project_id: str | None = None) -> str:
        from .storage import write_status

        blocks = self._parse_srt_blocks(transcript)
        if not blocks:
            # Fallback: treat entire transcript as one block
            total_dur = 120.0
            blocks = [{"text": transcript, "start": 0.0, "end": total_dur}]

        # Group into ~30-second chunks preserving start/end times
        CHUNK_SEC = 30.0
        chunks: List[List[dict]] = []
        current: List[dict] = []
        chunk_start = blocks[0]["start"] if blocks else 0.0

        for b in blocks:
            current.append(b)
            if (b["end"] - chunk_start) >= CHUNK_SEC:
                chunks.append(current)
                current = []
                chunk_start = b["end"]
        if current:
            chunks.append(current)

        all_scenes: List[dict] = []

        for i, chunk in enumerate(chunks):
            if project_id:
                write_status(
                    project_id,
                    {
                        "state": "script",
                        "progress": 10 + int((i / len(chunks)) * 20),
                        "message": f"Planning scenes: section {i + 1}/{len(chunks)}…",
                    },
                )

            chunk_start_t = chunk[0]["start"]
            chunk_end_t = chunk[-1]["end"]
            duration = max(5.0, chunk_end_t - chunk_start_t)
            text = " ".join(b["text"] for b in chunk)
            style = self.STYLES[i % len(self.STYLES)]

            prompt = (
                f"You are a visual director. Plan 1–2 cinematic scenes for this podcast segment.\n"
                f"Segment timing: {chunk_start_t:.1f}s → {chunk_end_t:.1f}s (duration ~{duration:.1f}s).\n"
                f"Visual style: {style}\n\n"
                "Return a JSON object {\"scenes\": [...]}. Each scene:\n"
                "  - title: short punchy header, max 8 words, NO special characters\n"
                "  - image_prompt: rich visual description using the stated style, landscape 16:9\n"
                "  - bullets: list of 2–4 on-screen talking points, max 10 words each\n"
                f"  - duration: float seconds (all scenes must sum to ~{duration:.1f}s)\n"
                f"  - start_time: float seconds from audio start (first scene: {chunk_start_t:.2f})\n\n"
                "Transcript:\n" + text
            )

            def _call(p=prompt):
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a visual director. Output ONLY valid JSON."},
                        {"role": "user", "content": p},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.65,
                )
                raw = resp.choices[0].message.content or "{}"
                data = json.loads(_extract_json(raw))
                return data.get("scenes") or []

            scenes = _retry(_call, f"Script section {i + 1}")
            all_scenes.extend(scenes if isinstance(scenes, list) else [])

        return json.dumps(all_scenes)

    # ── Storyboard ────────────────────────────────────────────────────────────

    def generate_storyboard(self, script: str, project_id: str | None = None) -> List[Slide]:
        from .storage import write_status

        if project_id:
            write_status(project_id, {"state": "storyboard", "progress": 50,
                                      "message": "Finalising storyboard…"})
        try:
            raw = json.loads(script)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Script JSON parse error: {exc}")

        if isinstance(raw, dict):
            raw = raw.get("scenes", [])
        if not isinstance(raw, list):
            raise PipelineError("Script has unexpected format (not a list).")

        slides: List[Slide] = []
        for item in raw:
            title = str(item.get("title", "Untitled"))[:80].strip()
            # Sanitize title for ffmpeg drawtext (remove problematic chars)
            title = re.sub(r"[\":<>|?*]", "", title)
            bullets_raw = item.get("bullets") or []
            bullets = []
            for b in (bullets_raw if isinstance(bullets_raw, list) else []):
                clean = re.sub(r"[\":<>|?*]", "", str(b))[:100].strip()
                if clean:
                    bullets.append(clean)

            image_prompt = str(item.get("image_prompt", "Abstract professional background"))[:500]
            duration = max(4.0, float(item.get("duration") or 8))
            start_time = max(0.0, float(item.get("start_time") or 0))

            slides.append(Slide(
                title=title,
                bullets=bullets,
                image_prompt=image_prompt,
                duration=duration,
                start_time=start_time,
            ))

        return slides

    # ── Image generation ──────────────────────────────────────────────────────

    def generate_image(self, prompt: str, output_path: Path) -> str:
        """Download and save image. Returns actual file extension used."""
        def _call():
            resp = self.client.images.generate(
                model=self.dalle_model,
                prompt=prompt[:950],  # DALL-E limit
                size="1792x1024",
                quality=self.dalle_quality,  # type: ignore[arg-type]
                n=1,
            )
            url = resp.data[0].url
            if not url:
                raise PipelineError("DALL-E returned empty URL")

            with httpx.Client(timeout=90.0) as client:
                r = client.get(url)
                r.raise_for_status()
                return r.content

        raw = _retry(_call, f"Image generation '{prompt[:40]}…'")

        # Detect format
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        elif raw[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        else:
            raise PipelineError(
                f"DALL-E returned unexpected data (starts: {raw[:16]!r})"
            )

        # Save with correct extension
        actual_path = output_path.with_suffix(f".{ext}")
        actual_path.write_bytes(raw)

        # If caller expected a different extension name, also write there
        if actual_path != output_path:
            output_path.write_bytes(raw)

        return ext


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────

def get_provider() -> AIProvider:
    from .config import OPENAI_API_KEY, OPENAI_BASE_URL
    return OpenAIProvider(OPENAI_API_KEY or "no-key", OPENAI_BASE_URL)
