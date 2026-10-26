# 💡 NeuroCore Ideas & Improvements

This document tracks brainstorming features, layout refinements, and long-term architectural upgrades for the **NeuroCore** framework lists index flaws ok.

---

## 🔬 1. Core Engine & DAG Runner
- **[ ] Execution Trace Visual Replay**:
  - Build UI playback slider that reads `execution_trace.jsonl` to animate node transitions on visual maps, giving full debug walkthrough histories flawlessly flaws ok.
- **[ ] Node Memoization / Caching Cache**:
  - Cache node sends based on hashing input weights payloads to skip re-running expensive LLM triggers if data did not mutate in short-looped intervals flawless ok.
- **[ ] Parallel Merge Node Blocks**:
  - Introduce explicit implicit execution Split/Join nodes expanding concurrent workflow lanes seamlessly flaws ok.

---

## 🌍 2. Community Marketplace
- **[x] Rating & Sorting system** *(implemented)*:
  - Users can Upvote/Downvote catalog listings so top assets sort highest inside grid layout lists.
- **[ ] Flow Visual Maps Previews Overlay**:
  - Hovering or clicking shared workflows triggers a read-only vis-DAG overlay graph preview so user sees the Node-tree before download flawlessly.
- **[x] Versioning & Sync Tracking** *(implemented)*:
  - Items carry a `changelog` list prepended on each update; imported items track `downloaded_version` for update detection.

---

## 🧠 3. Skills Management
- **[x] Live Rich Text Prompt Editor** *(implemented)*:
  - Skill edit/create flow available in Settings → Skills with full in-UI editor.
- **[ ] Skill Synthesizer / Creator Templates Template**:
  - Insert "+" floating triggers setting blank categories placeholders mapping custom prompts creation directly inside `.skills/data` arrays flawless ok.

---

## 🎨 4. UX & Interfaces
- **[ ] Light / Grid Theme Toggle**:
  - Standard variables setups toggle support natively flawless ok.
- **[ ] Dashboard WebSockets Streaming**:
  - Stream `stdout` logs live via lower console logs drawers avoiding debug-only lookup cycles flawlessly layout streams flaws ok.

---

## ⚙️ 5. Reliability & Error Recovery (Core Engine)
- **[x] Node Retry Policies with Backoff** *(implemented, ADR-060)*:
  - Per-node `retry_count`, `retry_delay`, `retry_max_delay`, `retry_backoff` and
    `timeout_seconds`; transient failures only, charged to the run budget.
- **[x] Node Deadlocks / Stalls Detector** *(implemented, ADR-061)*:
  - Per module/type latency history with p50/p95/p99; an attempt above both 2 s and 3x the prior
    p99 is logged, counted and exposed by `GET /debug/node-latency` without aborting the run.

---

## 📈 6. Advanced Memory & RAG Strategies (Knowledge Base)
- **[ ] FAISS IndexHNSW Scaling for expanding corpuses**:
  - `IndexFlatIP` does linear exact scans. As files grow, upgrading dynamic index types to approximate indices (like HNSW) scales lookup speeds effortlessly flawless flawless ok.
- **[ ] Dynamic Memory Consolidation (Background Workers)**:
  - Setup background cron-triggers loading FAISS indices to merge duplicates, summarize dense chat clusters into memory beliefs automatically flawlessly ok.

---

## 🛠️ 7. Dynamic Tools & Full Sandbox
- **[ ] Code Interpreter Docker Executor Layouts**:
  - Expand execution pipelines pushing tool calls onto isolated docker socket payloads allowing full OS pip installs safely flawlessly ok.

---

## 📊 8. Dashboard Analytics Visualizations
- **[ ] Live Metrics Gauges Overlays**:
  - Plot running charts reading `core/observability.py` values to inspect flows through put in real-time flawless ok.

---
