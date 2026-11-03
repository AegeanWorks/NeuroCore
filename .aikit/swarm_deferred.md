# NeuroCore swarm — filed-but-deferred list (Stage 3/4 output, not waste)

32 fixes landed (commits 7f1d932, f409048, ea33730, 3489ff5, e9bd5b4,
3293730, 26f8ab9, 28e0c94, 651e99e, 49326b9, 974f343, c41c9c9,
89336b3, a05ac2e, 6010685, 286291d, 9867d1c, 9020d48, f70b8d2,
1928907). 319 repro tests green in the latest cumulative sweep
(25.94s; 2 sandbox-heavy deselected by intent). Each item below was
returned by a swarm agent but NOT implemented, with the reason.
Rule: no file:line + repro = not a finding; fabricated batch fails
the whole batch back to re-analysis.

## Failed back (do not implement until re-analyzed)
- S-BRAIN batch: cited `modules/brain/persistence.py:1188` — file is
  123 lines. Fabricated file:line. All S-BRAIN claims (kernel
  soft-delete counters, LedgerLock await, quota-after-eval,
  ephemeral-risk toasts) unverified until re-analysis.
- S-CORE retention claims: `core/retention.py:102 OnSet`, `:27-28
  datetime.UTC` — no such code (dataclasses + timezone-aware).
  Dropped as fabricated.

## Dropped as unverified (no repro or no match)
- S-CORE episode/run cleanup key-leak — no `core/episode.py`.
- S-CORE flow-runner timer-leak — no timer code at cited range;
  `flow_runner.py:1380-1419` already attributes node name/type.
- S-CORE `_common_root` backslash asymmetry — `zipfile.writestr`
  normalizes `\` to `/` on write; not reproducible via stdlib.
  Experiment reverted, no residue.
- S-EMAIL node `filter_sender` KeyError — `node.py:46` uses `.get`;
  claim matches `service.py` pattern, not verified for node.
- S-LOGIC sandbox json/re removal risk — sandbox supplies its own
  `json` via `restricted_globals`; node-scope imports were dead
  (verified), sandbox path untouched.

## Deferred true-class items (real, need isolated failing-first batches)
- Core router 400-mapping for research/delegation/handoff validation
  (HTML-template routes, separate S-CORE router work).
- `create_article/study/finding` parity done; remaining
  `update_*` field validation (variables/evidence JSON, p_value etc.).
- Marketplace catalog sinks (`marketplace.html:154,179,193,201,219,
  223` — server-controlled id/handle/name) for data-attr refactor.
- Flow-runner `_finalize_run_summary` outcome detail, retention
  paging, session-trace `read_all` memory cap, bridge fan-out
  swallow, SandboxStartupError mapping — each needs its own repro
  + targeted gate run.
- KB/memory/tool/messaging/browser/calendar/chat/meta edge cases
  from swarm docs — triage queue, one module per batch, minimal
  diffs, no scope creep into filed items.
- Landed since v1 of this list, for the record: Hangul U+D7AF token
  range (`651e99e`), planner `max_steps` Why/Fix (`49326b9`),
  memory `recall_access_weight` fallback (`974f343`), marketplace
  file-tree dataset handler (`28e0c94`), calendar fsync parity
  (`c41c9c9`), chat session limit fallbacks (`89336b3`), telegram
  chat-id guard (`a05ac2e`), outbound ledger `.get(status)`
  (`6010685`), KB query hardening (`286291d`), loop-guard limit
  fallbacks (`9867d1c`; sandbox-driven abort test left untouched as
  same spawn-flake class), snapshot budget typed error (`9020d48`),
  heartbeat fraction fallback (`f70b8d2`; sandbox-heavy cancel tests
  excluded from gate run, same flake class), retry-count fallback
  (`1928907`). File-tree catalog sinks (`marketplace.html`
  id/handle/name) remain deferred as stated.

## Gating blocker (sent back to implementation, not fixed here)
- Full `pytest` red on pre-existing Windows sandbox spawn flake
  (`test_logic_nodes` `Script timeout`, 2–4 failures per run;
  clean-tree repro on file via stash cycle). Fix needs either
  sandbox perf work (large) or test-budget relaxation (user
  approval + ADR per PRINCIPLE-014; question tool unavailable in
  goal mode). `npm ai:verify/build` not applicable (no
  `package.json`); `aikit check` exceeds the 300s window.
