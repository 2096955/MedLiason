"""PubMed / NCBI E-utilities MCP server.

Provides tools for searching PubMed, retrieving article abstracts, and
fetching article metadata via NCBI E-utilities (esearch, efetch, esummary).

Rate limits: 3 req/s without API key, 10 req/s with NCBI_API_KEY.
"""

import os
import sys
import xml.etree.ElementTree as ET
import logging

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, structured_error_response
from mcp_servers._security import sanitize_query
from lifesci_common.constants import (
    PUBMED_ESEARCH_URL,
    PUBMED_EFETCH_URL,
    PUBMED_ESUMMARY_URL,
)

log = logging.getLogger(__name__)

mcp = FastMCP("pubmed")

NCBI_API_KEY = os.environ.get("NCBI_API_KEY")


def _base_params() -> dict:
    """Return base params shared across all NCBI requests."""
    params = {"retmode": "xml"}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return params


def _parse_id_list(xml_text: str) -> list[str]:
    """Extract PMIDs from an esearch XML response."""
    root = ET.fromstring(xml_text)
    id_list = root.find("IdList")
    if id_list is None:
        return []
    return [id_elem.text for id_elem in id_list.findall("Id") if id_elem.text]


def _parse_abstract(xml_text: str) -> dict:
    """Parse efetch XML for a single article abstract and title."""
    root = ET.fromstring(xml_text)
    article = root.find(".//PubmedArticle")
    if article is None:
        return {"error": "Article not found in response"}

    # Title
    title_elem = article.find(".//ArticleTitle")
    title = title_elem.text if title_elem is not None and title_elem.text else ""

    # Abstract text (may have multiple AbstractText elements)
    abstract_parts = []
    for abs_elem in article.findall(".//AbstractText"):
        label = abs_elem.get("Label", "")
        text = abs_elem.text or ""
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = "\n".join(abstract_parts)

    # PMID
    pmid_elem = article.find(".//PMID")
    pmid = pmid_elem.text if pmid_elem is not None else ""

    # Authors
    authors = []
    for author in article.findall(".//Author"):
        last = author.findtext("LastName", "")
        fore = author.findtext("ForeName", "")
        if last:
            authors.append(f"{last} {fore}".strip())

    # Journal
    journal_elem = article.find(".//Journal/Title")
    journal = journal_elem.text if journal_elem is not None else ""

    # Publication date
    pub_date = article.find(".//PubDate")
    year = pub_date.findtext("Year", "") if pub_date is not None else ""
    month = pub_date.findtext("Month", "") if pub_date is not None else ""

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "journal": journal,
        "pub_date": f"{year} {month}".strip(),
    }


def _parse_esummary(xml_text: str) -> dict:
    """Parse esummary XML for article metadata."""
    root = ET.fromstring(xml_text)
    doc_sum = root.find(".//DocSum")
    if doc_sum is None:
        return {"error": "No summary found in response"}

    result = {}
    id_elem = doc_sum.find("Id")
    result["pmid"] = id_elem.text if id_elem is not None else ""

    for item in doc_sum.findall("Item"):
        name = item.get("Name", "")
        if name in (
            "Title",
            "Source",
            "PubDate",
            "EPubDate",
            "Volume",
            "Issue",
            "Pages",
            "DOI",
            "PmcRefCount",
            "FullJournalName",
            "ESSN",
            "PubTypeList",
        ):
            if name == "PubTypeList":
                result["pub_types"] = [
                    sub.text for sub in item.findall("Item") if sub.text
                ]
            else:
                result[name.lower()] = item.text or ""

    # Author list
    author_list = doc_sum.find(".//Item[@Name='AuthorList']")
    if author_list is not None:
        result["authors"] = [
            a.text for a in author_list.findall("Item") if a.text
        ]

    return result


@mcp.tool()
async def search_pubmed(query: str, max_results: int = 10) -> dict:
    """Search PubMed for biomedical literature by keyword or MeSH term.

    Returns a list of PMIDs matching the query, along with total count.
    Use get_article_abstract() or get_article_metadata() on individual PMIDs
    for detailed information.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = _base_params()
    params.update({
        "db": "pubmed",
        "term": safe_query,
        "retmax": str(min(max_results, 100)),
        "sort": "relevance",
    })

    try:
        response = await resilient_get(PUBMED_ESEARCH_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "pubmed", "search_pubmed"), "results": []}

    if response.status_code != 200:
        return {"error": f"PubMed API returned {response.status_code}", "results": []}

    xml_text = response.text

    try:
        pmids = _parse_id_list(xml_text)
    except ET.ParseError as exc:
        log.error("Failed to parse esearch ID list: %s", exc)
        return {"error": "Malformed XML response from PubMed", "results": []}

    # Extract total count
    total_count = 0
    try:
        root = ET.fromstring(xml_text)
        count_elem = root.find("Count")
        total_count = int(count_elem.text) if count_elem is not None and count_elem.text else 0
    except ET.ParseError as exc:
        log.error("Failed to parse esearch count: %s", exc)

    return {
        "success": True,
        "query": safe_query,
        "total_count": total_count,
        "returned_count": len(pmids),
        "pmids": pmids,
    }


@mcp.tool()
async def get_article_abstract(pmid: str) -> dict:
    """Retrieve the full abstract, title, and author list for a PubMed article.

    Requires a valid PMID (PubMed identifier).
    """
    if not pmid or not pmid.strip().isdigit():
        return {"error": "Invalid PMID: must be a numeric string"}

    params = _base_params()
    params.update({
        "db": "pubmed",
        "id": pmid.strip(),
        "rettype": "abstract",
    })

    try:
        response = await resilient_get(PUBMED_EFETCH_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return structured_error_response(exc, "pubmed", "get_article_abstract")

    if response.status_code != 200:
        return {"error": f"PubMed efetch returned {response.status_code}"}

    return {**_parse_abstract(response.text), "success": True}


@mcp.tool()
async def get_article_metadata(pmid: str) -> dict:
    """Retrieve bibliographic metadata for a PubMed article (journal, DOI, dates, etc.).

    Uses NCBI esummary for compact metadata. Requires a valid PMID.
    """
    if not pmid or not pmid.strip().isdigit():
        return {"error": "Invalid PMID: must be a numeric string"}

    params = _base_params()
    params.update({
        "db": "pubmed",
        "id": pmid.strip(),
    })

    try:
        response = await resilient_get(PUBMED_ESUMMARY_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return structured_error_response(exc, "pubmed", "get_article_metadata")

    if response.status_code != 200:
        return {"error": f"PubMed esummary returned {response.status_code}"}

    return {**_parse_esummary(response.text), "success": True}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9001)
