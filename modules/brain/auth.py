"""Brain actor identity, token registry and role gates.

Loopback/test clients are trusted as local admins. Remote clients must present a token.
Tokens are stored as salted PBKDF2 hashes; plaintext is only returned when an actor is created
or rotated. The environment token remains a backward-compatible admin override.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from core.paths import state_path

from .persistence import LedgerLock, atomic_write_json, ledger_transaction

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient", "testserver"}
ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}
VALID_ROLES = tuple(ROLE_LEVEL)
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class BrainActor:
    name: str
    role: str = "admin"
    authenticated: bool = False


class BrainActorRegistry:
    """Persistent actor credentials; only salted hashes are stored."""

    def __init__(self, path: Optional[str] = None, iterations: int = 120000):
        self.path = path or state_path("data", "brain_actors.json")
        self.iterations = max(10000, int(iterations))
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {"actors": {}}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read brain actor registry: %s", exc)
                return {"actors": {}}
            if not isinstance(data, dict) or not isinstance(data.get("actors"), dict):
                return {"actors": {}}
            return data

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data)

    def _hash(self, token: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", token.encode("utf-8"), bytes.fromhex(salt), self.iterations
        ).hex()

    @ledger_transaction
    def create(self, name: str, role: str = "operator",
               token: Optional[str] = None) -> Dict[str, Any]:
        if not NAME_RE.match(name or ""):
            raise ValueError("actor name must be 1-64 letters, digits, _ . or -")
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {', '.join(VALID_ROLES)}")
        data = self.load()
        actors = data["actors"]
        if name in actors:
            raise ValueError(f"actor '{name}' already exists")
        secret = token or secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        actors[name] = {
            "name": name,
            "role": role,
            "salt": salt,
            "token_hash": self._hash(secret, salt),
            "enabled": True,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_used": None,
        }
        self.save(data)
        return {"actor": self._public(actors[name]), "token": secret}

    @ledger_transaction
    def authenticate(self, token: str) -> Optional[BrainActor]:
        if not token:
            return None
        data = self.load()
        matched = None
        for record in data["actors"].values():
            if not record.get("enabled", True):
                continue
            try:
                candidate = self._hash(str(token), str(record.get("salt", "")))
            except (TypeError, ValueError):
                continue
            if hmac.compare_digest(candidate, str(record.get("token_hash", ""))):
                matched = record
                break
        if matched is None:
            return None
        matched["last_used"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self.save(data)
        except OSError as exc:
            logger.warning("Could not update actor last_used: %s", exc)
        return BrainActor(
            name=str(matched["name"]),
            role=str(matched.get("role", "operator")),
            authenticated=True,
        )

    def list(self) -> list:
        return [self._public(record) for record in self.load()["actors"].values()]

    @ledger_transaction
    def rotate(self, name: str) -> Dict[str, Any]:
        data = self.load()
        record = data["actors"].get(name)
        if record is None:
            raise ValueError(f"actor '{name}' does not exist")
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        record.update({
            "salt": salt,
            "token_hash": self._hash(secret, salt),
            "enabled": True,
            "last_used": None,
        })
        self.save(data)
        return {"actor": self._public(record), "token": secret}

    @ledger_transaction
    def disable(self, name: str) -> Dict[str, Any]:
        data = self.load()
        record = data["actors"].get(name)
        if record is None:
            raise ValueError(f"actor '{name}' does not exist")
        record["enabled"] = False
        self.save(data)
        return self._public(record)

    @ledger_transaction
    def delete(self, name: str) -> bool:
        data = self.load()
        if name not in data["actors"]:
            return False
        del data["actors"][name]
        self.save(data)
        return True

    @staticmethod
    def _public(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": record.get("name"),
            "role": record.get("role"),
            "enabled": bool(record.get("enabled", True)),
            "created_at": record.get("created_at"),
            "last_used": record.get("last_used"),
        }


_registry: Optional[BrainActorRegistry] = None


def get_actor_registry() -> BrainActorRegistry:
    global _registry
    if _registry is None:
        _registry = BrainActorRegistry()
    return _registry


def reset_actor_registry() -> None:
    global _registry
    _registry = None


def _is_local(host: str) -> bool:
    return str(host or "").lower() in LOCAL_HOSTS


def _set_request_actor(request: Request, actor: BrainActor) -> BrainActor:
    request.state.brain_actor = actor
    return actor


def get_brain_actor(request: Request) -> BrainActor:
    supplied = request.headers.get("x-brain-token")
    actor_name = (request.headers.get("x-brain-actor") or "").strip()[:80]
    env_token = os.environ.get("NEUROCORE_BRAIN_TOKEN")

    if supplied:
        actor = get_actor_registry().authenticate(str(supplied))
        if actor is not None:
            return _set_request_actor(request, actor)
        if env_token and hmac.compare_digest(str(supplied), str(env_token)):
            return _set_request_actor(
                request, BrainActor(actor_name or "brain-admin", "admin", True)
            )
        raise HTTPException(status_code=401, detail="a valid X-Brain-Token is required")

    # Loopback stays trusted (ADR-031) even when a token is configured for remote callers;
    # otherwise enabling remote access would lock the local dashboard out.
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client else ""
    if _is_local(host):
        return _set_request_actor(
            request, BrainActor(actor_name or "local", "admin", False)
        )

    if env_token:
        raise HTTPException(
            status_code=401, detail="a valid X-Brain-Token is required"
        )

    raise HTTPException(
        status_code=403,
        detail="remote brain access requires an actor token",
    )


def require_role(minimum: str):
    if minimum not in ROLE_LEVEL:
        raise ValueError(f"unknown role '{minimum}'")

    def dependency(actor: BrainActor = Depends(get_brain_actor)) -> BrainActor:
        if ROLE_LEVEL.get(actor.role, -1) < ROLE_LEVEL[minimum]:
            raise HTTPException(
                status_code=403,
                detail=f"role '{actor.role}' cannot perform this action; '{minimum}' required",
            )
        return actor

    return dependency
