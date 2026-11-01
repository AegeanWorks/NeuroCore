"""Recorded provider cassettes with strict replay (IDEA-619).

A cassette stores the digest and structural shape of each request plus the provider response,
so replay never needs a model. Strict mode compares the whole body, structural mode compares
roles and the tool schema, and loose mode compares only the endpoint.
"""

import json

import httpx
import pytest

from core.cassette import (
    Cassette,
    CassetteMiss,
    CassetteSchemaError,
    CassetteTemplateMismatch,
)
from core.llm import LLMBridge

ENDPOINT = "http://localhost:1234/v1/chat/completions"

REQUEST = {
    "model": "test-model",
    "messages": [
        {"role": "system", "content": "You are a test."},
        {"role": "user", "content": "Hello"},
    ],
    "temperature": 0.2,
    "max_tokens": 64,
    "stream": False,
}

RESPONSE = {"choices": [{"message": {"role": "assistant", "content": "Hi"}}]}


def test_a_recorded_exchange_replays_identically():
    cassette = Cassette(mode="strict")
    cassette.record(REQUEST, RESPONSE, endpoint=ENDPOINT)

    assert cassette.replay(REQUEST, endpoint=ENDPOINT) == RESPONSE


def test_a_changed_message_body_fails_in_strict_and_passes_in_structural():
    changed = {
        **REQUEST,
        "messages": [
            {"role": "system", "content": "You are a test."},
            {"role": "user", "content": "Hello, changed"},
        ],
    }
    strict = Cassette(mode="strict")
    strict.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    with pytest.raises(CassetteMiss):
        strict.replay(changed, endpoint=ENDPOINT)

    structural = Cassette(mode="structural")
    structural.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    assert structural.replay(changed, endpoint=ENDPOINT) == RESPONSE


def test_a_volatile_key_is_ignored_in_structural_and_fails_in_strict():
    volatile = {**REQUEST, "timestamp": "2026-10-30T12:00:00Z"}

    structural = Cassette(mode="structural")
    structural.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    assert structural.replay(volatile, endpoint=ENDPOINT) == RESPONSE

    strict = Cassette(mode="strict")
    strict.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    with pytest.raises(CassetteMiss):
        strict.replay(volatile, endpoint=ENDPOINT)


def test_a_changed_tool_schema_fails_in_structural():
    request = {**REQUEST, "tools": [{"type": "function", "function": {"name": "search"}}]}
    changed = {**REQUEST, "tools": [{"type": "function", "function": {"name": "write"}}]}

    cassette = Cassette(mode="structural")
    cassette.record(request, RESPONSE, endpoint=ENDPOINT)
    with pytest.raises(CassetteMiss) as info:
        cassette.replay(changed, endpoint=ENDPOINT)
    assert "tools" in str(info.value)


def test_a_different_template_digest_fails_with_both_digests():
    recorded = "a" * 64
    current = "b" * 64
    cassette = Cassette(mode="structural", template_digest=recorded)
    cassette.record(REQUEST, RESPONSE, endpoint=ENDPOINT)

    with pytest.raises(CassetteTemplateMismatch) as info:
        cassette.replay(REQUEST, endpoint=ENDPOINT, template_digest=current)

    assert recorded in str(info.value)
    assert current in str(info.value)


def test_an_unknown_schema_is_refused(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema": 2, "mode": "strict", "exchanges": []}), encoding="utf-8")

    with pytest.raises(CassetteSchemaError) as info:
        Cassette.load(path)

    assert "2" in str(info.value)
    assert "1" in str(info.value)


def test_a_malformed_cassette_is_refused_instead_of_guessed(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CassetteSchemaError):
        Cassette.load(path)


def test_a_credential_shaped_prompt_leaves_no_plaintext_in_the_saved_cassette(tmp_path):
    secret = "sk-" + "a1b2c3d4" * 4
    request = {"messages": [{"role": "user", "content": f"my key is {secret}"}]}
    response = {"choices": [{"message": {"content": f"the key {secret} is accepted"}}]}
    path = tmp_path / "cassette.json"

    cassette = Cassette(mode="strict")
    cassette.record(request, response, endpoint=ENDPOINT)
    cassette.save(path)

    saved = path.read_text(encoding="utf-8")
    assert secret not in saved
    assert "my key is" not in saved
    assert "[REDACTED]" in saved


def test_exchanges_replay_in_recorded_order():
    cassette = Cassette(mode="strict")
    cassette.record(REQUEST, {"n": 1}, endpoint=ENDPOINT)
    cassette.record(REQUEST, {"n": 2}, endpoint=ENDPOINT)

    assert cassette.replay(REQUEST, endpoint=ENDPOINT) == {"n": 1}
    assert cassette.replay(REQUEST, endpoint=ENDPOINT) == {"n": 2}
    with pytest.raises(CassetteMiss):
        cassette.replay(REQUEST, endpoint=ENDPOINT)


def test_loose_mode_matches_only_the_endpoint():
    cassette = Cassette(mode="loose")
    cassette.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    changed = {**REQUEST, "model": "other-model", "messages": []}

    assert cassette.replay(changed, endpoint=ENDPOINT) == RESPONSE
    with pytest.raises(CassetteMiss):
        cassette.replay(REQUEST, endpoint="http://localhost:1234/v1/embeddings")


def test_a_saved_cassette_round_trips(tmp_path):
    path = tmp_path / "cassette.json"
    cassette = Cassette(mode="structural", template_digest="c" * 64)
    cassette.record(REQUEST, RESPONSE, endpoint=ENDPOINT)
    cassette.save(path)

    loaded = Cassette.load(path)

    assert loaded.mode == "structural"
    assert loaded.template_digest == "c" * 64
    assert loaded.replay(REQUEST, endpoint=ENDPOINT) == RESPONSE


@pytest.mark.asyncio
async def test_the_bridge_records_then_replays_without_http(httpx_mock, tmp_path):
    httpx_mock.add_response(url=ENDPOINT, json=RESPONSE, method="POST")
    path = tmp_path / "bridge.json"

    recorder = LLMBridge(
        base_url="http://localhost:1234/v1",
        cassette_mode="record",
        cassette_path=path,
    )
    first = await recorder.chat_completion([{"role": "user", "content": "hi"}], model="test-model")
    assert first == RESPONSE

    replayer = LLMBridge(
        base_url="http://localhost:1234/v1",
        cassette_mode="replay",
        cassette_path=path,
    )
    second = await replayer.chat_completion([{"role": "user", "content": "hi"}], model="test-model")

    assert second == RESPONSE
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_the_bridge_replay_reports_a_changed_prompt_in_strict_mode(httpx_mock, tmp_path):
    httpx_mock.add_response(url=ENDPOINT, json=RESPONSE, method="POST")
    path = tmp_path / "bridge.json"

    recorder = LLMBridge(
        base_url="http://localhost:1234/v1",
        cassette_mode="record",
        cassette_path=path,
        cassette_match="strict",
    )
    await recorder.chat_completion([{"role": "user", "content": "hi"}], model="test-model")

    replayer = LLMBridge(
        base_url="http://localhost:1234/v1",
        cassette_mode="replay",
        cassette_path=path,
    )
    with pytest.raises(CassetteMiss):
        await replayer.chat_completion(
            [{"role": "user", "content": "a different prompt"}], model="test-model"
        )


@pytest.mark.asyncio
async def test_the_bridge_records_then_replays_a_stream(tmp_path):
    body = (
        b'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    path = tmp_path / "stream.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recorder = LLMBridge(
            base_url="http://test/v1",
            client=client,
            cassette_mode="record",
            cassette_path=path,
        )
        recorded = [
            chunk
            async for chunk in recorder.chat_completion_stream(
                [{"role": "user", "content": "hi"}], model="test-model"
            )
        ]

    replayer = LLMBridge(
        base_url="http://test/v1",
        cassette_mode="replay",
        cassette_path=path,
    )
    replayed = [
        chunk
        async for chunk in replayer.chat_completion_stream(
            [{"role": "user", "content": "hi"}], model="test-model"
        )
    ]

    assert [chunk["choices"][0]["delta"]["content"] for chunk in recorded] == ["He", "llo"]
    assert replayed == recorded


def test_an_unknown_bridge_mode_is_refused():
    with pytest.raises(ValueError):
        LLMBridge(base_url="http://test/v1", cassette_mode="recorded")


def test_replay_without_a_cassette_path_is_refused():
    with pytest.raises(ValueError):
        LLMBridge(base_url="http://test/v1", cassette_mode="replay")
