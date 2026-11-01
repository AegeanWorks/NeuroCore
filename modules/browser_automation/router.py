from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from .service import browser_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class BrowserActionPayload(BaseModel):
    action: str
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    full_page: bool = False
    # Indexed actions (IDEA-1208) and explicit snapshot control (IDEA-1209).
    element_id: Optional[str] = None
    expect_kind: Optional[str] = None
    expect_value: Optional[str] = None
    timeout_ms: int = 2000
    allow_truncate: bool = False

@router.on_event("startup")
async def startup_event():
    logger.info("Browser Automation module starting up.")
    # We delay starting the browser until the first use to save resources,
    # but we can configure it to pre-warm here if needed.

@router.on_event("shutdown")
async def shutdown_event():
    logger.info("Browser Automation module shutting down.")
    await browser_service.stop()

@router.post("/api/action")
async def perform_browser_action(payload: BrowserActionPayload):
    """
    Internal API endpoint for the sandboxed tool to perform browser actions.
    This bypasses the sandbox restrictions safely by validating the action here.
    """
    try:
        action = payload.action.lower()
        
        if action == "goto":
            if not payload.url:
                raise HTTPException(status_code=400, detail="URL is required for 'goto' action")
            return await browser_service.goto(payload.url)
            
        elif action == "click":
            if not payload.selector:
                raise HTTPException(status_code=400, detail="Selector is required for 'click' action")
            return await browser_service.click(payload.selector)
            
        elif action == "type":
            if not payload.selector or payload.text is None:
                raise HTTPException(status_code=400, detail="Selector and text are required for 'type' action")
            return await browser_service.type_text(payload.selector, payload.text)
            
        elif action == "snapshot":
            return await browser_service.snapshot(payload.selector)

        elif action in ("click_element", "type_element"):
            if not payload.element_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"element_id is required for '{action}'; call snapshot first",
                )
            from modules.browser_automation.indexed import ActionExpectation

            expectation = ActionExpectation(
                kind=payload.expect_kind or "auto",
                value=payload.expect_value,
                timeout_ms=payload.timeout_ms,
            )
            if action == "click_element":
                return await browser_service.click_element(payload.element_id, expectation)
            if payload.text is None:
                raise HTTPException(status_code=400, detail="text is required for 'type_element'")
            return await browser_service.type_element(
                payload.element_id, payload.text, expectation
            )

        elif action == "screenshot":
            return await browser_service.screenshot(
                full_page=payload.full_page, allow_truncate=payload.allow_truncate
            )
            
        elif action == "get_html":
            return await browser_service.get_html(
                payload.selector, allow_truncate=payload.allow_truncate
            )
            
        elif action == "extract_text":
            return await browser_service.extract_text(
                payload.selector, allow_truncate=payload.allow_truncate
            )
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown browser action: {action}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"BrowserService internal error during action '{payload.action}': {e}")
        raise HTTPException(status_code=500, detail=f"Browser service error: {e}")
