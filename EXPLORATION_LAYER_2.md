# Layer 2 Exploration Mode — KDP Scoring Policy

## Overview

**Layer 2 Exploration Mode** implements frequency dampening to suppress consensus patterns and surface secondary/tertiary signals from the knowledge base.

This is designed to address the problem where the most repeated YouTube-derived tactics (Publisher Rocket analysis, Competitor Analysis, median review threshold ~200) dominate rankings and mask alternative strategies that are valid but underrepresented.

## Architecture

### Three-Layer System

```
Layer 1: Consensus Signal (baseline)
  └─ High-frequency tactics, heuristics, patterns
  └─ Default production mode
  └─ All items scored equally

Layer 2: Consensus Suppressed (exploration)
  └─ Apply frequency dampening to dominant patterns
  └─ Reduce top patterns by 60-80% weight
  └─ Surface previously masked signals
  └─ Reversible; baseline remains unchanged

Layer 3: Contradiction Detection (future)
  └─ Identify conflicting signals
  └─ Detect contradictory patterns
  └─ Flag ambiguous heuristics
```

## Usage

### Generate Baseline + Exploration Report

```bash
# Generate standard policy only (Layer 1)
python3 policy.py

# Generate with Layer 2 exploration analysis
python3 policy.py --exploration-layer-2

# Both with low-confidence rules included
python3 policy.py --exploration-layer-2 --include-low
```

### Outputs

- **`config/scoring_policy.json`** — Standard policy (Layer 1, unchanged)
- **`config/exploration_layer_2_report.json`** — Exploration analysis with:
  - Dominant patterns detected
  - Emergent signals (post-dampening)
  - Mid-frequency signals (3-15x support)
  - Strategy theme discovery
  - Statistical comparison

## Algorithm: Frequency Dampening

### Step 1: Detect Dominant Patterns

Programmatically identify (no hardcoding):

```python
Top 5 Tactics (by support_count)
Top 3 Heuristics (by support_count)
Top 2 Niche Patterns (by support_count)
```

Example output:
```
Tactic: "Keyword Research with Publisher Rocket" — 23 occurrences (99.86 percentile)
Tactic: "Competitor Analysis" — 17 occurrences (99.73 percentile)
Tactic: "Category Optimization" — 12 occurrences (99.59 percentile)
...
```

### Step 2: Apply Sigmoid-Logarithmic Dampening

**Dampening Curve Formula:**

```
percentile_threshold = 0.7  (apply dampening to top 30%)
alpha = 2.0                  (steepness parameter)
min_dampening_factor = 0.25  (floor: 25% of original weight)

If percentile < threshold:
    dampening_factor = 1.0  (no dampening)

Else:
    normalized = (percentile - threshold) / (1.0 - threshold)
    dampening_factor = 0.25 + (1 - 0.25) / (1 + normalized^2.0)
    dampening_factor = max(0.25, min(1.0, dampening_factor))
```

**Behavior:**

- Items below 70th percentile: **no dampening** (factor = 1.0)
- Top 30% items: **sigmoid decay** from 1.0 down to 0.25
- Steepest reduction at the very top (99+ percentile)
- Gradual falloff, not abrupt cliff

**Example:**

```
Original support_count = 23
Percentile = 0.998
Dampening factor ≈ 0.30
Dampened support_count = 23 * 0.30 = 6.9 ≈ 7

Result: This dominant tactic now contributes ~70% less to rankings,
        allowing secondary tactics to surface.
```

### Step 3: Recompute Rankings

Sort by dampened support_count and compare:

- **Baseline rankings** — original frequency
- **Exploration rankings** — post-dampening
- **Position shifts** — which items rose/fell
- **Emergent signals** — items now visible in top 20

### Step 4: Identify Strategy Themes

Scan all items for keyword matches in domains:

- **Seasonal Strategies** — "seasonal", "holiday", "calendar", "event", "period"
- **Velocity Mechanics** — "momentum", "launch", "speed", "ranking", "velocity"
- **Ad-Based Tactics** — "ad", "campaign", "paid", "click", "impression"
- **Category Exploitation** — "category", "subcategory", "niche", "vertical"
- **Format Arbitrage** — "format", "series", "bundle", "volume", "collection"
- **Indexing Quirks** — "index", "algorithm", "rank", "keyword", "visibility"
- **Launch Sequencing** — "launch", "sequence", "order", "rollout", "phase"

Reports counts and examples for each theme.

## Implementation

### Core Functions

#### `detect_dominant_patterns()`

Identifies top N items in each category without hardcoding.

```python
result = detect_dominant_patterns(tactics, heuristics, niche_patterns)
# Returns:
# {
#   "tactics": [{"name": "...", "support_count": N, "percentile": 0-1, ...}, ...],
#   "heuristics": [...],
#   "niche_patterns": [...],
#   "summary": {"total_tactics": N, "detected_at": "..."}
# }
```

#### `apply_frequency_dampening()`

Applies sigmoid curve to items based on dominance detection.

```python
dampened_items = apply_frequency_dampening(
    items=tactics,
    dominant_items=detected_dominant["tactics"],
    item_key="name",
)
# Returns: new list with "_dampening_applied" metadata on affected items
```

#### `compute_emergent_rankings()`

Compares baseline vs. exploration rankings.

```python
rankings = compute_emergent_rankings(original_tactics, dampened_tactics)
# Returns:
# {
#   "baseline_top_20": [...],
#   "exploration_top_20": [...],
#   "emergent_rising": [{"key": "...", "shift": +3, ...}, ...],
#   "suppressed_falling": [...],
#   "total_position_shifts": N
# }
```

#### `generate_exploration_report()`

Comprehensive report generation.

```python
report = generate_exploration_report(tactics, heuristics, niche_patterns)
# Writes to: config/exploration_layer_2_report.json
```

### Integration with Policy Generator

In `policy.py`:

```python
from exploration_layer import generate_exploration_report

# New flag in argument parser
parser.add_argument("--exploration-layer-2", action="store_true",
    help="Generate Layer 2 Exploration Report")

# In generate_policy():
if exploration_layer_2:
    exploration_report = generate_exploration_report(
        tactics, heuristics, niche_patterns, raw_dir
    )
    EXPLORATION_REPORT_FILE.write_text(json.dumps(exploration_report, indent=2))
```

## Report Structure

### `baseline` — Dominant Patterns (No Dampening)

```json
{
  "dominant_patterns": {
    "tactics": [
      {
        "name": "Keyword Research with Publisher Rocket",
        "support_count": 23,
        "position": 1,
        "percentile": 0.9986,
        "description": "...",
        "signals_used": ["BSR", "review_count"]
      },
      ...
    ],
    "heuristics": [...],
    "niche_patterns": [...]
  }
}
```

### `emergent` — Top 10 Rising Items (Post-Dampening)

```json
{
  "emergent": {
    "tactics": {
      "top_10_rising": [
        {
          "key": "Competitor Reverse ASIN",
          "baseline_position": 8,
          "exploration_position": 5,
          "shift": 3,
          "support_baseline": 6
        },
        ...
      ],
      "top_20_by_frequency": [...]
    },
    "heuristics": {...},
    "niche_patterns": {...}
  }
}
```

### `mid_frequency_signals` — 3-15 Occurrence Range

Items with moderate support that are neither consensus nor singletons.

```json
{
  "mid_frequency_signals": {
    "tactics": [
      {
        "name": "...",
        "support_count": 7,
        "description": "..."
      },
      ...
    ],
    "heuristics": [...],
    "niche_patterns": [...]
  }
}
```

### `strategy_themes` — Domain Pattern Discovery

```json
{
  "strategy_themes": {
    "seasonal_strategies": {
      "count": 79,
      "examples": [
        {
          "item": "seasonal_niche_spin",
          "support": 2
        }
      ]
    },
    "velocity_mechanics": {
      "count": 153,
      "examples": [...]
    },
    ...
  }
}
```

## Key Insights from Current Data

### Dominant Patterns (Layer 1)

**Top 5 Tactics:**
1. Keyword Research with Publisher Rocket — 23x
2. Competitor Analysis — 17x
3. Category Optimization — 12x
4. BSR + Review Analysis — 8x
5. Category Analysis — 8x

**Top 3 Heuristics:**
1. `low_review_boost` — 39x (boost action)
2. `Review Count Threshold` — 36x (boost action)
3. `review_threshold` — 22x (boost action)

**Top 2 Niche Patterns:**
1. IDENTITY + PROBLEM + CONSTRAINT — 133x
2. IDENTITY + PROBLEM + CONSTRAINT (variant) — 2x

### Emergent Signals (Layer 2, Post-Dampening)

**Tactics Rising in Rankings:**
- Competitor Reverse ASIN (+3 positions)
- Publisher Rocket Analysis (+2)
- Keyword Validation (+2)
- Keyword Competition Analysis (+2)
- Sub-Niche Focus (+2)

**Strategy Themes Discovered:**
- Category Exploitation: 1011 signals
- Format Arbitrage: 324 signals
- Ad-Based Tactics: 234 signals
- Velocity Mechanics: 153 signals
- Seasonal Strategies: 79 signals

## Tuning Parameters

Located in `exploration_layer.py`:

```python
DAMPENING_TOP_N_TACTICS = 5           # Number of top tactics to dampen
DAMPENING_TOP_N_HEURISTICS = 3        # Number of top heuristics to dampen
DAMPENING_TOP_N_PATTERNS = 2          # Number of top patterns to dampen

DAMPENING_CURVE_ALPHA = 2.0           # Sigmoid steepness (2.0 = moderate)
DAMPENING_PERCENTILE_THRESHOLD = 0.7  # Apply to top 30% (70th percentile+)
DAMPENING_MIN_FACTOR = 0.25           # Minimum dampening (25% of original)
```

### Tuning Guide

- **Increase `ALPHA`** → Steeper drop-off at the top (more aggressive dampening)
- **Decrease `PERCENTILE_THRESHOLD`** → Apply dampening to more items (wider scope)
- **Increase `DAMPENING_MIN_FACTOR`** → Don't reduce dominant items as much (more conservative)

### Validation

Test different parameters:

```bash
# Current tuning (balanced)
python3 policy.py --exploration-layer-2

# More aggressive dampening (stronger suppression of consensus)
# Edit: DAMPENING_CURVE_ALPHA = 3.0, DAMPENING_MIN_FACTOR = 0.15
python3 policy.py --exploration-layer-2

# Conservative dampening (preserve more of consensus)
# Edit: DAMPENING_CURVE_ALPHA = 1.5, DAMPENING_MIN_FACTOR = 0.40
python3 policy.py --exploration-layer-2
```

## Design Principles

### 1. No Destruction

The baseline scoring system is **completely preserved**. Layer 2 is purely additive:
- Baseline policy unchanged
- Exploration report is separate
- Flag is fully reversible

### 2. Statistical Validity

Dampening curve is mathematically sound:
- Sigmoid function (smooth, natural rolloff)
- Calibrated to realistic frequency distributions
- Percentile-based (scale-invariant)
- Tunable without code changes

### 3. Emergent Discovery

Secondary signals surface naturally:
- Not hand-curated
- Data-driven ranking change
- Mid-frequency signals preserved (3-15x range)
- Theme detection automated

### 4. Transparent

Every dampened item includes:
- Original support_count
- Dampening factor applied
- Dampened support_count
- Position shift in rankings

## Next Steps (Layer 3)

Layer 3 will detect contradictions:

```python
# Example contradiction:
# Heuristic A: "If median_reviews < 200 → BOOST"
# Heuristic B: "If median_reviews > 200 AND rising → BOOST"
# Tactic C: "Avoid books with >100 reviews"

# Layer 3 would flag these conflicts and surface
# the underlying strategic disagreements in the knowledge base.
```

## Testing

### Manual Validation

```bash
# 1. Generate baseline policy
python3 policy.py
cat config/scoring_policy.json | jq '.boost_rules[0:3]'

# 2. Generate with exploration
python3 policy.py --exploration-layer-2
cat config/exploration_layer_2_report.json | jq '.baseline.dominant_patterns'

# 3. Verify dampening was applied
cat config/exploration_layer_2_report.json | jq '.emergent.tactics.top_10_rising[0:3]'
```

### Comparison

Compare emergent_rising items between:
- What ranked #15-20 in baseline
- What ranked #5-10 in exploration
- Verify the "shift" value matches position change

## FAQ

**Q: Does this change my production policy?**
A: No. Layer 2 is opt-in via `--exploration-layer-2` flag. Baseline policy is untouched.

**Q: What if I don't like the dampening curve?**
A: Tune `DAMPENING_CURVE_ALPHA`, `DAMPENING_PERCENTILE_THRESHOLD`, and `DAMPENING_MIN_FACTOR`. See tuning guide above.

**Q: Can I apply these dampened weights directly?**
A: Not yet. Layer 2 is analysis-only. To use dampened weights in production, integrate the exploration report into your scoring pipeline (future work).

**Q: Why sigmoid instead of just dividing by support_count?**
A: Sigmoid provides smooth, natural rolloff without abrupt cliffs. It's calibrated for real frequency distributions and tunable without code changes.

**Q: What about very rare items (support_count = 1)?**
A: They're never dampened (stay below percentile threshold). Layer 2 focuses on the consensus problem, not rare signals.
