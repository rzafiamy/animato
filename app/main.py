"""
main.py – FastAPI application with:
  - SSE real-time progress streaming
  - Project listing and management endpoints
  - Storyboard editor API (GET/PATCH per-slide)
  - Slide image preview
  - Re-render single slide
  - Download video
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import APP_NAME, MAX_UPLOAD_MB, STATIC_DIR
from .pipeline import (
    PipelineError,
    _load_storyboard,
    _render_fallback_slide,
    _render_slide_video,
    _save_storyboard,
    _sync_durations_to_audio,
    run_project,
)
from .storage import (
    ensure_project_dirs,
    new_project_id,
    project_dir,
    read_status,
    write_status,
)

# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_NAME, version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ──────────────────────────────────────────────────────────────────────────────
# HTML entry
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Project management
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects() -> List[Dict[str, Any]]:
    """List all projects with their status."""
    from .storage import PROJECTS_DIR
    projects: List[Dict[str, Any]] = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            if d.is_dir():
                status = read_status(d.name)
                audio_files = list((d / "audio").glob("*")) if (d / "audio").exists() else []
                has_video = (d / "output" / "final.mp4").exists()
                projects.append({
                    "project_id": d.name,
                    "status": status,
                    "has_video": has_video,
                    "audio_name": audio_files[0].name if audio_files else None,
                    "created_at": d.stat().st_ctime,
                })
    return projects


@app.post("/api/projects")
async def create_project() -> Dict[str, str]:
    project_id = new_project_id()
    ensure_project_dirs(project_id)
    write_status(project_id, {"state": "created", "progress": 0, "message": "Project created"})
    return {"project_id": project_id}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> Dict[str, str]:
    import shutil
    p = project_dir(project_id)
    if not p.exists():
        raise HTTPException(404, "Project not found")
    shutil.rmtree(p, ignore_errors=True)
    return {"status": "deleted"}


# ──────────────────────────────────────────────────────────────────────────────
# Audio upload
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/projects/{project_id}/upload")
async def upload_audio(project_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    if not project_dir(project_id).exists():
        raise HTTPException(404, "Project not found")

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(400, f"File too large ({size_mb:.1f} MB). Limit: {MAX_UPLOAD_MB} MB")

    paths = ensure_project_dirs(project_id)
    # Clear old audio
    for old in paths["audio"].glob("*"):
        old.unlink(missing_ok=True)

    safe_name = Path(file.filename or "audio.mp3").name
    audio_path = paths["audio"] / safe_name
    async with aiofiles.open(audio_path, "wb") as fp:  # fp avoids shadowing `file` param
        await fp.write(data)

    write_status(project_id, {"state": "uploaded", "progress": 5,
                              "message": f"Audio uploaded: {safe_name}"})
    return {"status": "ok", "filename": safe_name, "size_mb": round(size_mb, 2)}


# ──────────────────────────────────────────────────────────────────────────────
# Generation
# ──────────────────────────────────────────────────────────────────────────────

# Track active pipeline threads per project
_active_pipelines: Dict[str, threading.Thread] = {}


@app.post("/api/projects/{project_id}/generate")
async def generate(
    project_id: str,
    reset: bool = False,
) -> Dict[str, str]:
    if not project_dir(project_id).exists():
        raise HTTPException(404, "Project not found")

    # Prevent double-run
    thread = _active_pipelines.get(project_id)
    if thread and thread.is_alive():
        raise HTTPException(409, "Pipeline already running for this project")

    write_status(project_id, {"state": "queued", "progress": 0, "message": "Queued for processing"})

    # Use an explicit daemon thread — BackgroundTasks can block the event loop
    # with long-running sync functions (subprocess, openai SDK calls, etc.)
    t = threading.Thread(
        target=_run_pipeline_bg,
        args=(project_id, reset),
        daemon=True,
        name=f"pipeline-{project_id[:8]}",
    )
    t.start()
    return {"status": "started"}


def _run_pipeline_bg(project_id: str, reset: bool) -> None:
    _active_pipelines[project_id] = threading.current_thread()
    try:
        run_project(project_id, reset=reset)
    except PipelineError as exc:
        write_status(project_id, {"state": "failed", "progress": 0,
                                  "message": f"Pipeline error: {exc}"})
    except Exception as exc:
        import traceback
        write_status(project_id, {"state": "failed", "progress": 0,
                                  "message": f"Unexpected error: {exc}\n{traceback.format_exc()[-800:]}"})
    finally:
        _active_pipelines.pop(project_id, None)


# ──────────────────────────────────────────────────────────────────────────────
# SSE progress stream
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/stream")
async def stream_status(project_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time pipeline progress."""

    if not project_dir(project_id).exists():
        raise HTTPException(404, "Project not found")

    async def _event_generator():
        last_json = ""
        while True:
            if await request.is_disconnected():
                break
            status = read_status(project_id)
            current_json = json.dumps(status)
            if current_json != last_json:
                last_json = current_json
                yield f"data: {current_json}\n\n"
            if status.get("state") in ("done", "failed"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Status (polling fallback)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/status")
async def get_status(project_id: str) -> Dict[str, Any]:
    if not project_dir(project_id).exists():
        raise HTTPException(404, "Project not found")
    return read_status(project_id)


# ──────────────────────────────────────────────────────────────────────────────
# Storyboard editor API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/storyboard")
async def get_storyboard(project_id: str) -> List[Dict[str, Any]]:
    sb_path = project_dir(project_id) / "output" / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "Storyboard not generated yet")
    return json.loads(sb_path.read_text(encoding="utf-8"))


class SlideUpdate(BaseModel):
    title: Optional[str] = None
    bullets: Optional[List[str]] = None
    image_prompt: Optional[str] = None
    duration: Optional[float] = None


@app.patch("/api/projects/{project_id}/storyboard/{slide_index}")
async def update_slide(
    project_id: str,
    slide_index: int,
    update: SlideUpdate,
) -> Dict[str, Any]:
    """Update a single slide's fields. slide_index is 1-based."""
    sb_path = project_dir(project_id) / "output" / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "Storyboard not generated yet")

    slides = _load_storyboard(sb_path)
    if slide_index < 1 or slide_index > len(slides):
        raise HTTPException(400, f"Slide index out of range (1–{len(slides)})")

    slide = slides[slide_index - 1]
    if update.title is not None:
        slide.title = update.title
    if update.bullets is not None:
        slide.bullets = update.bullets
    if update.image_prompt is not None:
        slide.image_prompt = update.image_prompt
    if update.duration is not None:
        slide.duration = max(4.0, update.duration)

    _save_storyboard(sb_path, slides)
    return {"status": "updated", "slide": slide_index}


@app.post("/api/projects/{project_id}/storyboard/{slide_index}/rerender")
async def rerender_slide(
    project_id: str,
    slide_index: int,
    regen_image: bool = False,
) -> Dict[str, str]:
    """Re-render a specific slide (optionally regenerate its image)."""
    sb_path = project_dir(project_id) / "output" / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "Storyboard not found")

    slides = _load_storyboard(sb_path)
    if slide_index < 1 or slide_index > len(slides):
        raise HTTPException(400, "Slide index out of range")

    paths = ensure_project_dirs(project_id)
    t = threading.Thread(
        target=_rerender_slide_bg,
        args=(project_id, slide_index, slides, paths, regen_image),
        daemon=True,
    )
    t.start()
    return {"status": "started", "slide": str(slide_index)}


def _rerender_slide_bg(
    project_id: str,
    slide_index: int,
    slides,
    paths: Dict[str, Path],
    regen_image: bool,
) -> None:
    from .pipeline import _generate_images_concurrent, _render_all_slides, _concat_videos

    write_status(project_id, {"state": "render", "progress": 50,
                              "message": f"Re-rendering slide {slide_index}…"})
    slide = slides[slide_index - 1]
    video_path = paths["output"] / f"slide_{slide_index:02d}.mp4"
    image_base = paths["assets"] / f"slide_{slide_index:02d}"

    try:
        if regen_image:
            for ext in ("png", "jpg", "jpeg"):
                old = image_base.with_suffix(f".{ext}")
                old.unlink(missing_ok=True)
            provider = get_provider()
            ext = provider.generate_image(slide.image_prompt, image_base.with_suffix(".png"))
            slide.image_ext = ext
            # Save updated ext
            sb_path = project_dir(project_id) / "output" / "storyboard.json"
            _save_storyboard(sb_path, slides)

        # Delete old slide video
        video_path.unlink(missing_ok=True)

        # Find image
        image_path = None
        for ext in (slide.image_ext, "png", "jpg", "jpeg"):
            c = image_base.with_suffix(f".{ext}")
            if c.exists():
                image_path = c
                break

        if image_path:
            _render_slide_video(slide, image_path, video_path, slide_index)
        else:
            _render_fallback_slide(slide, video_path)

        # Rebuild final video
        final = paths["output"] / "final.mp4"
        final.unlink(missing_ok=True)
        audio_files = [f for f in paths["audio"].glob("*") if f.is_file()]
        if audio_files:
            slide_videos = []
            for i, s in enumerate(slides, 1):
                vp = paths["output"] / f"slide_{i:02d}.mp4"
                if vp.exists():
                    slide_videos.append(vp)
            if slide_videos:
                _concat_videos(slide_videos, audio_files[0], final)

        write_status(project_id, {"state": "done", "progress": 100,
                                  "message": f"Slide {slide_index} re-rendered."})
    except Exception as exc:
        write_status(project_id, {"state": "failed", "progress": 0,
                                  "message": f"Re-render failed: {exc}"})


def get_provider():
    from .ai_providers import get_provider as _gp
    return _gp()


# ──────────────────────────────────────────────────────────────────────────────
# Media endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/video")
async def get_video(project_id: str) -> FileResponse:
    output = project_dir(project_id) / "output" / "final.mp4"
    if not output.exists():
        raise HTTPException(404, "Video not ready yet")
    return FileResponse(
        output,
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="animato_{project_id[:8]}.mp4"'},
    )


@app.get("/api/projects/{project_id}/slides/{slide_index}/image")
async def get_slide_image(project_id: str, slide_index: int) -> FileResponse:
    assets = project_dir(project_id) / "assets"
    for ext in ("png", "jpg", "jpeg"):
        path = assets / f"slide_{slide_index:02d}.{ext}"
        if path.exists():
            return FileResponse(path, media_type=f"image/{ext}")
    raise HTTPException(404, "Slide image not found")


@app.get("/api/projects/{project_id}/slides/{slide_index}/video")
async def get_slide_video(project_id: str, slide_index: int) -> FileResponse:
    path = project_dir(project_id) / "output" / f"slide_{slide_index:02d}.mp4"
    if not path.exists():
        raise HTTPException(404, "Slide video not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/projects/{project_id}/transcript")
async def get_transcript(project_id: str) -> Dict[str, str]:
    path = project_dir(project_id) / "output" / "transcript.srt"
    if not path.exists():
        raise HTTPException(404, "Transcript not available")
    return {"transcript": path.read_text(encoding="utf-8")}
