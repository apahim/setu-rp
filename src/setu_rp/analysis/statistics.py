"""Statistical tests: t-test, Mann-Whitney, proportion z-test, effect sizes."""

import math

import numpy as np
from scipy import stats


def descriptive_stats(values: list[float]) -> dict:
    """Compute descriptive statistics for a list of values.

    Returns:
        Dict with keys: n, mean, median, std_dev, min_val, max_val, q1, q3.
    """
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "std_dev": None,
            "min_val": None,
            "max_val": None,
            "q1": None,
            "q3": None,
        }

    arr = np.array(values, dtype=float)
    return {
        "n": len(arr),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std_dev": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        "min_val": float(np.min(arr)),
        "max_val": float(np.max(arr)),
        "q1": float(np.percentile(arr, 25)),
        "q3": float(np.percentile(arr, 75)),
    }


def check_normality(values: list[float], alpha: float = 0.05) -> tuple[bool, float]:
    """Check normality using the Shapiro-Wilk test.

    Args:
        values: Sample data.
        alpha: Significance level.

    Returns:
        Tuple of (is_normal, p_value).
    """
    if len(values) < 3:
        return False, 0.0
    # Shapiro-Wilk has a max sample size; subsample if needed
    arr = np.array(values, dtype=float)
    if len(arr) > 5000:
        rng = np.random.default_rng(42)
        arr = rng.choice(arr, 5000, replace=False)
    stat, p_value = stats.shapiro(arr)
    return p_value > alpha, float(p_value)


def cohens_d(pre: list[float], post: list[float]) -> float:
    """Compute Cohen's d effect size (pooled standard deviation)."""
    n1, n2 = len(pre), len(post)
    if n1 < 2 or n2 < 2:
        return 0.0
    m1, m2 = np.mean(pre), np.mean(post)
    s1, s2 = np.std(pre, ddof=1), np.std(post, ddof=1)
    pooled = math.sqrt(((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2))
    if pooled == 0:
        return 0.0
    return float((m2 - m1) / pooled)


def rank_biserial(u_stat: float, n1: int, n2: int) -> float:
    """Compute rank-biserial correlation as effect size for Mann-Whitney U."""
    if n1 * n2 == 0:
        return 0.0
    return float(1 - (2 * u_stat) / (n1 * n2))


def choose_and_run_test(pre: list[float], post: list[float]) -> dict:
    """Check normality and run the appropriate test.

    If both samples pass normality: Welch's t-test + Cohen's d.
    Otherwise: Mann-Whitney U + rank-biserial correlation.

    Returns:
        Dict matching the statistical_tests table schema.
    """
    n_pre, n_post = len(pre), len(post)

    if n_pre < 2 or n_post < 2:
        return {
            "test_name": "insufficient_data",
            "statistic": None,
            "p_value": None,
            "effect_size": None,
            "effect_size_type": None,
            "ci_lower": None,
            "ci_upper": None,
            "pre_n": n_pre,
            "post_n": n_post,
            "significant": 0,
            "notes": f"Insufficient data: pre_n={n_pre}, post_n={n_post}",
        }

    pre_normal, pre_p = check_normality(pre)
    post_normal, post_p = check_normality(post)

    if pre_normal and post_normal:
        stat, p_value = stats.ttest_ind(pre, post, equal_var=False)
        d = cohens_d(pre, post)
        # Bootstrap CI for Cohen's d
        ci_lo, ci_hi = _bootstrap_ci_cohens_d(pre, post)
        return {
            "test_name": "welch_t_test",
            "statistic": float(stat),
            "p_value": float(p_value),
            "effect_size": d,
            "effect_size_type": "cohens_d",
            "ci_lower": ci_lo,
            "ci_upper": ci_hi,
            "pre_n": n_pre,
            "post_n": n_post,
            "significant": 1 if p_value < 0.05 else 0,
            "notes": f"Normality: pre p={pre_p:.4f}, post p={post_p:.4f}",
        }
    else:
        stat, p_value = stats.mannwhitneyu(pre, post, alternative="two-sided")
        rb = rank_biserial(stat, n_pre, n_post)
        return {
            "test_name": "mann_whitney_u",
            "statistic": float(stat),
            "p_value": float(p_value),
            "effect_size": rb,
            "effect_size_type": "rank_biserial",
            "ci_lower": None,
            "ci_upper": None,
            "pre_n": n_pre,
            "post_n": n_post,
            "significant": 1 if p_value < 0.05 else 0,
            "notes": f"Non-normal. Normality: pre p={pre_p:.4f}, post p={post_p:.4f}",
        }


def run_proportion_test(
    pre_count: int,
    pre_total: int,
    post_count: int,
    post_total: int,
) -> dict:
    """Run a two-proportion z-test with odds ratio and 95% CI.

    Args:
        pre_count: Number of "successes" in pre-period.
        pre_total: Total observations in pre-period.
        post_count: Number of "successes" in post-period.
        post_total: Total observations in post-period.

    Returns:
        Dict matching the statistical_tests table schema.
    """
    if pre_total == 0 or post_total == 0:
        return {
            "test_name": "proportion_z_test",
            "statistic": None,
            "p_value": None,
            "effect_size": None,
            "effect_size_type": "odds_ratio",
            "ci_lower": None,
            "ci_upper": None,
            "pre_n": pre_total,
            "post_n": post_total,
            "significant": 0,
            "notes": "Empty group",
        }

    stat, p_value = _manual_proportion_z(pre_count, pre_total, post_count, post_total)

    # Odds ratio
    a, b = post_count, post_total - post_count
    c, d = pre_count, pre_total - pre_count
    if b == 0 or c == 0 or d == 0:
        odds_ratio = float("inf") if a > 0 else 0.0
        ci_lo, ci_hi = None, None
    else:
        odds_ratio = (a * d) / (b * c) if b * c > 0 else float("inf")
        log_or = math.log(odds_ratio) if odds_ratio > 0 and odds_ratio != float("inf") else 0
        se = math.sqrt(1 / max(a, 1) + 1 / max(b, 1) + 1 / max(c, 1) + 1 / max(d, 1))
        ci_lo = math.exp(log_or - 1.96 * se)
        ci_hi = math.exp(log_or + 1.96 * se)

    return {
        "test_name": "proportion_z_test",
        "statistic": float(stat) if stat is not None else None,
        "p_value": float(p_value) if p_value is not None else None,
        "effect_size": float(odds_ratio) if odds_ratio != float("inf") else None,
        "effect_size_type": "odds_ratio",
        "ci_lower": ci_lo,
        "ci_upper": ci_hi,
        "pre_n": pre_total,
        "post_n": post_total,
        "significant": 1 if p_value is not None and p_value < 0.05 else 0,
        "notes": None,
    }


def wilson_ci(successes: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    """Compute Wilson score confidence interval for a proportion.

    More accurate than the normal approximation for small samples or
    proportions near 0 or 1.

    Args:
        successes: Number of successes.
        total: Total observations.
        alpha: Significance level (default 0.05 for 95% CI).

    Returns:
        Tuple of (lower, upper) bounds.
    """
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    z = stats.norm.ppf(1 - alpha / 2)
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator
    return max(0.0, float(centre - margin)), min(1.0, float(centre + margin))


def _manual_proportion_z(
    pre_count: int, pre_total: int, post_count: int, post_total: int
) -> tuple[float, float]:
    """Manual two-proportion z-test fallback."""
    p1 = post_count / post_total
    p2 = pre_count / pre_total
    p_pool = (post_count + pre_count) / (post_total + pre_total)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / post_total + 1 / pre_total))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    return z, p_value


def benjamini_hochberg(p_values: list[tuple[int, float]]) -> list[tuple[int, float, bool]]:
    """Apply Benjamini-Hochberg FDR correction to a list of p-values.

    Args:
        p_values: List of (row_id, raw_p_value) tuples. Entries with None
            p-values are excluded from correction.

    Returns:
        List of (row_id, adjusted_p_value, significant) tuples.
    """
    # Filter out None p-values
    valid = [(row_id, p) for row_id, p in p_values if p is not None]
    if not valid:
        return []

    # Sort by p-value ascending
    valid.sort(key=lambda x: x[1])
    m = len(valid)

    results = []
    # Compute adjusted p-values (step-up procedure)
    prev_adj = 1.0
    for i in range(m - 1, -1, -1):
        row_id, p = valid[i]
        rank = i + 1
        adj = min(prev_adj, p * m / rank)
        adj = min(adj, 1.0)
        prev_adj = adj
        results.append((row_id, adj, adj < 0.05))

    results.reverse()
    return results


def _bootstrap_ci_cohens_d(
    pre: list[float],
    post: list[float],
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Bootstrap 95% CI for Cohen's d."""
    rng = np.random.default_rng(42)
    pre_arr = np.array(pre)
    post_arr = np.array(post)
    ds = []
    for _ in range(n_bootstrap):
        pre_sample = rng.choice(pre_arr, len(pre_arr), replace=True)
        post_sample = rng.choice(post_arr, len(post_arr), replace=True)
        ds.append(cohens_d(pre_sample.tolist(), post_sample.tolist()))
    lo = float(np.percentile(ds, 100 * alpha / 2))
    hi = float(np.percentile(ds, 100 * (1 - alpha / 2)))
    return lo, hi
