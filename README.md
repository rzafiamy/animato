# Animato Studio

Animato Studio is a FastAPI + TailwindCSS + vanilla JS web app that turns an input audio file into a full HD podcast-style video with animated slides, titles, and visuals.

## What it does

- Upload audio
- ASR -> script -> storyboard
- Render slides and motion visuals into `final.mp4`

The default `mock` provider makes the pipeline runnable without external AI keys. If you have your own AI stack, switch to the `rest` provider and point it at your inference endpoints.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open `http://localhost:8000`.

## REST provider contract

Set `PROVIDER=rest` and implement these endpoints in your AI service:

- `POST /asr` -> returns `{ "text": "..." }`
- `POST /text` -> returns `{ "script": "..." }`
- `POST /storyboard` -> returns `{ "slides": [{ "title": "", "bullets": [], "image_prompt": "", "duration": 6 }] }`
- `POST /image` -> returns `{ "image_hex": "..." }` (hex-encoded PNG/JPG bytes)

## Notes

- Rendering uses `ffmpeg`. Install it and ensure it is on your PATH.
- Output artifacts live under `app/data/projects/<project_id>/output/`.
