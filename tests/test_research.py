"""
Tests for ResearchManager CRUD operations.
"""

import os
import tempfile
import pytest

from core.research_manager import ResearchManager


@pytest.fixture
def rm(tmp_path):
    """Fresh ResearchManager backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_research.sqlite3")
    return ResearchManager(db_path=db_path)


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------

class TestHypotheses:
    def test_create_and_get(self, rm):
        h = rm.create_hypothesis({
            "title": "Sleep improves memory",
            "statement": "Students who sleep 8h score higher than those who sleep 4h.",
            "hypothesis_type": "causal",
            "domain": "cognitive_science",
        })
        assert h["id"]
        assert h["title"] == "Sleep improves memory"
        assert h["hypothesis_type"] == "causal"
        assert h["status"] == "proposed"

        fetched = rm.get_hypothesis(h["id"])
        assert fetched is not None
        assert fetched["title"] == h["title"]

    def test_list_all(self, rm):
        rm.create_hypothesis({"title": "H1", "statement": "S1"})
        rm.create_hypothesis({"title": "H2", "statement": "S2"})
        result = rm.get_hypotheses()
        assert len(result) == 2

    def test_filter_by_status(self, rm):
        h = rm.create_hypothesis({"title": "H1", "statement": "S1"})
        rm.update_hypothesis(h["id"], {"status": "supported"})
        rm.create_hypothesis({"title": "H2", "statement": "S2"})  # stays 'proposed'
        supported = rm.get_hypotheses(status="supported")
        assert len(supported) == 1
        assert supported[0]["status"] == "supported"

    def test_filter_by_domain(self, rm):
        rm.create_hypothesis({"title": "H1", "statement": "S1", "domain": "neuroscience"})
        rm.create_hypothesis({"title": "H2", "statement": "S2", "domain": "physics"})
        result = rm.get_hypotheses(domain="neuroscience")
        assert len(result) == 1
        assert result[0]["domain"] == "neuroscience"

    def test_update(self, rm):
        h = rm.create_hypothesis({"title": "Original", "statement": "Stmt"})
        updated = rm.update_hypothesis(h["id"], {"title": "Updated", "status": "tested"})
        assert updated["title"] == "Updated"
        assert updated["status"] == "tested"
        # original statement preserved
        assert updated["statement"] == "Stmt"

    def test_update_nonexistent(self, rm):
        result = rm.update_hypothesis("bad-id", {"title": "X"})
        assert result is None

    def test_delete(self, rm):
        h = rm.create_hypothesis({"title": "Delete me", "statement": "Stmt"})
        deleted = rm.delete_hypothesis(h["id"])
        assert deleted is True
        assert rm.get_hypothesis(h["id"]) is None

    def test_delete_nonexistent(self, rm):
        assert rm.delete_hypothesis("no-such-id") is False

    def test_variables_serialized(self, rm):
        h = rm.create_hypothesis({
            "title": "H",
            "statement": "S",
            "variables": ["sleep_duration", "exam_score"],
            "independent_variable": "sleep_duration",
            "dependent_variable": "exam_score",
        })
        fetched = rm.get_hypothesis(h["id"])
        assert isinstance(fetched["variables"], list)
        assert "sleep_duration" in fetched["variables"]
        assert fetched["independent_variable"] == "sleep_duration"


# ---------------------------------------------------------------------------
# Article tests
# ---------------------------------------------------------------------------

class TestArticles:
    def test_create_and_get(self, rm):
        a = rm.create_article({
            "title": "Sleep and Cognition",
            "authors": ["Smith J.", "Doe A."],
            "year": 2023,
            "journal": "Nature",
            "doi": "10.1234/test",
            "article_type": "research",
            "domain": "cognitive_science",
        })
        assert a["id"]
        assert a["title"] == "Sleep and Cognition"
        assert a["authors"] == ["Smith J.", "Doe A."]
        assert a["year"] == 2023

        fetched = rm.get_article(a["id"])
        assert fetched is not None
        assert isinstance(fetched["authors"], list)

    def test_list_and_filter(self, rm):
        rm.create_article({"title": "A1", "domain": "neuroscience"})
        rm.create_article({"title": "A2", "domain": "physics"})
        result = rm.get_articles(domain="neuroscience")
        assert len(result) == 1

    def test_delete(self, rm):
        a = rm.create_article({"title": "Remove"})
        assert rm.delete_article(a["id"]) is True
        assert rm.get_article(a["id"]) is None

    def test_delete_nonexistent(self, rm):
        assert rm.delete_article("no-id") is False


# ---------------------------------------------------------------------------
# Study Design tests
# ---------------------------------------------------------------------------

class TestStudyDesigns:
    def test_create_and_get(self, rm):
        s = rm.create_study_design({
            "title": "Sleep Study",
            "study_type": "experimental",
            "population": "University students aged 18-25",
            "sample_size": 100,
            "control_group": True,
            "randomization": True,
            "blinding": "double",
        })
        assert s["id"]
        assert s["study_type"] == "experimental"
        assert s["control_group"] is True
        assert s["randomization"] is True
        assert s["status"] == "planned"

    def test_list_and_filter_status(self, rm):
        s = rm.create_study_design({
            "title": "S1", "study_type": "experimental", "population": "P"
        })
        rm.update_study_design(s["id"], {"status": "active"})
        rm.create_study_design({"title": "S2", "study_type": "observational", "population": "P"})
        active = rm.get_study_designs(status="active")
        assert len(active) == 1
        assert active[0]["status"] == "active"

    def test_update_status(self, rm):
        s = rm.create_study_design({
            "title": "S", "study_type": "experimental", "population": "P"
        })
        updated = rm.update_study_design(s["id"], {"status": "completed"})
        assert updated["status"] == "completed"

    def test_delete(self, rm):
        s = rm.create_study_design({
            "title": "Del", "study_type": "experimental", "population": "P"
        })
        assert rm.delete_study_design(s["id"]) is True
        assert rm.get_study_design(s["id"]) is None

    def test_booleans_roundtrip(self, rm):
        s = rm.create_study_design({
            "title": "Bool", "study_type": "experimental", "population": "P",
            "control_group": False, "randomization": False,
        })
        fetched = rm.get_study_design(s["id"])
        assert fetched["control_group"] is False
        assert fetched["randomization"] is False


# ---------------------------------------------------------------------------
# Finding tests
# ---------------------------------------------------------------------------

class TestFindings:
    def test_create_and_get(self, rm):
        f = rm.create_finding({
            "title": "Sleep boosts scores",
            "summary": "8h sleep → 15% higher scores",
            "conclusion": "Adequate sleep significantly improves performance.",
            "statistical_test": "independent t-test",
            "p_value": 0.001,
            "effect_size": 0.72,
            "significance": "significant",
        })
        assert f["id"]
        assert f["significance"] == "significant"
        assert f["p_value"] == pytest.approx(0.001)
        assert f["effect_size"] == pytest.approx(0.72)

    def test_list_all(self, rm):
        rm.create_finding({"title": "F1", "summary": "S", "conclusion": "C"})
        rm.create_finding({"title": "F2", "summary": "S", "conclusion": "C"})
        assert len(rm.get_findings()) == 2

    def test_update_status(self, rm):
        f = rm.create_finding({"title": "F", "summary": "S", "conclusion": "C"})
        updated = rm.update_finding(f["id"], {"status": "published"})
        assert updated["status"] == "published"

    def test_delete(self, rm):
        f = rm.create_finding({"title": "Del", "summary": "S", "conclusion": "C"})
        assert rm.delete_finding(f["id"]) is True
        assert rm.get_finding(f["id"]) is None

    def test_none_numeric_fields(self, rm):
        f = rm.create_finding({"title": "F", "summary": "S", "conclusion": "C"})
        fetched = rm.get_finding(f["id"])
        assert fetched["p_value"] is None
        assert fetched["effect_size"] is None


# ---------------------------------------------------------------------------
# Stats test
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_empty(self, rm):
        stats = rm.get_stats()
        assert stats == {"hypotheses": 0, "articles": 0, "study_designs": 0, "findings": 0}

    def test_stats_populated(self, rm):
        rm.create_hypothesis({"title": "H", "statement": "S"})
        rm.create_article({"title": "A"})
        rm.create_study_design({"title": "SD", "study_type": "experimental", "population": "P"})
        rm.create_finding({"title": "F", "summary": "S", "conclusion": "C"})
        stats = rm.get_stats()
        assert stats == {"hypotheses": 1, "articles": 1, "study_designs": 1, "findings": 1}
