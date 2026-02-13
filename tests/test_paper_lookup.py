from app.paper_lookup import (
    dedupe_urls,
    extract_crossref_pdf_links,
    extract_europe_pmc_pdf_url,
    normalize_text,
    pick_best_crossref_item,
)


def test_normalize_text_removes_punctuation_and_extra_spaces() -> None:
    assert normalize_text("  CRISPR-Cas9, in   Biotech! ") == "crispr cas9 in biotech"


def test_pick_best_crossref_item_uses_title_and_author() -> None:
    items = [
        {
            "title": ["A study that should not match"],
            "author": [{"family": "Wrong"}],
            "DOI": "10.1000/nope",
        },
        {
            "title": ["Editing CAR-T cells with CRISPR-Cas9 improves persistence"],
            "author": [{"family": "Miller"}],
            "DOI": "10.1000/match",
        },
    ]

    best_item, score = pick_best_crossref_item(
        items=items,
        requested_title="Editing CAR-T cells with CRISPR Cas9 improves persistence",
        requested_first_author_last_name="Miller",
    )

    assert best_item is not None
    assert best_item.get("DOI") == "10.1000/match"
    assert score > 0.8


def test_extract_crossref_pdf_links_filters_non_pdf_links() -> None:
    item = {
        "link": [
            {"URL": "https://example.org/file.pdf", "content-type": "application/pdf"},
            {"URL": "https://example.org/xml", "content-type": "text/xml"},
            {"URL": "https://example.org/dup.pdf", "content-type": "application/pdf"},
            {"URL": "https://example.org/dup.pdf", "content-type": "application/pdf"},
        ]
    }
    assert extract_crossref_pdf_links(item) == [
        "https://example.org/file.pdf",
        "https://example.org/dup.pdf",
    ]


def test_extract_europe_pmc_pdf_url_prefers_explicit_pdf_url() -> None:
    result = {
        "fullTextUrlList": {
            "fullTextUrl": [
                {"url": "https://example.org/html", "documentStyle": "html"},
                {"url": "https://example.org/paper.pdf", "documentStyle": "pdf"},
            ]
        },
        "pmcid": "PMC9999999",
    }
    assert extract_europe_pmc_pdf_url(result) == "https://example.org/paper.pdf"


def test_dedupe_urls_keeps_only_http_and_https() -> None:
    urls = [
        "https://example.org/a.pdf",
        "https://example.org/a.pdf",
        "http://example.org/b.pdf",
        "ftp://example.org/not-allowed.pdf",
        "javascript:alert(1)",
    ]
    assert dedupe_urls(urls) == ["https://example.org/a.pdf", "http://example.org/b.pdf"]
