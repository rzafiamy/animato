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
    # Background image processing: scale and blur or crop to fit
    bullets_text = "\n".join(f"• {b}" for b in slide.bullets)
    bullets = _escape_drawtext(bullets_text)
    
    # We use a complex filter to:
    # 1. Scale background to fit
    # 2. Add an overlay (dark gradient) to make text readable
    # 3. Draw title and bullets
    filter_complex = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        "drawbox=y=0:color=black@0.4:width=iw:height=ih:t=fill,"
        f"drawtext=fontcolor=white:fontsize=80:x=(w-text_w)/2:y=H*0.2:text='{title}':shadowcolor=black:shadowx=2:shadowy=2,"
        f"drawtext=fontcolor=white:fontsize=44:x=(w-text_w)/2:y=H*0.5:text='{bullets}':line_spacing=20:shadowcolor=black:shadowx=2:shadowy=2"
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
            filter_complex,
            "-r",
            str(FPS),
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ]
    )


def _concat_videos(video_paths: List[Path], audio_path: Path, output_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("ffmpeg not found")

    list_file = output_path.parent / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in video_paths))

    # Concat videos and then add audio
    # We use -shortest to make sure video ends when audio ends (or vice versa)
    temp_video = output_path.parent / "temp_no_audio.mp4"
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
            str(temp_video),
        ]
    )

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(temp_video),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            str(output_path),
        ]
    )


def run_project(project_id: str, reset: bool = False) -> None:
    provider = get_provider()
    paths = ensure_project_dirs(project_id)

    if reset:
        # Clear previous progress/files but keep the uploaded audio
        # We can just delete output and assets
        shutil.rmtree(paths["output"], ignore_errors=True)
        shutil.rmtree(paths["assets"], ignore_errors=True)
        paths = ensure_project_dirs(project_id)

    audio_files = list(paths["audio"].glob("*"))
    if not audio_files:
        raise PipelineError("No audio uploaded. The production workspace is empty.")
    audio_path = audio_files[0]

    # --- Stage 1: ASR ---
    transcript_path = paths["output"] / "transcript.txt"
    if transcript_path.exists():
        write_status(project_id, {"state": "asr", "progress": 10, "message": "Using cached transcript"})
        transcript = transcript_path.read_text()
    else:
        write_status(project_id, {"state": "asr", "progress": 10, "message": "Transcribing audio with timestamps (SRT)"})
        try:
            transcript = provider.transcribe(audio_path)
            transcript_path.write_text(transcript)
        except Exception as e:
            raise PipelineError(f"Stage ASR failed: {str(e)}")

    # --- Stage 2: Script ---
    script_path = paths["output"] / "script.txt"
    if script_path.exists():
        write_status(project_id, {"state": "script", "progress": 30, "message": "Using cached script"})
        script = script_path.read_text()
    else:
        write_status(project_id, {"state": "script", "progress": 30, "message": "Neural script synthesis"})
        try:
            script = provider.generate_script(transcript)
            script_path.write_text(script)
        except Exception as e:
            raise PipelineError(f"Stage Scripting failed: {str(e)}")

    # --- Stage 3: Storyboard ---
    storyboard_path = paths["output"] / "storyboard.json"
    if storyboard_path.exists():
        write_status(project_id, {"state": "storyboard", "progress": 50, "message": "Using cached storyboard"})
        try:
            data = json.loads(storyboard_path.read_text())
            slides = []
            for item in data:
                # Robust reconstruction from cache
                slides.append(Slide(
                    title=item.get("title", "Untitled"),
                    bullets=item.get("bullets", []),
                    image_prompt=item.get("image_prompt", "Abstract background"),
                    duration=int(item.get("duration", 6))
                ))
        except Exception as e:
            # If JSON is corrupted, regenerate
            write_status(project_id, {"state": "storyboard", "progress": 50, "message": f"Cache corrupted ({str(e)}), regenerating..."})
            slides = provider.generate_storyboard(script)
            storyboard_path.write_text(json.dumps([slide.__dict__ for slide in slides], indent=2))
    else:

        write_status(
            project_id,
            {"state": "storyboard", "progress": 50, "message": "Constructing AI storyboard"},
        )
        try:
            slides = provider.generate_storyboard(script)
            if not slides:
                 raise PipelineError("AI failed to generate any slides for this script.")
                 
            storyboard_path.write_text(
                json.dumps([slide.__dict__ for slide in slides], indent=2)
            )
        except Exception as e:
            raise PipelineError(f"Stage Storyboarding failed: {str(e)}")

    # --- Stage 4: Render ---
    write_status(project_id, {"state": "render", "progress": 70, "message": "Synthesizing visuals & rendering"})

    slide_videos = []
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise PipelineError("FFmpeg executable not found on the server paths.")

    for idx, slide in enumerate(slides, start=1):
        msg = f"Processing slide {idx}/{len(slides)}: {slide.title[:20]}..."
        write_status(project_id, {"state": "render", "progress": 70 + int((idx/len(slides))*25), "message": msg})
        
        image_path = paths["assets"] / f"slide_{idx:02d}.png"
        video_path = paths["output"] / f"slide_{idx:02d}.mp4"
        
        # 1. Image Check/Generate
        if not image_path.exists():
            try:
                provider.generate_image(slide.image_prompt, image_path)
            except Exception:
                # Fallback for image gen failure
                _run_ffmpeg([
                    ffmpeg, "-y", "-f", "lavfi", "-i", 
                    f"color=c=0x0f172a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}", 
                    "-frames:v", "1", str(image_path)
                ])

        # 2. Slide Video Check/Render
        if not video_path.exists():
            try:
                _render_slide_video(slide, image_path, video_path)
            except Exception as e:
                raise PipelineError(f"Slide {idx} render failed: {str(e)}")
        
        slide_videos.append(video_path)

    # --- Stage 5: Final Mastering ---
    output_video = paths["output"] / "final.mp4"
    if output_video.exists():
        write_status(project_id, {"state": "done", "progress": 100, "message": "Production complete (cached)"})
    else:
        write_status(project_id, {"state": "render", "progress": 95, "message": "Mastering final composite"})
        try:
            _concat_videos(slide_videos, audio_path, output_video)
        except Exception as e:
            raise PipelineError(f"Final mastering failed: {str(e)}")

        write_status(project_id, {"state": "done", "progress": 100, "message": "Production complete. Master available."})



