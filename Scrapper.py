"""
PubMed scraper using Playwright.

What it does:
- Accepts one or more search terms/phrases
- Converts them to PubMed "structured" format:  "term1"+"term2"+"term phrase"
- Opens PubMed home (tries local PubmedMain.html first; falls back to live site)
- Runs the search
- Saves:
  - the resulting HTML page (similar to your PubmedAfterSearch.html snapshot)
  - structured JSON of results (PMID/title/citation/snippet/url)

Requirements:
  pip install playwright
  playwright install
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WHITESPACE_RE = re.compile(r"\s+")
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")


def _clean_text(s: str) -> str:
    return WHITESPACE_RE.sub(" ", (s or "").strip())


def build_pubmed_structured_query(terms: list[str]) -> str:
    cleaned = [t.strip() for t in terms if t and t.strip()]
    if not cleaned:
        raise ValueError("No terms provided. Provide at least one word or phrase.")
    return "+".join(f"\"{t}\"" for t in cleaned)


def parse_terms_from_cli(args: argparse.Namespace) -> list[str]:
    # Preferred: --terms older alzheimer "factor analysis"
    if args.terms:
        return list(args.terms)

    # Alternate: --query 'older alzheimer "factor analysis"'
    if args.query:
        # shlex preserves quoted phrases
        return shlex.split(args.query)

    # Last resort: positional remainder
    if args.positional:
        return list(args.positional)

    raise ValueError("Provide search terms via --terms or --query.")


def safe_inner_text(locator) -> str:
    try:
        if locator.count() == 0:
            return ""
        return locator.first.inner_text()
    except Exception:
        return ""


def safe_attr(locator, name: str) -> str:
    try:
        if locator.count() == 0:
            return ""
        return locator.first.get_attribute(name) or ""
    except Exception:
        return ""


def to_full_pubmed_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"https://pubmed.ncbi.nlm.nih.gov{href}"
    return href


def build_pubmed_search_url(structured_query: str) -> str:
    # Keep '+' as-is (PubMed uses + heavily); encode quotes and other special chars.
    encoded = quote(structured_query, safe="+")
    return f"https://pubmed.ncbi.nlm.nih.gov/?term={encoded}"


def build_date_publication_clause(
    pub_date_start: Optional[str],
    pub_date_end: Optional[str],
) -> str:
    """
    Build a PubMed 'Date - Publication' range clause that can be embedded into the term query.
    Uses wide defaults if only one side is provided.
    """
    start = (pub_date_start or "").strip()
    end = (pub_date_end or "").strip()
    if not start and not end:
        return ""
    if not start:
        start = "1800/01/01"
    if not end:
        end = "3000/12/31"
    return f"(\"{start}\"[Date - Publication] : \"{end}\"[Date - Publication])"


def parse_publication_year(citation_text: str) -> Optional[int]:
    m = YEAR_RE.search(citation_text or "")
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def parse_publication_date_text(citation_text: str) -> str:
    """
    Best-effort extraction of the date-ish part from PubMed's citation string.
    Examples:
      "Brain Imaging Behav. 2012 Dec;6(4):..." -> "2012 Dec"
      "JAMA Netw Open. 2025 Jun 2;8(6):..."   -> "2025 Jun 2"
      "Alzheimer Dis Assoc Disord. 2024 Jul-Sep 01;..." -> "2024 Jul-Sep 01"
    """
    s = citation_text or ""
    # Grab year plus up to the next 2 tokens (month/season and optional day).
    m = re.search(r"\b((?:18|19|20)\d{2})(?:\s+([A-Za-z]{3,}(?:-[A-Za-z]{3,})?))?(?:\s+(\d{1,2}))?", s)
    if not m:
        return ""
    year, token1, token2 = m.group(1), m.group(2), m.group(3)
    parts = [year]
    if token1:
        parts.append(token1)
    if token2:
        parts.append(token2)
    return " ".join(parts)


def scrape_pubmed_results(page, max_results: Optional[int] = None) -> dict[str, Any]:
    # Metadata
    total_results_text = _clean_text(safe_inner_text(page.locator(".results-amount .value")))
    total_results = int(total_results_text) if total_results_text.isdigit() else None

    pages_total_text = _clean_text(safe_inner_text(page.locator(".page-number-wrapper .of-total-pages")))
    pages_total = None
    if pages_total_text.lower().startswith("of "):
        maybe = pages_total_text[3:].strip()
        pages_total = int(maybe) if maybe.isdigit() else None

    chunk = page.locator("div.search-results-chunk.results-chunk").first
    next_page_url = safe_attr(chunk, "data-next-page-url")
    chunk_ids = safe_attr(chunk, "data-chunk-ids")
    pages_total_from_chunk = safe_attr(chunk, "data-pages-amount")
    if pages_total is None and pages_total_from_chunk.isdigit():
        pages_total = int(pages_total_from_chunk)

    # Results
    articles_loc = page.locator("article.full-docsum")
    n = articles_loc.count()
    if max_results is not None:
        n = min(n, max_results)

    results: list[dict[str, Any]] = []
    for i in range(n):
        art = articles_loc.nth(i)

        title_loc = art.locator("a.docsum-title")
        title = _clean_text(safe_inner_text(title_loc))
        href = safe_attr(title_loc, "href")
        url = to_full_pubmed_url(href)
        article_id = safe_attr(title_loc, "data-article-id")

        pmid = _clean_text(safe_inner_text(art.locator("span.docsum-pmid"))) or article_id

        authors_full = _clean_text(safe_inner_text(art.locator("span.docsum-authors.full-authors")))
        authors_short = _clean_text(safe_inner_text(art.locator("span.docsum-authors.short-authors")))
        # Only output one field: prefer full authors, fallback to short if full isn't present.
        authors = authors_full or authors_short
        journal_citation_full = _clean_text(
            safe_inner_text(art.locator("span.docsum-journal-citation.full-journal-citation"))
        )
        journal_citation_short = _clean_text(
            safe_inner_text(art.locator("span.docsum-journal-citation.short-journal-citation"))
        )
        journal_citation = journal_citation_full or journal_citation_short

        publication_year = (
            parse_publication_year(journal_citation_short)
            or parse_publication_year(journal_citation_full)
            or parse_publication_year(journal_citation)
        )
        publication_date_text = (
            parse_publication_date_text(journal_citation_full)
            or parse_publication_date_text(journal_citation_short)
        )

        snippet = _clean_text(
            safe_inner_text(art.locator(".docsum-snippet .full-view-snippet"))
            or safe_inner_text(art.locator(".docsum-snippet .short-view-snippet"))
        )

        results.append(
            {
                "pmid": pmid,
                "title": title,
                "url": url,
                "authors": authors,
                "journal_citation": journal_citation,
                "journal_citation_short": journal_citation_short,
                "journal_citation_full": journal_citation_full,
                "publication_year": publication_year,
                "publication_date_text": publication_date_text,
                "snippet": snippet,
            }
        )

    return {
        "total_results": total_results,
        "pages_total": pages_total,
        "next_page_url": next_page_url,
        "chunk_ids": [c.strip() for c in chunk_ids.split(",") if c.strip()] if chunk_ids else [],
        "results": results,
    }


def run_pubmed_scrape(
    *,
    terms: list[str],
    headless: bool = True,
    slowmo: int = 0,
    step_delay: int = 0,
    max_results: int = 10,
    save_html: Optional[str] = "PubmedAfterSearch_generated.html",
    output_json: Optional[str] = "pubmed_results.json",
    source: str = "live",
    local_home: Optional[str] = None,
    pub_date_start: Optional[str] = None,
    pub_date_end: Optional[str] = None,
    debug_html_on_error: Optional[str] = "pubmed_debug_last.html",
    user_data_dir: Optional[str] = None,
    user_agent: Optional[str] = None,
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
    chromium_channel: Optional[str] = None,
) -> dict[str, Any]:
    """
    Programmatic API for this scraper (used by the chat agent).
    Returns the structured JSON dict. Optionally writes HTML/JSON to disk.
    """
    structured_query = build_pubmed_structured_query(terms)

    script_dir = Path(__file__).resolve().parent
    local_home_path = Path(local_home).expanduser().resolve() if local_home else (script_dir / "PubmedMain.html")
    local_home_url = local_home_path.as_uri() if local_home_path.exists() else ""

    save_html_path = Path(save_html).expanduser() if save_html else None
    out_json_path = Path(output_json).expanduser() if output_json else None

    with sync_playwright() as p:
        step_delay_ms = 0 if headless else max(0, int(step_delay))
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        if user_data_dir:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                slow_mo=max(0, int(slowmo)),
                channel=chromium_channel,
                args=launch_args,
                locale=locale,
                timezone_id=timezone_id,
                user_agent=user_agent,
            )
            page = context.new_page()
            browser = None
        else:
            browser = p.chromium.launch(
                headless=headless,
                slow_mo=max(0, int(slowmo)),
                channel=chromium_channel,
                args=launch_args,
            )
            context = browser.new_context(
                locale=locale,
                timezone_id=timezone_id,
                user_agent=user_agent,
            )
            page = context.new_page()

        def _dismiss_common_popups() -> None:
            for sel in [
                "button#onetrust-accept-btn-handler",
                "button[aria-label='Close Clipboard and Search History not available warning banner']",
                "button.close-banner-button",
                "button.ncbi-close-button",
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=1000)
                except Exception:
                    pass

        def _run_advanced_search_with_publication_date() -> None:
            try:
                adv_link = page.locator("a.adv-search-link[href='/advanced/']").first
                if adv_link.count() > 0:
                    adv_link.click(timeout=3000)
                else:
                    raise RuntimeError("advanced link not found")
            except Exception:
                page.goto("https://pubmed.ncbi.nlm.nih.gov/advanced/", wait_until="domcontentloaded", timeout=30_000)

            _dismiss_common_popups()
            # Sometimes the container exists but isn't considered "visible" (overlay/layout); attached is enough.
            page.wait_for_selector("#advanced-search-page-container", state="attached", timeout=60_000)

            page.wait_for_selector("select#field-selector", timeout=30_000)
            try:
                page.select_option("#field-selector", label="Date - Publication")
            except Exception:
                page.select_option("#field-selector", value="Date - Publication")
            if step_delay_ms:
                page.wait_for_timeout(step_delay_ms)

            if pub_date_start:
                page.locator("#start-date-input").fill(pub_date_start)
                if step_delay_ms:
                    page.wait_for_timeout(step_delay_ms)
            if pub_date_end:
                page.locator("#end-date-input").fill(pub_date_end)
                if step_delay_ms:
                    page.wait_for_timeout(step_delay_ms)

            page.locator("button.add-button").click()
            page.wait_for_timeout(300 if headless else max(300, step_delay_ms))

            query_box = page.locator("textarea#query-box-input[name='term']").first
            query_box.wait_for(state="attached", timeout=30_000)
            existing_query = (query_box.input_value() or "").strip()
            if existing_query:
                combined_query = f"({existing_query}) AND ({structured_query})"
            else:
                combined_query = structured_query
            query_box.fill(combined_query)
            if step_delay_ms:
                page.wait_for_timeout(step_delay_ms)

            page.locator("button.search-btn[data-ga-action='search_button']").click()
            if step_delay_ms:
                page.wait_for_timeout(step_delay_ms)

        opened = False
        if source == "live":
            page.goto("https://pubmed.ncbi.nlm.nih.gov/", wait_until="domcontentloaded", timeout=30_000)
            _dismiss_common_popups()
            opened = True

        if not opened and source == "local":
            if not local_home_url:
                raise FileNotFoundError(f"Local home not found at: {local_home_path}")
            page.goto(local_home_url, wait_until="domcontentloaded", timeout=12_000)
            page.wait_for_selector("form#search-form input#id_term[name='term']", state="attached", timeout=8_000)
            opened = True

        if not opened and source == "auto":
            if local_home_url:
                try:
                    page.goto(local_home_url, wait_until="domcontentloaded", timeout=12_000)
                    page.wait_for_selector("form#search-form input#id_term[name='term']", state="attached", timeout=8_000)
                    opened = True
                except PlaywrightTimeoutError:
                    opened = False

            if not opened:
                page.goto("https://pubmed.ncbi.nlm.nih.gov/", wait_until="domcontentloaded", timeout=30_000)
                _dismiss_common_popups()
                opened = True

        performed_search = False
        if pub_date_start or pub_date_end:
            # Advanced Search UI can be flaky in headless due to slow loads / bot mitigation.
            # Try it first; if it fails, fall back to a direct URL with an embedded date clause.
            try:
                _run_advanced_search_with_publication_date()
                performed_search = True
            except Exception:
                date_clause = build_date_publication_clause(pub_date_start, pub_date_end)
                combined = f"({date_clause}) AND ({structured_query})" if date_clause else structured_query
                results_url = build_pubmed_search_url(combined)
                page.goto(results_url, wait_until="domcontentloaded", timeout=60_000)
                performed_search = True

        if not performed_search:
            did_ui_search = False
            try:
                search_input = page.locator("form#search-form input[name='term']").first
                search_input.wait_for(state="attached", timeout=8_000)
                _dismiss_common_popups()
                search_input.click(timeout=2_000)
                search_input.fill(structured_query, timeout=5_000)
                search_input.press("Enter", timeout=5_000)
                did_ui_search = True
            except Exception:
                did_ui_search = False

            if not did_ui_search:
                results_url = build_pubmed_search_url(structured_query)
                page.goto(results_url, wait_until="domcontentloaded", timeout=30_000)

        try:
            # PubMed can render elements in ways Playwright considers not "visible" yet.
            # Attached is sufficient for scraping DOM text/attrs.
            page.wait_for_selector("#search-results", state="attached", timeout=60_000)
            page.wait_for_selector("article.full-docsum", state="attached", timeout=60_000)
        except PlaywrightTimeoutError:
            if debug_html_on_error:
                try:
                    Path(debug_html_on_error).write_text(page.content(), encoding="utf-8")
                except Exception:
                    pass
            raise

        if save_html_path:
            html = page.content()
            save_html_path.write_text(html, encoding="utf-8")

        data = scrape_pubmed_results(page, max_results=max_results)
        if out_json_path:
            out_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        if browser is not None:
            browser.close()
        else:
            context.close()
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape PubMed search results using Playwright.")
    parser.add_argument("--terms", nargs="*", help='Terms/phrases, e.g. --terms older alzheimer "factor analysis"')
    parser.add_argument("--query", help='Query string to split like a shell, e.g. --query \'older alzheimer "factor analysis"\'')
    parser.add_argument("positional", nargs="*", help=argparse.SUPPRESS)

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (invisible). Default is headed so you can watch Playwright actions.",
    )
    parser.add_argument(
        "--slowmo",
        type=int,
        default=350,
        help="Slow down Playwright actions (ms). Useful to watch what happens. Set 0 to disable.",
    )
    parser.add_argument(
        "--step-delay",
        type=int,
        default=600,
        help="Extra pause between major steps (ms). Set 0 to disable. Auto-disabled in headless mode.",
    )
    parser.add_argument("--max-results", type=int, default=10, help="How many results to extract from the first page.")

    parser.add_argument("--save-html", default="PubmedAfterSearch_generated.html", help="Path to save the resulting HTML.")
    parser.add_argument("--output", default="pubmed_results.json", help="Path to save structured results JSON.")

    parser.add_argument(
        "--source",
        choices=["auto", "local", "live"],
        default="live",
        help=(
            "Where to open PubMed from. "
            "'live' forces https://pubmed.ncbi.nlm.nih.gov/ (default); "
            "'auto' tries local PubmedMain.html then falls back to live; "
            "'local' forces local file; "
            "'live' forces https://pubmed.ncbi.nlm.nih.gov/."
        ),
    )
    parser.add_argument(
        "--local-home",
        default=None,
        help="Optional path to local PubmedMain.html. If missing, uses PubmedMain.html next to this script.",
    )
    parser.add_argument(
        "--pub-date-start",
        default=None,
        help="If set, uses Advanced Search and sets Publication Date start (YYYY/MM/DD), e.g. 2012/01/01",
    )
    parser.add_argument(
        "--pub-date-end",
        default=None,
        help="If set, uses Advanced Search and sets Publication Date end (YYYY/MM/DD), e.g. 2012/12/31",
    )
    args = parser.parse_args()

    try:
        terms = parse_terms_from_cli(args)
    except Exception as e:
        print(f"Input error: {e}", file=sys.stderr)
        return 2

    data = run_pubmed_scrape(
        terms=terms,
        headless=bool(args.headless),
        slowmo=int(args.slowmo),
        step_delay=int(args.step_delay),
        max_results=int(args.max_results),
        save_html=str(args.save_html) if args.save_html else None,
        output_json=str(args.output) if args.output else None,
        source=str(args.source),
        local_home=str(args.local_home) if args.local_home else None,
        pub_date_start=str(args.pub_date_start) if args.pub_date_start else None,
        pub_date_end=str(args.pub_date_end) if args.pub_date_end else None,
    )
    print(json.dumps(data, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
