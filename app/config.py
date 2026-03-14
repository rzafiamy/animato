import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "app" / "data")).resolve()
STATIC_DIR = Path(os.getenv("STATIC_DIR", BASE_DIR / "app" / "static")).resolve()

APP_NAME = os.getenv("APP_NAME", "Animato Studio")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1920"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1080"))
FPS = int(os.getenv("FPS", "30"))

PROVIDER = os.getenv("PROVIDER", "mock")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
DALLE_MODEL = os.getenv("DALLE_MODEL", "dall-e-3")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))



DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
