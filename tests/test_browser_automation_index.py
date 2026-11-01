"""Indexed element actions and bounded snapshots (IDEA-1208/1209)."""

import asyncio

import pytest

from modules.browser_automation.indexed import (
    CODE_DONE,
    CODE_POSTCONDITION,
    CODE_REFUSED,
    CODE_STALE,
    CODE_TIMEOUT,
    CODE_UNVERIFIED,
    ActionExpectation,
    SnapshotBudgetExceeded,
    build_snapshot,
    element_id_for,
    is_policy_refused,
    result_for,
    wait_for_expectation,
)
from modules.browser_automation.snapshot import (
    SnapshotBudget,
    measure_html,
    measure_screenshot,
    measure_text,
)


# -- snapshot refs ---------------------------------------------------------------------------

def _raw(role="button", name="Save", selector="#save"):
    return {"role": role, "name": name, "selector": selector,
            "bounds": [1.0, 2.0, 3.0, 4.0], "value": "", "checked": None}


def test_element_ids_are_stable_within_a_generation():
    first = build_snapshot([_raw()], generation=1)[0]
    second = build_snapshot([_raw()], generation=1)[0]
    assert first.element_id == second.element_id
    assert first.element_id == element_id_for("button", "Save", "#save", 1)


def test_element_ids_change_across_generations():
    first = build_snapshot([_raw()], generation=1)[0]
    second = build_snapshot([_raw()], generation=2)[0]
    assert first.element_id != second.element_id


def test_snapshot_refuses_over_the_element_budget():
    with pytest.raises(SnapshotBudgetExceeded) as caught:
        build_snapshot([_raw() for _ in range(201)], generation=1)
    assert caught.value.code == "element_budget_exceeded"
    assert "Fix:" in str(caught.value)


def test_snapshot_refuses_over_the_char_budget():
    with pytest.raises(SnapshotBudgetExceeded):
        build_snapshot([_raw(name="x" * 500) for _ in range(100)], generation=1,
                       max_chars=1000)


def test_file_and_select_roles_are_policy_refused():
    assert is_policy_refused("file") is True
    assert is_policy_refused("select") is True
    assert is_policy_refused("button") is False


# -- expectations ----------------------------------------------------------------------------

def test_unknown_expectation_kind_is_refused():
    with pytest.raises(ValueError) as caught:
        ActionExpectation(kind="teleport").validate()
    assert "Fix:" in str(caught.value)


def test_expectation_budget_is_capped():
    assert ActionExpectation(timeout_ms=999999).budget_ms == 2000


def test_auto_detects_a_url_change():
    matched, latest = asyncio.run(wait_for_expectation(
        _reader([{"url": "/a", "dom_version": 1}]),
        ActionExpectation(kind="auto"),
        {"url": "/b", "dom_version": 1},
    ))
    assert matched is True
    assert latest["url"] == "/a"


def test_auto_reports_no_change_within_the_budget():
    state = {"url": "/same", "dom_version": 3, "value": "", "checked": None, "focus": ""}

    async def run():
        return await wait_for_expectation(_reader([state]), ActionExpectation(kind="auto",
                                                                             timeout_ms=150), state)

    matched, _ = asyncio.run(run())
    assert matched is False


def test_value_equals_waits_for_the_field_to_match():
    states = [{"value": "a"}, {"value": "ab"}, {"value": "abc"}]

    async def run():
        return await wait_for_expectation(
            _reader(states),
            ActionExpectation(kind="value_equals", value="abc"),
            {"value": ""},
        )

    matched, latest = asyncio.run(run())
    assert matched is True
    assert latest["value"] == "abc"


def test_checkbox_expectation_reads_checked():
    async def run():
        return await wait_for_expectation(
            _reader([{"checked": True}]),
            ActionExpectation(kind="checked", value="true"),
            {"checked": False},
        )

    matched, _ = asyncio.run(run())
    assert matched is True


def _reader(states):
    """A state reader that yields each sample once, then repeats the last."""
    remaining = list(states)

    async def read():
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return read


# -- result codes ----------------------------------------------------------------------------

def test_only_done_is_ok():
    ref = build_snapshot([_raw()], generation=1)[0]
    assert result_for(True, CODE_DONE, ref, 1).ok is True
    for code in (CODE_UNVERIFIED, CODE_STALE, CODE_POSTCONDITION, CODE_REFUSED, CODE_TIMEOUT):
        assert result_for(False, code, ref, 1).ok is False


# -- snapshot budget (IDEA-1209) -------------------------------------------------------------

def test_html_within_budget_is_returned_untouched():
    measured = measure_html("<p>hi</p>", SnapshotBudget())
    assert measured["ok"] is True
    assert measured["truncated"] is False
    assert measured["html"] == "<p>hi</p>"


def test_oversized_html_is_refused_not_silently_cut():
    measured = measure_html("x" * 60, SnapshotBudget(max_html_chars=50))
    assert measured["ok"] is False
    refusal = measured["refusal"]
    assert refusal["code"] == "html_too_large"
    assert refusal["budget"] == {"max_html_chars": 50}
    assert "Fix:" in refusal["next"]["hint"] + " Fix:"


def test_explicit_truncation_reports_what_was_omitted():
    measured = measure_html("x" * 60, SnapshotBudget(max_html_chars=50), allow_truncate=True)
    assert measured["ok"] is True
    assert measured["truncated"] is True
    assert measured["omitted_chars"] == 10
    assert measured["resume"]["offset"] == 50


def test_text_budget_is_independent_of_html():
    measured = measure_text("t" * 30, SnapshotBudget(max_text_chars=20))
    assert measured["ok"] is False
    assert measured["refusal"]["code"] == "text_too_large"


def test_screenshot_bytes_are_bounded():
    ok = measure_screenshot(b"x" * 10, SnapshotBudget(max_screenshot_bytes=100))
    assert ok["ok"] is True
    too_big = measure_screenshot(b"x" * 500, SnapshotBudget(max_screenshot_bytes=100))
    assert too_big["ok"] is False
    assert too_big["refusal"]["code"] == "screenshot_too_large"


def test_budget_validation_refuses_zero():
    with pytest.raises(ValueError):
        SnapshotBudget(max_html_chars=0).validate()


# -- service integration with a fake page ----------------------------------------------------

def _service_with_fake_page(monkeypatch, page):
    from modules.browser_automation import service as browser_service_module

    browser = browser_service_module.BrowserService()
    monkeypatch.setattr(browser, "ensure_running", _noop)
    browser.page = page
    # Each test starts at a fresh generation with no cached ids.
    browser.reset_element_index()
    return browser


async def _noop(*args, **kwargs):
    return None


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        await self.page.do_click(self.selector)

    async def fill(self, text, timeout=None):
        await self.page.do_fill(self.selector, text)


class _FakePage:
    def __init__(self, elements, click_effect=None, fill_value=None):
        self.elements = elements
        self.state = {"url": "/start", "dom_version": 1, "value": "", "checked": None,
                      "focus": ""}
        self.click_effect = click_effect
        self.fill_value = fill_value
        self.clicked = []
        self.filled = []
        self.is_closed = lambda: False

    async def evaluate(self, script, arg=None):
        # The snapshot script walks the DOM; the state sample reports one small record.
        if "getBoundingClientRect" in script:
            return self.elements
        return dict(self.state)

    def locator(self, selector):
        return _FakeLocator(self, selector)

    async def do_click(self, selector):
        self.clicked.append(selector)
        if self.click_effect:
            self.click_effect(self.state)

    async def do_fill(self, selector, text):
        self.filled.append((selector, text))
        if self.fill_value is not None:
            self.state["value"] = self.fill_value
        else:
            self.state["value"] = text

    async def wait_for_load_state(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_click_that_changes_the_page_reports_done(monkeypatch):
    def navigate(state):
        state["url"] = "/done"

    page = _FakePage([_raw()], click_effect=navigate)
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    result = await browser.click_element(element_id)

    assert result["ok"] is True
    assert result["code"] == CODE_DONE
    assert page.clicked == ["#save"]


@pytest.mark.asyncio
async def test_click_with_no_observable_change_is_unverified(monkeypatch):
    page = _FakePage([_raw()])
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    result = await browser.click_element(
        element_id, ActionExpectation(kind="auto", timeout_ms=150)
    )

    assert result["ok"] is False
    assert result["code"] == CODE_UNVERIFIED


@pytest.mark.asyncio
async def test_stale_generation_never_clicks(monkeypatch):
    page = _FakePage([_raw()])
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    browser.advance_generation()

    result = await browser.click_element(element_id)

    assert result["code"] == CODE_STALE
    assert page.clicked == []


@pytest.mark.asyncio
async def test_type_reports_postcondition_failure_when_the_field_differs(monkeypatch):
    page = _FakePage([_raw(role="textbox", name="Email", selector="#email")],
                     fill_value="something else")
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    result = await browser.type_element(element_id, "wanted@example.com",
                                        ActionExpectation(kind="value_equals",
                                                          value="wanted@example.com",
                                                          timeout_ms=150))

    assert result["code"] == CODE_POSTCONDITION
    assert page.filled == [("#email", "wanted@example.com")]


@pytest.mark.asyncio
async def test_type_reports_done_when_the_value_matches(monkeypatch):
    page = _FakePage([_raw(role="textbox", name="Email", selector="#email")])
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    result = await browser.type_element(element_id, "wanted@example.com")

    assert result["code"] == CODE_DONE


@pytest.mark.asyncio
async def test_file_input_is_policy_refused_and_not_clicked(monkeypatch):
    page = _FakePage([_raw(role="file", name="Upload", selector="#upload")])
    browser = _service_with_fake_page(monkeypatch, page)

    snapshot = await browser.snapshot()
    element_id = snapshot["elements"][0]["element_id"]
    result = await browser.click_element(element_id)

    assert result["code"] == CODE_REFUSED
    assert page.clicked == []
