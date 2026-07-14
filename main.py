"""
Lishas Virtual Try-On Backend API
==================================
POST /try-on
  Accepts multipart/form-data:
    - person_image        image file (JPEG/PNG)
    - clothing_image      image file (JPEG/PNG)
    - category            string  (e.g. "upper_body")
    - garment_description string

  Returns:
    { "request_id": "<uuid>", "status": "pending" }

GET /try-on/status/{request_id}
  Returns:
    { "request_id": "<uuid>", "status": "pending|processing|completed|failed",
      "results": { "try_on_image": "<url>" } }   # only when completed
"""

import os
import uuid
import asyncio
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APP_API_KEY = os.getenv("APP_API_KEY", "")
VIRTUAL_TRYON_API_URL = os.getenv(
    "VIRTUAL_TRYON_API_URL",
    "https://tryfit-virtualtryon-backend.onrender.com",
)
VIRTUAL_TRYON_API_KEY = os.getenv("VIRTUAL_TRYON_API_KEY", "")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Lishas Try-On API",
    description="Proxy that forwards try-on requests to the upstream AI service.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict to your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_api_key(authorization: Optional[str] = Security(_api_key_header)) -> None:
    """Validate Bearer token if APP_API_KEY is configured."""
    if not APP_API_KEY:
        return  # auth disabled — dev mode
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != APP_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ---------------------------------------------------------------------------
# In-memory job store (replace with DB in production)
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/try-on")
async def start_try_on(
    person_image: UploadFile = File(...),
    clothing_image: UploadFile = File(...),
    category: str = Form(default="upper_body"),
    garment_description: str = Form(default="Garment"),
    _: None = Security(verify_api_key),
):
    """Start a try-on job; returns request_id immediately for polling."""
    request_id = str(uuid.uuid4())
    jobs[request_id] = {"status": "processing", "result": None, "error": None}

    person_bytes = await person_image.read()
    clothing_bytes = await clothing_image.read()

    asyncio.create_task(
        _run_tryon_job(
            request_id,
            person_bytes,
            person_image.content_type or "image/jpeg",
            clothing_bytes,
            clothing_image.content_type or "image/jpeg",
            category,
            garment_description,
        )
    )

    return {"request_id": request_id, "status": "pending"}


@app.get("/try-on/status/{request_id}")
async def get_status(
    request_id: str,
    _: None = Security(verify_api_key),
):
    """Poll for the result of a try-on job."""
    job = jobs.get(request_id)
    if not job:
        raise HTTPException(status_code=404, detail="Request ID not found")

    if job["status"] == "completed":
        return {
            "request_id": request_id,
            "status": "completed",
            "results": {"try_on_image": job["result"]},
        }

    if job["status"] == "failed":
        return {
            "request_id": request_id,
            "status": "failed",
            "message": job["error"] or "Processing failed",
        }

    return {"request_id": request_id, "status": job["status"]}


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _run_tryon_job(
    request_id: str,
    person_bytes: bytes,
    person_mime: str,
    clothing_bytes: bytes,
    clothing_mime: str,
    category: str,
    garment_description: str,
) -> None:
    """Forward images to the upstream AI service and store the result."""
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{VIRTUAL_TRYON_API_URL}/try-on",
                headers={"Authorization": f"Bearer {VIRTUAL_TRYON_API_KEY}"},
                files={
                    "person_image": ("person.jpg", person_bytes, person_mime),
                    "clothing_image": ("clothing.jpg", clothing_bytes, clothing_mime),
                },
                data={"category": category, "garment_description": garment_description},
            )

        data = response.json()

        if not response.is_success:
            jobs[request_id] = {
                "status": "failed",
                "result": None,
                "error": data.get("message", f"Upstream error {response.status_code}"),
            }
            return

        upstream_id = data.get("request_id")
        if not upstream_id:
            jobs[request_id] = {
                "status": "failed",
                "result": None,
                "error": "No request_id in upstream response",
            }
            return

        result_url = await _poll_upstream(upstream_id)
        jobs[request_id] = {"status": "completed", "result": result_url, "error": None}

    except Exception as exc:
        jobs[request_id] = {"status": "failed", "result": None, "error": str(exc)}


async def _poll_upstream(upstream_id: str, max_polls: int = 60) -> str:
    """Poll the upstream status endpoint until the job completes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(max_polls):
            await asyncio.sleep(3)
            resp = await client.get(
                f"{VIRTUAL_TRYON_API_URL}/try-on/status/{upstream_id}",
                headers={"Authorization": f"Bearer {VIRTUAL_TRYON_API_KEY}"},
            )
            data = resp.json()
            status = data.get("status")

            if status == "completed":
                url = data.get("results", {}).get("try_on_image")
                if not url:
                    raise ValueError("No try_on_image in completed response")
                return url

            if status == "failed":
                raise ValueError(data.get("message", "Upstream job failed"))

    raise TimeoutError("Try-on job timed out after 3 minutes")
