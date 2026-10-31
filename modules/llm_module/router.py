from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
import json

router = APIRouter()

@router.post("/settings/generation")
async def save_generation_settings(request: Request, temperature: float = Form(...), max_tokens: int = Form(...)):
    module_manager = request.app.state.module_manager
    # FIX: Use proper accessor method with locking instead of direct modules access
    llm_module = module_manager.get_module("llm_module")
    if not llm_module:
        return Response(status_code=404)
        
    config = llm_module.get("config", {}).copy()
    config["temperature"] = temperature
    config["max_tokens"] = max_tokens
    
    module_manager.update_module_config("llm_module", config)
    
    return Response(status_code=200, headers={"HX-Trigger": json.dumps({"showMessage": {"level": "success", "message": "Generation settings saved"}})})
