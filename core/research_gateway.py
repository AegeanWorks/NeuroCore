"""Allowlisted research sources with SSRF protection, caching and provenance.

Every request leaves through one of three fixed adapters (Wikipedia, arXiv, PubMed) and every
URL, including each redirect hop, must stay on that adapter's hosts and resolve to a public
address. Responses are size- and time-bounded, cached for a day, and results carry the source,
retrieval time, licence and a content hash so a caller can cite them. Failures never fall back
to an unlisted service (ADR-049).
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from core.paths import data_path

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 10.0
CACHE_TTL_SECONDS = 24 * 3600
MAX_REDIRECTS = 3
MAX_QUERY_CHARS = 500
MAX_CACHE_ENTRIES = 500
MAX_LIMIT = 20
USER_AGENT = "NeuroCore/1.0 (+https://github.com/AegeanWorks/NeuroCore)"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

SOURCES: Dict[str, Dict[str, Any]] = {
    "wikipedia": {
        "label": "Wikipedia",
        "license": "CC BY-SA 4.0",
        "hosts": ("en.wikipedia.org",),
        "endpoint": "https://en.wikipedia.org/w/api.php",
    },
    "arxiv": {
        "label": "arXiv",
        "license": "arXiv author licence (per paper)",
        "hosts": ("export.arxiv.org",),
        "endpoint": "https://export.arxiv.org/api/query",
    },
    "pubmed": {
        "label": "PubMed",
        "license": "NLM terms of use",
        "hosts": ("eutils.ncbi.nlm.nih.gov",),
        "endpoint": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
    },
}


class SourcePolicyError(ValueError):
    """A request violates the source allowlist or URL safety policy."""


class SourceUnavailableError(RuntimeError):
    """The upstream source failed and no cached result can be served."""


def sources() -> List[Dict[str, Any]]:
    return [
        {"id": key, "label": value["label"], "license": value["license"]}
        for key, value in SOURCES.items()
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_markup(text: str) -> str:
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", str(text or ""))).strip()


def _validate_url(url: str, allowed_hosts: tuple, resolver: Callable = socket.getaddrinfo) -> str:
    """Require HTTPS, an allowlisted host and a public address for every hop."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SourcePolicyError("only https URLs are allowed")
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts:
        raise SourcePolicyError(f"host {host!r} is not an allowlisted research source")
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourcePolicyError(f"could not resolve {host!r}: {exc}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SourcePolicyError(f"invalid resolved address {address!r}") from exc
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise SourcePolicyError(f"{host!r} resolves to a non-public address")
    return url


async def _safe_get(url: str, allowed_hosts: tuple) -> tuple:
    """Fetch one page, validating the start URL and every redirect hop."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/atom+xml, */*"}
    current = url
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False,
                                 headers=headers) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            # DNS resolution is blocking; keep it off the event loop.
            await asyncio.to_thread(_validate_url, current, allowed_hosts)
            async with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise SourceUnavailableError("redirect without a location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise SourceUnavailableError(
                        f"{urlparse(current).hostname} returned HTTP {response.status_code}"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise SourcePolicyError("response exceeds the 2 MB limit")
                return current, bytes(body)
    raise SourcePolicyError("too many redirects")


def build_request(source: str, query: str, limit: int) -> List[str]:
    if source not in SOURCES:
        raise SourcePolicyError(f"unknown source {source!r}")
    encoded = quote_plus(query)
    if source == "wikipedia":
        return [
            f"{SOURCES[source]['endpoint']}?action=query&list=search&srsearch={encoded}"
            f"&format=json&srlimit={limit}&utf8=1"
        ]
    if source == "arxiv":
        return [
            f"{SOURCES[source]['endpoint']}?search_query=all:{encoded}&start=0"
            f"&max_results={limit}"
        ]
    return [
        f"{SOURCES[source]['endpoint']}esearch.fcgi?db=pubmed&term={encoded}"
        f"&retmode=json&retmax={limit}",
    ]


def parse_wikipedia(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    rows = ((payload or {}).get("query") or {}).get("search") or []
    items = []
    for row in rows[:limit]:
        items.append({
            "title": str(row.get("title") or ""),
            "summary": _strip_markup(row.get("snippet")),
            "url": f"https://en.wikipedia.org/?curid={row.get('pageid')}",
            "authors": [],
            "published": None,
            "doi": None,
        })
    return items


def parse_arxiv(payload: str, limit: int) -> List[Dict[str, Any]]:
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(payload)
    items = []
    for entry in root.findall("atom:entry", namespace)[:limit]:
        title = _strip_markup("".join(entry.findtext("atom:title", "", namespace)))
        summary = _strip_markup("".join(entry.findtext("atom:summary", "", namespace)))
        authors = [
            _strip_markup("".join(author.findtext("atom:name", "", namespace)))
            for author in entry.findall("atom:author", namespace)
        ]
        items.append({
            "title": title,
            "summary": summary,
            "url": (entry.findtext("atom:id", "", namespace) or "").strip(),
            "authors": [name for name in authors if name],
            "published": (entry.findtext("atom:published", "", namespace) or "")[:10] or None,
            "doi": None,
        })
    return items


def parse_pubmed(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    result = (payload or {}).get("result") or {}
    items = []
    for uid in (result.get("uids") or [])[:limit]:
        record = result.get(uid) or {}
        doi = None
        for identifier in record.get("articleids") or []:
            if identifier.get("idtype") == "doi":
                doi = identifier.get("value")
                break
        authors = [
            author.get("name") for author in record.get("authors") or []
            if author.get("name")
        ]
        items.append({
            "title": _strip_markup(record.get("title")),
            "summary": "",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
            "authors": authors,
            "published": str(record.get("pubdate") or "")[:20] or None,
            "doi": doi,
        })
    return items


async def _fetch(source: str, query: str, limit: int) -> List[Dict[str, Any]]:
    hosts = SOURCES[source]["hosts"]
    urls = build_request(source, query, limit)
    _final, body = await _safe_get(urls[0], hosts)
    if source == "wikipedia":
        return parse_wikipedia(json.loads(body.decode("utf-8")), limit)
    if source == "arxiv":
        return parse_arxiv(body.decode("utf-8"), limit)
    ids = ((json.loads(body.decode("utf-8")).get("esearchresult") or {}).get("idlist") or [])
    if not ids:
        return []
    summary_url = (
        f"{SOURCES[source]['endpoint']}esummary.fcgi?db=pubmed"
        f"&id={','.join(ids[:limit])}&retmode=json"
    )
    _final, summary_body = await _safe_get(summary_url, hosts)
    return parse_pubmed(json.loads(summary_body.decode("utf-8")), limit)


class _Cache:
    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("research_gateway_cache.json")
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._load().get(key)

    def put(self, key: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            data[key] = payload
            if len(data) > MAX_CACHE_ENTRIES:
                oldest = sorted(data, key=lambda item: float(data[item].get("_stored_at", 0)))
                for stale_key in oldest[:len(data) - MAX_CACHE_ENTRIES]:
                    data.pop(stale_key, None)
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            try:
                os.makedirs(directory, exist_ok=True)
                fd, temp = tempfile.mkstemp(prefix=os.path.basename(self.path) + ".",
                                            suffix=".tmp", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
            except OSError as exc:
                logger.warning("Could not persist the research cache: %s", exc)


_cache = _Cache()


def _cache_key(source: str, query: str, limit: int) -> str:
    raw = f"{source}|{query.strip().lower()}|{int(limit)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _persist_articles(source: str, query: str, items: List[Dict[str, Any]],
                      retrieved_at: str, license_text: str) -> List[str]:
    """Store fetched items as research articles, carrying provenance in notes."""
    from core.research_manager import get_research_manager_instance

    manager = get_research_manager_instance()
    existing = {}
    try:
        for article in manager.get_articles(domain=source):
            if article.get("url"):
                existing[str(article["url"])] = article.get("id")
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        logger.warning("Could not read existing research articles for deduplication: %s", exc)
    stored = []
    for item in items:
        url = str(item.get("url") or "")
        if url and url in existing:
            stored.append(existing[url])
            continue
        notes = {
            "query": query,
            "retrieved_at": retrieved_at,
            "license": license_text,
            "content_hash": hashlib.sha256(
                json.dumps(item, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
        article = manager.create_article({
            "title": item["title"] or "Untitled",
            "authors": item.get("authors") or [],
            "year": (item.get("published") or "")[:4] or None,
            "journal": SOURCES[source]["label"],
            "doi": item.get("doi"),
            "url": item.get("url"),
            "abstract": item.get("summary"),
            "article_type": "research",
            "domain": source,
            "source": source,
            "notes": json.dumps(notes, sort_keys=True),
        })
        existing[url] = article["id"]
        stored.append(article["id"])
    return stored


async def search(source: str, query: str, limit: int = 5, *,
                 persist: bool = False) -> Dict[str, Any]:
    """Search one allowlisted source and return items with provenance."""
    if source not in SOURCES:
        raise SourcePolicyError(f"source must be one of {', '.join(SOURCES)}")
    if not isinstance(query, str) or not query.strip():
        raise SourcePolicyError("query is required")
    query = query.strip()[:MAX_QUERY_CHARS]
    limit = max(1, min(int(limit), MAX_LIMIT))
    key = _cache_key(source, query, limit)
    cached = _cache.get(key)
    if cached and (time.time() - float(cached.get("_stored_at", 0))) < CACHE_TTL_SECONDS:
        result = dict(cached)
        result["cached"] = True
        if persist:
            result["article_ids"] = _persist_articles(
                source, query, result.get("items") or [], result.get("retrieved_at", ""),
                result.get("license", SOURCES[source]["license"]),
            )
        result.pop("_stored_at", None)
        return result

    retrieved_at = _now()
    try:
        items = await _fetch(source, query, limit)
    except (SourcePolicyError, SourceUnavailableError, httpx.HTTPError, ValueError,
            ET.ParseError) as exc:
        if cached:
            result = dict(cached)
            result.update({"cached": True, "stale": True,
                           "warning": f"source unavailable, serving cached results: {exc}"})
            result.pop("_stored_at", None)
            return result
        if isinstance(exc, SourcePolicyError):
            raise
        raise SourceUnavailableError(str(exc)) from exc

    result = {
        "source": source,
        "label": SOURCES[source]["label"],
        "query": query,
        "retrieved_at": retrieved_at,
        "cached": False,
        "stale": False,
        "license": SOURCES[source]["license"],
        "items": items,
        "content_hash": hashlib.sha256(
            json.dumps(items, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "source_url": build_request(source, query, limit)[0],
        "_stored_at": time.time(),
    }
    _cache.put(key, result)
    if persist:
        result["article_ids"] = _persist_articles(
            source, query, items, retrieved_at, SOURCES[source]["license"]
        )
    public = dict(result)
    public.pop("_stored_at", None)
    return public
