"""
SMTPBridge — simple SMTP send helper.

Uses only stdlib (smtplib, email.mime) — no extra dependencies.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SMTPBridge:
    """Sends plain-text emails via SMTP with optional In-Reply-To threading."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        use_tls: bool = True,
        reply_to_address: str = "",
        log_fn: Callable[[str], None] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.use_tls = use_tls
        self.reply_to_address = reply_to_address or from_address
        self.log = log_fn or logger.info

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
    ) -> bool:
        """Send a plain-text email. Returns True on success."""
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_address
            msg["To"] = to
            msg["Subject"] = subject

            if self.reply_to_address and self.reply_to_address != self.from_address:
                msg["Reply-To"] = self.reply_to_address

            if in_reply_to:
                msg["In-Reply-To"] = in_reply_to
                msg["References"] = in_reply_to

            msg.attach(MIMEText(body, "plain", "utf-8"))

            if self.port == 465:
                # Implicit SSL
                with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as server:
                    server.login(self.username, self.password)
                    server.sendmail(self.from_address, to, msg.as_string())
            else:
                # Plain or STARTTLS
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    if self.use_tls:
                        server.starttls()
                    server.login(self.username, self.password)
                    server.sendmail(self.from_address, to, msg.as_string())

            self.log(f"SMTPBridge: sent email to {to!r} subject={subject!r}")
            return True
        except Exception as e:
            logger.error(f"SMTPBridge: send failed to {to!r}: {e}")
            return False
