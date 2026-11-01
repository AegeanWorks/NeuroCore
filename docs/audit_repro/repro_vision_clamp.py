"""Audit finding H-1: default model limits refuse a chat request carrying one ordinary image.

core/llm.py estimates context as len(json.dumps(payload)) / 4, so base64 image bytes count as text,
and core/model_registry.py raises ModelLimitExceeded above max_context_tokens even outside strict
mode. DEFAULT_SETTINGS gives the default model "local-model" a 32768-token context ceiling.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_vision_clamp.py
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from core.llm import _clamp_request, _estimate_context_tokens  # noqa: E402
from core.model_registry import ModelLimitExceeded  # noqa: E402

image = base64.b64encode(os.urandom(150_000)).decode()  # about the size of a phone JPEG
messages = [{"role": "user", "content": [
    {"type": "text", "text": "What is in this picture?"},
    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
]}]
print("estimated context tokens for one 150 KB image:", _estimate_context_tokens(messages))
try:
    _clamp_request("local-model", 2048, messages)
    print("VERDICT: not reproduced (request allowed)")
except ModelLimitExceeded as error:
    print("VERDICT: BUG CONFIRMED (request refused):", str(error)[:90])

turkish = [{"role": "user", "content": "Şişli'deki çağrı merkezi ığdır " * 10}]
print(f"non-ASCII inflation: {len(turkish[0]['content'])} chars -> {len(json.dumps(turkish))} JSON chars "
      f"-> {_estimate_context_tokens(turkish)} estimated tokens")
