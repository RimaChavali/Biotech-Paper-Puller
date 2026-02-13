from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

import httpx

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_URL_TEMPLATE = "https://api.unpaywall.org/v2/{doi}"
USER_AGENT = "Biotech-Paper-Puller/0.1"


def normalize_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_last_name(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def extract_crossref_pdf_links(item: dict[str, Any]) -> list[str]:
    links = []
    for link in item.get("link", []) or []:
        url = (link or {}).get("URL", "")
        content_type = ((link or {}).get("content-type") or "").lower()
        if url and "pdf" in content_type:
            links.append(url)
    return dedupe_urls(links)


def extract_europe_pmc_pdf_url(result: dict[str, Any]) -> str | None:
    full_text_urls = (((result.get("fullTextUrlList") or {}).get("fullTextUrl")) or [])
    for entry in full_text_urls:
        url = (entry or {}).get("url", "")
        style = ((entry or {}).get("documentStyle") or "").lower()
        if url and (style == "pdf" or url.lower().endswith(".pdf")):
            return url

    pmcid = (result.get("pmcid") or "").strip()
    if pmcid:
        return f"https://europepmc.org/articles/{pmcid}?pdf=render"
    return None


def _extract_year_crossref(item: dict[str, Any]) -> str | None:
    issued = item.get("issued") or {}
    date_parts = issued.get("date-parts") or []
    if not date_parts or not date_parts[0]:
        return None
    return str(date_parts[0][0])


def _format_crossref_authors(item: dict[str, Any]) -> tuple[list[str], str | None]:
    formatted_authors: list[str] = []
    first_author_last_name: str | None = None

    authors = item.get("author") or []
    for index, author in enumerate(authors):
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        if index == 0 and family:
            first_author_last_name = family
        name = " ".join(part for part in [given, family] if part).strip()
        if name:
            formatted_authors.append(name)
    return formatted_authors, first_author_last_name


def _score_crossref_item(
    item: dict[str, Any], requested_title: str, requested_first_author_last_name: str
) -> float:
    titles = item.get("title") or []
    candidate_title = titles[0] if titles else ""
    score = title_similarity(candidate_title, requested_title)

    target_last_name = normalize_last_name(requested_first_author_last_name)
    if not target_last_name:
        return score

    authors = item.get("author") or []
    if not authors:
        return score - 0.05

    first_author_last_name = normalize_last_name((authors[0] or {}).get("family", ""))
    if first_author_last_name and first_author_last_name == target_last_name:
        return score + 0.30

    for author in authors:
        if normalize_last_name((author or {}).get("family", "")) == target_last_name:
            return score + 0.10
    return score


def pick_best_crossref_item(
    items: list[dict[str, Any]], requested_title: str, requested_first_author_last_name: str
) -> tuple[dict[str, Any] | None, float]:
    best_item: dict[str, Any] | None = None
    best_score = -1.0

    for item in items:
        score = _score_crossref_item(item, requested_title, requested_first_author_last_name)
        if score > best_score:
            best_item = item
            best_score = score

    if best_score < 0.45:
        return None, 0.0
    return best_item, best_score


def _score_europe_pmc_result(
    result: dict[str, Any], requested_title: str, requested_first_author_last_name: str
) -> float:
    score = title_similarity(result.get("title", ""), requested_title)
    target_last_name = normalize_last_name(requested_first_author_last_name)
    if not target_last_name:
        return score

    first_author_raw = str(result.get("firstAuthor", "")).strip()
    first_author_last_name = first_author_raw.split(" ")[0]
    first_author = normalize_last_name(first_author_last_name)
    if first_author and first_author == target_last_name:
        return score + 0.30
    return score


def pick_best_europe_pmc_result(
    results: list[dict[str, Any]], requested_title: str, requested_first_author_last_name: str
) -> tuple[dict[str, Any] | None, float]:
    best_result: dict[str, Any] | None = None
    best_score = -1.0

    for result in results:
        score = _score_europe_pmc_result(result, requested_title, requested_first_author_last_name)
        if score > best_score:
            best_result = result
            best_score = score

    if best_score < 0.45:
        return None, 0.0
    return best_result, best_score


def dedupe_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        cleaned = parsed.geturl()
        if cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _build_crossref_match(item: dict[str, Any], score: float) -> dict[str, Any]:
    title_candidates = item.get("title") or []
    title = title_candidates[0] if title_candidates else ""
    authors, first_author_last_name = _format_crossref_authors(item)
    return {
        "source": "crossref",
        "title": title,
        "doi": item.get("DOI"),
        "publisher": item.get("publisher"),
        "year": _extract_year_crossref(item),
        "authors": authors,
        "first_author_last_name": first_author_last_name,
        "score": round(score, 3),
        "pdf_links": extract_crossref_pdf_links(item),
    }


def _build_europe_pmc_match(result: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "source": "europe_pmc",
        "title": result.get("title"),
        "doi": result.get("doi"),
        "journal": result.get("journalTitle"),
        "year": result.get("pubYear"),
        "authors": [result.get("authorString")] if result.get("authorString") else [],
        "first_author_last_name": result.get("firstAuthor"),
        "score": round(score, 3),
        "pdf_url": extract_europe_pmc_pdf_url(result),
        "is_open_access": str(result.get("isOpenAccess", "")).lower() == "y",
    }


async def fetch_crossref_match(
    client: httpx.AsyncClient, requested_title: str, requested_first_author_last_name: str
) -> dict[str, Any] | None:
    try:
        response = await client.get(
            CROSSREF_WORKS_URL,
            params={"query.title": requested_title, "rows": 15},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    items = ((response.json() or {}).get("message") or {}).get("items") or []
    best_item, score = pick_best_crossref_item(
        items, requested_title, requested_first_author_last_name
    )
    if not best_item:
        return None
    return _build_crossref_match(best_item, score)


async def fetch_europe_pmc_match(
    client: httpx.AsyncClient, requested_title: str, requested_first_author_last_name: str
) -> dict[str, Any] | None:
    query = f'TITLE:"{requested_title}" AND AUTH:"{requested_first_author_last_name}"'
    try:
        response = await client.get(
            EUROPE_PMC_SEARCH_URL,
            params={"query": query, "format": "json", "pageSize": 15},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    results = (((response.json() or {}).get("resultList") or {}).get("result") or [])
    best_result, score = pick_best_europe_pmc_result(
        results, requested_title, requested_first_author_last_name
    )
    if not best_result:
        return None
    return _build_europe_pmc_match(best_result, score)


async def fetch_unpaywall_pdf_url(
    client: httpx.AsyncClient, doi: str, unpaywall_email: str
) -> str | None:
    if not doi or not unpaywall_email:
        return None

    try:
        response = await client.get(
            UNPAYWALL_URL_TEMPLATE.format(doi=doi),
            params={"email": unpaywall_email},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    payload = response.json() or {}
    best_location = payload.get("best_oa_location") or {}
    direct = best_location.get("url_for_pdf") or best_location.get("url")
    if direct:
        return direct

    for location in payload.get("oa_locations", []) or []:
        url = (location or {}).get("url_for_pdf") or (location or {}).get("url")
        if url:
            return url
    return None


def _pick_primary_match(
    crossref_match: dict[str, Any] | None, europe_pmc_match: dict[str, Any] | None
) -> dict[str, Any] | None:
    if crossref_match and europe_pmc_match:
        if europe_pmc_match.get("score", 0.0) > crossref_match.get("score", 0.0):
            return europe_pmc_match
        return crossref_match
    return crossref_match or europe_pmc_match


async def discover_paper(
    requested_title: str, requested_first_author_last_name: str
) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(20.0, connect=10.0)
    unpaywall_email = os.getenv("UNPAYWALL_EMAIL", "").strip()

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        crossref_match = await fetch_crossref_match(
            client, requested_title, requested_first_author_last_name
        )
        europe_pmc_match = await fetch_europe_pmc_match(
            client, requested_title, requested_first_author_last_name
        )

        primary_match = _pick_primary_match(crossref_match, europe_pmc_match)
        doi = (primary_match or {}).get("doi") or (crossref_match or {}).get("doi")

        candidate_urls: list[str] = []
        if europe_pmc_match and europe_pmc_match.get("pdf_url"):
            candidate_urls.append(europe_pmc_match["pdf_url"])
        if crossref_match:
            candidate_urls.extend(crossref_match.get("pdf_links", []))
        if doi and unpaywall_email:
            unpaywall_url = await fetch_unpaywall_pdf_url(client, doi, unpaywall_email)
            if unpaywall_url:
                candidate_urls.append(unpaywall_url)

        warnings: list[str] = []
        if doi and not unpaywall_email:
            warnings.append(
                "UNPAYWALL_EMAIL is not set. Add it to increase legal full-text coverage."
            )

        return {
            "match": primary_match,
            "candidate_urls": dedupe_urls(candidate_urls),
            "warnings": warnings,
            "crossref_match": crossref_match,
            "europe_pmc_match": europe_pmc_match,
        }
