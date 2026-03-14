from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_NAME, MAX_UPLOAD_MB, STATIC_DIR
from .pipeline import PipelineError, run_project
from .storage import (
    ensure_project_dirs,
    new_project_id,
    project_dir,
    read_status,
    write_status,
)

app = FastAPI(title=APP_NAME)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text()


@app.post("/api/projects")
async def create_project() -> Dict[str, str]:
    project_id = new_project_id()
    ensure_project_dirs(project_id)
    write_status(project_id, {"state": "created", "progress": 0, "message": "Project created"})
    return {"project_id": project_id}


@app.post("/api/projects/{project_id}/upload")
async def upload_audio(project_id: str, file: UploadFile = File(...)) -> Dict[str, str]:
    if not project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="Project not found")
    if file.content_type and not file.content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="Upload an audio file")

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(status_code=400, detail="File too large")

    paths = ensure_project_dirs(project_id)
    audio_path = paths["audio"] / file.filename
    async with aiofiles.open(audio_path, "wb") as f:
        await f.write(data)

    write_status(project_id, {"state": "uploaded", "progress": 5, "message": "Audio uploaded"})
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/generate")
async def generate(project_id: str, background: BackgroundTasks) -> Dict[str, str]:
    if not project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="Project not found")

    write_status(project_id, {"state": "queued", "progress": 0, "message": "Queued"})
    background.add_task(_run_pipeline, project_id)
    return {"status": "started"}


def _run_pipeline(project_id: str) -> None:
    try:
        run_project(project_id)
    except PipelineError as exc:
        write_status(
            project_id,
            {"state": "failed", "progress": 0, "message": f"Failed: {exc}"},
        )
    except Exception as exc:  # noqa: BLE001
        write_status(
            project_id,
            {"state": "failed", "progress": 0, "message": f"Error: {exc}"},
        )


@app.get("/api/projects/{project_id}/status")
async def status(project_id: str) -> Dict:
    if not project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return read_status(project_id)


@app.get("/api/projects/{project_id}/video")
async def video(project_id: str) -> FileResponse:
    output = project_dir(project_id) / "output" / "final.mp4"
    if not output.exists():
        raise HTTPException(status_code=404, detail="Video not ready")
    return FileResponse(output)
