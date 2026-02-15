from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _normalize_pubmed_date(s: Optional[str], *, kind: str) -> Optional[str]:
    """
    Accepts YYYY, YYYY/MM/DD, YYYY-MM-DD and returns YYYY/MM/DD (or None).
    kind: "start" or "end" for year-only defaults.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}", s):
        return f"{s}/01/01" if kind == "start" else f"{s}/12/31"
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return s


def build_query_from_terms(terms: List[str]) -> str:
    cleaned = [_clean_text(t) for t in (terms or []) if _clean_text(t)]
    if not cleaned:
        raise ValueError("No terms provided")
    if len(cleaned) == 1:
        return f"\"{cleaned[0]}\""
    return " AND ".join(f"\"{t}\"" for t in cleaned)


def _http_get_json(url: str, *, timeout_s: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ResearchAgent-Scrapp/1.0 (E-utilities; contact: local)",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _parse_publication_year(pubdate: str) -> Optional[int]:
    m = YEAR_RE.search(pubdate or "")
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _format_journal_citation(doc: Dict[str, Any]) -> str:
    source = _clean_text(str(doc.get("source", "")))
    pubdate = _clean_text(str(doc.get("pubdate", "")))
    volume = _clean_text(str(doc.get("volume", "")))
    issue = _clean_text(str(doc.get("issue", "")))
    pages = _clean_text(str(doc.get("pages", "")))
    elocation = _clean_text(str(doc.get("elocationid", "")))

    parts: List[str] = []
    if source:
        parts.append(f"{source}.")
    if pubdate:
        parts.append(f"{pubdate};")
    vol_issue = ""
    if volume:
        vol_issue += volume
    if issue:
        vol_issue += f"({issue})" if volume else issue
    if vol_issue:
        parts.append(vol_issue + ":")
    if pages:
        parts.append(pages + ".")
    elif elocation:
        parts.append(elocation + ".")
    return _clean_text(" ".join(parts)).strip(";")


@dataclass
class PubMedSearchParams:
    terms: List[str]
    max_results: int = 10
    pub_date_start: Optional[str] = None
    pub_date_end: Optional[str] = None
    retstart: int = 0
    sort: str = "relevance"


def pubmed_search(params: PubMedSearchParams) -> Dict[str, Any]:
    query = build_query_from_terms(params.terms)
    mindate = _normalize_pubmed_date(params.pub_date_start, kind="start")
    maxdate = _normalize_pubmed_date(params.pub_date_end, kind="end")

    esearch_qs = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": str(int(params.max_results)),
        "retstart": str(int(params.retstart)),
        "sort": params.sort,
    }
    if mindate or maxdate:
        esearch_qs["datetype"] = "pdat"
        if mindate:
            esearch_qs["mindate"] = mindate
        if maxdate:
            esearch_qs["maxdate"] = maxdate

    esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(esearch_qs)
    esearch = _http_get_json(esearch_url, timeout_s=30)
    es = esearch.get("esearchresult") or {}

    count = int(es.get("count") or 0)
    idlist = es.get("idlist") or []
    uids = [str(x) for x in idlist if str(x)]

    retmax = max(1, int(params.max_results))
    pages_total = (count + retmax - 1) // retmax if count else 0

    next_retstart: Optional[int] = None
    if params.retstart + retmax < count:
        next_retstart = params.retstart + retmax

    results: List[Dict[str, Any]] = []
    if uids:
        esummary_qs = {
            "db": "pubmed",
            "id": ",".join(uids),
            "retmode": "json",
        }
        esummary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(esummary_qs)
        esummary = _http_get_json(esummary_url, timeout_s=30)
        summ = esummary.get("result") or {}

        for pmid in uids:
            doc = summ.get(pmid) or {}
            title = _clean_text(str(doc.get("title", "")))
            pubdate = _clean_text(str(doc.get("pubdate", "")))
            authors = doc.get("authors") or []
            author_str = ", ".join(
                _clean_text(str(a.get("name", ""))) for a in authors if isinstance(a, dict) and a.get("name")
            )

            journal_citation = _format_journal_citation(doc)
            results.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "authors": author_str,
                    "journal_citation": journal_citation,
                    "journal_citation_short": "",
                    "journal_citation_full": journal_citation,
                    "publication_year": _parse_publication_year(pubdate),
                    "publication_date_text": pubdate,
                    "snippet": "",
                }
            )

    next_page_url = ""
    if next_retstart is not None:
        esearch_qs_next = dict(esearch_qs)
        esearch_qs_next["retstart"] = str(next_retstart)
        next_page_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
            esearch_qs_next
        )

    return {
        "source": "eutils",
        "query": query,
        "total_results": count,
        "pages_total": pages_total,
        "next_page_url": next_page_url,
        "chunk_ids": uids,
        "results": results,
    }

