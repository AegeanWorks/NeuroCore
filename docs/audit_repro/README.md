# Audit reproduction scripts

Evidence for [`docs/CODEBASE_AUDIT_2026-11-01.md`](../CODEBASE_AUDIT_2026-11-01.md). Each script
reproduces one or more findings and prints a `VERDICT:` line. They are not part of the test suite
(pytest only collects `tests/`). Once a finding is fixed, port its script into a regression test
under `tests/test_<area>.py` and delete it from here.

## Running

From the repository root, with the project dependencies installed:

```bash
python docs/audit_repro/run_all.py
```

`run_all.py` gives every script a fresh temporary `NEUROCORE_STATE_DIR`, so no settings, flows or
databases in the working tree are touched. No network access, LLM server or browser is needed.
To run one script on its own:

```bash
NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_flow_runner.py
```

## Index

| Script | Findings | What it shows |
|---|---|---|
| `repro_flow_runner.py` | H-2, H-3, H-9, M-FLOW-1…4 | Duplicate node runs, stale final result, inflated usage, bridge re-runs, escaping/masked exceptions, planner skipping a blocked step |
| `repro_vision_clamp.py` | H-1 | One 150 KB image is refused by the default `local-model` limits |
| `repro_dispatcher.py` | H-4 | More than `max_tools_per_turn` tool calls leaves unanswered `tool_call` ids and drops the rest |
| `repro_fts.py` | H-5 | Knowledge-base keyword search errors out (and returns `[]`) for ordinary questions |
| `repro_mem_keyword.py` | H-6 | Memory keyword fallback (default install) finds nothing for natural-language questions |
| `repro_repeater.py` | H-7 | The default flow's reminder loop is never auto-started |
| `repro_allowlist.py` | H-8 | `module_allowlist` does not stop a flow from running a module's node code |
| `repro_llm_retry.py` | M-LLM-1, M-LLM-2 | 401 / `model_limit` retried 4×; `request_timeout` not applied to the LLM node |
| `repro_fence.py` | M-SEC-1 | A tool result that begins with a fence-shaped header is passed through unfenced |
| `repro_on_event.py` | M-LIFE-1 | Router-level `on_event` handlers never run under the app's lifespan |
| `repro_retry.py` | L-1 | `run_with_retry` raises `UnboundLocalError` on a malformed `retry_after` |

Last run (2026-11-01, Python 3.13, at commit `8b5ddcd`): **19 confirmed verdicts, 0 not reproduced.**
