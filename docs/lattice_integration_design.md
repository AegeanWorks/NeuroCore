# Lattice-NeuroCore Integration Design

## Overview

This document outlines the design for integrating **lattice** (a provenance-first research knowledge graph) into **neurocore's research module**. The goal is to leverage lattice's evidence graph, claim extraction, and MCP server as the backend for neurocore's research entities (hypotheses, articles, study designs, findings).

**CRITICAL REQUIREMENT: Zero detail loss from lattice.** Every lattice feature, capability, data field, CLI command, MCP tool, web UI component, and analytical function must be preserved and accessible in neurocore. No feature degradation, no field dropping, no simplification.

## Current State Analysis

### Lattice Capabilities - Complete Feature Inventory (ALL MUST BE PRESERVED)

#### Core Data Model (Zero Field Loss)
- **Paper** (`p_` prefix, SHA-1 of DOI/arXiv/normalized title):
  - `id`, `title`, `authors`, `year`, `venue`, `doi`, `arxiv`, `url`, `abstract`, `tags`, `study_type`, `kind`, `created_at`
  - Deduplication: DOI → arXiv ID → normalized title/year pair (fills missing fields on merge)
  - Study types (29 canonical): Meta-Analysis, Systematic Review, Literature Review, Review, Randomized Controlled Trial, Cohort Study, Case-Control Study, Cross-Sectional Study, Observational Study, Qualitative Study, Case Study, Experimental Study, In Vitro Study, Animal Study, Computational Study, General Study (+ inference rules from title/abstract)
  - Tags: derived from title/abstract (weighted, stopword-filtered, deduped, max 6) or explicit from ingestion

- **Claim** (`c_NNNNNN` sequential):
  - `id`, `paper_id`, `text` (assertion), `quote` (exact source text, REQUIRED), `locator` (page/section), `stance` (supports/contradicts/mentions), `confidence` [0.0-1.0], `created_at`
  - Validation: non-empty text + quote + existing paper; stance enum; confidence bounds

- **Question** (`q_NNNNNN` sequential):
  - `id`, `text`, `status` (open/resolved/abandoned), `created_at`

- **Edge** (unique per src+dst+kind):
  - `id` (int), `src` (ref: paper:p_xxx / claim:c_xxx / question:q_xxx), `dst` (ref), `kind` (cites/supports/contradicts/replicates/extends/uses_method/measures/answers/mentions/related), `note`, `created_at`, `details` (dict)
  - Reference validation: prefix matching, format enforcement

#### CLI Commands (ALL MUST BE ACCESSIBLE)
```
python -m lattice [--db PATH] [--json] init
python -m lattice [--db PATH] [--json] ingest PATH [PATH ...]          # BibTeX, RIS, CSV, Markdown, text
python -m lattice [--db PATH] [--json] fetch QUERY [--source offline|arxiv|crossref|openalex|europepmc] [--limit N]
python -m lattice [--db PATH] [--json] extract [PAPER_REF] [--all] [--question Q_REF]
python -m lattice [--db PATH] [--json] claim --paper REF --text TEXT --quote QUOTE [--locator LOC] [--stance S] [--confidence C]
python -m lattice [--db PATH] [--json] question TEXT
python -m lattice [--db PATH] [--json] link SRC DST --type KIND [--note NOTE]
python -m lattice [--db PATH] [--json] search QUERY [--stance S] [--min-confidence C] [--year Y] [--tokens N]
python -m lattice [--db PATH] [--json] show REF
python -m lattice [--db PATH] [--json] subgraph REF [--depth 1..3] [--tokens N]
python -m lattice [--db PATH] [--json] related REF [--limit N] [--tokens N]
python -m lattice [--db PATH] [--json] outline [--tokens N]
python -m lattice [--db PATH] [--json] ask TARGET
python -m lattice [--db PATH] [--json] brief TARGET [--tokens N]
python -m lattice [--db PATH] [--json] review TARGET [--out PATH]
python -m lattice [--db PATH] [--json] synthesize TARGET [--tokens N] [--out PATH]
python -m lattice [--db PATH] [--json] analyze [--auto-link] [--limit N]
python -m lattice [--db PATH] [--json] export PATH [--format jsonl|bibtex]
python -m lattice [--db PATH] [--json] import PATH
python -m lattice [--db PATH] [--json] stats
python -m lattice [--db PATH] check
python -m lattice [--db PATH] mcp
python -m lattice [--db PATH] web [--host 127.0.0.1] [--port 8765] [--demo] [--open]
```

#### MCP Server Tools (ALL MUST BE AVAILABLE TO NEUROCORE AGENTS)
- **Discovery**: `discover_papers`, `add_paper`
- **Extraction**: `extract_claims`
- **Graph Read**: `search`, `get_entity`, `neighbors`, `subgraph`, `related_findings`, `outline`
- **Graph Write**: `add_claim`, `add_question`, `set_question_status`, `add_edge`
- **Analysis**: `brief`, `review`, `synthesize`, `analyze_graph`, `detect_contradictions`
- **Snapshots**: `export_snapshot`, `import_snapshot`
- **Stats**: `stats`
- **Resources**: `lattice://stats`, `lattice://questions`, `lattice://matrix`
- **Prompts**: `literature-synthesis`

#### Web Research Studio (ALL UI COMPONENTS MUST BE EMBEDDABLE/REPLICATED)
- **Tab 1: Discovery** - Multi-source search (offline, arxiv, crossref, openalex, europepmc), federated queries, deduplication preview
- **Tab 2: Corpus Outline** - Entity counts, study-type breakdown, edge-relation breakdown, evidenced question refs, "Synthesize"/"Explore Map" links
- **Tab 3: Literature Search** - Tag chips, study-type filter, full-sentence search, results with study-type labels, "Export Filtered BibTeX"
- **Tab 4: Subgraph Explorer** - Interactive SVG, paper/claim/question nodes with study-type, edges colored by relation (dashed amber for `related`), legend, "Connect Findings" action
- **Tab 5: Evidence Matrix & Contradictions** - Paper x Question matrix, contradiction pairs with auto-link, coverage gaps
- **Tab 6: Synthesis** - Consensus score [-1,1], thematic clusters with verbatim quotes, research gaps, follow-up queries, Markdown + BibTeX copy buttons
- **Stats Bar** - Entity counts, "Export BibTeX" (full corpus), JSONL snapshot download
- **Browser Paste-Ingestion** - BibTeX/RIS/CSV/Markdown via `/api/ingest-text`
- **One-click demo seeding** (`--demo` flag)

#### Analytical Functions (ALL MUST BE CALLABLE)
- **Extraction**: `extract_claims_for_paper` (empirical sentence segmentation → stance/confidence inference → question auto-linking)
- **Analysis**: `detect_contradictions` (supports vs contradicts on shared question/domain), `auto_link` (idempotent edge creation), `rank_influential_entities` (keystone centrality), `build_evidence_matrix` (Paper x Question), `find_related_findings` (shared question OR ≥2 domain keywords), `link_related_findings` (deterministic `related` edges, capped at 3/claim, idempotent)
- **Synthesis**: `synthesize_question` (confidence-weighted consensus [-1,1], thematic clustering, gap identification, follow-up queries, Markdown + BibTeX)
- **Snapshots**: Atomic JSONL export (`temp` + `fsync` + `os.replace`), idempotent merge import (fills missing, never overwrites)
- **BibTeX**: `export_bibtex` (query/tag/study_type/year filters), atomic file write, CLI auto-detect by `.bib` extension

#### Token-Frugal Context Views (ALL BUDGETS PRESERVED)
- `outline` (~120 tokens): counts, edge kinds, study-type mix, evidenced question refs
- `compact_search` (default 800 tokens): truncated abstracts, stable `ref`, `omitted_*` counts
- `compact_related` (default 600 tokens): snippet + ref + relation + reason + study_type
- `compact_subgraph` (default 800 tokens): packed nodes/edges, `omitted_*` counts
- `get_entity` / `neighbors`: full payloads (explicit drill-down)
- `MIN_TOKENS = 100` floor; budgets bind item lists not JSON envelope

#### Ingestion Formats (ALL SUPPORTED)
- BibTeX (`.bib`, `.bibtex`): nested braces, quoted values, `keywords`, `studytype`
- RIS (`.ris`): TY, TI, AU, PY, DO, UR, AB, KW, M3, ER
- CSV (`.csv`): header mapping, `keywords`/`tags`, `study_type`/`type`
- Markdown (`.md`): YAML front matter (`tags`, `study_type`), heading/filename title
- Plain text (`.txt`): filename=title, body=abstract
- PDF: explicitly OUT OF SCOPE (convert first)

### NeuroCore Research Module Entities
- **Hypotheses**: Title, statement, type, status, domain, variables, confidence, evidence links
- **Articles**: Title, authors, year, journal, DOI, URL, abstract, keywords, type, domain, key_findings, methodology, source, related_hypotheses
- **Study Designs**: Title, study_type, hypothesis_id, population, sample_size, design details, status
- **Findings**: Title, summary, conclusion, study_id, hypothesis_id, statistical_test, p_value, effect_size, significance, status, limitations, implications
- **Interventions**: Name, description, dates, metrics, events

## Integration Architecture

### 1. Data Model Mapping - Zero Field Loss (Exhaustive)

| Lattice Field | NeuroCore Target | Preservation Strategy |
|---|---|---|
| **Paper → Article** |
| `paper.id` (`p_` + SHA1) | `article.id` | Direct mapping; neurocore stores lattice_id as foreign key |
| `paper.title` | `article.title` | Direct |
| `paper.authors` | `article.authors` (JSON array) | Direct; lattice stores as string, neurocore as array |
| `paper.year` | `article.year` | Direct |
| `paper.venue` | `article.journal` | Direct |
| `paper.doi` | `article.doi` | Direct; deduplication key |
| `paper.arxiv` | `article.url` (if arXiv URL) | Map arXiv ID → `https://arxiv.org/abs/{id}` |
| `paper.url` | `article.url` | Direct |
| `paper.abstract` | `article.abstract` | Direct |
| `paper.tags` | `article.keywords` (JSON array) | Split on `, `; lattice derives, neurocore stores array |
| `paper.study_type` | `article.article_type` | Direct mapping (29 canonical types) |
| `paper.kind` | — | Always "paper"; implicit |
| `paper.created_at` | `article.created_at` | Direct |
| **Deduplication logic** | `article.source` + custom fields | Preserve: DOI/arXiv/title+year merge behavior; neurocore adds `lattice_dedupe_key` |
| **Claim → Evidence (Hypothesis.evidence + Article.key_findings)** |
| `claim.id` (`c_NNNNNN`) | `evidence.claim_id` + `key_finding.claim_id` | Direct; neurocore stores lattice claim_id |
| `claim.paper_id` | `evidence.paper_id` / `key_finding.paper_id` | Direct FK to article |
| `claim.text` | `evidence.assertion` / `key_finding.text` | Direct |
| `claim.quote` | `evidence.exact_quote` / `key_finding.quote` | **REQUIRED** - never dropped; neurocore validates presence |
| `claim.locator` | `evidence.locator` / `key_finding.locator` | Direct (page/section) |
| `claim.stance` | `evidence.stance` (supports/contradicts/mentions) | Direct enum |
| `claim.confidence` | `evidence.confidence` / `key_finding.confidence` | Direct float [0,1] |
| `claim.created_at` | `evidence.created_at` | Direct |
| **Question → Hypothesis** |
| `question.id` (`q_NNNNNN`) | `hypothesis.id` | Direct; neurocore stores lattice question_id |
| `question.text` | `hypothesis.statement` | Direct |
| `question.status` | `hypothesis.status` (proposed/supported/contradicted/resolved/abandoned) | Map: open→proposed, resolved→supported/contradicted, abandoned→abandoned |
| `question.created_at` | `hypothesis.created_at` | Direct |
| **NeuroCore-only hypothesis fields** | `hypothesis.title`, `hypothesis_type`, `domain`, `variables`, `confidence_level`, `independent_variable`, `dependent_variable`, `notes` | Stored in neurocore; synced to lattice question `details` JSON |
| **Edge → Evidence Links + StudyDesign Relations** |
| `edge.src` + `edge.dst` + `edge.kind` | `evidence.links` (JSON array of {src, dst, kind, note}) | Full edge preserved; neurocore stores as structured JSON |
| `edge.kind` (10 types) | `evidence.edge_kind` | Direct enum: cites/supports/contradicts/replicates/extends/uses_method/measures/answers/mentions/related |
| `edge.note` | `evidence.link_note` | Direct |
| `edge.details` | `evidence.link_details` | Direct JSON |
| `edge.created_at` | `evidence.link_created_at` | Direct |
| **Analysis Outputs → Findings + Statistics** |
| `ContradictionPair` (claim_a, claim_b, question, stance_a, stance_b) | `finding.contradiction_pair` (JSON) | Full object preserved |
| `keystone_rank` (paper/claim centrality) | `article.keystone_score` / `evidence.keystone_score` | Direct float |
| `evidence_matrix` (Paper x Question) | `hypothesis.evidence_matrix` (JSON) | Full matrix preserved |
| `related_findings` (claim_id, related_claim_id, relation, reason, study_type) | `evidence.related_findings` (JSON array) | Full array preserved |
| **Synthesis Outputs → Findings + PaperWriter** |
| `consensus_score` [-1, 1] | `finding.consensus_score` | Direct |
| `thematic_clusters` (claims grouped, verbatim quotes) | `finding.thematic_clusters` (JSON) | Full structure preserved |
| `research_gaps` | `finding.research_gaps` (JSON array) | Direct |
| `follow_up_queries` | `finding.follow_up_queries` (JSON array) | Direct |
| `markdown_synthesis` | `finding.synthesis_markdown` | Direct |
| `bibtex_references` | `finding.bibtex` / `article.bibtex` | Direct |
| **Snapshots → NeuroCore Backup/Export** |
| JSONL snapshot | `neurocore.export_snapshot()` | Full fidelity; lattice format preserved |
| BibTeX export | `neurocore.export_bibtex()` | Full fidelity; lattice format preserved |

### 2. Integration Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                    NeuroCore Presentation Layer                 │
│  (HTMX Templates: research.html, research_*.html templates)    │
├─────────────────────────────────────────────────────────────────┤
│                    NeuroCore Core Layer                         │
│  (research_manager.py → LatticeAdapter)                        │
├─────────────────────────────────────────────────────────────────┤
│                    Lattice Integration Layer                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ LatticeAdapter │  │  MCP Client  │  │ Sync Engine │          │
│  │  (Python)     │  │  (stdio)     │  │  (bidirectional)       │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│                    Lattice Core (SQLite + stdlib)               │
│  (models.py, repository.py, extraction.py, analysis.py,        │
│   synthesis.py, discovery.py, mcp.py, web.py)                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3. LatticeAdapter Interface - Complete API Surface (Every Lattice Function)

```python
# neurocore/modules/research/lattice_adapter.py
class LatticeAdapter:
    """Adapter translating NeuroCore ResearchManager calls to Lattice operations.
    
    ZERO DETAIL LOSS: Every lattice function, field, and capability must be accessible.
    """
    
    def __init__(self, lattice_db_path: str = None, use_mcp: bool = True):
        self.lattice_db_path = lattice_db_path or "projects/lattice/lattice.db"
        self.use_mcp = use_mcp
        self._mcp_client = None
        self._repo = None  # Direct lattice.repository.Repository instance
    
    # ============================================================
    # CORE REPOSITORY ACCESS (Direct Python API - Zero Overhead)
    # ============================================================
    
    def get_repository(self) -> 'lattice.repository.Repository':
        """Return direct lattice Repository instance for full fidelity access."""
    
    def get_inquiry(self) -> 'lattice.inquiry.InquiryService':
        """Return direct lattice InquiryService for ask/brief/review/synthesize."""
    
    # ============================================================
    # PAPER / ARTICLE OPERATIONS (All Fields Preserved)
    # ============================================================
    
    async def create_paper(self, paper_data: dict) -> dict:
        """Create paper in lattice with ALL fields: title, authors, year, venue, doi, arxiv, url, abstract, tags, study_type.
        Returns lattice Paper dict with id, created_at."""
    
    async def get_paper(self, paper_ref: str) -> dict:
        """Get paper by lattice ref (paper:p_xxx). Returns ALL fields."""
    
    async def search_papers(self, query: str = "", tags: list = None, study_type: str = None, year: int = None, limit: int = 50) -> list:
        """Full lattice search_literature: matches title, authors, year, venue, doi, arxiv, url, abstract, tags, study_type.
        Query can be tag filter, words, or exact sentence. Empty query with filters = browse."""
    
    async def fetch_papers(self, query: str, sources: list = None, limit: int = 20) -> list:
        """Multi-source discovery: offline, arxiv, crossref, openalex, europepmc (comma-separated).
        Returns PaperCandidate DTOs with deduplication by DOI/arXiv/title+year."""
    
    async def ingest_files(self, paths: list) -> dict:
        """Ingest BibTeX, RIS, CSV, Markdown, text files. Returns {added, updated, errors}."""
    
    async def update_paper_metadata(self, paper_ref: str, updates: dict) -> dict:
        """Update any paper field. Deduplication keys (doi, arxiv, title+year) trigger merge logic."""
    
    async def delete_paper(self, paper_ref: str) -> bool:
        """Delete paper and all dependent claims/edges (CASCADE)."""
    
    # ============================================================
    # CLAIM / EVIDENCE OPERATIONS (All Fields Preserved)
    # ============================================================
    
    async def create_claim(self, paper_ref: str, text: str, quote: str, locator: str = "", stance: str = "mentions", confidence: float = 0.5) -> dict:
        """Create quote-backed claim. VALIDATES: non-empty text + quote + existing paper. Returns Claim with id, created_at."""
    
    async def extract_claims(self, paper_ref: str = None, question_ref: str = None, all_papers: bool = False) -> list:
        """Automated extraction: empirical sentence segmentation → stance/confidence inference → question auto-linking.
        Returns created claims with auto-linked edges."""
    
    async def get_claim(self, claim_ref: str) -> dict:
        """Get claim by lattice ref (claim:c_xxx). Returns ALL fields."""
    
    async def search_claims(self, query: str, stance: str = None, min_confidence: float = None, year: int = None, tokens: int = None) -> list:
        """Token-budgeted search. Returns compact or full claims based on tokens param."""
    
    async def get_claims_for_paper(self, paper_ref: str) -> list:
        """All claims citing a paper."""
    
    async def get_claims_for_question(self, question_ref: str, stance: str = None) -> list:
        """Claims linked to question via supports/contradicts/answers edges."""
    
    async def update_claim(self, claim_ref: str, updates: dict) -> dict:
        """Update claim fields (text, quote, locator, stance, confidence). Validates constraints."""
    
    # ============================================================
    # QUESTION / HYPOTHESIS OPERATIONS (All Fields Preserved)
    # ============================================================
    
    async def create_question(self, text: str) -> dict:
        """Create research question. Returns Question with id (q_NNNNNN), status=open, created_at."""
    
    async def get_question(self, question_ref: str) -> dict:
        """Get question by lattice ref (question:q_xxx). Returns ALL fields."""
    
    async def get_questions(self, status: str = None) -> list:
        """List questions, optionally filtered by status (open/resolved/abandoned)."""
    
    async def set_question_status(self, question_ref: str, status: str) -> dict:
        """Update question status. Validates enum."""
    
    async def link_evidence(self, question_ref: str, claim_ref: str, stance: str, note: str = "") -> dict:
        """Create supports/contradicts/answers edge. Validates stance enum. Returns Edge."""
    
    async def get_evidence_for_question(self, question_ref: str) -> dict:
        """Returns {supports: [claims], contradicts: [claims], mentions: [claims], answers: [claims]} with full claim objects."""
    
    # ============================================================
    # EDGE / GRAPH OPERATIONS (All 10 Edge Kinds Preserved)
    # ============================================================
    
    async def create_edge(self, src_ref: str, dst_ref: str, kind: str, note: str = "", details: dict = None) -> dict:
        """Create typed edge. Validates: kind in 10 enum, refs valid, unique per (src,dst,kind). Returns Edge with int id."""
    
    async def get_edges(self, src_ref: str = None, dst_ref: str = None, kind: str = None) -> list:
        """Query edges with filters. Returns full Edge objects with details."""
    
    async def get_subgraph(self, center_ref: str, depth: int = 1, tokens: int = None) -> dict:
        """Subgraph exploration. If tokens provided: compact_subgraph (packed, omitted counts). Else: full subgraph."""
    
    async def get_related_findings(self, target_ref: str, limit: int = 10, tokens: int = None) -> list:
        """Find related claims: direct edges → shared question → keyword overlap (≤2).
        Returns: relation, reason, source paper study_type, ref for next hop."""
    
    async def auto_link_related(self, claim_ref: str = None, all_claims: bool = False) -> dict:
        """Idempotent related-finding link creation. Capped at 3 new links/claim. Returns {linked, skipped}."""
    
    # ============================================================
    # ANALYSIS OPERATIONS (Full Fidelity)
    # ============================================================
    
    async def detect_contradictions(self, question_ref: str = None, auto_link: bool = False, limit: int = None) -> list:
        """Returns ContradictionPair DTOs: claim_a, claim_b, question, stance_a, stance_b.
        If auto_link: creates contradicts edges idempotently."""
    
    async def rank_keystones(self, entity_type: str = "paper") -> list:
        """Returns ranked papers/claims by evidence centrality (eigenvector/PageRank)."""
    
    async def build_evidence_matrix(self, question_refs: list = None) -> dict:
        """Paper x Question matrix: {papers: [...], questions: [...], matrix: {paper_id: {question_id: {supports: n, contradicts: n, mentions: n}}}, gaps: [...]."""
    
    # ============================================================
    # SYNTHESIS OPERATIONS (Full Fidelity)
    # ============================================================
    
    async def synthesize(self, target_ref: str, tokens: int = 4000) -> dict:
        """Full synthesis: consensus_score [-1,1], thematic_clusters (claims + verbatim quotes), research_gaps, follow_up_queries, markdown, bibtex.
        Returns complete synthesis dict."""
    
    async def brief(self, target_ref: str, tokens: int = 1000) -> dict:
        """Token-budgeted brief: grouped evidence, omitted counts, character-based token estimate (ceil(chars/4))."""
    
    async def review(self, target_ref: str, out_path: str = None) -> str:
        """Markdown review with resolved paper references. Never generates unsupported sentences.
        If out_path: writes file atomically. Returns markdown string."""
    
    # ============================================================
    # SNAPSHOT / EXPORT / IMPORT (Full Fidelity)
    # ============================================================
    
    async def export_snapshot(self, path: str, format: str = "jsonl") -> dict:
        """Atomic export: temp + fsync + os.replace. JSONL or BibTeX (auto-detect .bib).
        Returns {papers, claims, questions, edges, stats}."""
    
    async def import_snapshot(self, path: str) -> dict:
        """Idempotent merge import: fills missing, never overwrites existing. Returns {added, updated, skipped}."""
    
    async def export_bibtex(self, path: str, query: str = "", tags: list = None, study_type: str = None, year: int = None) -> dict:
        """Filtered BibTeX export with content-disposition headers. Atomic write."""
    
    # ============================================================
    # OUTLINE / TOKEN-FRUGAL VIEWS (All Budgets Preserved)
    # ============================================================
    
    async def get_outline(self, tokens: int = 400) -> dict:
        """Cheap corpus index: entity counts, edge kind counts, study-type mix, evidenced question refs. ~120 tokens default."""
    
    async def compact_search(self, query: str, tokens: int = 800) -> dict:
        """Budgeted search results with ref, snippet, omitted counts."""
    
    async def compact_related(self, target_ref: str, tokens: int = 600) -> dict:
        """Budgeted related findings with ref, snippet, relation, reason, study_type, omitted counts."""
    
    async def compact_subgraph(self, center_ref: str, depth: int = 1, tokens: int = 800) -> dict:
        """Budgeted subgraph with packed nodes/edges, omitted counts."""
    
    # ============================================================
    # STUDY DESIGN / FINDING BRIDGE (NeuroCore-specific)
    # ============================================================
    
    async def create_study_design(self, data: dict) -> dict:
        """Create neurocore StudyDesign linked to lattice question. Stores lattice_question_id."""
    
    async def create_finding(self, data: dict) -> dict:
        """Create neurocore Finding. Optionally runs lattice synthesis for consensus/clusters/gaps."""
    
    async def sync_neurocore_article_to_lattice(self, article_data: dict) -> dict:
        """Bidirectional sync: neurocore Article → lattice Paper. Preserves neurocore-only fields in article.notes."""
    
    async def sync_lattice_paper_to_neurocore(self, paper_ref: str) -> dict:
        """Bidirectional sync: lattice Paper → neurocore Article. Preserves lattice-only fields in article.lattice_metadata."""
    
    # ============================================================
    # MCP CLIENT (All Tools Exposed)
    # ============================================================
    
    async def mcp_call(self, tool: str, params: dict) -> dict:
        """Generic MCP tool call. Exposes ALL lattice MCP tools:
        discover_papers, add_paper, extract_claims, search, get_entity, neighbors, subgraph, 
        related_findings, outline, add_claim, add_question, set_question_status, add_edge,
        brief, review, synthesize, analyze_graph, detect_contradictions, export_snapshot, 
        import_snapshot, stats"""
    
    async def mcp_search(self, query: str, tokens: int = 1000, stance: str = None, min_confidence: float = None) -> dict:
        """MCP search with token budget."""
    
    async def mcp_synthesize(self, target: str, tokens: int = 4000) -> dict:
        """MCP synthesize with token budget."""
    
    async def mcp_subgraph(self, entity_ref: str, depth: int = 2, tokens: int = 2000) -> dict:
        """MCP subgraph with token budget."""
    
    async def mcp_outline(self, tokens: int = 400) -> dict:
        """MCP outline (cheap first peek)."""
    
    async def mcp_related_findings(self, target: str, limit: int = 10, tokens: int = 600) -> dict:
        """MCP related_findings with token budget."""
    
    async def mcp_analyze_graph(self, question_ref: str = None, auto_link: bool = False) -> dict:
        """MCP analyze_graph (contradictions + related-finding links + keystone + matrix)."""
    
    # ============================================================
    # WEB RESEARCH STUDIO INTEGRATION
    # ============================================================
    
    def get_web_studio_url(self, host: str = "127.0.0.1", port: int = 8765, demo: bool = False) -> str:
        """Returns URL for embedding lattice Web Research Studio in neurocore tabs."""
    
    def get_web_api_endpoints(self) -> dict:
        """Returns mapping of neurocore tab → lattice web API endpoint:
        literature → /api/search + /api/outline
        hypotheses → /api/questions + /api/evidence
        studies → /api/study-types + /api/matrix
        findings → /api/synthesize + /api/contradictions
        paper-writer → /api/export-bibtex + /api/synthesize
        statistics → /api/stats + /api/keystones + /api/matrix"""
    
    # ============================================================
    # UTILITIES
    # ============================================================
    
    async def check_integrity(self) -> dict:
        """Runs lattice check: broken endpoints, missing quotes, duplicate IDs, support cycles, unlinked evidence."""
    
    async def get_stats(self) -> dict:
        """Returns lattice stats: paper_count, claim_count, question_count, edge_counts_by_kind, study_type_distribution."""
    
    def validate_ref(self, ref: str) -> str:
        """Validates and normalizes lattice reference (paper:p_xxx, claim:c_xxx, question:q_xxx)."""
```

### 4. Bidirectional Sync Strategy - Zero Data Loss

Since both systems may be edited independently, a sync engine handles conflicts with **full fidelity preservation**:

```
NeuroCore Edit → LatticeAdapter → Lattice DB
     ↑                                    ↓
     └──────── Sync Engine (event sourcing + conflict resolution) ────────┘
```

**Event Sourcing Model:**
- Every write (NeuroCore or Lattice) emits an event to an append-only sync log
- Events contain: `entity_type`, `entity_id`, `operation` (create/update/delete), `field_diffs`, `source` (neurocore|lattice), `timestamp`, `user_intent`
- Sync engine replays events to reconstruct state; conflicts resolved at replay time

**Conflict Resolution Rules (Field-Level Granularity):**

| Field Category | Source of Truth | Resolution |
|---|---|---|
| **Bibliographic identifiers** (DOI, arXiv, normalized title+year) | **Lattice** | Lattice wins; these are deduplication keys |
| **Core paper metadata** (title, authors, year, venue, url, abstract) | **Lattice** | Lattice wins; neurocore edits synced as `neurocore_override` sub-object |
| **Lattice-derived fields** (tags, study_type, kind) | **Lattice** | Lattice wins; neurocore can add `neurocore_tags`, `neurocore_study_type` |
| **NeuroCore-only article fields** (source, methodology, related_hypotheses, notes) | **NeuroCore** | NeuroCore wins; stored in lattice paper `details` JSON |
| **Claim core fields** (text, quote, locator, stance, confidence, paper_id) | **Lattice** | Lattice wins; quote is REQUIRED and immutable once set |
| **NeuroCore evidence fields** (hypothesis-specific tags, user notes) | **NeuroCore** | Stored in claim `details` JSON |
| **Question core fields** (text, status) | **Lattice** | Lattice wins |
| **NeuroCore hypothesis fields** (title, hypothesis_type, domain, variables, confidence_level, independent/dependent_variable, notes) | **NeuroCore** | Stored in question `details` JSON |
| **Edge kind/structure** (src, dst, kind, note, details) | **Lattice** | Lattice wins; full graph preserved |
| **Analysis outputs** (contradictions, keystones, matrix, related_findings) | **Lattice** | Lattice wins; neurocore caches for UI |
| **Synthesis outputs** (consensus, clusters, gaps, queries, markdown, bibtex) | **Lattice** | Lattice wins; neurocore mirrors for PaperWriter |
| **StudyDesign / Finding** (neurocore entities) | **NeuroCore** | NeuroCore wins; lattice stores as linked metadata via `details` |

**Sync Guarantees:**
- **No field dropping**: Every lattice field has a neurocore storage location (direct column or JSON `details`)
- **No schema loss**: Lattice's 29 study types, 10 edge kinds, 3 stances, confidence bounds all enforced
- **No behavioral loss**: Deduplication logic, extraction pipeline, auto-linking, token budgets all preserved via direct repository access
- **Audit trail**: Sync log enables full reconstruction and debug

### 5. MCP Server Integration - Full Tool Surface

NeuroCore agents access lattice's **complete MCP tool surface** via stdio JSON-RPC:

```python
# In neurocore agent_loop or tools
from mcp import ClientSession, StdioServerParameters

class LatticeMCPClient:
    """Full MCP client exposing ALL lattice tools to neurocore agents."""
    
    def __init__(self):
        self.params = StdioServerParameters(
            command="python",
            args=["-m", "lattice", "mcp"],
            cwd="projects/lattice"
        )
    
    async def call_tool(self, tool: str, params: dict) -> dict:
        """Generic tool call. Available tools:
        - discover_papers(query, sources, limit)
        - add_paper(paper_data)
        - extract_claims(paper_ref, question_ref, all)
        - search(query, stance, min_confidence, year, tokens)
        - get_entity(ref)
        - neighbors(ref)
        - subgraph(ref, depth, tokens)
        - related_findings(target, limit, tokens)
        - outline(tokens)
        - add_claim(paper_ref, text, quote, locator, stance, confidence)
        - add_question(text)
        - set_question_status(question_ref, status)
        - add_edge(src, dst, kind, note)
        - brief(target, tokens)
        - review(target, out_path)
        - synthesize(target, tokens, out_path)
        - analyze_graph(question_ref, auto_link, limit)
        - detect_contradictions(question_ref, auto_link, limit)
        - export_snapshot(path, format)
        - import_snapshot(path)
        - stats()
        """
    
    # Convenience methods for each tool (all params preserved)
    async def search(self, query: str, tokens: int = 1000, **kwargs) -> dict: ...
    async def synthesize(self, target: str, tokens: int = 4000, **kwargs) -> dict: ...
    async def subgraph(self, entity_ref: str, depth: int = 2, tokens: int = 2000) -> dict: ...
    async def outline(self, tokens: int = 400) -> dict: ...
    async def related_findings(self, target: str, limit: int = 10, tokens: int = 600) -> dict: ...
    async def analyze_graph(self, question_ref: str = None, auto_link: bool = False) -> dict: ...
    async def get_entity(self, ref: str) -> dict: ...
    async def discover_papers(self, query: str, sources: list = None, limit: int = 20) -> dict: ...
    async def extract_claims(self, paper_ref: str = None, question_ref: str = None, all: bool = False) -> dict: ...
```

**Agent Tool Registration:**
```python
# neurocore/modules/tools/rlm_library/LatticeSearch.py
class LatticeSearch(Tool):
    """Agent tool: Search lattice evidence graph with token budget."""
    async def execute(self, query: str, tokens: int = 1000, stance: str = None) -> dict:
        return await lattice_mcp_client.search(query, tokens, stance)

# neurocore/modules/tools/rlm_library/LatticeSynthesize.py
class LatticeSynthesize(Tool):
    """Agent tool: Synthesize consensus from lattice evidence."""
    async def execute(self, target: str, tokens: int = 4000) -> dict: ...

# neurocore/modules/tools/rlm_library/LatticeSubgraph.py
class LatticeSubgraph(Tool):
    """Agent tool: Get compact subgraph for context."""
    async def execute(self, entity_ref: str, depth: int = 2, tokens: int = 2000) -> dict: ...

# neurocore/modules/tools/rlm_library/LatticeOutline.py
class LatticeOutline(Tool):
    """Agent tool: Cheap corpus orientation peek."""
    async def execute(self, tokens: int = 400) -> dict: ...

# neurocore/modules/tools/rlm_library/LatticeContradictions.py
class LatticeContradictions(Tool):
    """Agent tool: Detect contradictory claims."""
    async def execute(self, question_ref: str = None, auto_link: bool = False) -> dict: ...
```

### 6. Web UI Integration - Full Research Studio Parity

NeuroCore's research tabs **embed or replicate** every lattice Web Research Studio component:

| NeuroCore Tab | Lattice Component | Integration Mode |
|---|---|---|
| **Literature** | Tab 1 (Discovery) + Tab 3 (Literature Search) + Tab 2 (Corpus Outline) | Embed lattice `/web` iframe OR replicate via `/api/search`, `/api/outline`, `/api/export-bibtex` |
| **Hypotheses** | Tab 2 (Corpus Outline - evidenced questions) + Tab 4 (Subgraph - question-centered) | Replicate via `/api/questions`, `/api/evidence/{q}`, `/api/subgraph/{q}` |
| **Studies** | Tab 3 (Study-type filter) + Tab 5 (Evidence Matrix) | Replicate via `/api/study-types`, `/api/matrix` |
| **Findings** | Tab 5 (Contradictions) + Tab 6 (Synthesis) | Replicate via `/api/contradictions`, `/api/synthesize/{q}` |
| **Paper Writer** | Tab 6 (Synthesis markdown + BibTeX copy) + Stats Bar (Export BibTeX) | Replicate via `/api/synthesize/{q}`, `/api/export-bibtex` |
| **Statistics** | Tab 5 (Keystone centrality) + Stats Bar (entity counts) + Tab 2 (Outline) | Replicate via `/api/stats`, `/api/keystones`, `/api/outline` |

**Embedding Options:**
1. **Iframe Embed** (simplest): `<iframe src="http://127.0.0.1:8765?tab=literature" />` with postMessage for tab switching
2. **API Replication** (native feel): NeuroCore templates call lattice `/api/*` endpoints directly, render with neurocore Tailwind components
3. **Hybrid**: Iframe for Subgraph Explorer (complex SVG), API for other tabs

**Required Lattice Web API Endpoints (ALL EXISTING):**
```
GET  /api/search           # Literature search with filters
GET  /api/outline          # Corpus outline drawer data
GET  /api/questions        # All questions with evidence counts
GET  /api/evidence/{q}     # Evidence for question (supports/contradicts/mentions)
GET  /api/subgraph/{ref}   # SVG subgraph data (nodes, edges, positions)
GET  /api/study-types      # Study type distribution
GET  /api/matrix           # Paper x Question evidence matrix
GET  /api/contradictions   # Contradiction pairs
GET  /api/synthesize/{q}   # Full synthesis output
GET  /api/stats            # Entity counts
GET  /api/keystones        # Ranked papers/claims
GET  /api/export-bibtex    # BibTeX download (filtered)
POST /api/ingest-text      # Browser paste ingestion
POST /api/link-related     # Connect Findings action
POST /api/export           # JSONL snapshot download
```

### 7. Lattice API Module (New Requirement)

To enable zero-overhead direct access, lattice must expose a clean **Python API module**:

```python
# lattice/api.py (NEW - to be created in lattice project)
"""Public Python API for neurocore LatticeAdapter. Zero-overhead, full fidelity."""

from lattice.repository import Repository
from lattice.inquiry import InquiryService
from lattice.analysis import (
    detect_contradictions, rank_influential_entities, build_evidence_matrix,
    find_related_findings, link_related_findings, ContradictionPair
)
from lattice.synthesis import synthesize_question
from lattice.snapshots import export_snapshot, import_snapshot, export_bibtex_file
from lattice.discovery import DiscoveryService, ProviderRegistry
from lattice.extraction import extract_claims_for_paper
from lattice.models import Paper, Claim, Question, Edge, STANCES, EDGE_KINDS, STUDY_TYPES
from lattice.context import outline, compact_search, compact_related, compact_subgraph
from lattice.errors import fail

__all__ = [
    # Core
    "Repository", "InquiryService",
    # Analysis
    "detect_contradictions", "rank_influential_entities", "build_evidence_matrix",
    "find_related_findings", "link_related_findings", "ContradictionPair",
    # Synthesis
    "synthesize_question",
    # Snapshots
    "export_snapshot", "import_snapshot", "export_bibtex_file",
    # Discovery
    "DiscoveryService", "ProviderRegistry",
    # Extraction
    "extract_claims_for_paper",
    # Models (value objects + constants)
    "Paper", "Claim", "Question", "Edge", "STANCES", "EDGE_KINDS", "STUDY_TYPES",
    # Context views
    "outline", "compact_search", "compact_related", "compact_subgraph",
    # Errors
    "fail",
]
```

This module enables `LatticeAdapter.get_repository()` and direct function calls without CLI/MCP overhead.

## Implementation Phases - Zero Detail Loss

### Phase 1: Foundation - Lattice API Module + Adapter + MCP Client
1. **Lattice side**: Create `lattice/api.py` public module exposing all internal functions (Repository, InquiryService, analysis, synthesis, snapshots, discovery, extraction, models, context, errors)
2. **NeuroCore side**: Create `LatticeAdapter` class with complete interface (Section 3)
3. **MCP Client**: Implement `LatticeMCPClient` with all 20+ tools exposed as agent tools
4. **Settings**: Add lattice config to neurocore `settings.json` (db_path, mcp_command, sync settings)
5. **Unit tests**: Adapter field mapping tests (every lattice field → neurocore storage), MCP tool call tests

### Phase 2: ResearchManager Migration - Full Fidelity Backend Swap
1. **Refactor `ResearchManager`**: Replace SQLite backend with `LatticeAdapter` calls
2. **Schema migration script**: One-time migration of existing neurocore research data → lattice
   - Articles → Papers (preserve neurocore-only fields in `details`)
   - Hypotheses → Questions (preserve neurocore-only fields in `details`)
   - Evidence links → Edges (all 10 kinds)
   - StudyDesigns/Findings → NeuroCore tables with `lattice_question_id` FK
3. **Bidirectional sync engine**: Event-sourced sync log with field-level conflict resolution (Section 4)
4. **Integration tests**: All existing neurocore research tests pass with lattice backend

### Phase 3: UI & Agent Integration - Full Research Studio Parity
1. **Research templates**: Update `research.html` and all `research_*.html` to use lattice data via API endpoints
2. **Web API endpoints**: Add neurocore routes proxying to lattice `/api/*` (or direct lattice.web dispatch)
3. **Agent tools**: Register 5+ lattice MCP tools in neurocore tool library (LatticeSearch, LatticeSynthesize, LatticeSubgraph, LatticeOutline, LatticeContradictions, LatticeExtractClaims, LatticeDiscoverPapers)
4. **Subgraph Explorer**: Embed lattice SVG subgraph renderer (iframe or replicated)
5. **Corpus Outline Drawer**: Replicate in neurocore sidebar
6. **BibTeX Export**: Wire "Export BibTeX" buttons to lattice `/api/export-bibtex`
7. **Paste Ingestion**: Add "Paste BibTeX/RIS/CSV/Markdown" to Literature tab → lattice `/api/ingest-text`

### Phase 4: Advanced Features - Full Lattice Power in NeuroCore
1. **Federated Discovery**: Literature tab → lattice `discover_papers` (arXiv, Crossref, OpenAlex, EuropePMC, offline)
2. **Automated Extraction**: "Extract Claims" button on articles → lattice `extract_claims_for_paper` + auto-link
3. **Real-time Evidence**: MCP resources (`lattice://stats`, `lattice://questions`, `lattice://matrix`) → neurocore live updates
4. **Collaborative Research**: "Export Snapshot" / "Import Snapshot" in neurocore UI → lattice JSONL snapshots
5. **Contradiction Detection UI**: "Find Contradictions" button on Hypotheses tab → lattice `detect_contradictions` + auto-link
6. **Synthesis UI**: "Synthesize" button → lattice `synthesize_question` with markdown/BibTeX copy
7. **Keystone Analysis**: Statistics tab → lattice `rank_influential_entities` visualization
8. **Evidence Matrix**: Studies tab → lattice `build_evidence_matrix` with coverage gaps
9. **Related Findings**: Findings tab → lattice `related_findings` graph traversal

### Phase 5: Hardening & Polish
1. **Performance**: Cache lattice outline/index; async adapter; connection pooling for MCP
2. **Migration verification**: Automated comparison of neurocore ↔ lattice data integrity
3. **Documentation**: Update neurocore docs with lattice-powered research workflow
4. **E2E tests**: Full research workflow (discover → ingest → extract → hypothesize → design → find → synthesize → write)

## Configuration

```json
// neurocore settings.json additions
{
  "lattice": {
    "enabled": true,
    "db_path": "projects/lattice/lattice.db",
    "use_mcp": true,
    "mcp_command": ["python", "-m", "lattice", "mcp"],
    "mcp_cwd": "projects/lattice",
    "sync_interval_seconds": 30,
    "conflict_resolution": "lattice_wins_metadata"
  }
}
```

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Schema divergence between lattice and neurocore entities | Versioned adapter with explicit field mapping; integration tests |
| Dual-write conflicts | Event sourcing: all writes go through adapter; sync engine uses timestamps + user intent |
| Performance: lattice queries in neurocore request path | Async adapter; cache lattice outline/index; token-budgeted MCP calls |
| Lattice MCP stdio process management | Supervised subprocess with health checks; connection pooling |
| Data loss during migration | One-way migration first (neurocore → lattice); verify before switch |

## Acceptance Criteria - Zero Detail Loss Verification

### Data Fidelity (Automated Tests)
1. **Round-trip field preservation**: For every lattice Paper field, Claim field, Question field, Edge field:
   - Write via neurocore → read via lattice API → assert exact match
   - Write via lattice CLI → read via neurocore → assert exact match (including neurocore-only fields in `details`)
2. **Deduplication behavior**: Insert paper with same DOI → lattice merges, neurocore sees merged result
3. **Claim validation**: Attempt claim without quote → rejected by both lattice and neurocore
4. **Stance/confidence bounds**: Invalid stance → rejected; confidence >1 or <0 → rejected
5. **Edge uniqueness**: Duplicate (src, dst, kind) → rejected; all 10 kinds accepted
6. **Study type canonicalization**: 29 study types + aliases all map correctly
7. **Reference format**: `paper:p_xxx`, `claim:c_xxx`, `question:q_xxx` validated everywhere

### Functional Parity (All Lattice Features Accessible)
8. **CLI commands**: All 25+ lattice CLI commands produce identical output when run via neurocore wrapper
9. **MCP tools**: All 20+ MCP tools callable from neurocore agents with identical params/results
10. **Web API**: All 12+ lattice `/api/*` endpoints accessible from neurocore templates
11. **Web UI**: Research Studio tabs 1-6 fully functional when embedded in neurocore
12. **Extraction pipeline**: `extract_claims` produces identical claims/edges via neurocore as via lattice CLI
13. **Discovery**: `fetch` with multi-source (offline,arxiv,crossref,openalex,europepmc) works identically
14. **Analysis**: `detect_contradictions`, `rank_influential_entities`, `build_evidence_matrix`, `link_related_findings` produce identical results
15. **Synthesis**: `synthesize` produces identical consensus/clusters/gaps/queries/markdown/bibtex
16. **Snapshots**: JSONL export/import round-trip preserves all entities and edges
17. **BibTeX**: Filtered export matches lattice CLI output exactly
18. **Token budgets**: `outline` (400), `search` (800), `related` (600), `subgraph` (800) all enforced with `omitted_*` counts

### NeuroCore Integration (No Regression)
19. **All existing neurocore research tests pass** with lattice backend (zero test modifications for behavior)
20. **Lattice's own tests continue to pass** (zero regression in lattice)
21. **Agent loops**: NeuroCore agents can use lattice MCP tools for research tasks
22. **Paper Writer**: Generates papers using lattice synthesis markdown + BibTeX
23. **Knowledge Base module**: Still functional for non-lattice documents (FAISS vector search separate)

### Sync Correctness
24. **Bidirectional sync**: Edit in neurocore → appears in lattice; edit in lattice → appears in neurocore
25. **Conflict resolution**: Field-level rules (Section 4) enforced; audit log reconstructible
26. **No data loss**: Stress test 1000 concurrent edits from both sides → zero field drops

### Performance
27. **Adapter overhead**: <5ms per call (direct repository access)
28. **MCP latency**: <100ms per tool call (stdio subprocess)
29. **Web API**: <50ms per endpoint (direct dispatch)
30. **Token budget compliance**: All compact views respect `tokens` parameter

## Dependencies

- `lattice` project must be available at `projects/lattice/` (or configured path)
- Python 3.10+ (shared requirement)
- MCP Python SDK for client integration
- No additional third-party dependencies (lattice is stdlib-only)

## Related Documents

- Lattice ADRs: `projects/lattice/.aikit/decisions.md` (ADR-001 through ADR-011)
- NeuroCore Research Manager: `projects/neurocore/core/research_manager.py`
- NeuroCore Knowledge Base Module: `projects/neurocore/modules/knowledge_base/`
- AIKit Project Management: `projects/neurocore/.aikit/project.json`