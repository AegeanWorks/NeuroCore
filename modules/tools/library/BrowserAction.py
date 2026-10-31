import json

# Define the action arguments payload to send over HTTP to the backend module
action = args.get('action')
url = args.get('url')
selector = args.get('selector')
text = args.get('text')
full_page = args.get('full_page', False)

if not action:
    result = "Error: No action provided."
else:
    try:
        # Since this tool executes in the python sandbox, we use the injected httpx client
        # which has been specifically whitelisted to allow connections to the browser module on localhost
        payload = {
            "action": action,
            "url": url,
            "selector": selector,
            "text": text,
            "full_page": full_page
        }
        
        # We assume the API server is listening on port 8000 (FastAPI default)
        resp = httpx.post(
            "http://127.0.0.1:8000/browser_automation/api/action", 
            json=payload,
            timeout=30.0
        )
        resp.raise_for_status()
        
        # Parse the JSON response
        data = resp.json()
        
        if not data.get("success", False):
            result = f"Browser action failed: {data.get('error', 'Unknown error')}"
        else:
            # Format successful result
            if action == 'goto':
                result = f"Successfully navigated to {data.get('url')} (Status: {data.get('status')})"
            elif action == 'click':
                result = f"Successfully clicked element: {selector}"
            elif action == 'type':
                result = f"Successfully typed text into: {selector}"
            elif action == 'screenshot':
                # The LLM doesn't usually need the raw base64, but we return it in case 
                # it's a multimodal LLM that can extract and use the image.
                result = {"message": "Screenshot captured successfully", "image_base64": data.get("image")}
            elif action == 'get_html':
                result = f"HTML Content:\n\n{data.get('html')}"
            elif action == 'extract_text':
                result = f"Extracted Text:\n\n{data.get('text')}"
            else:
                result = f"Action {action} completed successfully."
                
    except Exception as e:
        result = f"BrowserAction failed during execution: {e}"
