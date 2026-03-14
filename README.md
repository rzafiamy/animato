# 🎬 Animato Studio

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![TailwindCSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)

**Animato Studio** is a high-performance, AI-driven video synthesis platform that transforms raw audio into cinematic Full HD videos. Leveraging state-of-the-art AI models for transcription, storyboarding, and visual generation, it creates perfectly synchronized content ready for social media, YouTube, or podcasts.

---

## ✨ Key Features

- 🎙️ **Neural ASR**: Whisper-grade transcription with millisecond-accurate SRT synchronization.
- 🧠 **LLM Storyboarding**: GPT-powered scene planning that understands tone, pacing, and context.
- 🎨 **Diffusion Visuals**: DALL-E 3 generated imagery tailored to every scene.
- 🎞️ **Cinematic Rendering**: Ken Burns motion effects, dynamic text overlays, and professional mastering via FFmpeg.
- 📂 **Project Management**: Persistent storage for all your creations.
- 🛠️ **Modular AI Stack**: Easily swap between mock providers for testing or real AI endpoints for production.

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **FFmpeg** (ensure it's in your system PATH)

### Installation

```bash
# Clone the repository
git clone https://github.com/user/animato.git
cd animato

# Setup virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

### Running the App

```bash
uvicorn app.main:app --reload
```

Visit [http://localhost:8000](http://localhost:8000) to launch the studio.

---

## 🛠️ Configuration & AI Providers

Animato is designed to be provider-agnostic. You can configure your AI stack in the `.env` file:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PROVIDER` | `mock` (no API keys needed) or `rest` | `mock` |
| `BASE_URL` | Base URL for REST AI services | `http://localhost:8001` |

### REST Provider Contract

If using `PROVIDER=rest`, your service must implement:

- `POST /asr` → `{ "text": "..." }`
- `POST /text` → `{ "script": "..." }`
- `POST /storyboard` → `{ "slides": [...] }`
- `POST /image` → `{ "image_hex": "..." }`

---

## 📁 Project Structure

```text
├── app/
│   ├── main.py          # FastAPI application entry recovery
│   ├── pipeline.py      # Core AI orchestration logic
│   ├── ai_providers.py  # Provider implementations (OpenAI, Anthropic, Mock)
│   ├── static/          # Frontend (HTML, CSS, JS)
│   └── data/            # Local storage for audio and rendered videos
└── requirements.txt     # Python dependencies
```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.

---

<p align="center">
  Built with ❤️ by the Animato Team
</p>
