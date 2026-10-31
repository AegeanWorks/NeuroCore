import asyncio
from playwright.async_api import async_playwright, Browser, Page
import logging

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
                return {"success": True, "url": self.page.url, "status": response.status if response else 200}
            except Exception as e:
                return {"success": False, "error": str(e)}

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

    async def screenshot(self, full_page: bool = False) -> dict:
        await self.ensure_running()
        import base64
        async with self.lock:
            try:
                image_bytes = await self.page.screenshot(full_page=full_page, type="jpeg", quality=70)
                b64 = base64.b64encode(image_bytes).decode("utf-8")
                return {"success": True, "image": f"data:image/jpeg;base64,{b64}"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def get_html(self, selector: str = None) -> dict:
        await self.ensure_running()
        async with self.lock:
            try:
                if selector:
                    content = await self.page.inner_html(selector, timeout=5000)
                else:
                    content = await self.page.content()
                
                # Truncate content if it's too large to prevent overloading LLM context
                if len(content) > 50000:
                    content = content[:50000] + "... [truncated]"
                
                return {"success": True, "html": content}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def extract_text(self, selector: str = None) -> dict:
        """Extracts visible text from the page or a specific selector."""
        await self.ensure_running()
        async with self.lock:
            try:
                if selector:
                    content = await self.page.inner_text(selector, timeout=5000)
                else:
                    content = await self.page.evaluate("document.body.innerText")
                
                if len(content) > 20000:
                    content = content[:20000] + "... [truncated]"
                    
                return {"success": True, "text": content}
            except Exception as e:
                return {"success": False, "error": str(e)}

browser_service = BrowserService()
