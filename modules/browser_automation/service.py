import asyncio
from playwright.async_api import async_playwright, Browser, Page
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # L-29-adjacent: quoted annotation needs a name for linters
    from modules.browser_automation.snapshot import SnapshotBudget

logger = logging.getLogger(__name__)

class BrowserService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BrowserService, cls).__new__(cls)
            cls._instance.playwright = None
            cls._instance.browser = None
            cls._instance.context = None
            cls._instance.page = None
            cls._instance._lock = None  # Lazy init to avoid event-loop issues at import time
            # Element generation and the last snapshot's id map (IDEA-1208).
            cls._instance._generation = 0
            cls._instance._snapshot_index = {}
        return cls._instance

    @property
    def lock(self):
        """Lazily create the asyncio.Lock on first access inside the running event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def start(self, config: dict = None):
        """Initializes the playwright browser if not already running."""
        config = config or {}
        headless = config.get("headless", True)
        
        async with self.lock:
            if not self.playwright:
                try:
                    self.playwright = await async_playwright().start()
                    self.browser = await self.playwright.chromium.launch(
                        headless=headless,
                        args=["--disable-dev-shm-usage", "--no-sandbox"]
                    )
                    self.context = await self.browser.new_context(
                        viewport=config.get("default_viewport", {"width": 1280, "height": 720})
                    )
                    self.page = await self.context.new_page()
                    logger.info("Playwright browser started successfully.")
                except Exception as e:
                    logger.error(f"Failed to start Playwright browser: {e}")
                    raise

    async def stop(self):
        """Stops the headless browser."""
        async with self.lock:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None
            logger.info("Playwright browser stopped.")

    async def ensure_running(self):
        if not self.page or self.page.is_closed():
            await self.start()

    async def goto(self, url: str) -> dict:
        await self.ensure_running()
        async with self.lock:
            try:
                response = await self.page.goto(url, wait_until="networkidle")
                self.advance_generation()
                return {"success": True, "url": self.page.url, "status": response.status if response else 200}
            except Exception as e:
                return {"success": False, "error": str(e)}

    # -- Indexed actions with post-condition verification (IDEA-1208) ------------------------

    _SNAPSHOT_JS = """
    () => {
      const scope = arguments[0] || document;
      const out = [];
      const nodes = scope.querySelectorAll(
        'a,button,input,select,textarea,[role=button],[role=link],[role=checkbox],' +
        '[role=radio],[role=tab],[role=menuitem],[role=switch],[contenteditable=true]'
      );
      for (const el of nodes) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const tag = el.tagName.toLowerCase();
        let role = el.getAttribute('role') || tag;
        if (tag === 'input') {
          const type = (el.getAttribute('type') || 'text').toLowerCase();
          role = type === 'checkbox' || type === 'radio' ? type
               : (type === 'file' ? 'file' : (type === 'submit' || type === 'button' ? 'button' : 'textbox'));
        }
        if (tag === 'select') role = 'select';
        const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.name || '').trim();
        const id = el.id ? ('#' + CSS.escape(el.id)) : '';
        let selector = '';
        if (id) { selector = id; }
        else if (el.name) { selector = tag + '[name="' + el.name.replace(/"/g, '\\"') + '"]'; }
        else { selector = tag; }
        out.push({
          role, name, selector,
          bounds: [rect.x, rect.y, rect.width, rect.height],
          value: (el.value === undefined ? '' : String(el.value)),
          checked: el.checked === undefined ? null : !!el.checked,
        });
        if (out.length > 400) break;
      }
      return out;
    }
    """

    async def _page_state(self) -> dict:
        """A small, comparable sample of the page for post-condition polling."""
        try:
            state = await self.page.evaluate(
                """() => {
                    const el = document.activeElement;
                    return {
                      url: location.href,
                      dom_version: document.querySelectorAll('*').length,
                      focus: el ? (el.tagName + '|' + (el.id || el.name || '')) : '',
                      value: el && el.value !== undefined ? String(el.value) : '',
                      checked: el && el.checked !== undefined ? !!el.checked : null,
                    };
                }"""
            )
            return state if isinstance(state, dict) else {}
        except Exception as exc:
            logger.debug("page state sample failed: %s", exc)
            return {}

    async def snapshot(self, scope: str = None) -> dict:
        """Bounded element refs for the current page generation (IDEA-1208)."""
        from modules.browser_automation.indexed import SnapshotBudgetExceeded, build_snapshot

        await self.ensure_running()
        async with self.lock:
            try:
                raw = await self.page.evaluate(self._SNAPSHOT_JS, scope)
            except Exception as exc:
                return {"success": False, "error": str(exc)}
            try:
                refs = build_snapshot(list(raw or []), self._generation)
            except SnapshotBudgetExceeded as exc:
                return {"success": False, "code": exc.code, "measured": exc.measured,
                        "budget": exc.budget, "error": str(exc)}
            self._snapshot_index = {ref.element_id: ref for ref in refs}
            return {"success": True, "generation": self._generation,
                    "elements": [ref.as_dict() for ref in refs],
                    "count": len(refs)}

    def advance_generation(self) -> int:
        """Invalidate every element id; ids are never reused across generations."""
        self._generation += 1
        self._snapshot_index = {}
        return self._generation

    def reset_element_index(self) -> int:
        """Start a fresh generation with no cached ids (used by navigation and by tests)."""
        return self.advance_generation()

    def current_generation(self) -> int:
        return self._generation

    def _resolve_element(self, element_id: str):
        """The cached ref for an id in the current generation, or None when stale."""
        ref = (self._snapshot_index or {}).get(str(element_id))
        if ref is None or ref.generation != self._generation:
            return None
        return ref

    async def _act_and_verify(self, element_id: str, action, expectation,
                              refuse_roles=("file", "select")) -> dict:
        from modules.browser_automation.indexed import (
            ActionExpectation, CODE_DONE, CODE_ERROR, CODE_POSTCONDITION, CODE_REFUSED,
            CODE_STALE, CODE_UNVERIFIED, is_policy_refused, result_for,
            wait_for_expectation,
        )

        await self.ensure_running()
        async with self.lock:
            ref = self._resolve_element(element_id)
            if ref is None:
                return result_for(False, CODE_STALE, None, self._generation,
                                  {"hint": "take a fresh snapshot"}).as_dict()
            if is_policy_refused(ref.role) and ref.role in refuse_roles:
                return result_for(False, CODE_REFUSED, ref, self._generation,
                                  {"role": ref.role}).as_dict()
            expectation = (expectation or ActionExpectation()).validate()
            before = await self._page_state()
            try:
                await action(ref)
            except Exception as exc:
                return result_for(False, CODE_ERROR, ref, self._generation,
                                  {"error": str(exc)}).as_dict()
            matched, after = await wait_for_expectation(self._page_state, expectation, before)

        if matched:
            return result_for(True, CODE_DONE, ref, self._generation, after).as_dict()
        if expectation.kind == "value_equals":
            return result_for(False, CODE_POSTCONDITION, ref, self._generation, after).as_dict()
        return result_for(False, CODE_UNVERIFIED, ref, self._generation, after).as_dict()

    async def click_element(self, element_id: str, expect=None) -> dict:
        """Click by element id and verify an observable change (IDEA-1208)."""
        async def action(ref):
            locator = self.page.locator(ref.selector_hint).first
            await locator.click(timeout=5000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=2000)

        return await self._act_and_verify(element_id, action, expect)

    async def type_element(self, element_id: str, text: str, expect=None) -> dict:
        """Fill by element id, then read the value back (IDEA-1208)."""
        async def action(ref):
            locator = self.page.locator(ref.selector_hint).first
            await locator.fill(text, timeout=5000)

        from modules.browser_automation.indexed import ActionExpectation

        expectation = expect or ActionExpectation(kind="value_equals", value=text)
        return await self._act_and_verify(element_id, action, expectation,
                                          refuse_roles=("file",))

    async def click(self, selector: str) -> dict:
        await self.ensure_running()
        async with self.lock:
            try:
                await self.page.click(selector, timeout=5000)
                await self.page.wait_for_load_state("networkidle")
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def type_text(self, selector: str, text: str) -> dict:
        await self.ensure_running()
        async with self.lock:
            try:
                await self.page.fill(selector, text, timeout=5000)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def screenshot(self, full_page: bool = False, budget: "SnapshotBudget" = None,
                         allow_truncate: bool = False) -> dict:
        """A screenshot with a byte ceiling; an oversized page is a typed refusal (IDEA-1209)."""
        await self.ensure_running()
        import base64
        from modules.browser_automation.snapshot import SnapshotBudget, measure_screenshot

        budget = (budget or SnapshotBudget()).validate()
        async with self.lock:
            try:
                image_bytes = await self.page.screenshot(full_page=full_page, type="jpeg", quality=70)
                measured = measure_screenshot(image_bytes, budget,
                                              allow_truncate=allow_truncate)
                if not measured["ok"]:
                    return {"success": False, **measured}
                b64 = base64.b64encode(image_bytes).decode("utf-8")
                return {"success": True, "image": f"data:image/jpeg;base64,{b64}",
                        "truncated": measured.get("truncated", False)}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def get_html(self, selector: str = None, allow_truncate: bool = False,
                       budget: "SnapshotBudget" = None) -> dict:
        """Page HTML, refused when it exceeds the budget instead of silently cut (IDEA-1209)."""
        from modules.browser_automation.snapshot import SnapshotBudget, measure_html

        await self.ensure_running()
        budget = (budget or SnapshotBudget()).validate()
        async with self.lock:
            try:
                if selector:
                    content = await self.page.inner_html(selector, timeout=5000)
                else:
                    content = await self.page.content()
                measured = measure_html(content, budget, selector, allow_truncate)
                if not measured["ok"]:
                    return {"success": False, **measured}
                return {"success": True, **measured}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def extract_text(self, selector: str = None, allow_truncate: bool = False,
                           budget: "SnapshotBudget" = None) -> dict:
        """Visible text with an explicit budget (IDEA-1209)."""
        from modules.browser_automation.snapshot import SnapshotBudget, measure_text

        await self.ensure_running()
        budget = (budget or SnapshotBudget()).validate()
        async with self.lock:
            try:
                if selector:
                    content = await self.page.inner_text(selector, timeout=5000)
                else:
                    content = await self.page.evaluate("document.body.innerText")
                measured = measure_text(content, budget, selector, allow_truncate)
                if not measured["ok"]:
                    return {"success": False, **measured}
                return {"success": True, **measured}
            except Exception as e:
                return {"success": False, "error": str(e)}

browser_service = BrowserService()
