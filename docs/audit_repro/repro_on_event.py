"""Audit finding M-LIFE-1: module-level @router.on_event handlers never run.

The original defect: main.py created FastAPI(lifespan=...) while ModuleManager included module
routers during that lifespan. FastAPI does not run startup/shutdown event handlers when a
lifespan is used, so modules/brain/router.py (auto-start/resume) and
modules/browser_automation/router.py (browser_service.stop) handlers were dead.

The remediation (main.py:139-151, 189-208) calls the hooks explicitly inside the lifespan.
This script mirrors that remediation with a toy router and expects both hooks to fire.
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
    # M-LIFE-1 remediation, as in main.py: under a lifespan FastAPI never fires the
    # router's on_event hooks, so the lifespan calls them explicitly.
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(lifespan=lifespan)
with TestClient(app):
    pass
print("router events fired:", fired)
print("VERDICT:", "BUG CONFIRMED (module startup/shutdown handlers never run)"
      if fired != ["startup", "shutdown"] else "not reproduced")
