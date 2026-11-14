# NeuroCore Audit Verification + Literature Report — 2026-11-01

Audited commit: `8b5ddcd` on `main` (AegeanWorks/NeuroCore).
Working tree at verification time: `8b5ddcd` + 40 modified files, ~2267+/781- uncommitted (fixes reference audit IDs in comments).
Method: `git show 8b5ddcd:<path>` for baseline; `git diff` for working-tree status; targeted subagent verification; web literature search.

## 1. Verdicts — Critical + High

| ID | Verdict | Baseline evidence | Working tree |
|----|---------|-------------------|--------------|
| C-1 Agent Loop `Any` import | TRUE | `modules/agent_loop/node.py:90,97,119` annotate `Any` with no `typing` import | FIXED (adds `from typing import Any`) but comment carries no `C-1` tag |
| C-2 Unsanitised model HTML | PARTIAL-TRUE | `chat_gui.html:192,408`, `chat_message_streaming.html:84,161` `innerHTML=marked.parse` with zero sanitiser hits — TRUE. `index.html:14` is only the `<script src marked>` include, no sink — sub-claim FALSE | PARTIALLY FIXED: `renderMarkdownSafe/DOMPurify.sanitize` added; still no SRI, no CSP, CDN unpinned in 2 templates, no regression test, API-key render not fully removed |
| H-1 Image refused by default limits | TRUE | `core/llm.py` `len(json.dumps(payload))//4` counts base64; `model_registry.py:149` refuses; `settings.py:30` 32768 ceiling | FIXED (`ensure_ascii=False`, image≈1000 tok) |
| H-2 Nodes run twice (BFS diamond) | TRUE | `flow_runner.py:837,840` queues all nodes upfront, `1124-1136` re-appends children, no readiness gate | FIXED (readiness/defer + routing-skip propagation) |
| H-3 Stale loop output as answer | PARTIAL-TRUE | Substance TRUE (static topological result, no seq). Direction inverted: code is `reversed(execution_order)` = LAST non-empty, not first | FIXED (seq numbers, latest-output wins) |
| H-4 >5 tool calls leave unanswered ids | TRUE | `tools/node.py:81` slices 5, `:63,188` appends all 7 calls with 5 results | FIXED (placeholder result per deferred id) |
| H-5 FTS5 keyword search fails | TRUE | `knowledge_base/backend.py:1343` strips only `" ' * .`, leaves `?-,():`; `:1368` returns `[]` on `OperationalError` | FIXED (word-token quoted OR + bm25 + over-fetch) |
| H-6 Memory recall finds nothing | TRUE | `memory/backend.py:561,582` every >2-char token required via `AND LIKE` | FIXED (tokenize/stop-words/ANY + escape + UI warning) |
| H-7 Reminder loop never starts | TRUE | `main.py:64-65`, `routers.py:971-972` `if has_incoming` skips sourceless repeater | FIXED (sourceless repeaters are triggers + loop registry) |
| H-8 Allowlist doesn't gate nodes | TRUE | `module_manager.py:98-99` gates routes only; `flow_runner.py:74` bare `import_module` | FIXED (`_check_module_policy` on run path + pre-run validate) |
| H-9 Planner completes blocked steps | TRUE | `planner/node.py:187-190` marks `0..current` complete, deps consulted only for next step | FIXED (normalizer + rely on `completed_steps`) |
| H-10 CI never runs / gate null | PARTIAL | Local gate `test_command:null, lint:null` TRUE. CI config DOES contain `pytest -q` + docker bind check — "never ran" is an infra/billing state, not a config absence | PARTIALLY FIXED: `.aikit/project.json` now `pytest -q` + `ruff check`; `ci.yml` still lacks ruff step |

Medium sample (13/13 TRUE, all already fixed in working tree): M-FLOW-1, M-FLOW-3, M-FLOW-4, M-LLM-1, M-LLM-2, M-SEC-1, M-SEC-3, M-DATA-1, M-DATA-2, M-TOOL-3, M-LIFE-1, M-CAL-1, M-CAL-2.

## 2. Working-tree gap analysis (accurate tag search, `(?<![A-Z0-9-])ID(?![0-9A-Z-])`)

Already addressed: C-1(code), C-2, H-1..H-9, H-10(code), M-FLOW-1..4, M-LLM-1..4, M-SEC-1..6, M-SEC-9, M-DATA-1..4, M-DATA-6, M-ASYNC-1..3, M-LIFE-1, M-CAL-1/2, M-TOOL-1..3, L-7, L-19, L-26.
Still open: M-FLOW-5 (journal lease/idempotency/cached-loop + sync fsync on loop), M-FLOW-6 (episode plan overwrite + no-output marking + input mutation), M-FLOW-7 (4× missing `episode_id=`), M-SEC-7 (CDN SRI + CSP), M-SEC-8 (token→cookie login), M-DATA-5 (module re-enable duplicate stores), M-PERF-1 (sessions JSON rewrite + inline base64), M-PERF-2 (metrics rewrite per counter), M-CHAT-1 (full conversations at INFO), plus L-1..L-6, L-8..L-18, L-20..L-25, L-27..L-31.

## 3. Literature — further improvements worth applying

1. Sanitize after parsing with explicit allowlist: `marked.parse` with `html:false`-style handling then `DOMPurify.sanitize(html, {ALLOWED_TAGS, ALLOWED_ATTR})`, block `img/iframe/form` by default; re-sanitize every streaming frame. (OWASP XSS Prevention Cheat Sheet)
2. Constrain URLs + strict CSP: `ALLOWED_URI_REGEXP=/^(https:|mailto:|#|\/)/`, `rel=noopener`, `script-src 'nonce-*' 'strict-dynamic'`, `img-src 'self'`, `object-src 'none'`, `require-trusted-types-for 'script'`. (OWASP / web.dev strict-CSP)
3. Pin + test the sanitizer: pin `marked`+`DOMPurify` with SRI, regression-test `javascript:`/`data:` payloads, prefer no-`dangerouslySetInnerHTML` renderers where possible. (library docs)
4. Proxy/allowlist remote images: disallow arbitrary hosts, allow only trusted prefixes or proxy server-side (prevents tracking/CSRF exfiltration via `![](https://attacker/…)`). (Streamdown/Multigrid guides)
5. Never pass raw input to FTS5 `MATCH`: tokenize `[\p{L}\p{N}]+`, double internal `"`, wrap each in `"…"`, join `OR`/`AND`, bind via `MATCH ?`. (SQLite docs)
6. Quote FTS5 operators as literals (`AND/OR/NOT - : * " ( )`) so `power-on`, `C++`, `Next.js` can't cause `no such column`; `ORDER BY bm25(ft)` + `highlight()/snippet()`. (SQLite FTS5 docs)
7. Validate DAGs with Kahn's sort: fail on cycle/missing dep, cache order; `ready = pending WHERE all(deps in Done/Skipped)`. (orchestrator DAG practice)
8. Readiness scheduling: fire newly-unblocked dependents on `mark_done`, run levels concurrently, keep per-node state + idempotent nodes, `RESERVED(epoch)` lease to prevent double-dispatch. (workflow/DAG patterns)
9. Persist before firing: fsync `next_run_at/last_fired_at` before running (at-most-once crash safety); anchor intervals to slot, not wall-clock `now`. (scheduler/Dapr Jobs practice)
10. `misfire=skip` + capped `catch_up`: collapse missed windows, cap burst (16–100), `MAX_STALENESS` expiry, CAS claim + deterministic slot idempotency key. (Schedy/effect-mq/Bernstein)
11. Harden tool workers beyond `ast` denylists: unprivileged user, `--cap-drop all`, `no-new-privileges`, read-only rootfs, `seccomp: RuntimeDefault`, no network by default, cgroup CPU/mem/timeout; gVisor/Kata for untrusted LLM code. (OWASP container hardening / gVisor docs)

## 4. Implementation plan (original) vs §5 outcome

- P0 (done in working tree, verify): C-1, H-1..H-9, M-FLOW-1..4, M-LLM-1..4, M-SEC-1..6/9, M-DATA-1..4/6, M-ASYNC-1..3, M-LIFE-1, M-CAL-1/2, M-TOOL-1..3.
- P1 (this session): C-2 hardening (SRI + CSP middleware + API-key render check + regression test), H-10 (ruff in CI), M-FLOW-7 (`episode_id=` ×4), M-FLOW-6 (plan overwrite guard), M-CHAT-1 (log redaction), L-1 (`exc` shadowing), L-9/L-10 (`hmac.compare_digest` bytes).
- P2 (roadmap, not this session): M-FLOW-5 (journal lease + keyed steps + off-loop IO), M-DATA-5/M-PERF-1/M-PERF-2 (SQLite consolidation), M-SEC-7/8 (vendored assets + cookie login), scheduler service, embedding management, flow linting.

## 5. This-session implementation (P1) + validation

- M-FLOW-6: episode `plan`/`current_step`/`completed_steps` now only overwritten when present in node output; no-output runs marked FAILED.
- M-CHAT-1: `[Chat] flow_result` INFO log replaced with DEBUG sizes-only.
- L-9/L-10: debug-key check via `hmac.compare_digest(str)`; token compare on UTF-8 bytes (non-ASCII → False, not 500).
- L-1: `node_retry` inner handlers renamed (`_parse_exc`/`_cap_exc`).
- C-2: CSP + nosniff + Referrer-Policy middleware (`SecurityHeadersMiddleware`); fail-closed sanitize fallback kept; new `tests/test_xss_hardening.py` (4 tests).
- H-10: CI + local gate run `ruff check --select F821,F823`; `pyproject` per-file-ignores for sandbox-injected `args`/`httpx`; fixed real F821 (`SnapshotBudget` TYPE_CHECKING) and F823 (`test_email_bridge` mime import); removed working-tree-introduced F401/F841.
- M-ASYNC-3 regression fixed: producer completion no longer pops the stream queue (late readers kept working); `_register_stream` bounds retention at 50; stream fallback uses patchable module-global session manager. `tests/test_chat_brain_turn.py` 5/5 again.
- M-TOOL-3 fallout fixed: restored `modules/tools/library/Calculator.py` (was deleted from worktree); test helper resolves case-insensitively.

Validation: `ruff check --select F821,F823 .` clean; full suite `pytest -q --ignore=tests/test_e2e.py`: **2406 passed** (e2e skipped, L-30 pre-existing).

## 6. P2 batch 1 (this session, working tree)

- M-FLOW-5: journal `run_registry.heartbeat_at` lease (default 600 s, `step_journal_lease_seconds` setting) with stale-`running` reclaim; `derive_run_id` random without an explicit idempotency key (completed-attach only on key); `(run_id, node_id, iteration)` step keys with in-place migration of legacy DBs; all runner journal I/O via `asyncio.to_thread`; 3 new tests.
- M-PERF-2: counters flush at most every 30 s / 100 dirty increments + `flush()` on lifespan shutdown; `reset()` clears dirty state; batching regression test.
- M-SEC-8: `POST /login` (form or JSON, rate-limited) exchanges the API token for an HttpOnly SameSite=Strict session cookie; `POST /logout`; guard exempts `/login` and accepts live sessions; 7 tests.
- M-DATA-5: `shutdown_module`/`shutdown`/`stop` stop-hook runs on disable and before sys.modules flush on re-enable; 2 tests. L-7 verified already fixed (auto-start only when flow in active list); L-8 verified already fixed (per-hook try/except).
- M-PERF-1 (incremental): `/chat/send` rejects non-raster types (SVG out) and >5 MB uploads before base64 inline; 2 tests. Full SQLite migration stays roadmap.
- L-14: `whatsapp_webhook_secret` config + `x-webhook-secret` enforcement when set, warn-once otherwise; 3 tests. L-15: bridge cache compares slash-normalized URLs. L-24: literal `\n` in auto-rename prompt fixed to real newlines (other mojibake already fixed in tree). L-27: all 28 `datetime.utcnow()` replaced with timezone-aware `now(timezone.utc)` (Z-suffixed format preserved).
- L-16: debug log serialization lazy (`isEnabledFor` + truncation). L-20: trace rotation at 5 MB (keeps most recent half, atomic replace, test). L-21: per-loop locks in a `WeakKeyDictionary` (no id-keyed leak/reuse; test).

## 7. P2 batch 2 (this session, working tree)

- L-2: deleted 3 byte-identical copies of the prompt-registry routes in `modules/brain/router.py` (944 → 679 lines); F811 clean.
- L-3: removed the shadowed shallow `set_tool_count` / `set_remaining_tool_calls` in `core/flow_data.py`, keeping the deep-copying defs; F811 clean, 82 flow_data tests pass.
- L-5: new names must match `^[A-Za-z0-9_-]{1,64}$` (`_validate_tool_name`, 400 on violation); legacy names keep the lenient sanitizer so they can be renamed away; 5 tests.
- L-22: runner loop-cap fallback reads `DEFAULT_SETTINGS` (100), no more divergent 1000.
- L-23: start-node fallback and bridge peer-edge lookups use the precomputed `incoming_edges` map instead of per-node scans.
- L-6: `_validate_flow_structure` flags empty router branch lists (`true/false/rlm/standard_branches`) and branch ids pointing at unknown nodes; 2 tests.
- L-18: corrupt/non-object versions file is quarantined (`.corrupt-<stamp>`) like the flow store instead of silently reset; 1 test.
- L-30: `requests>=2.31` declared in both `requirements-dev.txt` and the pyproject `dev` extra (packaging invariant requires both); e2e suite no longer import-skipped in fresh envs.

Final validation: `ruff --select F821,F823,F811` clean on touched files; full suite `pytest -q --ignore=tests/test_e2e.py`: **2432 passed, 0 failed** (one packaging exact-match test caught the requirements/pyproject drift and now passes; e2e skipped, L-30 pre-existing gate).

Final validation: `ruff check --select F821,F823 .` clean; full suite `pytest -q --ignore=tests/test_e2e.py`: **2427 passed, 0 failed** (e2e skipped, L-30 pre-existing).
