"""
Research Manager

Thread-safe SQLite-backed manager for research entities:
- Hypotheses
- Articles (literature library)
- Study Designs
- Findings
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.paths import data_path

logger = logging.getLogger(__name__)

DB_PATH = data_path("research.sqlite3")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchManager:
    """
    Manages persistent storage of research entities using SQLite.

    Thread-safe via a reentrant lock. Uses WAL mode for concurrent reads.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS interventions (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        start_date TEXT NOT NULL,
                        end_date_projected TEXT,
                        end_date_actual TEXT,
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS metrics (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT,
                        unit TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS metric_logs (
                        id TEXT PRIMARY KEY,
                        intervention_id TEXT NOT NULL,
                        metric_name TEXT NOT NULL,
                        log_date TEXT NOT NULL,
                        value REAL NOT NULL,
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (intervention_id) REFERENCES interventions(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS experiment_events (
                        id TEXT PRIMARY KEY,
                        intervention_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        event_datetime TEXT NOT NULL,
                        severity TEXT NOT NULL DEFAULT 'low',
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (intervention_id) REFERENCES interventions(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS hypotheses (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        statement TEXT NOT NULL,
                        hypothesis_type TEXT NOT NULL DEFAULT 'correlation',
                        status TEXT NOT NULL DEFAULT 'proposed',
                        domain TEXT NOT NULL DEFAULT 'general',
                        independent_variable TEXT,
                        dependent_variable TEXT,
                        variables TEXT NOT NULL DEFAULT '[]',
                        confidence_level REAL NOT NULL DEFAULT 0.95,
                        evidence TEXT NOT NULL DEFAULT '[]',
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS articles (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        authors TEXT NOT NULL DEFAULT '[]',
                        year INTEGER,
                        journal TEXT,
                        doi TEXT,
                        url TEXT,
                        abstract TEXT,
                        keywords TEXT NOT NULL DEFAULT '[]',
                        article_type TEXT NOT NULL DEFAULT 'research',
                        domain TEXT NOT NULL DEFAULT 'general',
                        key_findings TEXT NOT NULL DEFAULT '[]',
                        methodology TEXT,
                        source TEXT NOT NULL DEFAULT 'manual',
                        related_hypotheses TEXT NOT NULL DEFAULT '[]',
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS study_designs (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        study_type TEXT NOT NULL,
                        hypothesis_id TEXT,
                        population TEXT NOT NULL,
                        sample_size INTEGER,
                        sampling_method TEXT NOT NULL DEFAULT 'random',
                        design_type TEXT,
                        control_group INTEGER NOT NULL DEFAULT 0,
                        randomization INTEGER NOT NULL DEFAULT 0,
                        blinding TEXT,
                        procedures TEXT NOT NULL DEFAULT '[]',
                        analysis_plan TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL DEFAULT 'planned',
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS findings (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        conclusion TEXT NOT NULL,
                        study_id TEXT,
                        hypothesis_id TEXT,
                        statistical_test TEXT,
                        p_value REAL,
                        effect_size REAL,
                        significance TEXT NOT NULL DEFAULT 'not_significant',
                        status TEXT NOT NULL DEFAULT 'preliminary',
                        limitations TEXT NOT NULL DEFAULT '[]',
                        implications TEXT NOT NULL DEFAULT '[]',
                        notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.commit()
            finally:
                conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        # Deserialize JSON-stored list fields
        for key in list(d.keys()):
            val = d[key]
            if isinstance(val, str) and val.startswith(("[", "{")):
                try:
                    d[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError) as _exc:
                    logger.debug("ignored exception: %s", _exc)
        return d

    # ------------------------------------------------------------------
    # Hypotheses
    # ------------------------------------------------------------------

    def create_hypothesis(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _now()
            row = {
                "id": str(uuid.uuid4()),
                "title": data["title"],
                "statement": data["statement"],
                "hypothesis_type": data.get("hypothesis_type", "correlation"),
                "status": data.get("status", "proposed"),
                "domain": data.get("domain", "general"),
                "independent_variable": data.get("independent_variable"),
                "dependent_variable": data.get("dependent_variable"),
                "variables": json.dumps(data.get("variables", [])),
                "confidence_level": float(data.get("confidence_level", 0.95)),
                "evidence": json.dumps(data.get("evidence", [])),
                "notes": data.get("notes"),
                "created_at": now,
                "updated_at": now,
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO hypotheses
                       (id, title, statement, hypothesis_type, status, domain,
                        independent_variable, dependent_variable, variables,
                        confidence_level, evidence, notes, created_at, updated_at)
                       VALUES (:id,:title,:statement,:hypothesis_type,:status,:domain,
                               :independent_variable,:dependent_variable,:variables,
                               :confidence_level,:evidence,:notes,:created_at,:updated_at)""",
                    row,
                )
                conn.commit()
                row["variables"] = json.loads(row["variables"])
                row["evidence"] = json.loads(row["evidence"])
                return row
            finally:
                conn.close()

    def get_hypotheses(self, status: Optional[str] = None, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                clauses, params = [], []
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if domain:
                    clauses.append("domain = ?")
                    params.append(domain)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM hypotheses {where} ORDER BY created_at DESC", params
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_hypothesis(self, hypothesis_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self.get_hypothesis(hypothesis_id)
            if not existing:
                return None
            fields = {
                "title": data.get("title", existing["title"]),
                "statement": data.get("statement", existing["statement"]),
                "hypothesis_type": data.get("hypothesis_type", existing["hypothesis_type"]),
                "status": data.get("status", existing["status"]),
                "domain": data.get("domain", existing["domain"]),
                "independent_variable": data.get("independent_variable", existing.get("independent_variable")),
                "dependent_variable": data.get("dependent_variable", existing.get("dependent_variable")),
                "variables": json.dumps(data.get("variables", existing.get("variables", []))),
                "confidence_level": float(data.get("confidence_level", existing["confidence_level"])),
                "evidence": json.dumps(data.get("evidence", existing.get("evidence", []))),
                "notes": data.get("notes", existing.get("notes")),
                "updated_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    """UPDATE hypotheses SET title=:title, statement=:statement,
                       hypothesis_type=:hypothesis_type, status=:status, domain=:domain,
                       independent_variable=:independent_variable,
                       dependent_variable=:dependent_variable, variables=:variables,
                       confidence_level=:confidence_level, evidence=:evidence,
                       notes=:notes, updated_at=:updated_at
                       WHERE id=:id""",
                    {**fields, "id": hypothesis_id},
                )
                conn.commit()
            finally:
                conn.close()
            return self.get_hypothesis(hypothesis_id)

    def delete_hypothesis(self, hypothesis_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM hypotheses WHERE id = ?", (hypothesis_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------

    def create_article(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _now()
            row = {
                "id": str(uuid.uuid4()),
                "title": data["title"],
                "authors": json.dumps(data.get("authors", [])),
                "year": data.get("year"),
                "journal": data.get("journal"),
                "doi": data.get("doi"),
                "url": data.get("url"),
                "abstract": data.get("abstract"),
                "keywords": json.dumps(data.get("keywords", [])),
                "article_type": data.get("article_type", "research"),
                "domain": data.get("domain", "general"),
                "key_findings": json.dumps(data.get("key_findings", [])),
                "methodology": data.get("methodology"),
                "source": data.get("source", "manual"),
                "related_hypotheses": json.dumps(data.get("related_hypotheses", [])),
                "notes": data.get("notes"),
                "created_at": now,
                "updated_at": now,
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO articles
                       (id,title,authors,year,journal,doi,url,abstract,keywords,
                        article_type,domain,key_findings,methodology,source,
                        related_hypotheses,notes,created_at,updated_at)
                       VALUES (:id,:title,:authors,:year,:journal,:doi,:url,:abstract,
                               :keywords,:article_type,:domain,:key_findings,:methodology,
                               :source,:related_hypotheses,:notes,:created_at,:updated_at)""",
                    row,
                )
                conn.commit()
                for f in ("authors", "keywords", "key_findings", "related_hypotheses"):
                    row[f] = json.loads(row[f])
                return row
            finally:
                conn.close()

    def get_articles(self, domain: Optional[str] = None, article_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                clauses, params = [], []
                if domain:
                    clauses.append("domain = ?")
                    params.append(domain)
                if article_type:
                    clauses.append("article_type = ?")
                    params.append(article_type)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM articles {where} ORDER BY created_at DESC", params
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_article(self, article_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def delete_article(self, article_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Study Designs
    # ------------------------------------------------------------------

    def create_study_design(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _now()
            row = {
                "id": str(uuid.uuid4()),
                "title": data["title"],
                "study_type": data["study_type"],
                "hypothesis_id": data.get("hypothesis_id"),
                "population": data["population"],
                "sample_size": data.get("sample_size"),
                "sampling_method": data.get("sampling_method", "random"),
                "design_type": data.get("design_type"),
                "control_group": 1 if data.get("control_group") else 0,
                "randomization": 1 if data.get("randomization") else 0,
                "blinding": data.get("blinding"),
                "procedures": json.dumps(data.get("procedures", [])),
                "analysis_plan": json.dumps(data.get("analysis_plan", [])),
                "status": data.get("status", "planned"),
                "notes": data.get("notes"),
                "created_at": now,
                "updated_at": now,
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO study_designs
                       (id,title,study_type,hypothesis_id,population,sample_size,
                        sampling_method,design_type,control_group,randomization,
                        blinding,procedures,analysis_plan,status,notes,created_at,updated_at)
                       VALUES (:id,:title,:study_type,:hypothesis_id,:population,:sample_size,
                               :sampling_method,:design_type,:control_group,:randomization,
                               :blinding,:procedures,:analysis_plan,:status,:notes,
                               :created_at,:updated_at)""",
                    row,
                )
                conn.commit()
                row["procedures"] = json.loads(row["procedures"])
                row["analysis_plan"] = json.loads(row["analysis_plan"])
                row["control_group"] = bool(row["control_group"])
                row["randomization"] = bool(row["randomization"])
                return row
            finally:
                conn.close()

    def get_study_designs(self, hypothesis_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                clauses, params = [], []
                if hypothesis_id:
                    clauses.append("hypothesis_id = ?")
                    params.append(hypothesis_id)
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM study_designs {where} ORDER BY created_at DESC", params
                ).fetchall()
                return [self._study_dict(r) for r in rows]
            finally:
                conn.close()

    def _study_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = self._row_to_dict(row)
        d["control_group"] = bool(d.get("control_group", 0))
        d["randomization"] = bool(d.get("randomization", 0))
        return d

    def get_study_design(self, study_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM study_designs WHERE id = ?", (study_id,)).fetchone()
                return self._study_dict(row) if row else None
            finally:
                conn.close()

    def update_study_design(self, study_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self.get_study_design(study_id)
            if not existing:
                return None
            fields = {
                "status": data.get("status", existing["status"]),
                "notes": data.get("notes", existing.get("notes")),
                "updated_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE study_designs SET status=:status, notes=:notes, updated_at=:updated_at WHERE id=:id",
                    {**fields, "id": study_id},
                )
                conn.commit()
            finally:
                conn.close()
            return self.get_study_design(study_id)

    def delete_study_design(self, study_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM study_designs WHERE id = ?", (study_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------

    def create_finding(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _now()
            row = {
                "id": str(uuid.uuid4()),
                "title": data["title"],
                "summary": data["summary"],
                "conclusion": data["conclusion"],
                "study_id": data.get("study_id"),
                "hypothesis_id": data.get("hypothesis_id"),
                "statistical_test": data.get("statistical_test"),
                "p_value": data.get("p_value"),
                "effect_size": data.get("effect_size"),
                "significance": data.get("significance", "not_significant"),
                "status": data.get("status", "preliminary"),
                "limitations": json.dumps(data.get("limitations", [])),
                "implications": json.dumps(data.get("implications", [])),
                "notes": data.get("notes"),
                "created_at": now,
                "updated_at": now,
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO findings
                       (id,title,summary,conclusion,study_id,hypothesis_id,
                        statistical_test,p_value,effect_size,significance,status,
                        limitations,implications,notes,created_at,updated_at)
                       VALUES (:id,:title,:summary,:conclusion,:study_id,:hypothesis_id,
                               :statistical_test,:p_value,:effect_size,:significance,:status,
                               :limitations,:implications,:notes,:created_at,:updated_at)""",
                    row,
                )
                conn.commit()
                row["limitations"] = json.loads(row["limitations"])
                row["implications"] = json.loads(row["implications"])
                return row
            finally:
                conn.close()

    def get_findings(self, hypothesis_id: Optional[str] = None, study_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                clauses, params = [], []
                if hypothesis_id:
                    clauses.append("hypothesis_id = ?")
                    params.append(hypothesis_id)
                if study_id:
                    clauses.append("study_id = ?")
                    params.append(study_id)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM findings {where} ORDER BY created_at DESC", params
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_finding(self, finding_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self.get_finding(finding_id)
            if not existing:
                return None
            fields = {
                "status": data.get("status", existing["status"]),
                "notes": data.get("notes", existing.get("notes")),
                "updated_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE findings SET status=:status, notes=:notes, updated_at=:updated_at WHERE id=:id",
                    {**fields, "id": finding_id},
                )
                conn.commit()
            finally:
                conn.close()
            return self.get_finding(finding_id)

    def delete_finding(self, finding_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM findings WHERE id = ?", (finding_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Interventions
    # ------------------------------------------------------------------

    def create_intervention(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _now()
            row = {
                "id": str(uuid.uuid4()),
                "name": data["name"],
                "description": data.get("description"),
                "start_date": data["start_date"],
                "end_date_projected": data.get("end_date_projected"),
                "end_date_actual": data.get("end_date_actual"),
                "notes": data.get("notes"),
                "created_at": now,
                "updated_at": now,
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO interventions
                       (id,name,description,start_date,end_date_projected,
                        end_date_actual,notes,created_at,updated_at)
                       VALUES (:id,:name,:description,:start_date,:end_date_projected,
                               :end_date_actual,:notes,:created_at,:updated_at)""",
                    row,
                )
                conn.commit()
                return dict(row)
            finally:
                conn.close()

    def get_interventions(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM interventions ORDER BY start_date DESC"
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_intervention(self, intervention_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM interventions WHERE id = ?", (intervention_id,)
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_intervention(self, intervention_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self.get_intervention(intervention_id)
            if not existing:
                return None
            fields = {
                "name": data.get("name", existing["name"]),
                "description": data.get("description", existing.get("description")),
                "start_date": data.get("start_date", existing["start_date"]),
                "end_date_projected": data.get("end_date_projected", existing.get("end_date_projected")),
                "end_date_actual": data.get("end_date_actual", existing.get("end_date_actual")),
                "notes": data.get("notes", existing.get("notes")),
                "updated_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    """UPDATE interventions SET name=:name, description=:description,
                       start_date=:start_date, end_date_projected=:end_date_projected,
                       end_date_actual=:end_date_actual, notes=:notes,
                       updated_at=:updated_at WHERE id=:id""",
                    {**fields, "id": intervention_id},
                )
                conn.commit()
            finally:
                conn.close()
            return self.get_intervention(intervention_id)

    def delete_intervention(self, intervention_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM interventions WHERE id = ?", (intervention_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def create_metric(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            row = {
                "id": str(uuid.uuid4()),
                "name": data["name"],
                "description": data.get("description"),
                "unit": data.get("unit"),
                "created_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO metrics (id,name,description,unit,created_at) "
                    "VALUES (:id,:name,:description,:unit,:created_at)",
                    row,
                )
                conn.commit()
                return dict(row)
            finally:
                conn.close()

    def get_metrics(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM metrics ORDER BY name").fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def delete_metric(self, metric_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM metrics WHERE id = ?", (metric_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Metric Logs
    # ------------------------------------------------------------------

    def log_metric(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            row = {
                "id": str(uuid.uuid4()),
                "intervention_id": data["intervention_id"],
                "metric_name": data["metric_name"],
                "log_date": data["log_date"],
                "value": float(data["value"]),
                "notes": data.get("notes"),
                "created_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO metric_logs
                       (id,intervention_id,metric_name,log_date,value,notes,created_at)
                       VALUES (:id,:intervention_id,:metric_name,:log_date,:value,:notes,:created_at)""",
                    row,
                )
                conn.commit()
                return dict(row)
            finally:
                conn.close()

    def get_metric_logs(self, intervention_id: str, metric_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                if metric_name:
                    rows = conn.execute(
                        "SELECT * FROM metric_logs WHERE intervention_id=? AND metric_name=? ORDER BY log_date",
                        (intervention_id, metric_name),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM metric_logs WHERE intervention_id=? ORDER BY log_date, metric_name",
                        (intervention_id,),
                    ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def delete_metric_log(self, log_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM metric_logs WHERE id = ?", (log_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_metric_names_for_intervention(self, intervention_id: str) -> List[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT metric_name FROM metric_logs WHERE intervention_id=? ORDER BY metric_name",
                    (intervention_id,),
                ).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Experiment Events
    # ------------------------------------------------------------------

    def log_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            row = {
                "id": str(uuid.uuid4()),
                "intervention_id": data["intervention_id"],
                "name": data["name"],
                "event_datetime": data["event_datetime"],
                "severity": data.get("severity", "low"),
                "notes": data.get("notes"),
                "created_at": _now(),
            }
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO experiment_events
                       (id,intervention_id,name,event_datetime,severity,notes,created_at)
                       VALUES (:id,:intervention_id,:name,:event_datetime,:severity,:notes,:created_at)""",
                    row,
                )
                conn.commit()
                return dict(row)
            finally:
                conn.close()

    def get_events(self, intervention_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM experiment_events WHERE intervention_id=? ORDER BY event_datetime DESC",
                    (intervention_id,),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM experiment_events WHERE id = ?", (event_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # CSV Export
    # ------------------------------------------------------------------

    def export_csv(self, intervention_id: str) -> str:
        """Return a CSV string of all metric logs for an intervention."""
        import csv
        import io
        logs = self.get_metric_logs(intervention_id)
        events = self.get_events(intervention_id)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["type", "date", "metric_name", "value", "severity", "notes"])
        for log in logs:
            writer.writerow(["metric", log["log_date"], log["metric_name"], log["value"], "", log.get("notes", "")])
        for ev in events:
            writer.writerow(["event", ev["event_datetime"][:10], ev["name"], "", ev["severity"], ev.get("notes", "")])
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Stats (for dashboard summary)
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            conn = self._connect()
            try:
                return {
                    "hypotheses": conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0],
                    "articles": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                    "study_designs": conn.execute("SELECT COUNT(*) FROM study_designs").fetchone()[0],
                    "findings": conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0],
                }
            finally:
                conn.close()


# Module-level singleton
_research_manager: Optional[ResearchManager] = None
_research_manager_lock = threading.Lock()


def get_research_manager_instance() -> ResearchManager:
    global _research_manager
    with _research_manager_lock:
        if _research_manager is None:
            _research_manager = ResearchManager()
    return _research_manager