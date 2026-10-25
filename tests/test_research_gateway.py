"""Research gateway: allowlist, SSRF policy, parsers, cache and provenance."""

import asyncio
import json

import pytest

import core.research_gateway as gateway
from core.research_gateway import (
    SourcePolicyError,
    SourceUnavailableError,
    _validate_url,
    build_request,
    parse_arxiv,
    parse_pubmed,
    parse_wikipedia,
    search,
    sources,
)


def public_resolver(host, port, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, **kwargs):
    return [(2, 1, 6, "", ("10.0.0.5", port))]


def test_the_source_list_is_fixed_and_explains_its_licence():
    listing = sources()
    assert {item["id"] for item in listing} == {"wikipedia", "arxiv", "pubmed"}
    assert all(item["license"] for item in listing)


def test_request_urls_are_built_from_the_allowlisted_endpoint():
    url = build_request("wikipedia", "quantum error correction", 3)[0]
    assert url.startswith("https://en.wikipedia.org/w/api.php?")
    assert "quantum+error+correction" in url
    assert "srlimit=3" in url

    arxiv = build_request("arxiv", "graph neural networks", 2)[0]
    assert arxiv.startswith("https://export.arxiv.org/api/query?")
    assert "max_results=2" in arxiv


def test_urls_must_be_https_allowlisted_and_public(monkeypatch):
    hosts = ("en.wikipedia.org",)
    assert _validate_url("https://en.wikipedia.org/w/api.php", hosts,
                         resolver=public_resolver) == "https://en.wikipedia.org/w/api.php"

    with pytest.raises(SourcePolicyError, match="https"):
        _validate_url("http://en.wikipedia.org/wiki/X", hosts, resolver=public_resolver)
    with pytest.raises(SourcePolicyError, match="allowlisted"):
        _validate_url("https://example.com/", hosts, resolver=public_resolver)
    with pytest.raises(SourcePolicyError, match="non-public"):
        _validate_url("https://en.wikipedia.org/", hosts, resolver=private_resolver)


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.1", "192.168.1.1", "0.0.0.0"])
def test_private_and_special_addresses_are_rejected(address):
    def resolver(host, port, **kwargs):
        return [(2, 1, 6, "", (address, port))]

    with pytest.raises(SourcePolicyError, match="non-public"):
        _validate_url("https://en.wikipedia.org/", ("en.wikipedia.org",), resolver=resolver)


def test_wikipedia_results_are_parsed_and_markup_removed():
    items = parse_wikipedia({
        "query": {"search": [
            {"title": "Error correction", "snippet": "<b>Quantum</b> error <span>correction</span>",
             "pageid": 42},
        ]},
        },
        limit=5,
    )
    assert items == [{
        "title": "Error correction",
        "summary": "Quantum error correction",
        "url": "https://en.wikipedia.org/?curid=42",
        "authors": [], "published": None, "doi": None,
    }]


def test_arxiv_results_are_parsed_from_atom():
    payload = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2401.00001v1</id>
        <published>2024-01-01T00:00:00Z</published>
        <title>A  Graph   Neural Network</title>
        <summary>We study graphs.</summary>
        <author><name>Ada Lovelace</name></author>
        <author><name>Alan Turing</name></author>
      </entry>
    </feed>"""
    items = parse_arxiv(payload, limit=3)
    assert items[0]["title"] == "A Graph Neural Network"
    assert items[0]["summary"] == "We study graphs."
    assert items[0]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert items[0]["published"] == "2024-01-01"


def test_pubmed_results_carry_a_doi_and_a_stable_url():
    items = parse_pubmed({
        "result": {
            "uids": ["12345"],
            "12345": {
                "title": "A trial of things",
                "pubdate": "2023 Mar",
                "authors": [{"name": "Smith J"}],
                "articleids": [{"idtype": "pubmed", "value": "12345"},
                               {"idtype": "doi", "value": "10.1000/xyz"}],
            },
        }
    }, limit=5)
    assert items == [{
        "title": "A trial of things", "summary": "",
        "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
        "authors": ["Smith J"], "published": "2023 Mar", "doi": "10.1000/xyz",
    }]


def use_tmp_cache(monkeypatch, tmp_path):
    cache = gateway._Cache(str(tmp_path / "cache.json"))
    monkeypatch.setattr(gateway, "_cache", cache)
    return cache


def test_a_fresh_cache_hit_skips_the_network(monkeypatch, tmp_path):
    use_tmp_cache(monkeypatch, tmp_path)
    calls = []

    async def fake_fetch(source, query, limit):
        calls.append(query)
        return [{"title": "x", "summary": "", "url": "u", "authors": [],
                 "published": None, "doi": None}]

    monkeypatch.setattr(gateway, "_fetch", fake_fetch)
    first = asyncio.run(search("wikipedia", "cache test", 2))
    second = asyncio.run(search("wikipedia", "cache test", 2))

    assert first["cached"] is False
    assert second["cached"] is True
    assert "_stored_at" not in second
    assert calls == ["cache test"]
    assert first["license"] == "CC BY-SA 4.0"
    assert first["source_url"].startswith("https://en.wikipedia.org/")


def test_an_expired_cache_serves_stale_results_with_a_warning(monkeypatch, tmp_path):
    cache = use_tmp_cache(monkeypatch, tmp_path)
    key = gateway._cache_key("arxiv", "stale test", 2)
    cache.put(key, {
        "source": "arxiv", "label": "arXiv", "query": "stale test",
        "retrieved_at": "2026-01-01T00:00:00Z", "cached": False, "stale": False,
        "license": "arXiv author licence (per paper)", "items": [], "content_hash": "x",
        "source_url": "https://export.arxiv.org/api/query", "_stored_at": 1.0,
    })

    async def failing_fetch(source, query, limit):
        raise SourceUnavailableError("upstream down")

    monkeypatch.setattr(gateway, "_fetch", failing_fetch)
    result = asyncio.run(search("arxiv", "stale test", 2))

    assert result["stale"] is True
    assert result["cached"] is True
    assert "upstream down" in result["warning"]
    assert "_stored_at" not in result


def test_an_unavailable_source_without_cache_raises(monkeypatch, tmp_path):
    use_tmp_cache(monkeypatch, tmp_path)

    async def failing_fetch(source, query, limit):
        raise SourceUnavailableError("upstream down")

    monkeypatch.setattr(gateway, "_fetch", failing_fetch)
    with pytest.raises(SourceUnavailableError, match="upstream down"):
        asyncio.run(search("arxiv", "no cache", 2))


def test_bad_input_is_rejected_before_any_request(monkeypatch, tmp_path):
    use_tmp_cache(monkeypatch, tmp_path)
    with pytest.raises(SourcePolicyError, match="source must be"):
        asyncio.run(search("reddit", "anything"))
    with pytest.raises(SourcePolicyError, match="query is required"):
        asyncio.run(search("arxiv", "  "))


def test_persist_stores_articles_with_provenance(monkeypatch, tmp_path):
    use_tmp_cache(monkeypatch, tmp_path)
    stored = []

    class FakeManager:
        def create_article(self, data):
            stored.append(data)
            return {"id": f"a{len(stored)}"}

    monkeypatch.setattr("core.research_manager.get_research_manager_instance",
                        lambda: FakeManager())

    async def fake_fetch(source, query, limit):
        return [{"title": "Paper", "summary": "Abstract", "url": "https://example.org/p",
                 "authors": ["A"], "published": "2024-05-01", "doi": "10.1/x"}]

    monkeypatch.setattr(gateway, "_fetch", fake_fetch)
    result = asyncio.run(search("arxiv", "persist test", 1, persist=True))

    assert result["article_ids"] == ["a1"]
    assert stored[0]["source"] == "arxiv"
    notes = json.loads(stored[0]["notes"])
    assert notes["query"] == "persist test"
    assert notes["license"].startswith("arXiv")
    assert len(notes["content_hash"]) == 64


def test_the_routes_list_sources_and_map_errors(monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    async def fake_search(source, query, limit, persist=False):
        if source == "down":
            raise SourceUnavailableError("unavailable")
        if source not in gateway.SOURCES:
            raise SourcePolicyError("source must be one of wikipedia, arxiv, pubmed")
        return {"source": source, "query": query, "limit": limit, "persist": persist,
                "items": []}

    monkeypatch.setattr(gateway, "search", fake_search)

    with TestClient(app) as client:
        listing = client.get("/research/sources")
        ok = client.post("/research/fetch", json={"source": "arxiv", "query": "x", "limit": 2})
        bad = client.post("/research/fetch", json={"source": "reddit", "query": "x"})
        down = client.post("/research/fetch", json={"source": "down", "query": "x"})

    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()["sources"]} == {"wikipedia", "arxiv", "pubmed"}
    assert ok.status_code == 200
    assert ok.json()["limit"] == 2
    assert bad.status_code == 400
    assert down.status_code == 502
