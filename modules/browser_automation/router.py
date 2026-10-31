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
            
        elif action == "screenshot":
            return await browser_service.screenshot(full_page=payload.full_page)
            
        elif action == "get_html":
            return await browser_service.get_html(payload.selector)
            
        elif action == "extract_text":
            return await browser_service.extract_text(payload.selector)
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown browser action: {action}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"BrowserService internal error during action '{payload.action}': {e}")
        raise HTTPException(status_code=500, detail=f"Browser service error: {e}")
