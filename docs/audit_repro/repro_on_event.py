"""Audit finding M-LIFE-1: module-level @router.on_event handlers never run.

main.py creates FastAPI(lifespan=...) and ModuleManager includes module routers during that
lifespan. FastAPI does not run startup/shutdown event handlers when a lifespan is used, so
modules/brain/router.py (auto-start/resume) and modules/browser_automation/router.py
(browser_service.stop) handlers are dead. This script mirrors that setup with a toy router.

Run:  python docs/audit_repro/repro_on_event.py
"""
import warnings
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

warnings.simplefilter("ignore")
fired = []
router = APIRouter()


@router.on_event("startup")
async def on_startup():
    fired.append("startup")


@router.on_event("shutdown")
async def on_shutdown():
    fired.append("shutdown")


@asynccontextmanager
async def lifespan(app):
    app.include_router(router, prefix="/brain")  # what ModuleManager.load_enabled_modules does
    yield


app = FastAPI(lifespan=lifespan)
with TestClient(app):
    pass
print("router events fired:", fired)
print("VERDICT:", "BUG CONFIRMED (module startup/shutdown handlers never run)" if not fired
      else "not reproduced")
