# Autonomous Thinker: Literature Report — 2026-11-02

Goal: design guidance for building an **autonomous thinker** — an agent that
sets its own sub-goals, reasons, acts, remembers, reflects, and improves with
minimal supervision — grounded in 2024–2026 literature and mapped onto
NeuroCore's existing brain/memory/tooling components.

Searched 2026-11-02 via live web search (arXiv, ACL/EMNLP Anthology, AAAI,
surveys, leaderboards). Where figures are vendor-reported they are marked.

## 0. TL;DR — the seven pillars

1. **Reason–act loop** (ReAct) with explicit think/act/observe turns.
2. **Reflection** (Reflexion/ExpeL-style verbal self-critique) with
   *programmatic* failure grounding — open-ended self-diagnosis confabulates.
3. **Deliberation budget** (test-time scaling): single-trace, sampled, and
   search regimes chosen per task, with compute reporting.
4. **Memory as Storage → Reflection → Experience**, with maintenance
   (dedupe/conflict/forget) and fact-vs-belief separation.
5. **Skill accumulation** (Voyager-style library + automatic curriculum) for
   lifelong competence growth.
6. **Metacognition**: competence self-assessment, strategy selection, input and
   output self-monitors.
7. **Governance + evaluation**: instruction hierarchy, deterministic guardrails
   with verified evolution, and long-horizon dynamic benchmarks with process
   rewards and guardrail metrics.

## 1. The reasoning core: from ReAct to deliberate search

- **ReAct** (Yao et al., 2023): interleave Thought → Action → Observation.
  Still the default agent scaffold (SPIRAL benchmarks against it; RLVMR
  extends it). Lesson: keep reasoning and acting as *separate, tagged* steps
  so each can be supervised, skipped, or budgeted independently.
- **Reflexion** (Shinn et al., 2023): Actor + Evaluator + Self-Reflection;
  verbal post-mortems stored in episodic memory, prepended next trial.
  91% pass@1 on HumanEval vs 80% GPT-4 baseline. No weight updates —
  learning entirely in context.
- **Verbal Reinforcement Learning (VRL)** (2026 survey): the family name for
  Reflexion/ExpeL/Voyager-style loops — natural-language feedback (self,
  human, tool) used directly as text or distilled into updates. Sub-types:
  episodic (raw logs), semantic (distilled rules), procedural (skills).
- **Tree-of-Thoughts + MCTS**: maintain a frontier of partial solutions,
  score with LLM evaluators, beam/prune or UCT-select. Findings:
  - Efficacy hinges more on the **propose/evaluate quality** than on the
    search algorithm (MCToT study: CoT + finetuned value ≈ MCTS at 1/7 cost).
  - **SPIRAL** (AAAI 2026): Planner + Simulator (world model) + Critic inside
    MCTS with reflection-shaped dense rewards — 83.6% on DailyLifeAPIs
    (+16 pts over next-best search baseline), more token-efficient.
  - **ASE-MCTS** (EMNLP 2026): async self-evaluated MCTS, no external reward
    model; up to 6× wall-clock speedup over sync MCTS.
  - **Const-o-T**: ⟨intent, constraint⟩ pairs prune MCTS to feasible plans.
- **Test-time scaling regimes** (multiple 2026 surveys; IJCAI RAIE taxonomy):
  single-trajectory (longer traces), leaf-level (Best-of-N + voting/verifier),
  prefix-level (ToT/RAP/MCTS). Returns are instance-dependent, can turn
  *negative* (accuracy falling with longer reasoning); report compute with
  the **SCRP** abstraction (topology × evaluation signals × control).
  **ParaThinker**: parallel-path reasoning beats sequential plateaus
  ("tunnel vision") at equal token budgets with lower latency.
- **Practical rule**: default to ReAct + reflection; escalate to
  search/sampling only when the evaluator judges the trace stuck
  (Reflexion's own trigger: repeated same action/response ×3, or >30 steps).

## 2. Memory: Storage → Reflection → Experience

- **Three-stage evolution** (ACL Findings 2026 survey): Storage (faithful
  trajectories) → Reflection (semantic filter, `m′ = F_ref(τ|φ)`, quality
  density over fidelity) → Experience (cross-trajectory abstraction into
  reusable rules, MDL-style compression).
- **Seven-stage pipeline** (2026 systems survey): ingestion → construction
  (absent/deterministic/LLM-mediated/agentic) → storage (vectors, inverted
  index, graphs, multi-store) → retrieval (sparse+dense+graph+rerank, or
  tool-mediated agentic retrieval) → prompt assembly → generation →
  **maintenance** (dedupe, conflict resolution, forgetting, re-embedding).
  Maintenance is "weak or absent" in most systems — the top gap.
- **Memory confabulation** ("Honest Lying", 2026): Reflexion agents store
  confident-but-wrong self-diagnoses and reuse them across trials (16 frozen
  ALFWorld tasks, 0/121 reflections mentioning the right object). Mitigations:
  **programmatic** trajectory-failure extraction (0% → 86% correct mention),
  Reflection Repetition Rate (RRR) metric, limiting memory to 1–3 items.
- **Design patterns that work**:
  - *Hindsight* (ACL 2026 demo): world/experience/observation/**opinion**
    networks — separate facts from beliefs, opinions carry confidence.
  - *DPCM*: System-1 daytime writer (revision chains) + System-2 nighttime
    engine (schema induction, cross-domain patterns).
  - *MGRetrieval*: keyword-pyramid iterative retrieval with sufficiency
    self-judgment (+8.9% F1 on LoCoMo).
  - *WorldEvolver*: episodic + semantic memory + selective foresight with
    prediction–observation mismatch revision.
- **NeuroCore mapping**: `memory` backend (SQLite+FAISS, recall fallback) ≈
  Storage; `knowledge_base` (FTS5+vectors) ≈ retrieval substrate;
  `reflection` lenses + `reasoning_book` ≈ Reflection stage; `brain/learning`
  (experiences/consolidation/held-out recall) ≈ Experience stage. Known gaps
  per prior audit: maintenance policy, embedding-change re-embed, fact/belief
  separation, retrieval-sufficiency judgment.

## 3. Learning to learn: skills, curricula, autotelic goals

- **Voyager** (Wang et al., 2023): automatic curriculum (novelty-maximizing
  task proposals) + ever-growing **executable-code skill library** (embeddexed
  descriptions, compositional reuse) + iterative self-verification.
  3.3× items, 15.3× faster tech-tree milestones; skills transfer to new worlds.
  Takeaway: code, not text, is the durable skill representation.
- **ExpeL / ERL (experiential)**: distill paired success/failure trials into
  reusable heuristics; pool of strategies, not raw logs.
- **ERL (experiential RL, 2026)**: explicit experience→reflection→
  consolidation in the training loop; reflection rewarded by downstream gain
  (up to +81% in sparse multi-step envs); internalized via distillation so
  gains persist without reflection at deploy time.
- **Autotelic/curiosity**: GLAM/SAC-GLAM (online RL grounding of LLMs);
  MAGELLAN (competence estimation → learning-progress goal selection);
  ICL-based curiosity rewards (theory: works for active-learning-style
  settings, biased in general MDPs). Voyager's curriculum is the pragmatic
  in-context form of novelty search.
- **NeuroCore mapping**: `skills` module + tool library ≈ skill library;
  `brain/improvement` (evaluation-gated promotion/rollback) ≈ ERL's
  consolidation gate; `reasoning_book` ≈ heuristic pool. Missing: automatic
  curriculum (self-proposed tasks), compositional skill synthesis,
  learning-progress prioritization.

## 4. Metacognition: the thinker watching itself think

- **MUSE** (2026): continual competence self-assessment + strategy selection
  loop; beats model-based RL and prompt-only agents on novel tasks.
- **Intrinsic metacognition** (PMLR 2025 position): metacognitive knowledge
  (what am I good at) + planning (what to learn) + evaluation (did learning
  work); current agents use *extrinsic* (human-fixed) loops — the ceiling to
  break for true autonomy.
- **RLVMR** (ICLR 2026): tag cognitive steps
  (`<plan>/<explore>/<reflect>/<monitor>`), programmatic rule-based rewards
  (explore→new state, reflect→fixes error); 7B model SOTA 83.6% on hardest
  ALFWorld/ScienceWorld splits; fewer redundant/invalid actions.
- **Self-monitors** (2026): parallel *input* monitor (conflict detection
  pre-generation) + sequential *output* monitor (draft → violation check →
  rewrite); cuts rule-following non-compliance 81–99%.
- **NeuroCore mapping**: `brain/self_model` (live state providers),
  `brain/self_budget` (run budget), `brain/kernel` (modes/gates/kill switch),
  `brain/goal_review` + `trends` (deterministic self-review). Missing:
  explicit plan/explore/reflect/monitor tagging, output-monitor repair pass,
  competence-aware strategy selection.

## 5. Multi-agent deliberation (for hard thoughts, not default)

- **Debate (MAD)**: 141-study survey finds convergence on static,
  fully-connected, verbatim, voting designs *by convention*, alternatives
  marginal. Identity bias (agreeing with own prior output) breaks independence.
- **Routing theory**: MAS ≈ mixture-of-experts with input-dependent routing;
  wins come from routing to the competent agent (relative confidence is the
  main observable proxy). Selection bottleneck: judge-based selection
  (win rate 0.81) ≫ synthesis aggregation (~chance).
- **Model pools**: bigger heterogeneous pools often *degrade* below the best
  single model; same-family groupings do best; homogeneous ensembles scale
  best with aggregation complexity.
- **Frameworks (Oct 2026 status)**: AutoGen maintenance-mode → Microsoft
  Agent Framework; MetaGPT few releases; active: CrewAI, LangGraph, MAF.
  Protocols: MCP (agent↔tool) + A2A (agent↔agent) complementary; ACP–A2A
  convergence.
- **Takeaway for the thinker**: single-agent ReAct+reflection first; add a
  second role (Critic/Judge, or Planner/Simulator/Critic per SPIRAL) only
  behind an evaluator trigger; if multi-agent, use judge selection over
  synthesis and homogeneous pools.

## 6. Safety and governance for an autonomous actor

- **Instruction hierarchy**: System > developer > user > tool; IH-Challenge
  RL training improves steerability + injection robustness without helpfulness
  loss. Extensions: **ManyIH** (up to 12 dynamic privilege tiers, frontier
  ≈40% — hard), runtime input/output self-monitors.
- **Constitutional approaches**: CAI self-critique→revision→RLAIF; agent
  constitutions must govern *actions* (tool use, resources, delegation), not
  words. **EACL**: signed Policy Constitution + Execution Intent Envelopes +
  deterministic Decision Engine + authority attenuation on delegation.
- **VEPA** (2026): guardrail = executable symbolic rules over typed events;
  LLM proposes patches, deterministic verifier admits conflict-free ones,
  evidence-aware authority activates only with support (revoked on
  contradiction). Best of both: adaptive + deterministic.
- **Trustworthiness (TADL, 2026)**: five dimensions (safety/robustness,
  alignment/oversight, transparency/auditability, privacy, compliance);
  failure taxonomy includes indirect injection, memory contamination,
  cross-session leakage; lifecycle gates per phase.
- **NeuroCore mapping**: `brain/kernel` (risk score, gates, kill switch) +
  `brain/guardrails` (deterministic local checks) + approval ledger +
  allowlists + `untrusted` fencing ≈ VEPA's deterministic core with LLM
  proposal (`brain/configuration`, policy-gated). Missing: signed execution
  intent envelopes, delegation attenuation, evidence-gated rule activation.

## 7. How to know it thinks: evaluation

| Benchmark | What it proves | 2026 state |
|---|---|---|
| SWE-bench Verified (500) | real-issue code repair | top ~97% (vendor) |
| SWE-bench Pro (1,865) | multi-file, hours-long edits | <25% pass@1 |
| GAIA → **Gaia2** (mobile apps, noise, multi-agent) | general assistant tool use | GAIA legacy; gap was ~17.5 pts |
| OSWorld 2.x (108 tasks, checkpoint credit) | computer use | ~78% partial (vendor) |
| Terminal-Bench / LHTB 2.0 (77 tasks, 3 h, dense ladders) | sustained terminal progress | binary 0.22 vs dense 0.65 — use process rewards |
| AgencyBench (138 tasks, ~90 tool calls, 1M tokens) | real-world long horizon | closed 48% vs open 32% |
| UltraHorizon (200k+ tok, 400+ calls) | exploration under partial observability | SOTA underperforms humans; simple scaling fails |
| HAL / Harbor | unified cross-env harness | emerging standard |

Design rules: dynamic/live environments over static traces (error cascades
matter); dense progress ladders + `pass^k` robustness + **guardrail metrics**
(success-via-violation = failure); protocol-matched compute reporting (SCRP).

## 8. Architecture proposal: NeuroCore autonomous thinker

```
Perceive (reactivity sources, deduped) → Deliberate → Act → Reflect → Consolidate
              │                              │         │         │
         state.py/self_model          kernel gates  tools   reflection lenses
         + trends detector            + budget     sandbox  + reasoning_book
```

- **Deliberate** (new `think` cycle): ReAct turns with tagged cognition
  (`<plan>/<explore>/<reflect>/<monitor>`, cf. RLVMR); default single-trace;
  evaluator-triggered escalation to sampling (Best-of-N + judge) or
  constrained search (ToT/MCTS with intent–constraint pruning, cf. Const-o-T).
  Difficulty-adaptive budget (cf. SCRP reporting).
- **Self-monitor pair**: input monitor (instruction-conflict scan before
  generation) + output monitor (draft → guardrail/verifier → rewrite), cf.
  §4. Wire into existing `guardrails` + approval ledger.
- **Memory write path**: Storage (episodes, journal) → Reflection
  (programmatic failure extraction first — confabulation guard — then LLM
  critique, RRR-tracked) → Experience (distilled rules into `reasoning_book`,
  skills into tool library as *code*, cf. Voyager). Nightly consolidation job
  (cf. DPCM System-2) with dedupe/conflict/forget maintenance.
- **Curriculum**: `autonomy` proposals extended with learning-progress scoring
  (cf. MAGELLAN): propose tasks maximizing expected competence gain,
  self-verify, promote via `improvement` gates.
- **Governance**: keep kernel kill-switch/budget; add execution-intent
  records (EACL-lite: signed structured intent per high-risk tool call) and
  evidence-gated guardrail updates (VEPA-lite).
- **Evaluation**: internal harness (`evaluation` module) extended with dense
  ladders + `pass^k` + guardrail-violation penalties; track SWE-bench-style
  repair, OSWorld-style computer use, and UltraHorizon-style exploration.

Phasing: (1) tagged cognition + monitors + programmatic reflection grounding;
(2) nightly consolidation + skill-as-code library; (3) curriculum +
competence-driven task selection; (4) EACL-lite intents + benchmark ladders.

## 9. Key references

ReAct (Yao 2023) · Reflexion (Shinn 2023) · ToT (Yao 2023) · ExpeL (Zhao
2024) · Voyager (Wang 2023) · VRL survey (2026) · Memory evolution survey
(ACL Findings 2026) · Agent memory systems survey (2026) · MGRetrieval
(2026) · Hindsight (ACL 2026 demo) · DPCM (EMNLP Findings 2026) ·
WorldEvolver (EMNLP Findings 2026) · SPIRAL (AAAI 2026) · ASE-MCTS (ACL
2026) · TTS surveys (EMNLP/CL/IJCAI 2026, SCRP) · ParaThinker (EMNLP 2026) ·
RAIE taxonomy (IJCAI 2026) · MUSE (2025/26) · Intrinsic metacognition (PMLR
2025) · RLVMR (ICLR 2026) · MAD survey (2026) · MoE-deliberation/FJ (2026) ·
Selection bottleneck (2026) · MAS orchestration surveys (2026) · CAI ·
IH-Challenge (OpenAI 2026) · ManyIH (EMNLP 2026) · VEPA (2026) · EACL
(SSRN 2026) · TADL (2026) · SWE-bench/Pro · Gaia2 · OSWorld 2.x · LHTB 2.0 ·
AgencyBench · UltraHorizon · Agent-eval survey (ACL Findings 2026).
