"""Server-side confirmation for irreversible routes (ADR-059).

A browser ``confirm()`` dialog is a usability guard, not an authorization check: any HTTP
client can call the endpoint directly. Destructive routes therefore require the exact phrase
in the ``confirm`` query parameter as well as the dialog.
"""

from typing import Optional

from fastapi import HTTPException


def require_confirmation(provided: Optional[str], phrase: str, action: str) -> None:
    """Allow the operation only when ``provided`` matches ``phrase`` after normalization."""
    normalized = " ".join(str(provided or "").lower().split())
    expected = " ".join(str(phrase).lower().split())
    if normalized == expected:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"Why: {action} is irreversible, so an HTTP client cannot rely on a browser "
            f"dialog. Fix: resend with the query parameter confirm='{expected}'."
        ),
    )
