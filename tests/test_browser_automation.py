import pytest
import httpx
from unittest.mock import AsyncMock, patch
from modules.browser_automation.router import router, BrowserActionPayload
from modules.browser_automation.service import browser_service

@pytest.fixture
def mock_browser_service():
    with patch("modules.browser_automation.router.browser_service") as mock_service:
        # Setup async return values
        mock_service.goto = AsyncMock(return_value={"success": True, "url": "http://example.com", "status": 200})
        mock_service.click = AsyncMock(return_value={"success": True})
        mock_service.type_text = AsyncMock(return_value={"success": True})
        mock_service.screenshot = AsyncMock(return_value={"success": True, "image": "data:image/jpeg;base64,mock"})
        mock_service.get_html = AsyncMock(return_value={"success": True, "html": "<html>mock</html>"})
        mock_service.extract_text = AsyncMock(return_value={"success": True, "text": "mock text"})
        yield mock_service

@pytest.mark.asyncio
async def test_browser_goto_action(mock_browser_service):
    # Fast API usually needs a test client, but we can test the router handler directly since it's just a function
    from modules.browser_automation.router import perform_browser_action
    
    payload = BrowserActionPayload(action="goto", url="http://example.com")
    result = await perform_browser_action(payload)
    
    assert result["success"] is True
    assert result["url"] == "http://example.com"
    assert result["status"] == 200
    mock_browser_service.goto.assert_called_once_with("http://example.com")

@pytest.mark.asyncio
async def test_browser_missing_url_for_goto(mock_browser_service):
    from modules.browser_automation.router import perform_browser_action
    from fastapi import HTTPException
    
    payload = BrowserActionPayload(action="goto")
    with pytest.raises(HTTPException) as exc_info:
        await perform_browser_action(payload)
    
    assert exc_info.value.status_code == 400
    assert "URL is required" in exc_info.value.detail
    mock_browser_service.goto.assert_not_called()

@pytest.mark.asyncio
async def test_browser_click_action(mock_browser_service):
    from modules.browser_automation.router import perform_browser_action
    
    payload = BrowserActionPayload(action="click", selector="#btn-login")
    result = await perform_browser_action(payload)
    
    assert result["success"] is True
    mock_browser_service.click.assert_called_once_with("#btn-login")

@pytest.mark.asyncio
async def test_sandbox_internal_api_whitelist():
    """Verify that localhost connections are permitted by the sandbox."""
    from modules.tools.sandbox import SafeHttpxClient
    
    client = SafeHttpxClient()
    
    # Internal module API should be allowed
    assert client._is_domain_allowed("http://127.0.0.1:8000/browser_automation/api/action") is True
    assert client._is_domain_allowed("http://localhost:8000/browser_automation/api/action") is True
    
    # External APIs and other local subnet IP addresses should still be blocked
    assert client._is_domain_allowed("http://192.168.1.1/admin") is False
    assert client._is_domain_allowed("http://10.0.0.1/internal") is False
    assert client._is_domain_allowed("file:///etc/passwd") is False
