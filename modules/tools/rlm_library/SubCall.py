# sub_call.py - Recursive LLM call
# Used in RLM (Recursive Language Model) to make sub-calls on chunks of the prompt
# This is THE key feature that enables processing arbitrarily long inputs

args = args.get("args", {})
repl_state = args.get("_repl_state", {})

prompt = args.get("prompt", "")
model = args.get("model")  # Optional: use specific model for sub-call
max_tokens = args.get("max_tokens", 2000)

# Get LLM config from args instead of environment variables (sandbox-safe)
api_url = args.get("api_url", "http://localhost:11434/v1")
api_key = args.get("api_key", "not-needed")

# If model not specified, try to get default from args
if not model:
    model = args.get("default_model", "llama3")

if not prompt:
    result = {"error": "No prompt provided for sub_call"}
else:
    # Use httpx for HTTP call
    import httpx
    
    # Prepare the request
    headers = {
        "Content-Type": "application/json"
    }
    if api_key and api_key != "not-needed":
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    
    # Make the API call
    try:
        response = httpx.post(
            f"{api_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0
        )
        
        if response.status_code == 200:
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"].get("content", "")
                result = {
                    "success": True,
                    "response": content,
                    "model_used": model,
                    "response_length": len(content)
                }
            else:
                result = {"error": "No response content from sub_call"}
        else:
            result = {"error": f"API error: {response.status_code}"}
            
    except Exception as e:
        # Fallback: if we can't make HTTP call, return error
        result = {
            "error": f"sub_call requires LLM API access: {str(e)}",
            "note": "Configure api_url and api_key in args"
        }

