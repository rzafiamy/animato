import json
import uuid
from pathlib import Path
from typing import Any, Dict

from .config import DATA_DIR

PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def new_project_id() -> str:
    return uuid.uuid4().hex


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def ensure_project_dirs(project_id: str) -> Dict[str, Path]:
    base = project_dir(project_id)
    audio_dir = base / "audio"
    assets_dir = base / "assets"
    output_dir = base / "output"
    for path in (base, audio_dir, assets_dir, output_dir):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "base": base,
        "audio": audio_dir,
        "assets": assets_dir,
        "output": output_dir,
    }


def status_path(project_id: str) -> Path:
    return project_dir(project_id) / "status.json"


def read_status(project_id: str) -> Dict[str, Any]:
    path = status_path(project_id)
    if not path.exists():
        return {"state": "new", "progress": 0, "message": "Not started"}
    try:
        return json.loads(path.read_text(encoding="utf-8"), strict=False)
    except (json.JSONDecodeError, OSError):
        return {"state": "unknown", "progress": 0, "message": "Status unreadable (corrupted)"}


def write_status(project_id: str, data: Dict[str, Any]) -> None:
    path = status_path(project_id)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
