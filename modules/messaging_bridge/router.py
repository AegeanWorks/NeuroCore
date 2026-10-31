from fastapi import APIRouter, Request, Form
from fastapi.responses import Response, JSONResponse
import json
import logging
from .service import messaging_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _success(message: str) -> Response:
    return Response(
        status_code=200,
        headers={
            "HX-Trigger": json.dumps({
                "showMessage": {"level": "success", "message": message}
            })
        },
    )


def _not_found() -> Response:
    return Response(status_code=404)


@router.post("/settings/save/telegram")
async def save_telegram_settings(
    request: Request,
    bot_token: str = Form(...),
    chat_id: int = Form(...),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    config = module.get("config", {}).copy()
    config["telegram_bot_token"] = bot_token
    config["telegram_chat_id"] = chat_id
    module_manager.update_module_config("messaging_bridge", config)

    # Restart Telegram listener with new credentials
    messaging_service.restart_platform("telegram")

    return _success("Telegram settings saved")


@router.post("/settings/save/discord")
async def save_discord_settings(
    request: Request,
    bot_token: str = Form(...),
    channel_id: str = Form(...),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    config = module.get("config", {}).copy()
    config["discord_bot_token"] = bot_token
    config["discord_channel_id"] = channel_id
    module_manager.update_module_config("messaging_bridge", config)

    messaging_service.restart_platform("discord")

    return _success("Discord settings saved")


@router.post("/settings/save/signal")
async def save_signal_settings(
    request: Request,
    api_url: str = Form(...),
    phone_number: str = Form(...),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    config = module.get("config", {}).copy()
    config["signal_api_url"] = api_url
    config["signal_phone_number"] = phone_number
    module_manager.update_module_config("messaging_bridge", config)

    messaging_service.restart_platform("signal")

    return _success("Signal settings saved")


@router.post("/settings/save/whatsapp")
async def save_whatsapp_settings(
    request: Request,
    api_url: str = Form(...),
    api_key: str = Form(...),
    instance: str = Form(...),
    phone_number: str = Form(...),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    config = module.get("config", {}).copy()
    config["whatsapp_api_url"] = api_url
    config["whatsapp_api_key"] = api_key
    config["whatsapp_instance"] = instance
    config["whatsapp_phone_number"] = phone_number
    module_manager.update_module_config("messaging_bridge", config)

    messaging_service.restart_platform("whatsapp")

    return _success("WhatsApp settings saved")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Receives inbound message events pushed by Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    try:
        handler = messaging_service.get_handler("whatsapp")
        if handler:
            handler.handle_incoming_webhook(payload)
    except Exception as e:
        logger.error(f"WhatsApp webhook processing error: {e}")

    # Always return 200 so Evolution API doesn't retry
    return JSONResponse(status_code=200, content={"ok": True})


# Start all platform listeners when the module is loaded
messaging_service.start()
