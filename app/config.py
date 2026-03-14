import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "app" / "data"))).resolve()
STATIC_DIR = Path(os.getenv("STATIC_DIR", str(BASE_DIR / "app" / "static"))).resolve()

APP_NAME = os.getenv("APP_NAME", "Animato Studio")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1920"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1080"))
FPS = int(os.getenv("FPS", "30"))
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "5M")
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "192k")

PROVIDER = os.getenv("PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
DALLE_MODEL = os.getenv("DALLE_MODEL", "dall-e-3")
DALLE_QUALITY = os.getenv("DALLE_QUALITY", "standard")  # standard | hd

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "300"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
CONCURRENT_IMAGES = int(os.getenv("CONCURRENT_IMAGES", "2"))
CONCURRENT_RENDERS = int(os.getenv("CONCURRENT_RENDERS", "3"))
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "10"))  # max AI-generated images; extras reuse cycling

# Slide visual settings
FONT_TITLE_SIZE = int(os.getenv("FONT_TITLE_SIZE", "80"))
FONT_BULLET_SIZE = int(os.getenv("FONT_BULLET_SIZE", "42"))
SLIDE_PADDING = float(os.getenv("SLIDE_PADDING", "0.12"))  # fraction of height

# Design style: modern | vintage | kawaii | neon | minimal
VIDEO_STYLE = os.getenv("VIDEO_STYLE", "modern")

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
