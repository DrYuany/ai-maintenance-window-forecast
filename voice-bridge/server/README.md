# Voice Bridge API

Python 3.10+ FastAPI service. Copy `.env.example` to `.env`, set a random `APP_SECRET_KEY` with at least 32 characters, a shared invite password, HTTPS `PUBLIC_BASE_URL`, and provider credentials/resources. Run `python -m venv .venv`, activate it, `pip install -e .`, then `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` from this folder. Cloud keys must remain server-side. ASR needs the provider to fetch a signed, five-minute HTTPS URL; recordings and TTS files remain in memory and are not persisted to disk.
