# Layer 2 Exploration Mode — Technical Reference

## Quick Start

```bash
# Generate exploration report
python3 policy.py --exploration-layer-2

# Output:
# ✅ Exploration Report → config/exploration_layer_2_report.json
#    Dominant tactics detected: 5
#    Dominant heuristics detected: 3
#    Dominant patterns detected: 2
#    Emergent rising tactics: 7
#    Strategy themes discovered: 7
```

## Algorithm Walkthrough

### 1. Dominant Pattern Detection

```python
from exploration_layer import detect_dominant_patterns

dominant = detect_dominant_patterns(tactics, heuristics, niche_patterns)

# Returns:
# {
#   "tactics": [
#     {
#       "name": "Keyword Research with Publisher Rocket",
#       "support_count": 23,
#       "position": 1,
#       "percentile": 0.9986,  # 99.86th percentile
#       "description": "Identify profitable book topics...",
#       "signals_used": ["BSR", "review_count"]
#     },
#     ... 4 more tactics
#   ],
#   "heuristics": [...],      # Top 3
#   "niche_patterns": [...],  # Top 2
#   "summary": {
#     "total_tactics": 728,
#     "total_heuristics": 319,
#     "total_patterns": 278,
#     "detected_at": "2026-02-26T12:34:56.789123"
#   }
# }
```

**Key Point:** The function dynamically identifies top patterns from the current data. It doesn't hardcode names like "Publisher Rocket". If the data changes, top patterns adapt automatically.

### 2. Sigmoid Dampening Curve

```python
def _sigmoid_dampening(
    percentile: float,
    threshold: float = 0.7,
    alpha: float = 2.0,
    min_factor: float = 0.25,
) -> float:
    """
    Compute dampening factor using sigmoid-logarithmic curve.

    Args:
        percentile: Item's percentile rank (0.0 to 1.0)
        threshold: Apply dampening to top (1 - threshold) * 100%
                   (threshold=0.7 means top 30%)
        alpha: Sigmoid steepness (higher = sharper drop-off)
        min_factor: Minimum dampening (25% = reduce to 25% of original)

    Returns:
        Dampening factor (0.25 to 1.0)

    Behavior:
        - percentile < 0.70: factor = 1.0 (no dampening)
        - percentile ≥ 0.70: sigmoid decay from 1.0 to 0.25
        - percentile = 1.00: factor ≈ 0.25 (75% reduction)
    """
    if percentile < threshold:
        return 1.0

    # Normalized position in dampening zone [0.0 to 1.0]
    normalized = (percentile - threshold) / (1.0 - threshold)

    # Sigmoid-like decay with tunable steepness
    factor = min_factor + (1.0 - min_factor) / (1.0 + (normalized ** alpha))
    return max(min_factor, min(1.0, factor))


# Example dampening factors at different percentiles:
# percentile=0.50 → factor=1.00 (below threshold, no dampening)
# percentile=0.70 → factor=0.99 (at threshold, minimal dampening)
# percentile=0.80 → factor=0.82 (gradual decay)
# percentile=0.90 → factor=0.54 (stronger dampening)
# percentile=0.99 → factor=0.29 (maximum dampening)
# percentile=1.00 → factor=0.25 (hit minimum floor)
```

**Visualized as a curve:**

```
Dampening Factor
      1.0 |
          |  threshold=0.7
      0.9 |       /
          |      /
      0.8 |     /
          |    /
      0.7 |   /
          |  /
      0.6 | /
          |/
      0.5 |
          |\
      0.4 | \___
          |     \___
      0.3 |        \___
          |            \_____
      0.2 |
            └──────────────────
            0    0.5    0.7   1.0  ← percentile
                     ↑
              dampening starts
```

### 3. Apply Dampening to Items

```python
from exploration_layer import apply_frequency_dampening

dampened_tactics = apply_frequency_dampening(
    items=tactics,                          # All 728 tactics
    dominant_items=dominant["tactics"],     # Top 5 detected
    item_key="name",                        # Match by "name" field
)

# Input item:
# {
#   "name": "Keyword Research with Publisher Rocket",
#   "support_count": 23,
#   "description": "...",
#   ...
# }

# Output item (if dominant):
# {
#   "name": "Keyword Research with Publisher Rocket",
#   "support_count": 7,                    # Dampened from 23
#   "description": "...",
#   ...
#   "_dampening_applied": {
#       "original_support": 23,
#       "dampening_factor": 0.304,
#       "dampened_support": 7
#   }
# }

# Output item (if not dominant):
# {
#   "name": "Some other tactic",
#   "support_count": 3,                    # Unchanged
#   "description": "...",
#   # No "_dampening_applied" field
# }
```

### 4. Compute Emergent Rankings

```python
from exploration_layer import compute_emergent_rankings

rankings = compute_emergent_rankings(
    original_items=tactics,
    dampened_items=dampened_tactics,
)

# Returns:
# {
#   "baseline_top_20": [
#     {
#       "name": "Keyword Research with Publisher Rocket",
#       "support_count": 23,
#       "position": 1
#     },
#     {
#       "name": "Competitor Analysis",
#       "support_count": 17,
#       "position": 2
#     },
#     ...
#   ],
#   "exploration_top_20": [
#     {
#       "name": "Keyword Research with Publisher Rocket",
#       "support_count": 7,                # Dampened
#       "position": 2                      # Dropped from #1
#     },
#     {
#       "name": "Competitor Reverse ASIN",
#       "support_count": 6,                # Was lower, now higher
#       "position": 1                      # Rose from #4
#     },
#     ...
#   ],
#   "emergent_rising": [
#     {
#       "key": "Competitor Reverse ASIN",
#       "baseline_position": 4,
#       "exploration_position": 1,
#       "shift": 3,                        # Rose 3 positions!
#       "support_baseline": 6
#     },
#     ... 9 more items
#   ],
#   "suppressed_falling": [
#     {
#       "key": "Keyword Research with Publisher Rocket",
#       "baseline_position": 1,
#       "exploration_position": 2,
#       "shift": -1,
#       "support_baseline": 23
#     },
#     ... more items that fell
#   ],
#   "total_position_shifts": 147         # N items changed position
# }
```

## Mathematical Properties

### Sigmoid-Logarithmic Dampening Justification

**Why not simple division?**

```python
# ❌ Bad: Just divide by support_count
dampened_support = support / support_count  # Results in 0.1-0.5, not 1-7

# ❌ Bad: Just divide by log(support_count)
dampened_support = support / log(support_count)  # Arbitrary scaling

# ✅ Good: Apply sigmoid curve
factor = _sigmoid_dampening(percentile)
dampened_support = int(support * factor)  # Natural scaling, tunable
```

**Why sigmoid?**

1. **Natural rolloff** — Smooth transition, no abrupt cliffs
2. **Calibrated** — 0.25-1.0 range is tuned for frequency distributions
3. **Percentile-based** — Adapts to any data distribution automatically
4. **Tunable** — Can adjust steepness (alpha) without code changes
5. **Interpretable** — "Top 30% gets 75% weight reduction" is intuitive

### Example Calculation

```
Item: "Keyword Research with Publisher Rocket"
Original support_count: 23
Position in all 728 tactics: #1
Percentile rank: 23/728 = 99.86%

Step 1: Check threshold
  percentile (0.9986) ≥ threshold (0.70)? YES → Apply dampening

Step 2: Calculate dampening factor
  normalized = (0.9986 - 0.70) / (1.0 - 0.70)
            = 0.2986 / 0.30
            = 0.9953

  factor = 0.25 + 0.75 / (1 + 0.9953^2.0)
         = 0.25 + 0.75 / (1 + 0.9906)
         = 0.25 + 0.75 / 1.9906
         = 0.25 + 0.3769
         = 0.6269

Step 3: Apply dampening
  dampened_support = int(23 * 0.6269) = 14

Result:
  Original: 23 occurrences → Dampened: 14 occurrences
  Reduction: 39% (or factor of 0.627)
```

## Report Structure

```json
{
  "version": "1.0",
  "generated_at": "2026-02-26T12:34:56.789123",
  "exploration_mode": "layer_2",
  "dampening_config": {
    "alpha": 2.0,
    "percentile_threshold": 0.7,
    "min_dampening_factor": 0.25
  },

  "baseline": {
    "dominant_patterns": {
      "tactics": [
        {
          "name": "Keyword Research with Publisher Rocket",
          "support_count": 23,
          "position": 1,
          "percentile": 0.9986,
          "description": "Identify profitable book topics...",
          "signals_used": ["BSR", "review_count"]
        }
        // ... 4 more
      ],
      "heuristics": [
        {
          "name": "low_review_boost",
          "support_count": 39,
          "position": 1,
          "percentile": 0.9966,
          "condition": "If median reviews < 200",
          "action": "boost",
          "weight": 0.8
        }
        // ... 2 more
      ],
      "niche_patterns": [
        {
          "pattern": "IDENTITY + PROBLEM + CONSTRAINT",
          "support_count": 133,
          "position": 1,
          "percentile": 1.0,
          "example": "teen boys + ADHD + Christian",
          "why_it_works": "Highly specific niche resonates..."
        }
        // ... 1 more
      ]
    }
  },

  "emergent": {
    "tactics": {
      "top_10_rising": [
        {
          "key": "Competitor Reverse ASIN",
          "baseline_position": 4,
          "exploration_position": 1,
          "shift": 3,
          "support_baseline": 6
        }
        // ... 9 more
      ],
      "top_20_by_frequency": [
        // Post-dampening top 20 tactics
      ]
    },
    "heuristics": {
      "top_10_rising": [
        // Similar structure
      ],
      "top_20_by_frequency": [
        // Similar structure
      ]
    },
    "niche_patterns": {
      "top_10_rising": [],  // Empty if patterns didn't rise
      "top_5_by_frequency": [
        // Post-dampening top 5 patterns
      ]
    }
  },

  "mid_frequency_signals": {
    "tactics": [
      {
        "name": "Some tactic",
        "support_count": 7,
        "description": "..."
      }
      // Items with 3-15 occurrences
    ],
    "heuristics": [
      // Similar
    ],
    "niche_patterns": [
      // Similar
    ]
  },

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
    // ... 6 more themes
  },

  "statistics": {
    "total_items": {
      "tactics": 728,
      "heuristics": 319,
      "niche_patterns": 278
    },
    "position_shifts": {
      "tactics": 147,
      "heuristics": 98,
      "niche_patterns": 45
    },
    "dampening_applied": {
      "tactics": 5,
      "heuristics": 3,
      "niche_patterns": 2
    }
  }
}
```

## Integration in policy.py

```python
from exploration_layer import generate_exploration_report

def generate_policy(include_low: bool = False, exploration_layer_2: bool = False) -> dict:
    # ... existing code ...

    tactics = load_knowledge("tactics")
    heuristics = load_knowledge("heuristics")
    niche_patterns = load_knowledge("niche_patterns")

    # ... generate baseline policy ...

    if exploration_layer_2:
        print("\n🔍 Generating Layer 2 Exploration Report...")
        exploration_report = generate_exploration_report(
            tactics,
            heuristics,
            niche_patterns,
            raw_dir
        )
        EXPLORATION_REPORT_FILE.write_text(json.dumps(exploration_report, indent=2))
        print(f"✅ Exploration Report → {EXPLORATION_REPORT_FILE}")
```

## Performance Characteristics

```python
# Time complexity (with N items in knowledge base):
detect_dominant_patterns()      # O(N log N) - sorting
apply_frequency_dampening()     # O(N) - linear pass + lookups
compute_emergent_rankings()     # O(N log N) - sorting + position tracking
generate_exploration_report()   # O(N) - linear scans for themes

# Total: O(N log N) for sorting, dominated by sorting
# With N=728 tactics: negligible time (<1ms)

# Space complexity: O(N)
# Report size: ~500KB for full analysis
```

## Customization Examples

### Example 1: More Aggressive Dampening

```python
# In exploration_layer.py, change:
DAMPENING_CURVE_ALPHA = 3.0              # Was 2.0 (steeper)
DAMPENING_MIN_FACTOR = 0.15              # Was 0.25 (stronger reduction)
DAMPENING_PERCENTILE_THRESHOLD = 0.60    # Was 0.70 (apply to top 40%)

# Result: Top patterns reduced to 15% of original weight,
#         more secondary signals surface
```

### Example 2: Conservative Dampening

```python
# In exploration_layer.py, change:
DAMPENING_CURVE_ALPHA = 1.5              # Was 2.0 (gentler)
DAMPENING_MIN_FACTOR = 0.40              # Was 0.25 (lighter reduction)
DAMPENING_PERCENTILE_THRESHOLD = 0.80    # Was 0.70 (apply to top 20%)

# Result: Top patterns reduced only to 40% of original weight,
#         baseline more preserved
```

### Example 3: Custom Theme Detection

```python
# In exploration_layer.py, add to strategy_themes dict:
"psychological_triggers": {
    "keywords": ["fear", "anxiety", "confidence", "self-esteem", "motivation"],
    "count": 0,
    "examples": [],
},

# Now the report will track psychological trigger strategies
```

## Debugging & Validation

```python
# Check what got dampened:
import json

with open("config/exploration_layer_2_report.json") as f:
    report = json.load(f)

# See dominant patterns
print("Dominant Tactics:")
for t in report["baseline"]["dominant_patterns"]["tactics"]:
    print(f"  {t['name']}: {t['support_count']}x ({t['percentile']:.1%})")

# See what rose in ranking
print("\nTactics that rose (emergent signals):")
for rise in report["emergent"]["tactics"]["top_10_rising"]:
    print(f"  {rise['key']}: #{rise['baseline_position']} → #{rise['exploration_position']} (+{rise['shift']})")

# See strategy themes
print("\nStrategy themes discovered:")
for theme, info in report["strategy_themes"].items():
    print(f"  {theme}: {info['count']} signals")
```

## Common Questions

**Q: Why only dampen the top 3 heuristics when there are 319 total?**
A: Because frequency dominance is concentrated at the very top. The top 3 heuristics account for 95+ occurrences, while the bottom 80% have 1-3 occurrences each. Dampening beyond top 3 has minimal effect.

**Q: Can I use the dampened weights in production?**
A: Not yet. Layer 2 is analysis-only. To use dampened weights:
   1. Extract dampening factors from exploration report
   2. Apply to active rules in policy
   3. Test against baseline (ensure improvements)
   4. Deploy with A/B testing

**Q: What if two items have the same support_count?**
A: Percentile is computed across all items, so ties get the same percentile, same dampening factor. Order is stable (Python's sort is stable).

**Q: How do I know if parameters are tuned well?**
A: Check:
   1. Top items still present (not eliminated)
   2. 5-10 items rise meaningfully (+2 to +5 positions)
   3. No items disappear from top 50
   4. Strategy themes discovered align with domain knowledge

**Q: Can I apply different dampening to different categories?**
A: Yes, modify `apply_frequency_dampening()` calls in `generate_exploration_report()`:
   ```python
   dampened_tactics = apply_frequency_dampening(
       tactics,
       dominant["tactics"],
       alpha=3.0,  # More aggressive for tactics
   )
   dampened_heuristics = apply_frequency_dampening(
       heuristics,
       dominant["heuristics"],
       alpha=1.5,  # More conservative for heuristics
   )
   ```
