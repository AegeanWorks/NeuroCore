"""
Statistics Engine

Provides statistical analysis methods for the Research module.
All methods accept plain Python lists of floats and return structured
dicts containing results, interpretations, and metadata.

Dependencies: numpy, scipy (both already in requirements.txt)
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as sp_stats


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_data(raw: str) -> List[float]:
    """
    Parse a user-supplied string of numbers into a float list.
    Accepts comma, semicolon, newline, tab, or space as delimiters.
    Raises ValueError if fewer than 2 valid numbers are found.
    """
    import re
    tokens = re.split(r"[,;\s\n\t]+", raw.strip())
    values = []
    for t in tokens:
        t = t.strip()
        if t:
            try:
                values.append(float(t))
            except ValueError:
                raise ValueError(f"Invalid number: '{t}'")
    if len(values) < 2:
        raise ValueError("At least 2 data points are required.")
    return values


def _fmt(v: Optional[float], decimals: int = 4) -> Optional[str]:
    """Format a float to fixed decimals, returning None if v is None."""
    if v is None:
        return None
    return f"{v:.{decimals}f}"


def _sig_stars(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    if p < 0.10:
        return "."
    return "ns"


def _interpret_effect_d(d: float) -> str:
    d = abs(d)
    if d < 0.2:
        return "negligible"
    if d < 0.5:
        return "small"
    if d < 0.8:
        return "medium"
    return "large"


def _interpret_r(r: float) -> str:
    r = abs(r)
    if r < 0.1:
        return "negligible"
    if r < 0.3:
        return "small"
    if r < 0.5:
        return "medium"
    if r < 0.7:
        return "large"
    return "very large"


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------

def descriptive_stats(data: List[float]) -> Dict[str, Any]:
    """Compute comprehensive descriptive statistics for one dataset."""
    a = np.array(data, dtype=float)
    n = len(a)
    if n == 0:
        raise ValueError("descriptive_stats requires at least one value.")
    q1, q3 = float(np.percentile(a, 25)), float(np.percentile(a, 75))

    if n >= 2:
        std = float(np.std(a, ddof=1))
        variance = float(np.var(a, ddof=1))
        se = float(sp_stats.sem(a))
        skew = float(sp_stats.skew(a))
        kurt = float(sp_stats.kurtosis(a))  # excess kurtosis
        skew_interp = (
            "approximately symmetric" if abs(skew) < 0.5
            else ("moderately skewed right" if 0.5 <= skew < 1 else
                  "highly skewed right" if skew >= 1 else
                  "moderately skewed left" if -1 < skew <= -0.5 else
                  "highly skewed left")
        )
        kurt_interp = (
            "mesokurtic (normal-like)" if abs(kurt) < 0.5
            else ("leptokurtic (heavy tails)" if kurt > 0 else "platykurtic (light tails)")
        )
    else:
        std = variance = se = skew = kurt = None
        skew_interp = kurt_interp = "undefined (n < 2)"

    # Normality test (Shapiro-Wilk, best for n < 5000)
    if n >= 3:
        sw_stat, sw_p = sp_stats.shapiro(a)
        normality = {
            "test": "Shapiro-Wilk",
            "statistic": round(float(sw_stat), 4),
            "p_value": round(float(sw_p), 4),
            "normal": sw_p > 0.05,
            "interpretation": (
                f"W = {sw_stat:.4f}, p = {sw_p:.4f} — "
                f"{'data appears normally distributed' if sw_p > 0.05 else 'data departs from normality'} "
                f"(α = 0.05)"
            ),
        }
    else:
        normality = None

    return {
        "n": n,
        "mean": round(float(np.mean(a)), 4),
        "median": round(float(np.median(a)), 4),
        "mode": round(float(sp_stats.mode(a, keepdims=True).mode[0]), 4),
        "std": round(std, 4) if std is not None else None,
        "variance": round(variance, 4) if variance is not None else None,
        "se": round(se, 4) if se is not None else None,
        "min": round(float(np.min(a)), 4),
        "max": round(float(np.max(a)), 4),
        "range": round(float(np.max(a) - np.min(a)), 4),
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "iqr": round(q3 - q1, 4),
        "skewness": round(skew, 4) if skew is not None else None,
        "kurtosis": round(kurt, 4) if kurt is not None else None,
        "skewness_interpretation": skew_interp,
        "kurtosis_interpretation": kurt_interp,
        "normality": normality,
        "sum": round(float(np.sum(a)), 4),
    }


# ---------------------------------------------------------------------------
# Confidence interval
# ---------------------------------------------------------------------------

def confidence_interval(data: List[float], confidence: float = 0.95) -> Dict[str, Any]:
    """Compute the confidence interval for the mean."""
    a = np.array(data, dtype=float)
    n = len(a)
    if n < 2:
        raise ValueError("Confidence interval requires at least two values.")
    mean = float(np.mean(a))
    se = float(sp_stats.sem(a))
    t_crit = float(sp_stats.t.ppf((1 + confidence) / 2, df=n - 1))
    margin = t_crit * se
    lo, hi = mean - margin, mean + margin
    return {
        "confidence": confidence,
        "mean": round(mean, 4),
        "lower": round(lo, 4),
        "upper": round(hi, 4),
        "margin_of_error": round(margin, 4),
        "t_critical": round(t_crit, 4),
        "df": n - 1,
        "interpretation": (
            f"We are {confidence*100:.0f}% confident the population mean lies "
            f"between {lo:.4f} and {hi:.4f}."
        ),
    }


# ---------------------------------------------------------------------------
# One-sample t-test
# ---------------------------------------------------------------------------

def one_sample_ttest(data: List[float], mu: float = 0.0, alpha: float = 0.05) -> Dict[str, Any]:
    """Test whether the sample mean differs from a hypothesized value mu."""
    a = np.array(data, dtype=float)
    n = len(a)
    if n < 2:
        raise ValueError("One-sample t-test requires at least two values.")
    t_stat, p_val = sp_stats.ttest_1samp(a, popmean=mu)
    t_stat, p_val = float(t_stat), float(p_val)
    df = n - 1
    mean = float(np.mean(a))
    std = float(np.std(a, ddof=1))
    d = (mean - mu) / std if std > 0 else 0.0
    reject = p_val < alpha
    ci = confidence_interval(data, 1 - alpha)

    return {
        "test": "One-sample t-test",
        "hypothesized_mean": mu,
        "sample_mean": round(mean, 4),
        "n": n,
        "df": df,
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "cohen_d": round(d, 4),
        "effect_interpretation": _interpret_effect_d(d),
        "ci": ci,
        "interpretation": (
            f"t({df}) = {t_stat:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀: μ = {mu} at α = {alpha}. "
            f"Cohen's d = {d:.4f} ({_interpret_effect_d(d)} effect)."
        ),
    }


# ---------------------------------------------------------------------------
# Two-sample t-test (independent)
# ---------------------------------------------------------------------------

def two_sample_ttest(
    data1: List[float],
    data2: List[float],
    equal_var: bool = False,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Welch's (or Student's) t-test for two independent samples."""
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Two-sample t-test requires at least two values per group.")
    t_stat, p_val = sp_stats.ttest_ind(a, b, equal_var=equal_var)
    t_stat, p_val = float(t_stat), float(p_val)

    # Levene's test for equality of variances
    lev_stat, lev_p = sp_stats.levene(a, b)

    pooled_std = math.sqrt(
        ((len(a) - 1) * float(np.var(a, ddof=1)) + (len(b) - 1) * float(np.var(b, ddof=1)))
        / (len(a) + len(b) - 2)
    )
    d = (float(np.mean(a)) - float(np.mean(b))) / pooled_std if pooled_std > 0 else 0.0
    reject = p_val < alpha
    test_name = "Student's t-test" if equal_var else "Welch's t-test"

    return {
        "test": test_name,
        "group_a": {
            "n": len(a), "mean": round(float(np.mean(a)), 4),
            "std": round(float(np.std(a, ddof=1)), 4),
        },
        "group_b": {
            "n": len(b), "mean": round(float(np.mean(b)), 4),
            "std": round(float(np.std(b, ddof=1)), 4),
        },
        "mean_difference": round(float(np.mean(a)) - float(np.mean(b)), 4),
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "cohen_d": round(d, 4),
        "effect_interpretation": _interpret_effect_d(d),
        "levene": {
            "statistic": round(float(lev_stat), 4),
            "p_value": round(float(lev_p), 4),
            "equal_variances": lev_p > 0.05,
        },
        "interpretation": (
            f"{test_name}: t = {t_stat:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀ at α = {alpha}. "
            f"Mean difference = {float(np.mean(a)) - float(np.mean(b)):.4f}. "
            f"Cohen's d = {d:.4f} ({_interpret_effect_d(d)} effect)."
        ),
    }


# ---------------------------------------------------------------------------
# Paired t-test
# ---------------------------------------------------------------------------

def paired_ttest(
    data1: List[float],
    data2: List[float],
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Paired-samples t-test (before/after or matched pairs)."""
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    if len(a) != len(b):
        raise ValueError("Paired t-test requires equal-length datasets.")
    if len(a) < 2:
        raise ValueError("Paired t-test requires at least two pairs.")
    diffs = a - b
    t_stat, p_val = sp_stats.ttest_rel(a, b)
    t_stat, p_val = float(t_stat), float(p_val)
    df = len(a) - 1
    diff_std = float(np.std(diffs, ddof=1))
    d = float(np.mean(diffs)) / diff_std if diff_std > 0 else 0.0
    reject = p_val < alpha

    return {
        "test": "Paired t-test",
        "n_pairs": len(a),
        "df": df,
        "mean_difference": round(float(np.mean(diffs)), 4),
        "std_difference": round(float(np.std(diffs, ddof=1)), 4),
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "cohen_d": round(d, 4),
        "effect_interpretation": _interpret_effect_d(d),
        "interpretation": (
            f"Paired t-test: t({df}) = {t_stat:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀ at α = {alpha}. "
            f"Mean difference = {float(np.mean(diffs)):.4f}. "
            f"Cohen's d = {d:.4f} ({_interpret_effect_d(d)} effect)."
        ),
    }


# ---------------------------------------------------------------------------
# Pearson correlation
# ---------------------------------------------------------------------------

def pearson_correlation(data1: List[float], data2: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Pearson product-moment correlation."""
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    if len(a) != len(b):
        raise ValueError("Correlation requires equal-length datasets.")
    if len(a) < 2:
        raise ValueError("Correlation requires at least two values.")
    if float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
        return {
            "test": "Pearson Correlation",
            "n": len(a),
            "r": None,
            "r_squared": None,
            "p_value": None,
            "alpha": alpha,
            "significant": False,
            "significance_stars": "",
            "effect_interpretation": "undefined (zero variance)",
            "direction": "none",
            "interpretation": (
                "Correlation is undefined because at least one series has zero variance."
            ),
        }
    r, p_val = sp_stats.pearsonr(a, b)
    r, p_val = float(r), float(p_val)
    r_sq = r ** 2
    reject = p_val < alpha

    return {
        "test": "Pearson Correlation",
        "n": len(a),
        "r": round(r, 4),
        "r_squared": round(r_sq, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "effect_interpretation": _interpret_r(r),
        "direction": "positive" if r > 0 else "negative" if r < 0 else "none",
        "interpretation": (
            f"r({len(a)-2}) = {r:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"R² = {r_sq:.4f} ({r_sq*100:.1f}% variance explained). "
            f"{_interpret_r(r).capitalize()} {('positive' if r > 0 else 'negative')} correlation. "
            f"{'Statistically significant' if reject else 'Not significant'} at α = {alpha}."
        ),
    }


# ---------------------------------------------------------------------------
# Spearman correlation
# ---------------------------------------------------------------------------

def spearman_correlation(data1: List[float], data2: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Spearman rank-order correlation (non-parametric)."""
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    if len(a) != len(b):
        raise ValueError("Correlation requires equal-length datasets.")
    if len(a) < 2:
        raise ValueError("Correlation requires at least two values.")
    if float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
        return {
            "test": "Spearman Correlation",
            "n": len(a),
            "rho": None,
            "p_value": None,
            "alpha": alpha,
            "significant": False,
            "significance_stars": "",
            "effect_interpretation": "undefined (zero variance)",
            "direction": "none",
            "interpretation": (
                "Correlation is undefined because at least one series has zero variance."
            ),
        }
    rho, p_val = sp_stats.spearmanr(a, b)
    rho, p_val = float(rho), float(p_val)
    reject = p_val < alpha

    return {
        "test": "Spearman Correlation",
        "n": len(a),
        "rho": round(rho, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "effect_interpretation": _interpret_r(rho),
        "direction": "positive" if rho > 0 else "negative" if rho < 0 else "none",
        "interpretation": (
            f"ρ = {rho:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{_interpret_r(rho).capitalize()} {('positive' if rho > 0 else 'negative')} "
            f"rank correlation. "
            f"{'Statistically significant' if reject else 'Not significant'} at α = {alpha}."
        ),
    }


# ---------------------------------------------------------------------------
# Chi-square goodness-of-fit
# ---------------------------------------------------------------------------

def chi_square_goodness(
    observed: List[float],
    expected: Optional[List[float]] = None,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Chi-square goodness-of-fit test. If expected is None, assumes uniform distribution."""
    obs = np.array(observed, dtype=float)
    if expected is not None:
        exp = np.array(expected, dtype=float)
        if len(obs) != len(exp):
            raise ValueError("Observed and expected must have the same length.")
    else:
        exp = np.full_like(obs, fill_value=np.sum(obs) / len(obs))

    chi2, p_val = sp_stats.chisquare(obs, f_exp=exp)
    chi2, p_val = float(chi2), float(p_val)
    df = len(obs) - 1
    reject = p_val < alpha

    # Cramér's V (effect size for chi-square)
    n = float(np.sum(obs))
    cramer_v = math.sqrt(chi2 / (n * (min(2, df + 1) - 1))) if n > 0 else 0.0

    return {
        "test": "Chi-square goodness-of-fit",
        "n_categories": len(obs),
        "df": df,
        "chi2_statistic": round(chi2, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "cramer_v": round(cramer_v, 4),
        "interpretation": (
            f"χ²({df}) = {chi2:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀ (uniform/expected distribution) "
            f"at α = {alpha}. Cramér's V = {cramer_v:.4f}."
        ),
    }


# ---------------------------------------------------------------------------
# One-way ANOVA
# ---------------------------------------------------------------------------

def one_way_anova(*groups: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """One-way ANOVA across multiple groups."""
    arrays = [np.array(g, dtype=float) for g in groups]
    if len(arrays) < 2:
        raise ValueError("ANOVA requires at least 2 groups.")

    f_stat, p_val = sp_stats.f_oneway(*arrays)
    f_stat, p_val = float(f_stat), float(p_val)

    # Effect size: eta-squared
    grand_mean = float(np.mean(np.concatenate(arrays)))
    ss_between = sum(len(a) * (float(np.mean(a)) - grand_mean) ** 2 for a in arrays)
    ss_total = sum(float(np.sum((a - grand_mean) ** 2)) for a in arrays)
    eta_sq = ss_between / ss_total if ss_total > 0 else 0.0
    df_between = len(arrays) - 1
    df_within = sum(len(a) for a in arrays) - len(arrays)
    reject = p_val < alpha

    group_stats = [
        {
            "group": i + 1,
            "n": len(a),
            "mean": round(float(np.mean(a)), 4),
            "std": round(float(np.std(a, ddof=1)), 4),
        }
        for i, a in enumerate(arrays)
    ]

    return {
        "test": "One-way ANOVA",
        "n_groups": len(arrays),
        "df_between": df_between,
        "df_within": df_within,
        "f_statistic": round(f_stat, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "eta_squared": round(eta_sq, 4),
        "group_stats": group_stats,
        "interpretation": (
            f"F({df_between}, {df_within}) = {f_stat:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀ (all group means equal) "
            f"at α = {alpha}. η² = {eta_sq:.4f} "
            f"({'small' if eta_sq < 0.06 else 'medium' if eta_sq < 0.14 else 'large'} effect)."
        ),
    }


# ---------------------------------------------------------------------------
# Mann-Whitney U (non-parametric alternative to two-sample t-test)
# ---------------------------------------------------------------------------

def mann_whitney(data1: List[float], data2: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Mann-Whitney U test (non-parametric, does not assume normality)."""
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    u_stat, p_val = sp_stats.mannwhitneyu(a, b, alternative="two-sided")
    u_stat, p_val = float(u_stat), float(p_val)
    n1, n2 = len(a), len(b)
    # Common language effect size (probability of superiority)
    ps = u_stat / (n1 * n2)
    reject = p_val < alpha

    return {
        "test": "Mann-Whitney U",
        "group_a": {"n": n1, "median": round(float(np.median(a)), 4)},
        "group_b": {"n": n2, "median": round(float(np.median(b)), 4)},
        "u_statistic": round(u_stat, 4),
        "p_value": round(p_val, 4),
        "alpha": alpha,
        "significant": reject,
        "significance_stars": _sig_stars(p_val),
        "probability_of_superiority": round(ps, 4),
        "interpretation": (
            f"Mann-Whitney U = {u_stat:.4f}, p = {p_val:.4f} {_sig_stars(p_val)}. "
            f"{'Reject' if reject else 'Fail to reject'} H₀ at α = {alpha}. "
            f"Probability of superiority = {ps:.4f}."
        ),
    }


# ---------------------------------------------------------------------------
# Power analysis (post-hoc, for t-tests)
# ---------------------------------------------------------------------------

def power_analysis(
    effect_size: float,
    n: int,
    alpha: float = 0.05,
    test_type: str = "two_sample",
) -> Dict[str, Any]:
    """
    Post-hoc power analysis for t-tests.
    effect_size: Cohen's d
    n: sample size per group
    """
    from scipy.stats import norm

    # Non-centrality parameter
    if test_type == "one_sample":
        nc = effect_size * math.sqrt(n)
        df = n - 1
    else:
        nc = effect_size * math.sqrt(n / 2)
        df = 2 * (n - 1)

    t_crit = float(sp_stats.t.ppf(1 - alpha / 2, df=df))
    # Power = P(|T| > t_crit | nc)
    power = float(1 - sp_stats.t.cdf(t_crit, df=df, loc=nc) + sp_stats.t.cdf(-t_crit, df=df, loc=nc))

    return {
        "effect_size_d": round(effect_size, 4),
        "n_per_group": n,
        "alpha": alpha,
        "power": round(power, 4),
        "power_percent": round(power * 100, 1),
        "adequate": power >= 0.80,
        "interpretation": (
            f"With d = {effect_size:.4f}, n = {n}/group, α = {alpha}: "
            f"Power = {power*100:.1f}%. "
            f"{'Adequate power (≥ 80%).' if power >= 0.80 else 'Insufficient power — consider increasing sample size.'}"
        ),
    }


# ---------------------------------------------------------------------------
# Bootstrap confidence interval for mean difference
# ---------------------------------------------------------------------------

def bootstrap_mean_diff_ci(
    data1: List[float],
    data2: List[float],
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Bootstrap 95% CI for the difference in means (data1 - data2).
    Uses the percentile method.
    """
    rng = np.random.default_rng(seed)
    a, b = np.array(data1, dtype=float), np.array(data2, dtype=float)
    observed_diff = float(np.mean(a)) - float(np.mean(b))

    boot_diffs = np.array([
        rng.choice(a, size=len(a), replace=True).mean()
        - rng.choice(b, size=len(b), replace=True).mean()
        for _ in range(n_boot)
    ])

    alpha_half = (1 - confidence) / 2
    lo = float(np.percentile(boot_diffs, alpha_half * 100))
    hi = float(np.percentile(boot_diffs, (1 - alpha_half) * 100))
    significant = lo > 0 or hi < 0  # CI doesn't span zero

    return {
        "method": "Bootstrap percentile",
        "n_bootstrap": n_boot,
        "confidence": confidence,
        "observed_diff": round(observed_diff, 4),
        "lower": round(lo, 4),
        "upper": round(hi, 4),
        "significant": significant,
        "interpretation": (
            f"Bootstrap {confidence*100:.0f}% CI for mean difference: "
            f"[{lo:.4f}, {hi:.4f}]. "
            f"{'CI excludes zero → statistically significant.' if significant else 'CI includes zero → not significant.'}"
        ),
    }


# ---------------------------------------------------------------------------
# Linear trend analysis (for time-series baseline check)
# ---------------------------------------------------------------------------

def linear_trend(values: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    """
    Fit a linear trend to a sequence of values.
    Returns slope, R², and significance (useful for baseline stability check).
    """
    a = np.array(values, dtype=float)
    n = len(a)
    x = np.arange(n, dtype=float)
    slope, intercept, r_value, p_value, se = sp_stats.linregress(x, a)
    r_sq = r_value ** 2
    significant = bool(p_value < alpha)

    return {
        "slope": round(float(slope), 6),
        "intercept": round(float(intercept), 4),
        "r_squared": round(float(r_sq), 4),
        "p_value": round(float(p_value), 4),
        "std_error": round(float(se), 6),
        "significant": significant,
        "significance_stars": _sig_stars(p_value),
        "interpretation": (
            f"Slope = {slope:.6f}/step, R² = {r_sq:.4f}, "
            f"p = {p_value:.4f} {_sig_stars(p_value)}. "
            f"{'Significant trend detected — baseline may not be stable.' if significant else 'No significant trend — baseline appears stable.'}"
        ),
    }


# ---------------------------------------------------------------------------
# Intervention analysis (baseline vs intervention comparison)
# ---------------------------------------------------------------------------

def intervention_analysis(
    baseline: List[float],
    intervention: List[float],
    alpha: float = 0.05,
    n_boot: int = 2000,
) -> Dict[str, Any]:
    """
    Full comparison of baseline vs intervention period.
    Returns descriptive stats, trend check, effect size, t-test,
    Mann-Whitney U, bootstrap CI, and human-readable warnings.
    """
    warnings = []
    MIN_RECOMMENDED = 7

    if len(baseline) < 2 or len(intervention) < 2:
        raise ValueError(
            "Intervention analysis requires at least two values per group."
        )

    if len(baseline) < MIN_RECOMMENDED:
        warnings.append(f"Baseline has only {len(baseline)} data points (recommended ≥ {MIN_RECOMMENDED}).")
    if len(intervention) < MIN_RECOMMENDED:
        warnings.append(f"Intervention has only {len(intervention)} data points (recommended ≥ {MIN_RECOMMENDED}).")

    baseline_desc = descriptive_stats(baseline)
    intervention_desc = descriptive_stats(intervention)

    # Trend checks
    baseline_trend = linear_trend(baseline, alpha=alpha) if len(baseline) >= 3 else None
    if baseline_trend and baseline_trend["significant"]:
        warnings.append("Baseline shows a significant trend — metric was already changing before the intervention.")

    # Mean difference
    mean_diff = float(np.mean(intervention)) - float(np.mean(baseline))

    # Cohen's d (intervention minus baseline)
    pooled_std = math.sqrt(
        ((len(baseline) - 1) * baseline_desc["variance"] + (len(intervention) - 1) * intervention_desc["variance"])
        / (len(baseline) + len(intervention) - 2)
    ) if (len(baseline) + len(intervention) - 2) > 0 else 0
    d = mean_diff / pooled_std if pooled_std > 0 else 0.0

    # Tests
    ttest = two_sample_ttest(intervention, baseline, equal_var=False, alpha=alpha)
    mw = mann_whitney(intervention, baseline, alpha=alpha)
    boot_ci = bootstrap_mean_diff_ci(intervention, baseline, n_boot=n_boot, confidence=1 - alpha)

    # Agreement between tests
    tests_agree = ttest["significant"] == mw["significant"]
    if not tests_agree:
        warnings.append("Welch's t-test and Mann-Whitney U disagree — interpret results cautiously.")

    return {
        "baseline": {
            "n": len(baseline),
            "mean": baseline_desc["mean"],
            "std": baseline_desc["std"],
            "trend": baseline_trend,
        },
        "intervention": {
            "n": len(intervention),
            "mean": intervention_desc["mean"],
            "std": intervention_desc["std"],
        },
        "mean_difference": round(mean_diff, 4),
        "cohen_d": round(d, 4),
        "effect_interpretation": _interpret_effect_d(d),
        "welch_ttest": ttest,
        "mann_whitney": mw,
        "bootstrap_ci": boot_ci,
        "significant": ttest["significant"] or mw["significant"],
        "warnings": warnings,
        "alpha": alpha,
        "summary": (
            f"Mean change: {mean_diff:+.4f} | Cohen's d = {d:.4f} ({_interpret_effect_d(d)}) | "
            f"Welch p = {ttest['p_value']:.4f} {ttest['significance_stars']} | "
            f"Mann-Whitney p = {mw['p_value']:.4f} {mw['significance_stars']} | "
            f"Bootstrap 95% CI [{boot_ci['lower']:.4f}, {boot_ci['upper']:.4f}]"
        ),
    }
