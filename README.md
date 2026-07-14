# Lishas Virtual Try-On Backend

FastAPI proxy that forwards try-on requests to the upstream AI service and exposes a clean polling API to the Lishas frontend.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/try-on` | Start a try-on job |
| GET | `/try-on/status/{request_id}` | Poll for result |

## Local setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your values
uvicorn main:app --reload --port 8000
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `APP_API_KEY` | Bearer token the frontend sends. Leave empty to disable auth in dev. |
| `VIRTUAL_TRYON_API_URL` | Upstream AI service base URL |
| `VIRTUAL_TRYON_API_KEY` | API key for the upstream AI service |

## Deploy on Render

1. Create a new **Web Service** pointing to this repo.
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `uvicorn main:app --host 0.0.0.0 --port 10000`
4. Add all environment variables in the Render dashboard under **Environment**.
