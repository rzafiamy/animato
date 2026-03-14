"""
pipeline.py – Video rendering pipeline with audio synchronisation.

Key improvements:
  - Audio-driven slide durations: actual audio length is measured, then slide
    durations are proportionally scaled so total == audio length.
  - Ken-Burns zoom animation on each slide image (zoompan filter).
  - Multi-line bullet text rendered per-line to avoid ffmpeg wrap issues.
  - Concurrent image generation (configurable via CONCURRENT_IMAGES).
  - Robust ffmpeg filter escaping.
  - Per-slide video and full-project SSE events.
  - Slide metadata (storyboard.json) is editable by the API layer.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .ai_providers import PipelineError, Slide, get_provider
from .config import (
    AUDIO_BITRATE,
    CONCURRENT_IMAGES,
    FPS,
    FONT_BULLET_SIZE,
    FONT_TITLE_SIZE,
    SLIDE_PADDING,
    VIDEO_BITRATE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from .storage import ensure_project_dirs, write_status

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ffmpeg_path() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise PipelineError("ffmpeg not found on PATH. Please install ffmpeg.")
    return p


def _run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        # Truncate long outputs
        raise PipelineError(proc.stdout[-3000:] if proc.stdout else "ffmpeg error")


def _get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _escape_drawtext(value: str) -> str:
    """Escape a string for use in an ffmpeg drawtext filter value."""
    return (
        value
        .replace("\\", "\\\\")
        .replace("'", "\u2019")   # replace apostrophe with right single quotation mark (safer than escaping)
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("%", "\\%")
    )


# ──────────────────────────────────────────────────────────────────────────────
# Ken-Burns animated slide rendering
# ──────────────────────────────────────────────────────────────────────────────

def _render_slide_video(
    slide: Slide,
    image_path: Path,
    out_path: Path,
    slide_idx: int,
) -> None:
    """Render a single slide to MP4 with animated Ken-Burns + text overlay."""
    ffmpeg = _ffmpeg_path()
    duration = max(4.0, slide.duration)
    total_frames = int(math.ceil(duration * FPS))

    # Ken-Burns direction alternates per slide for visual variety
    if slide_idx % 4 == 0:
        zoom_expr = f"min(1+{0.0004}*on,1.12)"   # slow zoom in
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"
    elif slide_idx % 4 == 1:
        zoom_expr = f"max(1.12-{0.0004}*on,1.0)"  # slow zoom out
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"
    elif slide_idx % 4 == 2:
        zoom_expr = f"min(1+{0.0003}*on,1.10)"
        x_expr = "0"                               # pan right
        y_expr = "(ih-ih/zoom)/2"
    else:
        zoom_expr = f"min(1+{0.0003}*on,1.10)"
        x_expr = "(iw-iw/zoom)"                   # pan left
        y_expr = "(ih-ih/zoom)/2"

    # Padding fraction → pixel offsets
    pad_y = int(VIDEO_HEIGHT * SLIDE_PADDING)
    pad_x = int(VIDEO_WIDTH * 0.06)

    # Build drawtext chain for bullets (one filter per line)
    bullet_filters: List[str] = []
    line_h = FONT_BULLET_SIZE + 14
    bullet_y_start = int(VIDEO_HEIGHT * 0.53)
    for bi, bullet in enumerate(slide.bullets[:5]):  # max 5 bullets
        txt = _escape_drawtext(f"• {bullet}")
        y = bullet_y_start + bi * line_h
        bullet_filters.append(
            f"drawtext=fontcolor=white:fontsize={FONT_BULLET_SIZE}:"
            f"x={pad_x}:y={y}:"
            f"text='{txt}':"
            f"shadowcolor=black@0.8:shadowx=2:shadowy=2:"
            f"box=0"
        )

    title_esc = _escape_drawtext(slide.title)
    title_filter = (
        f"drawtext=fontcolor=white:fontsize={FONT_TITLE_SIZE}:"
        f"x={pad_x}:y={pad_y}:"
        f"text='{title_esc}':"
        f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
        f"fontweight=bold:"
        f"box=0"
    )

    # Gradient overlay: dark at top + bottom (letterbox-style)
    gradient_filter = (
        "drawbox=y=0:color=black@0.55:width=iw:height=ih*0.45:t=fill,"
        "drawbox=y=ih*0.55:color=black@0.35:width=iw:height=ih*0.45:t=fill"
    )

    # Separator line under title
    sep_y = pad_y + FONT_TITLE_SIZE + 16
    sep_filter = (
        f"drawbox=x={pad_x}:y={sep_y}:width={VIDEO_WIDTH - pad_x*2}:"
        f"height=3:color=0x38bdf8@0.9:t=fill"
    )

    filters = (
        f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:flags=lanczos,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FPS},"
        f"{gradient_filter},"
        f"{sep_filter},"
        f"{title_filter}"
    )
    for bf in bullet_filters:
        filters += f",{bf}"

    _run_ffmpeg([
        ffmpeg, "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", filters,
        "-r", str(FPS),
        "-c:v", "libx264",
        "-b:v", VIDEO_BITRATE,
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ])


def _render_fallback_slide(slide: Slide, out_path: Path) -> None:
    """Create a solid-colour slide (no image) as emergency fallback."""
    ffmpeg = _ffmpeg_path()
    duration = max(4.0, slide.duration)
    pad_x = int(VIDEO_WIDTH * 0.06)
    pad_y = int(VIDEO_HEIGHT * 0.12)
    title_esc = _escape_drawtext(slide.title)

    title_filter = (
        f"drawtext=fontcolor=white:fontsize={FONT_TITLE_SIZE}:"
        f"x={pad_x}:y={pad_y}:text='{title_esc}':"
        f"shadowcolor=black:shadowx=2:shadowy=2"
    )
    bullet_filters: List[str] = []
    line_h = FONT_BULLET_SIZE + 14
    y0 = int(VIDEO_HEIGHT * 0.52)
    for bi, bullet in enumerate(slide.bullets[:5]):
        txt = _escape_drawtext(f"• {bullet}")
        bullet_filters.append(
            f"drawtext=fontcolor=white:fontsize={FONT_BULLET_SIZE}:"
            f"x={pad_x}:y={y0 + bi * line_h}:text='{txt}':"
            f"shadowcolor=black:shadowx=2:shadowy=2"
        )

    vf = f"color=c=0x0f172a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT},{title_filter}"
    for bf in bullet_filters:
        vf += f",{bf}"

    _run_ffmpeg([
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", vf,
        "-t", str(duration),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ])


# ──────────────────────────────────────────────────────────────────────────────
# Concat + master
# ──────────────────────────────────────────────────────────────────────────────

def _concat_videos(video_paths: List[Path], audio_path: Path, output_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    list_file = output_path.parent / "concat.txt"
    lines = "\n".join(f"file '{p.as_posix()}'" for p in video_paths)
    list_file.write_text(lines, encoding="utf-8")

    temp_video = output_path.parent / "_temp_noaudio.mp4"

    # Step 1: concatenate slide videos (video only)
    _run_ffmpeg([
        ffmpeg, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(temp_video),
    ])

    # Step 2: mux audio track
    _run_ffmpeg([
        ffmpeg, "-y",
        "-i", str(temp_video),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ])

    temp_video.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Main pipeline entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_project(project_id: str, reset: bool = False) -> None:
    provider = get_provider()
    paths = ensure_project_dirs(project_id)

    if reset:
        shutil.rmtree(paths["output"], ignore_errors=True)
        shutil.rmtree(paths["assets"], ignore_errors=True)
        paths = ensure_project_dirs(project_id)

    # ── Find audio ─────────────────────────────────────────────────────────────
    audio_files = [f for f in paths["audio"].glob("*") if f.is_file()]
    if not audio_files:
        raise PipelineError("No audio file found. Please upload an audio file first.")
    audio_path = audio_files[0]
    audio_duration = _get_audio_duration(audio_path)

    # ── Stage 1: ASR ───────────────────────────────────────────────────────────
    transcript_path = paths["output"] / "transcript.srt"
    if transcript_path.exists():
        write_status(project_id, {"state": "asr", "progress": 10,
                                  "message": "Using cached transcript"})
        transcript = transcript_path.read_text(encoding="utf-8")
    else:
        write_status(project_id, {"state": "asr", "progress": 10,
                                  "message": "Transcribing audio with timestamps…"})
        transcript = provider.transcribe(audio_path)
        transcript_path.write_text(transcript, encoding="utf-8")

    # ── Stage 2: Script ────────────────────────────────────────────────────────
    script_path = paths["output"] / "script.json"
    if script_path.exists():
        write_status(project_id, {"state": "script", "progress": 30,
                                  "message": "Using cached scene plan"})
        script = script_path.read_text(encoding="utf-8")
    else:
        write_status(project_id, {"state": "script", "progress": 30,
                                  "message": "Planning visual scenes with LLM…"})
        script = provider.generate_script(transcript, project_id=project_id)
        script_path.write_text(script, encoding="utf-8")

    # ── Stage 3: Storyboard ────────────────────────────────────────────────────
    storyboard_path = paths["output"] / "storyboard.json"
    if storyboard_path.exists():
        write_status(project_id, {"state": "storyboard", "progress": 50,
                                  "message": "Using cached storyboard"})
        slides = _load_storyboard(storyboard_path)
    else:
        write_status(project_id, {"state": "storyboard", "progress": 50,
                                  "message": "Building AI storyboard…"})
        slides = provider.generate_storyboard(script, project_id=project_id)
        if not slides:
            raise PipelineError("AI produced an empty storyboard.")
        _save_storyboard(storyboard_path, slides)

    # ── Adjust durations to match actual audio length ─────────────────────────
    if audio_duration > 0 and slides:
        slides = _sync_durations_to_audio(slides, audio_duration)
        # Re-save with corrected durations
        _save_storyboard(storyboard_path, slides)

    # ── Stage 4: Image generation (concurrent) ─────────────────────────────────
    write_status(project_id, {"state": "images", "progress": 60,
                              "message": f"Generating {len(slides)} AI images…"})
    _generate_images_concurrent(provider, slides, paths, project_id)

    # ── Stage 5: Slide video rendering ────────────────────────────────────────
    write_status(project_id, {"state": "render", "progress": 75,
                              "message": "Rendering animated slides…"})
    slide_videos = _render_all_slides(slides, paths, project_id)

    # ── Stage 6: Final mastering ───────────────────────────────────────────────
    output_video = paths["output"] / "final.mp4"
    if not output_video.exists():
        write_status(project_id, {"state": "render", "progress": 95,
                                  "message": "Mastering final composite…"})
        _concat_videos(slide_videos, audio_path, output_video)

    write_status(project_id, {"state": "done", "progress": 100,
                              "message": "Production complete. Master ready."})


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _save_storyboard(path: Path, slides: List[Slide]) -> None:
    data = [
        {
            "title": s.title,
            "bullets": s.bullets,
            "image_prompt": s.image_prompt,
            "duration": s.duration,
            "start_time": s.start_time,
            "image_ext": s.image_ext,
        }
        for s in slides
    ]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_storyboard(path: Path) -> List[Slide]:
    data = json.loads(path.read_text(encoding="utf-8"))
    slides: List[Slide] = []
    for item in data:
        slides.append(Slide(
            title=str(item.get("title", "Untitled")),
            bullets=list(item.get("bullets") or []),
            image_prompt=str(item.get("image_prompt", "Abstract background")),
            duration=float(item.get("duration") or 8),
            start_time=float(item.get("start_time") or 0),
            image_ext=str(item.get("image_ext") or "png"),
        ))
    return slides


def _sync_durations_to_audio(slides: List[Slide], audio_duration: float) -> List[Slide]:
    """Scale slide durations proportionally so they sum to audio_duration."""
    total = sum(s.duration for s in slides)
    if total <= 0:
        equal = audio_duration / len(slides)
        for s in slides:
            s.duration = equal
        return slides

    scale = audio_duration / total
    cumulative = 0.0
    for i, s in enumerate(slides):
        s.duration = round(s.duration * scale, 3)
        s.start_time = round(cumulative, 3)
        cumulative += s.duration
    return slides


def _generate_images_concurrent(
    provider,
    slides: List[Slide],
    paths: Dict[str, Path],
    project_id: str,
) -> None:
    """Generate images using a thread pool of size CONCURRENT_IMAGES."""
    semaphore = threading.Semaphore(CONCURRENT_IMAGES)
    errors: List[str] = []
    completed = [0]

    def _gen_one(idx: int, slide: Slide) -> None:
        ffmpeg = shutil.which("ffmpeg") or ""
        image_base = paths["assets"] / f"slide_{idx:02d}"

        # Check if already generated
        for ext in ("png", "jpg", "jpeg"):
            if (paths["assets"] / f"slide_{idx:02d}.{ext}").exists():
                slide.image_ext = ext
                return

        with semaphore:
            try:
                ext = provider.generate_image(slide.image_prompt, image_base.with_suffix(".png"))
                slide.image_ext = ext
            except Exception as exc:
                errors.append(f"Slide {idx}: {exc}")
                # Fallback: generate solid-colour image via ffmpeg
                if ffmpeg:
                    fallback = image_base.with_suffix(".png")
                    subprocess.run(
                        [
                            ffmpeg, "-y", "-f", "lavfi",
                            "-i", f"color=c=0x0f172a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                            "-frames:v", "1", str(fallback),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    slide.image_ext = "png"

        completed[0] += 1
        write_status(
            project_id,
            {
                "state": "images",
                "progress": 60 + int((completed[0] / len(slides)) * 15),
                "message": f"Image {completed[0]}/{len(slides)} ready",
            },
        )

    threads = [
        threading.Thread(target=_gen_one, args=(i, slide), daemon=True)
        for i, slide in enumerate(slides, start=1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        # Non-fatal: log but continue with fallback slides
        print(f"[WARN] Image gen errors (fallbacks used): {errors}")


def _render_all_slides(
    slides: List[Slide],
    paths: Dict[str, Path],
    project_id: str,
) -> List[Path]:
    slide_videos: List[Path] = []
    for idx, slide in enumerate(slides, start=1):
        video_path = paths["output"] / f"slide_{idx:02d}.mp4"
        if video_path.exists():
            slide_videos.append(video_path)
            continue

        write_status(
            project_id,
            {
                "state": "render",
                "progress": 75 + int((idx / len(slides)) * 20),
                "message": f"Rendering slide {idx}/{len(slides)}: {slide.title[:30]}…",
            },
        )

        # Locate image (png or jpg)
        image_path: Optional[Path] = None
        for ext in (slide.image_ext, "png", "jpg", "jpeg"):
            candidate = paths["assets"] / f"slide_{idx:02d}.{ext}"
            if candidate.exists():
                image_path = candidate
                break

        try:
            if image_path and image_path.exists():
                _render_slide_video(slide, image_path, video_path, idx)
            else:
                _render_fallback_slide(slide, video_path)
        except PipelineError as exc:
            print(f"[WARN] Slide {idx} render error: {exc}. Using fallback.")
            try:
                _render_fallback_slide(slide, video_path)
            except Exception as e2:
                raise PipelineError(f"Slide {idx} render failed entirely: {e2}")

        slide_videos.append(video_path)

    return slide_videos
