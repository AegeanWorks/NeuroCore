"""End-to-end tests for NeuroCore module configuration workflows.

These tests require a running NeuroCore server and are intentionally skipped in
normal unit-test runs unless explicitly enabled.
"""

from __future__ import annotations

import json
import os

import pytest

requests = pytest.importorskip("requests", reason="E2E tests require optional 'requests' dependency")

BASE_URL = os.getenv("NEUROCORE_E2E_BASE_URL", "http://localhost:8000")
ENABLE_E2E = os.getenv("NEUROCORE_RUN_E2E") == "1"

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def base_url() -> str:
    """Return base URL for E2E server after validating availability."""
    if not ENABLE_E2E:
        pytest.skip("Set NEUROCORE_RUN_E2E=1 to run E2E tests")

    try:
        response = requests.get(f"{BASE_URL}/", timeout=2)
        if response.status_code >= 500:
            pytest.skip(f"E2E server unhealthy at {BASE_URL} (status {response.status_code})")
    except requests.RequestException as exc:
        pytest.skip(f"E2E server not reachable at {BASE_URL}: {exc}")

    return BASE_URL


def test_planner_module_details(base_url: str) -> None:
    response = requests.get(f"{base_url}/modules/planner/details", timeout=5)
    assert response.status_code == 200


def test_planner_default_prompt(base_url: str) -> None:
    response = requests.get(f"{base_url}/modules/planner/default-prompt", timeout=5)
    assert response.status_code == 200
    assert "{max_steps}" in response.text


def test_save_custom_prompt(base_url: str) -> None:
    custom_prompt = "Custom planner prompt with {request} and max {max_steps} steps"
    response = requests.post(
        f"{base_url}/modules/planner/config",
        data={"planner_prompt": custom_prompt, "max_steps": "20", "enabled": "true"},
        timeout=5,
    )
    assert response.status_code == 200


def test_verify_custom_prompt_saved(base_url: str) -> None:
    with open("modules/planner/module.json", "r", encoding="utf-8") as file:
        config = json.load(file)

    saved_custom = config.get("config", {}).get("planner_prompt", "")
    saved_max_steps = config.get("config", {}).get("max_steps", 0)

    assert saved_custom == "Custom planner prompt with {request} and max {max_steps} steps"
    assert saved_max_steps == 20


def test_get_default_prompt_again(base_url: str) -> None:
    response = requests.get(f"{base_url}/modules/planner/default-prompt", timeout=5)
    assert response.status_code == 200
    assert "{max_steps}" in response.text


def test_load_default_prompt_button(base_url: str) -> None:
    response = requests.get(f"{base_url}/modules/planner/default-prompt", timeout=5)
    assert response.status_code == 200
    assert len(response.text) > 0
    assert "\n" in response.text


def test_reflection_module_workflow(base_url: str) -> None:
    response = requests.get(f"{base_url}/modules/reflection/default-prompt", timeout=5)
    assert response.status_code == 200

    custom_reflection = "Custom reflection prompt for testing"
    response = requests.post(
        f"{base_url}/modules/reflection/config",
        data={"reflection_prompt": custom_reflection, "inject_improvement": "true"},
        timeout=5,
    )
    assert response.status_code == 200

    with open("modules/reflection/module.json", "r", encoding="utf-8") as file:
        reflection_config = json.load(file)

    assert "default_reflection_prompt" in reflection_config.get("config", {})
    assert reflection_config.get("config", {}).get("reflection_prompt") == custom_reflection
