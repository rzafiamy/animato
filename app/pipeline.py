from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import List

from .ai_providers import Slide, get_provider
from .config import FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from .storage import ensure_project_dirs, read_status, write_status


class PipelineError(RuntimeError):
    pass


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("\n", "\\n")
    )


def _run_ffmpeg(args: List[str], cwd: Path | None = None) -> None:
    process = subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise PipelineError(process.stdout)


def _ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


def _render_slide_image(slide: Slide, image_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("ffmpeg not found")

    title = _escape_drawtext(slide.title)
    prompt = _escape_drawtext(slide.image_prompt)
    draw = (
        "drawtext=fontcolor=white:fontsize=72:x=(w-text_w)/2:y=H*0.35:"
        f"text='{title}',"
        "drawtext=fontcolor=white:fontsize=36:x=(w-text_w)/2:y=H*0.55:"
        f"text='{prompt}'"
    )

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x111827:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
            "-vf",
            draw,
            "-frames:v",
            "1",
            str(image_path),
        ]
    )


def _render_slide_video(slide: Slide, image_path: Path, out_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("ffmpeg not found")

    title = _escape_drawtext(slide.title)
    bullets = _escape_drawtext("\n".join(f"• {b}" for b in slide.bullets))
    draw = (
        "drawtext=fontcolor=white:fontsize=72:x=(w-text_w)/2:y=H*0.18:"
        f"text='{title}',"
        "drawtext=fontcolor=white:fontsize=40:x=(w-text_w)/2:y=H*0.65:"
        f"text='{bullets}':line_spacing=10"
    )

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            str(slide.duration),
            "-vf",
            draw,
            "-r",
            str(FPS),
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ]
    )


def _concat_videos(video_paths: List[Path], output_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("ffmpeg not found")

    list_file = output_path.parent / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in video_paths))

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def run_project(project_id: str) -> None:
    provider = get_provider()
    paths = ensure_project_dirs(project_id)

    audio_files = list(paths["audio"].glob("*"))
    if not audio_files:
        raise PipelineError("No audio uploaded")
    audio_path = audio_files[0]

    write_status(project_id, {"state": "asr", "progress": 10, "message": "Transcribing audio"})
    transcript = provider.transcribe(audio_path)
    (paths["output"] / "transcript.txt").write_text(transcript)

    write_status(project_id, {"state": "script", "progress": 30, "message": "Generating script"})
    script = provider.generate_script(transcript)
    (paths["output"] / "script.txt").write_text(script)

    write_status(
        project_id,
        {"state": "storyboard", "progress": 50, "message": "Building storyboard"},
    )
    slides = provider.generate_storyboard(script)
    (paths["output"] / "storyboard.json").write_text(
        json.dumps([slide.__dict__ for slide in slides], indent=2)
    )

    write_status(project_id, {"state": "render", "progress": 70, "message": "Rendering slides"})

    slide_videos = []
    for idx, slide in enumerate(slides, start=1):
        image_path = paths["assets"] / f"slide_{idx:02d}.png"
        video_path = paths["output"] / f"slide_{idx:02d}.mp4"
        _render_slide_image(slide, image_path)
        _render_slide_video(slide, image_path, video_path)
        slide_videos.append(video_path)

    output_video = paths["output"] / "final.mp4"
    _concat_videos(slide_videos, output_video)

    write_status(project_id, {"state": "done", "progress": 100, "message": "Video ready"})
