"""Regression tests for M4: secrets must never reach an export or a log in plaintext."""

import logging
from types import SimpleNamespace

import pytest

from core.debug import DebugLogger
from core.routers import export_config
from core.security import REDACTED, redact_secrets, strip_secrets


def test_redact_secrets_masks_flat_and_nested():
    values = {
        "llm_api_key": "sk-live-secret",
        "llm_api_url": "http://localhost:1234/v1",
        "messaging_bridge": {"whatsapp_api_key": "wha-secret", "instance": "main"},
        "active_ai_flows": ["flow-1"],
    }
    redacted = redact_secrets(values)
    assert redacted["llm_api_key"] == REDACTED
    assert redacted["llm_api_url"] == "http://localhost:1234/v1"
    assert redacted["messaging_bridge"]["whatsapp_api_key"] == REDACTED
    assert redacted["messaging_bridge"]["instance"] == "main"
    assert redacted["active_ai_flows"] == ["flow-1"]


def test_redact_secrets_keeps_empty_values():
    assert redact_secrets({"llm_api_key": ""})["llm_api_key"] == ""


def test_strip_secrets_drops_sensitive_keys():
    stripped = strip_secrets({"llm_api_key": "sk", "temperature": 0.7})
    assert "llm_api_key" not in stripped
    assert stripped["temperature"] == 0.7


async def test_export_config_strips_secrets_by_default(monkeypatch):
    monkeypatch.delenv("NEUROCORE_EXPORT_SECRETS", raising=False)
    fake = SimpleNamespace(settings={"llm_api_key": "sk-live-secret", "temperature": 0.5})
    response = await export_config(settings_man=fake)
    assert b"sk-live-secret" not in response.body
    assert b'"temperature"' in response.body


async def test_export_config_includes_secrets_when_opted_in(monkeypatch):
    monkeypatch.setenv("NEUROCORE_EXPORT_SECRETS", "1")
    fake = SimpleNamespace(settings={"llm_api_key": "sk-live-secret"})
    response = await export_config(settings_man=fake)
    assert b"sk-live-secret" in response.body


def test_debug_logger_redacts_secrets(caplog):
    logger = DebugLogger(max_logs=5)
    with caplog.at_level(logging.DEBUG):
        logger.log("flow", "node", "Node", "event", {"llm_api_key": "sk-live-secret", "n": 1})
    stored = logger.get_logs()[0]["details"]
    assert stored["llm_api_key"] == REDACTED
    assert stored["n"] == 1
    assert "sk-live-secret" not in caplog.text
    assert REDACTED in caplog.text