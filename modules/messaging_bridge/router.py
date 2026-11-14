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
    # M-SEC-4: secrets are never rendered; blank input keeps the stored value.
    if str(bot_token or "").strip():
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
    if str(bot_token or "").strip():
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
    webhook_secret: str = Form(default=""),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    config = module.get("config", {}).copy()
    config["whatsapp_api_url"] = api_url
    if str(api_key or "").strip():
        config["whatsapp_api_key"] = api_key
    # L-14: a per-webhook shared secret authenticates inbound webhook calls.
    # Blank keeps the stored value (M-SEC-4: secrets are never re-rendered).
    if str(webhook_secret or "").strip():
        config["whatsapp_webhook_secret"] = webhook_secret
    config["whatsapp_instance"] = instance
    config["whatsapp_phone_number"] = phone_number
    module_manager.update_module_config("messaging_bridge", config)

    messaging_service.restart_platform("whatsapp")

    return _success("WhatsApp settings saved")


def _parse_id_list(raw) -> list:
    """Parse a comma/space-separated allowlist into a clean list of ids."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = []
        for part in raw:
            items.extend(str(part).replace(",", " ").split())
        return [i.strip() for i in items if i.strip()]
    return [i.strip() for i in str(raw).replace(",", " ").split() if i.strip()]


@router.post("/settings/save/access")
async def save_access_settings(request: Request):
    """Save sender/chat allowlists for every platform in one form.

    Deny-by-default (ADR-083): an empty sender list denies every sender;
    chats empty means no extra chat restriction once the sender is allowed.
    Telegram falls back to telegram_chat_id and Discord to discord_channel_id
    when no explicit list is given. Restart is unnecessary: the access gate
    reads config on every message.
    """
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("messaging_bridge")
    if not module:
        return _not_found()

    form = await request.form()
    config = module.get("config", {}).copy()
    for platform in ("telegram", "discord", "signal", "whatsapp"):
        senders_key = f"{platform}_allowed_senders"
        chats_key = f"{platform}_allowed_chats"
        if senders_key in form:
            config[senders_key] = _parse_id_list(form.get(senders_key))
        if chats_key in form:
            config[chats_key] = _parse_id_list(form.get(chats_key))
    module_manager.update_module_config("messaging_bridge", config)

    return _success("Access controls saved")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Receives inbound message events pushed by Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # L-14: when a webhook secret is configured, the caller must present it.
    # Without it, anything on the Docker network could post messages that
    # claim an allowlisted sender. Unconfigured setups keep the previous
    # behaviour (global guard + sender allowlist) so existing single-host
    # deployments do not break.
    try:
        module_manager = request.app.state.module_manager
        module = (module_manager.modules.get("messaging_bridge")
                  if module_manager else None)
        secret = ((module.get("config", {}) or {}).get("whatsapp_webhook_secret")
                  if module else None)
    except Exception:
        secret = None
    if secret:
        import hmac as _hmac

        presented = request.headers.get("x-webhook-secret", "")
        try:
            ok = bool(presented) and _hmac.compare_digest(
                str(presented).encode("utf-8"), str(secret).encode("utf-8")
            )
        except Exception:
            ok = False
        if not ok:
            return JSONResponse(status_code=403, content={"error": "Forbidden"})
    else:
        # Warn once per process: without a secret the global guard and the
        # sender allowlist are the only inbound checks.
        global _webhook_secret_warned
        try:
            warned = _webhook_secret_warned
        except NameError:
            warned = False
        if not warned:
            _webhook_secret_warned = True
            logger.warning(
                "WhatsApp webhook has no shared secret configured; "
                "set one in the messaging settings."
            )

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
