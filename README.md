# Biotech Paper Puller

Simple web app for biotech investors to look up a publication using:
- full paper title
- first author's last name

The backend then attempts to find a legal full-text URL (open-access sources) and gives the user a one-click download endpoint.

## What this MVP does

- Minimal UI/UX (single page form + result card)
- API endpoint to resolve paper metadata:
  - Crossref lookup by title
  - Europe PMC lookup by title + author
  - Optional Unpaywall enrichment using DOI
- One-click backend download endpoint using short-lived tokens
- Basic matching tests

## Legal/ethical scope

This project only surfaces legal/open-access full-text sources and **does not bypass paywalls or publisher access controls**.

## Quick start

### 1) Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) (Optional) improve coverage with Unpaywall

Set an email address for Unpaywall API access:

```bash
export UNPAYWALL_EMAIL="you@example.com"
```

### 4) Run the app

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## API

### POST `/api/lookup`

Request:

```json
{
  "title": "Full paper title",
  "first_author_last_name": "Smith"
}
```

Response includes:
- best metadata match
- candidate full-text URLs found
- short-lived download endpoint (`/api/download/{token}`)

### GET `/api/download/{token}`

Downloads the resolved full text file (when available).

## Run tests

```bash
pytest -q
```
