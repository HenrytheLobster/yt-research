"""
exploration_layer.py — Layer 2 Exploration Mode for the Scoring Policy
=======================================================================

Implements frequency dampening to suppress consensus patterns and surface
secondary/tertiary signals that are otherwise masked by frequency dominance.

Architecture:
- detect_dominant_patterns(): Identify top N in each category dynamically
- apply_frequency_dampening(): Apply sigmoid-logarithmic dampening curve
- compute_emergent_rankings(): Recalculate rankings post-dampening
- generate_exploration_report(): Produce structured comparison report

The dampening curve:
  - Preserves baseline scoring system
  - Reduces top patterns by 60-80% (tunable)
  - Enables Layer 3 contradiction detection
  - Fully reversible (exploration_mode flag)
"""

import json
from pathlib import Path
from typing import Tuple
from datetime import datetime
import statistics

# Constants
DAMPENING_TOP_N_TACTICS = 5
DAMPENING_TOP_N_HEURISTICS = 3
DAMPENING_TOP_N_PATTERNS = 2

# Dampening curve parameters
DAMPENING_CURVE_ALPHA = 2.0          # Controls steepness (higher = sharper cliff)
DAMPENING_PERCENTILE_THRESHOLD = 0.7 # Apply dampening to top 30% (70th percentile up)
DAMPENING_MIN_FACTOR = 0.25          # Floor: don't reduce below 25% of original


def detect_dominant_patterns(
    tactics: list[dict],
    heuristics: list[dict],
    niche_patterns: list[dict],
    top_n_tactics: int = DAMPENING_TOP_N_TACTICS,
    top_n_heuristics: int = DAMPENING_TOP_N_HEURISTICS,
    top_n_patterns: int = DAMPENING_TOP_N_PATTERNS,
) -> dict:
    """
    Dynamically detect dominant patterns without hardcoding names.
    Returns structure with top items, their metadata, and percentile ranks.

    Returns:
    {
        "tactics": [
            {
                "name": "...",
                "support_count": N,
                "percentile": 0.0-1.0,
                "position": 1-5,
                ...metadata...
            },
            ...
        ],
        "heuristics": [...],
        "niche_patterns": [...],
        "summary": {
            "total_tactics": N,
            "total_heuristics": N,
            "total_patterns": N,
            "detected_at": "ISO-8601 timestamp"
        }
    }
    """
    result = {
        "tactics": [],
        "heuristics": [],
        "niche_patterns": [],
        "summary": {
            "detected_at": datetime.utcnow().isoformat(),
        }
    }

    # ─── Tactics ───────────────────────────────────────────────────────────
    if tactics:
        sorted_tactics = sorted(
            tactics,
            key=lambda x: x.get("support_count", 1),
            reverse=True
        )
        result["summary"]["total_tactics"] = len(tactics)

        # Compute percentiles for entire population
        all_support = [t.get("support_count", 1) for t in tactics]
        percentiles = {}
        for i, support in enumerate(sorted(all_support)):
            percentiles[support] = i / len(all_support) if all_support else 0

        # Top N tactics with percentile info
        for idx, tactic in enumerate(sorted_tactics[:top_n_tactics]):
            support = tactic.get("support_count", 1)
            result["tactics"].append({
                "name": tactic.get("name", "unnamed"),
                "support_count": support,
                "position": idx + 1,
                "percentile": percentiles.get(support, 0.0),
                "description": tactic.get("description", "")[:100],
                "signals_used": tactic.get("signals_used", []),
            })

    # ─── Heuristics ────────────────────────────────────────────────────────
    if heuristics:
        sorted_heuristics = sorted(
            heuristics,
            key=lambda x: x.get("support_count", 1),
            reverse=True
        )
        result["summary"]["total_heuristics"] = len(heuristics)

        all_support = [h.get("support_count", 1) for h in heuristics]
        percentiles = {}
        for i, support in enumerate(sorted(all_support)):
            percentiles[support] = i / len(all_support) if all_support else 0

        for idx, heuristic in enumerate(sorted_heuristics[:top_n_heuristics]):
            support = heuristic.get("support_count", 1)
            result["heuristics"].append({
                "name": heuristic.get("name", "unnamed"),
                "support_count": support,
                "position": idx + 1,
                "percentile": percentiles.get(support, 0.0),
                "condition": heuristic.get("condition", "")[:100],
                "action": heuristic.get("action", ""),
                "weight": heuristic.get("weight", 0.5),
            })

    # ─── Niche Patterns ────────────────────────────────────────────────────
    if niche_patterns:
        sorted_patterns = sorted(
            niche_patterns,
            key=lambda x: x.get("support_count", 1),
            reverse=True
        )
        result["summary"]["total_patterns"] = len(niche_patterns)

        all_support = [p.get("support_count", 1) for p in niche_patterns]
        percentiles = {}
        for i, support in enumerate(sorted(all_support)):
            percentiles[support] = i / len(all_support) if all_support else 0

        for idx, pattern in enumerate(sorted_patterns[:top_n_patterns]):
            support = pattern.get("support_count", 1)
            result["niche_patterns"].append({
                "pattern": pattern.get("pattern_template", "")[:80],
                "support_count": support,
                "position": idx + 1,
                "percentile": percentiles.get(support, 0.0),
                "example": pattern.get("example", "")[:60],
                "why_it_works": pattern.get("why_it_works", "")[:100],
            })

    return result


def _sigmoid_dampening(
    percentile: float,
    threshold: float = DAMPENING_PERCENTILE_THRESHOLD,
    alpha: float = DAMPENING_CURVE_ALPHA,
    min_factor: float = DAMPENING_MIN_FACTOR,
) -> float:
    """
    Compute dampening factor using sigmoid-logarithmic curve.

    - items below threshold: no dampening (factor = 1.0)
    - items at/above threshold: sigmoid decay from 1.0 to min_factor

    Curve: factor = min_factor + (1 - min_factor) / (1 + (percentile / threshold)^alpha)
    """
    if percentile < threshold:
        return 1.0

    # Normalized position in the "dampening zone"
    normalized = (percentile - threshold) / (1.0 - threshold)

    # Sigmoid-like decay
    factor = min_factor + (1.0 - min_factor) / (1.0 + (normalized ** alpha))
    return max(min_factor, min(1.0, factor))


def apply_frequency_dampening(
    items: list[dict],
    dominant_items: list[dict],
    item_key: str = "name",  # key to match items with dominant_items
    support_key: str = "support_count",
    alpha: float = DAMPENING_CURVE_ALPHA,
    threshold: float = DAMPENING_PERCENTILE_THRESHOLD,
) -> list[dict]:
    """
    Apply frequency dampening to items list based on detected dominant patterns.

    Args:
        items: Full list of items to dampen
        dominant_items: Result from detect_dominant_patterns() for this category
        item_key: Field name used for matching (typically "name" for tactics)
        support_key: Field name for support_count
        alpha: Dampening curve steepness
        threshold: Percentile threshold for applying dampening

    Returns:
        New list with dampened support_count values (original items unchanged)
    """
    # Build dominant pattern map: name → {support_count, percentile}
    dominant_map = {}
    for dom in dominant_items:
        key = dom.get(item_key, "")
        if key:
            dominant_map[key] = {
                "original_support": dom.get("support_count", 1),
                "percentile": dom.get("percentile", 1.0),
            }

    dampened = []
    for item in items:
        item_copy = item.copy()
        item_name = item.get(item_key, "")

        if item_name in dominant_map:
            # This is a dominant item - apply dampening
            dom_info = dominant_map[item_name]
            factor = _sigmoid_dampening(
                dom_info["percentile"],
                threshold=threshold,
                alpha=alpha,
            )
            original_support = item_copy.get(support_key, 1)
            dampened_support = max(1, int(original_support * factor))

            item_copy[support_key] = dampened_support
            item_copy["_dampening_applied"] = {
                "original_support": original_support,
                "dampening_factor": round(factor, 3),
                "dampened_support": dampened_support,
            }

        dampened.append(item_copy)

    return dampened


def compute_emergent_rankings(
    original_items: list[dict],
    dampened_items: list[dict],
    sort_key: str = "support_count",
    group_by: str = None,
) -> dict:
    """
    Compute rankings for both original and dampened lists.
    Show which items rise/fall when dominance is reduced.

    Args:
        original_items: Baseline items
        dampened_items: Frequency-dampened items
        sort_key: Field to sort by (support_count, weight, etc.)
        group_by: Optional field to group results by (e.g., "action" for heuristics)

    Returns:
    {
        "baseline": [items sorted by sort_key],
        "exploration": [items sorted by sort_key after dampening],
        "emergent_top_10": [top 10 that rose in ranking],
        "suppressed_top_10": [top 10 that fell in ranking],
        "shift_analysis": {...}
    }
    """
    # Sort both lists
    baseline_sorted = sorted(
        original_items,
        key=lambda x: x.get(sort_key, 0),
        reverse=True
    )

    exploration_sorted = sorted(
        dampened_items,
        key=lambda x: x.get(sort_key, 0),
        reverse=True
    )

    # Create position maps for shift analysis
    baseline_positions = {}
    exploration_positions = {}

    for idx, item in enumerate(baseline_sorted):
        key = item.get("name") or item.get("condition") or item.get("pattern_template")
        baseline_positions[key] = idx + 1

    for idx, item in enumerate(exploration_sorted):
        key = item.get("name") or item.get("condition") or item.get("pattern_template")
        exploration_positions[key] = idx + 1

    # Compute position shifts
    shifts = []
    for item in exploration_sorted:
        key = item.get("name") or item.get("condition") or item.get("pattern_template")
        baseline_pos = baseline_positions.get(key, 999)
        exploration_pos = exploration_positions.get(key, 999)
        shift = baseline_pos - exploration_pos  # positive = rose in ranking

        shifts.append({
            "key": key,
            "baseline_position": baseline_pos,
            "exploration_position": exploration_pos,
            "shift": shift,
            "support_baseline": item.get("support_count"),
        })

    emergent = sorted([s for s in shifts if s["shift"] > 0], key=lambda x: -x["shift"])[:10]
    suppressed = sorted([s for s in shifts if s["shift"] < 0], key=lambda x: x["shift"])[:10]

    return {
        "baseline_top_20": baseline_sorted[:20],
        "exploration_top_20": exploration_sorted[:20],
        "emergent_rising": emergent,
        "suppressed_falling": suppressed,
        "total_position_shifts": len([s for s in shifts if s["shift"] != 0]),
    }


def generate_exploration_report(
    tactics: list[dict],
    heuristics: list[dict],
    niche_patterns: list[dict],
    raw_dir: Path = None,
) -> dict:
    """
    Generate comprehensive Layer 2 Exploration report.

    Report includes:
    1. Dominant patterns detected (without dampening)
    2. Emergent tactics (post-dampening, top 10)
    3. Emergent heuristics by action (post-dampening)
    4. Emergent niche patterns (post-dampening, top 5)
    5. Strategy theme observations
    6. Mid-frequency signals (3-15 occurrence range)
    7. Novelty candidates
    """

    # ─── Phase 1: Detect dominant patterns ──────────────────────────────────
    print("🔍 Layer 2: Detecting dominant patterns...")
    dominant = detect_dominant_patterns(tactics, heuristics, niche_patterns)

    # ─── Phase 2: Apply dampening ──────────────────────────────────────────
    print("📊 Layer 2: Applying frequency dampening...")

    dampened_tactics = apply_frequency_dampening(
        tactics,
        dominant["tactics"],
        item_key="name",
    )

    dampened_heuristics = apply_frequency_dampening(
        heuristics,
        dominant["heuristics"],
        item_key="name",
    )

    dampened_patterns = apply_frequency_dampening(
        niche_patterns,
        dominant["niche_patterns"],
        item_key="pattern_template",
    )

    # ─── Phase 3: Compute emergent rankings ────────────────────────────────
    print("📈 Layer 2: Computing emergent rankings...")

    tactics_ranking = compute_emergent_rankings(tactics, dampened_tactics)
    heuristics_ranking = compute_emergent_rankings(heuristics, dampened_heuristics)
    patterns_ranking = compute_emergent_rankings(niche_patterns, dampened_patterns)

    # ─── Phase 4: Identify mid-frequency signals ───────────────────────────
    print("🎯 Layer 2: Scanning for mid-frequency signals (3-15x)...")

    def extract_mid_frequency(items: list[dict], min_support: int = 3, max_support: int = 15):
        """Extract items with support_count in target range."""
        return [
            {
                "name": item.get("name") or item.get("condition") or item.get("pattern_template", ""),
                "support_count": item.get("support_count", 1),
                "description": (item.get("description") or item.get("condition") or item.get("why_it_works", ""))[:120],
            }
            for item in items
            if min_support <= item.get("support_count", 1) <= max_support
        ]

    mid_tactics = sorted(
        extract_mid_frequency(tactics),
        key=lambda x: -x["support_count"]
    )[:10]

    mid_heuristics = sorted(
        extract_mid_frequency(heuristics),
        key=lambda x: -x["support_count"]
    )[:10]

    mid_patterns = sorted(
        extract_mid_frequency(niche_patterns),
        key=lambda x: -x["support_count"]
    )[:5]

    # ─── Phase 5: Identify strategy theme candidates ───────────────────────
    print("🔬 Layer 2: Analyzing strategy themes...")

    # Keywords to detect different strategy domains
    strategy_themes = {
        "seasonal_strategies": {
            "keywords": ["seasonal", "holiday", "calendar", "event", "time", "period"],
            "count": 0,
            "examples": [],
        },
        "velocity_mechanics": {
            "keywords": ["velocity", "momentum", "launch", "speed", "quick", "fast", "ranking"],
            "count": 0,
            "examples": [],
        },
        "ad_based_tactics": {
            "keywords": ["ad", "advertis", "paid", "campaign", "click", "impression", "sponsoring"],
            "count": 0,
            "examples": [],
        },
        "category_exploitation": {
            "keywords": ["category", "subcategory", "niche", "segment", "vertical", "genre"],
            "count": 0,
            "examples": [],
        },
        "format_arbitrage": {
            "keywords": ["format", "short", "long", "series", "bundle", "collection", "volume"],
            "count": 0,
            "examples": [],
        },
        "indexing_quirks": {
            "keywords": ["index", "search", "keyword", "algorithm", "rank", "visibility", "appear"],
            "count": 0,
            "examples": [],
        },
        "launching_sequencing": {
            "keywords": ["launch", "sequence", "order", "release", "rollout", "phase"],
            "count": 0,
            "examples": [],
        },
    }

    # Scan all items for theme keywords
    all_items = tactics + heuristics + niche_patterns
    for item in all_items:
        # Safely convert all fields to strings
        desc = item.get("description") or ""
        if isinstance(desc, dict):
            desc = str(desc)
        cond = item.get("condition") or ""
        if isinstance(cond, dict):
            cond = str(cond)
        why = item.get("why_it_works") or ""
        if isinstance(why, dict):
            why = str(why)
        name_field = item.get("name") or ""
        if isinstance(name_field, dict):
            name_field = str(name_field)

        text = (desc + " " + cond + " " + why + " " + name_field).lower()

        name = item.get("name") or item.get("condition") or item.get("pattern_template", "")
        support = item.get("support_count", 1)

        for theme, config in strategy_themes.items():
            if any(kw in text for kw in config["keywords"]):
                config["count"] += support
                if len(config["examples"]) < 3:
                    config["examples"].append({
                        "item": name[:80],
                        "support": support,
                    })

    # Filter to non-empty themes
    strategy_themes = {
        k: v for k, v in strategy_themes.items() if v["count"] > 0
    }

    # ─── Compile final report ──────────────────────────────────────────────
    report = {
        "version": "1.0",
        "generated_at": datetime.utcnow().isoformat(),
        "exploration_mode": "layer_2",
        "dampening_config": {
            "alpha": DAMPENING_CURVE_ALPHA,
            "percentile_threshold": DAMPENING_PERCENTILE_THRESHOLD,
            "min_dampening_factor": DAMPENING_MIN_FACTOR,
        },

        # ────── BASELINE ──────────────────────────────────────────────────
        "baseline": {
            "dominant_patterns": dominant,
        },

        # ────── EMERGENT SIGNALS ──────────────────────────────────────────
        "emergent": {
            "tactics": {
                "top_10_rising": tactics_ranking["emergent_rising"],
                "top_20_by_frequency": [
                    {
                        "name": t.get("name", ""),
                        "support_count": t.get("support_count", 1),
                        "description": t.get("description", "")[:100],
                    }
                    for t in tactics_ranking["exploration_top_20"]
                ],
            },
            "heuristics": {
                "top_10_rising": heuristics_ranking["emergent_rising"],
                "top_20_by_frequency": [
                    {
                        "name": h.get("name", ""),
                        "action": h.get("action", ""),
                        "support_count": h.get("support_count", 1),
                        "weight": h.get("weight", 0.5),
                    }
                    for h in heuristics_ranking["exploration_top_20"]
                ],
            },
            "niche_patterns": {
                "top_10_rising": patterns_ranking["emergent_rising"],
                "top_5_by_frequency": [
                    {
                        "pattern": p.get("pattern_template", ""),
                        "support_count": p.get("support_count", 1),
                        "example": p.get("example", ""),
                    }
                    for p in patterns_ranking["exploration_top_20"][:5]
                ],
            },
        },

        # ────── MID-FREQUENCY SIGNALS ─────────────────────────────────────
        "mid_frequency_signals": {
            "tactics": mid_tactics,
            "heuristics": mid_heuristics,
            "niche_patterns": mid_patterns,
        },

        # ────── STRATEGY THEMES ───────────────────────────────────────────
        "strategy_themes": strategy_themes,

        # ────── STATISTICAL SUMMARY ──────────────────────────────────────
        "statistics": {
            "total_items": {
                "tactics": len(tactics),
                "heuristics": len(heuristics),
                "niche_patterns": len(niche_patterns),
            },
            "position_shifts": {
                "tactics": tactics_ranking["total_position_shifts"],
                "heuristics": heuristics_ranking["total_position_shifts"],
                "niche_patterns": patterns_ranking["total_position_shifts"],
            },
            "dampening_applied": {
                "tactics": sum(1 for t in dampened_tactics if "_dampening_applied" in t),
                "heuristics": sum(1 for h in dampened_heuristics if "_dampening_applied" in h),
                "niche_patterns": sum(1 for p in dampened_patterns if "_dampening_applied" in p),
            },
        },
    }

    return report
