from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.paper_lookup import USER_AGENT, discover_paper

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_TTL_SECONDS = 30 * 60


@dataclass
class DownloadEntry:
    url: str
    filename: str
    created_at: float


DOWNLOAD_CACHE: dict[str, DownloadEntry] = {}

app = FastAPI(title="Biotech Paper Puller", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LookupRequest(BaseModel):
    title: str = Field(min_length=5, max_length=500)
    first_author_last_name: str = Field(min_length=2, max_length=100)


def _sanitize_filename(raw_title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw_title).strip("_")
    if not cleaned:
        cleaned = "paper"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned[:140]


def _filename_from_content_disposition(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
    if not match:
        return None
    filename = match.group(1).strip()
    if filename:
        return _sanitize_filename(filename)
    return None


def _prune_download_cache() -> None:
    now = time.time()
    expired_tokens = [
        token for token, entry in DOWNLOAD_CACHE.items() if now - entry.created_at > DOWNLOAD_TTL_SECONDS
    ]
    for token in expired_tokens:
        DOWNLOAD_CACHE.pop(token, None)


def _register_download(url: str, title: str) -> str:
    _prune_download_cache()
    token = uuid.uuid4().hex
    DOWNLOAD_CACHE[token] = DownloadEntry(
        url=url, filename=_sanitize_filename(title or "paper"), created_at=time.time()
    )
    return token


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/lookup")
async def lookup(request: LookupRequest) -> dict:
    result = await discover_paper(
        requested_title=request.title.strip(),
        requested_first_author_last_name=request.first_author_last_name.strip(),
    )
    match = result.get("match")
    candidate_urls = result.get("candidate_urls", [])

    if not match and not candidate_urls:
        raise HTTPException(
            status_code=404,
            detail="No likely match found in the currently configured legal-access sources.",
        )

    download = None
    if candidate_urls:
        token = _register_download(candidate_urls[0], (match or {}).get("title") or request.title)
        download = {"token": token, "endpoint": f"/api/download/{token}"}

    return {
        "match": match,
        "candidate_urls": candidate_urls,
        "download_available": bool(download),
        "download": download,
        "warnings": result.get("warnings", []),
    }


@app.get("/api/download/{token}")
async def download(token: str) -> Response:
    _prune_download_cache()
    entry = DOWNLOAD_CACHE.get(token)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail="Download token not found or expired. Run lookup again to create a fresh token.",
        )

    timeout = httpx.Timeout(120.0, connect=20.0)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=timeout
        ) as client:
            response = await client.get(entry.url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch upstream full text: {exc}") from exc

    media_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    filename = _filename_from_content_disposition(response.headers.get("content-disposition")) or entry.filename
    return Response(
        content=response.content,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
