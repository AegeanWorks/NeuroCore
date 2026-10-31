"""
Email Bridge router — settings management endpoints.
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
import json
import logging
from .service import email_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _success(message: str) -> Response:
    return Response(
        status_code=200,
        headers={
            "HX-Trigger": json.dumps(
                {"showMessage": {"level": "success", "message": message}}
            )
        },
    )


def _error(message: str) -> Response:
    return Response(
        status_code=400,
        headers={
            "HX-Trigger": json.dumps(
                {"showMessage": {"level": "error", "message": message}}
            )
        },
    )


@router.post("/settings/save")
async def save_settings(
    request: Request,
    imap_host: str = Form(""),
    imap_port: int = Form(993),
    imap_use_ssl: bool = Form(True),
    imap_username: str = Form(""),
    imap_password: str = Form(""),
    imap_folder: str = Form("INBOX"),
    imap_filter_sender: str = Form(""),
    poll_interval: int = Form(60),
    mark_as_read: bool = Form(True),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_use_tls: bool = Form(True),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_address: str = Form(""),
    reply_to_address: str = Form(""),
):
    module_manager = request.app.state.module_manager
    module = module_manager.modules.get("email_bridge")
    if not module:
        return Response(status_code=404)

    config = module.get("config", {}).copy()
    config.update(
        {
            "imap_host": imap_host.strip(),
            "imap_port": imap_port,
            "imap_use_ssl": imap_use_ssl,
            "imap_username": imap_username.strip(),
            "imap_password": imap_password,
            "imap_folder": imap_folder.strip() or "INBOX",
            "imap_filter_sender": imap_filter_sender.strip(),
            "poll_interval": max(10, poll_interval),
            "mark_as_read": mark_as_read,
            "smtp_host": smtp_host.strip(),
            "smtp_port": smtp_port,
            "smtp_use_tls": smtp_use_tls,
            "smtp_username": smtp_username.strip(),
            "smtp_password": smtp_password,
            "smtp_from_address": smtp_from_address.strip(),
            "reply_to_address": reply_to_address.strip(),
        }
    )
    module_manager.update_module_config("email_bridge", config)

    # Restart polling thread with new config
    email_service.restart()

    return _success("Email Bridge settings saved")
