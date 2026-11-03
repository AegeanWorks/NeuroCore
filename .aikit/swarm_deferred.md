# NeuroCore swarm — filed-but-deferred list (Stage 3/4 output, not waste)

54 fixes landed (commits 7f1d932, f409048, ea33730, 3489ff5, e9bd5b4,
3293730, 26f8ab9, 28e0c94, 651e99e, 49326b9, 974f343, c41c9c9,
89336b3, a05ac2e, 6010685, 286291d, 9867d1c, 9020d48, f70b8d2,
1928907, 9b81cdb, ac42d74, 1552972, a47c3c3, a0015c8, 05c9aa3,
537f45a, 8421221, 1861d1e, 4653f68, 80905de, 05692f6, 7aa4796,
a3d480f, 5dee550, b783dca, cccc638, d4bb81b, 2e1e1fa, af1de74, e1f5770, 23a9892, 781cac0). 319 repro tests
green in the latest cumulative sweep (25.94s; 2 sandbox-heavy
deselected by intent) plus 3 approvals (19.92s), 10 LLM (2.76s),
11 chat-node (14.07s), 1 plan-validation (9.26s), 1
replan-validation, 8 goal-compress (2.41s), 2 email-service
(14.04s), 1 reflection-timeout (31.40s), 1 event-bus limit
(2.80s), 1 rotate-guard (1.69s), 12 budget-file (5.62s), and 1 trend-atomicity (7.12s), and 4 router-validation (2.76s), and 2 gauge-guard (2.49s), and 1 state-coercion after. Each item
below was returned by a swarm agent but NOT implemented, with the
reason.
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
- Core router 400-mapping for delegation/handoff validation (HTML-template routes, separate S-CORE router work; brain-router numerics closed in `e1f5770`).
- `create_article/study/finding` parity done; remaining
  `update_*` field validation (variables/evidence JSON, p_value etc.).
- Marketplace catalog sinks (`marketplace.html:154,179,193,201,219,
  223` — server-controlled id/handle/name) for data-attr refactor.
- Flow-runner `_finalize_run_summary` outcome detail, retention
  paging, session-trace `read_all` memory cap, bridge fan-out
  swallow, SandboxStartupError mapping — each needs its own repro
  + targeted gate run.
- S-BRAIN-R2 B4 (config-file race) DROPPED after failed repro: `apply` runs under the store file lock with refresh, so the read-modify-write is already serialized; concurrent applies keep both keys (verified live).
- S-BRAIN-R2 B6 (ledger lock held across slow eval/publish): `improvement.evaluate` awaits the evaluation runner and `reactivity` awaits bus publish inside `@engine_transaction_async`; a second concurrent evaluate times out at 10s. Needs a split into lock-free run + locked commit with quota re-validation, plus a slow-runner contention test; structural refactor, not a minimal diff. Repro: two concurrent `evaluate` calls on a 30s-task suite, second raises filelock Timeout.
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
  (`1928907`), approvals ledger `.get(status)` (`9b81cdb`; parity
  with outbound), LLM timeout fallback (`ac42d74`), agent-loop
  config validation (`1552972` main block; `a47c3c3` RLM/Hybrid/
  GoalPursuit + replan counts, fast tests, no sandbox; deeper
  SubCall/REPL/synthesis internals deferred as own batch), retention
  validation + prune-loop fallbacks (`a0015c8`), GoalPursuit
  coverage test (`05c9aa3`), SubCall error-dict guard (`537f45a`),
  catalog listing dataset handlers (`8421221`; publisher-controlled
  handle/name). Vote `item.id` sinks CLOSED with provenance
  evidence (catalog ids are server-side `uuid4`, routers.py:2643;
  flow-import ids sanitized, routers.py:2299-2301 — not attacker
  input), chat malformed tool_calls fall-through (`1861d1e`),
  GoalPursuit plan-creation validation (`4653f68`; LLM-call
  numerics guarded, same fast-test pattern), _replan numerics
  (`80905de`; same class, fast test, no sandbox), lesson/synthesis
  threshold fallbacks (`05692f6`; tuning knobs, no LLM on fallback
  paths), repl-state budget validation (`7aa4796`; in-scope
  helpers; DecomposeGoal depth reads verified safe by provenance),
  email bridge port/interval validation (`a3d480f`; fail-fast typed
  errors, proven no-thread/no-network via direct `_start_poller`
  test), cycle-count coercion on brain-state load (`781cac0`; hand-edited state crashed the next cycle), brain router numerics to typed 400s (`e1f5770`; 4 HTTP-level tests, closes the brain half of the router item), replan LLM-call numerics (`5dee550`; grouped typed error,
  same fast-test pattern), reflection-save timeout fallback
  (`b783dca`; bare float discarded a whole successful run — proven
  via empty-content repro, full-run mocked test), event-bus
  non-positive limit (`cccc638`; from audited S-BRAIN-R2
  re-analysis after the original batch failed for a fabricated
  file:line), rotate-guard for disabled actors (`d4bb81b`;
  fail-closed, same re-analysis batch). learning-write budget on evaluation outcomes (`2e1e1fa`; reserve/skip/release pattern, mocked-runner test). trend record/detect atomicity (`af1de74`; ledger lock across RMW, deterministic rendezvous test 20-vs-40). malformed gauge/threshold skip (`23a9892`; per-metric skip with warning, exact-line failing-first tests).
  DecomposeGoal depth reads (`node.py:3306-3307`) and
  `sub_step_timeout` (`:3332`) CLOSED with evidence: full-file
  audit shows entry-validated provenance and a pre-existing try
  around the wait_for — no public repro path remains.
## Gating blocker (sent back to implementation, not fixed here)
- Full `pytest` red on pre-existing Windows sandbox spawn flake
  (`test_logic_nodes` `Script timeout`, 2–4 failures per run;
  clean-tree repro on file via stash cycle). Fix needs either
  sandbox perf work (large) or test-budget relaxation (user
  approval + ADR per PRINCIPLE-014; question tool unavailable in
  goal mode). `npm ai:verify/build` not applicable (no
  `package.json`); `aikit check` exceeds the 300s window.
