"""
Tests for core/statistics_engine.py and the new ResearchManager experiment tables.
"""

import math
import pytest
from core.statistics_engine import (
    parse_data,
    descriptive_stats,
    confidence_interval,
    one_sample_ttest,
    two_sample_ttest,
    paired_ttest,
    pearson_correlation,
    spearman_correlation,
    chi_square_goodness,
    one_way_anova,
    mann_whitney,
    bootstrap_mean_diff_ci,
    linear_trend,
    intervention_analysis,
)
from core.research_manager import ResearchManager


@pytest.fixture
def rm(tmp_path):
    return ResearchManager(db_path=str(tmp_path / "test.sqlite3"))


# ────────────────────────────────────────────────────────────────────────────
# parse_data
# ────────────────────────────────────────────────────────────────────────────

class TestParseData:
    def test_comma_separated(self):
        assert parse_data("1, 2, 3") == [1.0, 2.0, 3.0]

    def test_newline_separated(self):
        assert parse_data("1\n2\n3") == [1.0, 2.0, 3.0]

    def test_mixed_delimiters(self):
        result = parse_data("1, 2\n3; 4")
        assert result == [1.0, 2.0, 3.0, 4.0]

    def test_invalid_token(self):
        with pytest.raises(ValueError, match="Invalid number"):
            parse_data("1, two, 3")

    def test_too_few_values(self):
        with pytest.raises(ValueError, match="At least 2"):
            parse_data("5")


# ────────────────────────────────────────────────────────────────────────────
# Descriptive statistics
# ────────────────────────────────────────────────────────────────────────────

class TestDescriptiveStats:
    DATA = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]

    def test_mean(self):
        r = descriptive_stats(self.DATA)
        assert r["mean"] == pytest.approx(5.0)

    def test_std(self):
        r = descriptive_stats(self.DATA)
        # sample std for [2,4,4,4,5,5,7,9] is ~2.138, not the population std of 2.0
        assert r["std"] == pytest.approx(2.138, abs=0.01)

    def test_n(self):
        r = descriptive_stats(self.DATA)
        assert r["n"] == 8

    def test_min_max(self):
        r = descriptive_stats(self.DATA)
        assert r["min"] == 2.0
        assert r["max"] == 9.0

    def test_normality_present(self):
        r = descriptive_stats(self.DATA)
        assert "normality" in r
        assert r["normality"] is not None

    def test_has_iqr(self):
        r = descriptive_stats(self.DATA)
        assert r["iqr"] == pytest.approx(r["q3"] - r["q1"], abs=1e-4)


# ────────────────────────────────────────────────────────────────────────────
# Confidence interval
# ────────────────────────────────────────────────────────────────────────────

class TestConfidenceInterval:
    def test_interval_contains_mean(self):
        data = [5.0, 6.0, 4.0, 7.0, 5.5]
        r = confidence_interval(data, confidence=0.95)
        assert r["lower"] < r["mean"] < r["upper"]

    def test_99_wider_than_95(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        r95 = confidence_interval(data, 0.95)
        r99 = confidence_interval(data, 0.99)
        assert r99["upper"] - r99["lower"] > r95["upper"] - r95["lower"]


# ────────────────────────────────────────────────────────────────────────────
# One-sample t-test
# ────────────────────────────────────────────────────────────────────────────

class TestOneSampleTtest:
    def test_reject_null(self):
        # Mean clearly different from 0
        data = [5.0, 5.1, 4.9, 5.2, 4.8] * 4
        r = one_sample_ttest(data, mu=0.0)
        assert r["significant"] is True
        assert r["p_value"] < 0.05

    def test_fail_to_reject(self):
        # Data centered on 0
        data = [-0.1, 0.1, 0.0, -0.05, 0.05]
        r = one_sample_ttest(data, mu=0.0)
        assert r["significant"] is False

    def test_cohen_d_positive_when_mean_above_mu(self):
        data = [4.8, 5.0, 5.2, 5.1, 4.9]  # mean ~5, some variance
        r = one_sample_ttest(data, mu=0.0)
        assert r["cohen_d"] > 0


# ────────────────────────────────────────────────────────────────────────────
# Two-sample t-test
# ────────────────────────────────────────────────────────────────────────────

class TestTwoSampleTtest:
    def test_different_means_significant(self):
        rng = __import__('random').Random(42)
        a = [10.0 + rng.gauss(0, 0.5) for _ in range(20)]
        b = [5.0 + rng.gauss(0, 0.5) for _ in range(20)]
        r = two_sample_ttest(a, b)
        assert r["significant"] is True
        assert r["cohen_d"] > 0

    def test_same_data_not_significant(self):
        data = [3.0, 3.1, 2.9, 3.0, 3.0]
        r = two_sample_ttest(data, data)
        assert r["significant"] is False

    def test_mean_difference_sign(self):
        a = [10.0, 10.0, 10.0]
        b = [5.0, 5.0, 5.0]
        r = two_sample_ttest(a, b)
        assert r["mean_difference"] == pytest.approx(5.0)

    def test_levene_included(self):
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        r = two_sample_ttest(a, b)
        assert "levene" in r


# ────────────────────────────────────────────────────────────────────────────
# Paired t-test
# ────────────────────────────────────────────────────────────────────────────

class TestPairedTtest:
    def test_before_after_significant(self):
        before = [5.0, 4.8, 5.2, 4.9, 5.1]
        after  = [7.0, 6.9, 7.1, 7.2, 6.8]
        r = paired_ttest(before, after)
        assert r["significant"] is True

    def test_unequal_length_raises(self):
        with pytest.raises(ValueError, match="equal-length"):
            paired_ttest([1.0, 2.0], [1.0])


# ────────────────────────────────────────────────────────────────────────────
# Pearson and Spearman correlation
# ────────────────────────────────────────────────────────────────────────────

class TestCorrelation:
    def test_perfect_positive_pearson(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        r = pearson_correlation(x, y)
        assert r["r"] == pytest.approx(1.0, abs=1e-6)

    def test_negative_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 4.0, 3.0, 2.0, 1.0]
        r = pearson_correlation(x, y)
        assert r["r"] == pytest.approx(-1.0, abs=1e-6)
        assert r["direction"] == "negative"

    def test_spearman_monotone(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [1.0, 4.0, 9.0, 16.0, 25.0]  # monotone but non-linear
        r = spearman_correlation(x, y)
        assert r["rho"] == pytest.approx(1.0, abs=1e-6)

    def test_unequal_length_raises(self):
        with pytest.raises(ValueError, match="equal-length"):
            pearson_correlation([1.0, 2.0], [1.0])


# ────────────────────────────────────────────────────────────────────────────
# Chi-square
# ────────────────────────────────────────────────────────────────────────────

class TestChiSquare:
    def test_uniform_not_significant(self):
        obs = [25.0, 25.0, 25.0, 25.0]
        r = chi_square_goodness(obs)
        assert r["significant"] is False

    def test_skewed_significant(self):
        obs = [90.0, 5.0, 3.0, 2.0]
        r = chi_square_goodness(obs)
        assert r["significant"] is True

    def test_expected_mismatch_raises(self):
        with pytest.raises(ValueError, match="same length"):
            chi_square_goodness([10.0, 20.0], [5.0, 10.0, 15.0])


# ────────────────────────────────────────────────────────────────────────────
# One-way ANOVA
# ────────────────────────────────────────────────────────────────────────────

class TestANOVA:
    def test_different_groups_significant(self):
        g1 = [1.0, 1.1, 0.9, 1.0]
        g2 = [5.0, 5.1, 4.9, 5.0]
        g3 = [10.0, 10.1, 9.9, 10.0]
        r = one_way_anova(g1, g2, g3)
        assert r["significant"] is True
        assert r["eta_squared"] > 0.9

    def test_same_groups_not_significant(self):
        g = [3.0, 3.0, 3.0, 3.0]
        r = one_way_anova(g, g, g)
        assert r["significant"] is False

    def test_requires_two_groups(self):
        with pytest.raises(ValueError, match="at least 2"):
            one_way_anova([1.0, 2.0])


# ────────────────────────────────────────────────────────────────────────────
# Mann-Whitney U
# ────────────────────────────────────────────────────────────────────────────

class TestMannWhitney:
    def test_clearly_different(self):
        a = [10.0] * 15
        b = [1.0] * 15
        r = mann_whitney(a, b)
        assert r["significant"] is True
        assert r["probability_of_superiority"] == pytest.approx(1.0)

    def test_same_data_not_significant(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        r = mann_whitney(data, data)
        assert r["significant"] is False


# ────────────────────────────────────────────────────────────────────────────
# Bootstrap CI
# ────────────────────────────────────────────────────────────────────────────

class TestBootstrapCI:
    def test_ci_excludes_zero_for_large_difference(self):
        a = [10.0] * 30
        b = [1.0] * 30
        r = bootstrap_mean_diff_ci(a, b, n_boot=1000)
        assert r["significant"] is True
        assert r["lower"] > 0

    def test_ci_includes_zero_for_same_data(self):
        data = [3.0, 4.0, 5.0, 3.0, 4.0]
        r = bootstrap_mean_diff_ci(data, data, n_boot=500)
        assert r["lower"] <= 0 <= r["upper"]

    def test_observed_diff_correct(self):
        a = [10.0, 10.0, 10.0]
        b = [5.0, 5.0, 5.0]
        r = bootstrap_mean_diff_ci(a, b, n_boot=100)
        assert r["observed_diff"] == pytest.approx(5.0)


# ────────────────────────────────────────────────────────────────────────────
# Linear trend
# ────────────────────────────────────────────────────────────────────────────

class TestLinearTrend:
    def test_strong_upward_trend_significant(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        r = linear_trend(vals)
        assert r["significant"] is True
        assert r["slope"] > 0

    def test_flat_data_not_significant(self):
        vals = [5.0] * 10
        r = linear_trend(vals)
        assert r["significant"] is False


# ────────────────────────────────────────────────────────────────────────────
# Intervention analysis
# ────────────────────────────────────────────────────────────────────────────

class TestInterventionAnalysis:
    BASELINE = [5.0, 5.1, 4.9, 5.0, 5.2, 4.8, 5.0, 5.1, 4.9, 5.0]
    INTERVENTION = [7.0, 7.1, 6.9, 7.2, 6.8, 7.0, 7.1, 6.9, 7.0, 7.2]

    def test_significant_improvement(self):
        r = intervention_analysis(self.BASELINE, self.INTERVENTION)
        assert r["significant"] is True
        assert r["mean_difference"] > 0
        assert r["cohen_d"] > 0.8  # large effect

    def test_no_effect(self):
        r = intervention_analysis(self.BASELINE, self.BASELINE)
        assert r["significant"] is False
        assert abs(r["mean_difference"]) < 0.01

    def test_small_sample_warning(self):
        r = intervention_analysis([1.0, 2.0], [3.0, 4.0])
        assert any("data points" in w for w in r["warnings"])

    def test_result_structure(self):
        r = intervention_analysis(self.BASELINE, self.INTERVENTION)
        assert "baseline" in r
        assert "intervention" in r
        assert "welch_ttest" in r
        assert "mann_whitney" in r
        assert "bootstrap_ci" in r
        assert "warnings" in r
        assert "summary" in r


# ────────────────────────────────────────────────────────────────────────────
# ResearchManager — Interventions, Metrics, Logs, Events
# ────────────────────────────────────────────────────────────────────────────

class TestResearchManagerExperiments:
    def test_create_intervention(self, rm):
        iv = rm.create_intervention({
            "name": "Vitamin D",
            "start_date": "2025-01-01",
        })
        assert iv["id"]
        assert iv["name"] == "Vitamin D"
        assert rm.get_intervention(iv["id"]) is not None

    def test_update_intervention_end_date(self, rm):
        iv = rm.create_intervention({"name": "Test", "start_date": "2025-01-01"})
        updated = rm.update_intervention(iv["id"], {"end_date_actual": "2025-03-01"})
        assert updated["end_date_actual"] == "2025-03-01"

    def test_delete_intervention(self, rm):
        iv = rm.create_intervention({"name": "Del", "start_date": "2025-01-01"})
        assert rm.delete_intervention(iv["id"]) is True
        assert rm.get_intervention(iv["id"]) is None

    def test_create_metric_unique(self, rm):
        rm.create_metric({"name": "Sleep Quality", "unit": "score"})
        with pytest.raises(Exception):
            rm.create_metric({"name": "Sleep Quality"})

    def test_log_and_get_metrics(self, rm):
        iv = rm.create_intervention({"name": "IV", "start_date": "2025-01-01"})
        rm.log_metric({"intervention_id": iv["id"], "metric_name": "Sleep", "log_date": "2025-01-02", "value": 7.5})
        rm.log_metric({"intervention_id": iv["id"], "metric_name": "Sleep", "log_date": "2025-01-03", "value": 8.0})
        logs = rm.get_metric_logs(iv["id"])
        assert len(logs) == 2
        logs_filtered = rm.get_metric_logs(iv["id"], metric_name="Sleep")
        assert len(logs_filtered) == 2

    def test_delete_metric_log(self, rm):
        iv = rm.create_intervention({"name": "IV", "start_date": "2025-01-01"})
        log = rm.log_metric({"intervention_id": iv["id"], "metric_name": "M", "log_date": "2025-01-01", "value": 1.0})
        assert rm.delete_metric_log(log["id"]) is True
        assert rm.get_metric_logs(iv["id"]) == []

    def test_log_and_get_events(self, rm):
        iv = rm.create_intervention({"name": "IV", "start_date": "2025-01-01"})
        rm.log_event({
            "intervention_id": iv["id"],
            "name": "Stress",
            "event_datetime": "2025-01-05T10:00",
            "severity": "high",
        })
        events = rm.get_events(iv["id"])
        assert len(events) == 1
        assert events[0]["severity"] == "high"

    def test_delete_event(self, rm):
        iv = rm.create_intervention({"name": "IV", "start_date": "2025-01-01"})
        ev = rm.log_event({"intervention_id": iv["id"], "name": "E", "event_datetime": "2025-01-01T00:00"})
        assert rm.delete_event(ev["id"]) is True
        assert rm.get_events(iv["id"]) == []

    def test_csv_export(self, rm):
        iv = rm.create_intervention({"name": "IV", "start_date": "2025-01-01"})
        rm.log_metric({"intervention_id": iv["id"], "metric_name": "X", "log_date": "2025-01-02", "value": 5.0})
        rm.log_event({"intervention_id": iv["id"], "name": "Stress", "event_datetime": "2025-01-03T10:00", "severity": "low"})
        csv = rm.export_csv(iv["id"])
        assert "metric" in csv
        assert "event" in csv
        assert "5.0" in csv
        assert "Stress" in csv
