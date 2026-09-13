"""
build_frontier_board.py — Frontier analysis board from extracted automation corpus.
===============================================================================

Purpose:
Turn extracted YouTube automation findings into a frontier-oriented literature
review. Instead of counting broad categories, this script clusters workflow
shapes, scores them for novelty + plausibility, and emits:

1. data/frontier_items.json   — normalized analysis-ready records
2. data/frontier_board.json   — scored clusters and board sections
3. data/frontier_board.md     — human-readable clustered board

This is intentionally separate from build_taxonomy.py:
- taxonomy = breadth / popularity / "how many videos mention X"
- frontier = novelty / plausibility / "what unexpected automations are out there"
"""

from __future__ import annotations

import collections
import glob
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent
EXTRACTED = ROOT / "data" / "youtube" / "extracted"
OUT_ITEMS = ROOT / "data" / "frontier_items.json"
OUT_BOARD = ROOT / "data" / "frontier_board.json"
OUT_MD = ROOT / "data" / "frontier_board.md"

SECTIONS = [
    "Operator Patterns",
    "Emerging Cross-Vertical Motifs",
    "Weird but Plausible",
    "Guru Frontier",
    "Expected / Saturated",
]

GENERIC_WORKFLOW_TERMS = {
    "agent", "agents", "ai", "automation", "automations", "automate",
    "business", "businesses", "workflow", "workflows", "system", "systems",
    "tool", "tools", "process", "processes", "solution", "solutions",
    "using", "build", "built", "create", "created", "guide", "tutorial",
    "course", "complete", "best", "free", "new", "simple", "smart", "easy",
    "customer", "client", "users", "people", "company", "companies",
}

GENERIC_VERTICAL_TERMS = {
    "small business", "small businesses", "small business owner",
    "small-business", "small-business owner", "general business",
    "online business", "local businesses", "service provider",
    "service-based business", "business owner", "small business owners",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "them", "this", "to", "with", "your", "you", "via", "after", "before",
    "when", "then", "than", "over", "under", "through",
}

TOOL_ALIASES = {
    "make": "make",
    "make.com": "make",
    "n8n": "n8n",
    "zapier": "zapier",
    "hubspot": "hubspot",
    "gohighlevel": "gohighlevel",
    "go high level": "gohighlevel",
    "highlevel": "gohighlevel",
    "airtable": "airtable",
    "notion": "notion",
    "slack": "slack",
    "gmail": "gmail",
    "google sheets": "google sheets",
    "sheets": "google sheets",
    "google calendar": "google calendar",
    "calendar": "google calendar",
    "openai": "openai",
    "chatgpt": "chatgpt",
    "claude": "claude",
    "whatsapp": "whatsapp",
    "twilio": "twilio",
    "voiceflow": "voiceflow",
    "elevenlabs": "elevenlabs",
    "stripe": "stripe",
    "quickbooks": "quickbooks",
    "shopify": "shopify",
    "salesforce": "salesforce",
    "google docs": "google docs",
    "google drive": "google drive",
}


def clean_json_file(path: str) -> dict | None:
    txt = Path(path).read_text(encoding="utf-8", errors="ignore").split("\x00")[0]
    end = txt.rfind("}")
    if end != -1:
        txt = txt[: end + 1]
    try:
        return json.loads(txt)
    except Exception:
        match = re.search(r"\{.*\}", txt, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except Exception:
            return None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tokenize(text: str) -> list[str]:
    return [
        tok for tok in re.findall(r"[a-z0-9][a-z0-9\-\+\.]{1,}", text.lower())
        if tok not in STOPWORDS and len(tok) > 2
    ]


def normalize_spaces(text: object) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return re.sub(r"\s+", " ", text.strip())


def canonical_vertical(value: str) -> str:
    text = normalize_spaces(value.lower())
    if not text:
        return "unknown"
    replacements = {
        "small-business": "small business",
        "small business owner": "small business",
        "small businesses": "small business",
        "local home service business": "home services",
        "service-based business": "service business",
        "service provider": "service business",
        "consulting services": "consulting",
        "consulting company": "consulting",
        "youtuber and entrepreneur": "content creator",
        "youtube content creator": "content creator",
        "content creation": "content creator",
    }
    return replacements.get(text, text)


def infer_vertical_from_pattern(pattern_template: str) -> str:
    if not pattern_template:
        return "unknown"
    left = re.split(r"\+|→|->|/|\|", pattern_template, maxsplit=1)[0]
    return canonical_vertical(left)


def normalize_tool_name(tool: str) -> str:
    value = normalize_spaces(tool.lower())
    return TOOL_ALIASES.get(value, value)


def tool_lexicon_from_extracts(paths: list[str]) -> set[str]:
    tools = set(TOOL_ALIASES)
    for path in paths:
        data = clean_json_file(path)
        if not data:
            continue
        for tactic in data.get("tactics", []):
            for tool in tactic.get("tools_mentioned", []) or []:
                cleaned = normalize_tool_name(tool)
                if cleaned:
                    tools.add(cleaned)
    return tools


def infer_tools(text_parts: list[str], known_tools: set[str]) -> list[str]:
    blob = " ".join(part for part in text_parts if part).lower()
    hits = []
    for tool in known_tools:
        pattern = r"\b" + re.escape(tool) + r"\b"
        if re.search(pattern, blob):
            hits.append(tool)
    return sorted(set(hits))


def evidence_class(first_party: object) -> str:
    if first_party is True or str(first_party).lower() == "true":
        return "operator"
    if first_party is False or str(first_party).lower() == "false":
        return "guru"
    return "unknown"


def is_generic_vertical(vertical: str) -> bool:
    return vertical in GENERIC_VERTICAL_TERMS or vertical == "unknown"


def workflow_text(item: dict) -> str:
    parts = [
        item.get("name", ""),
        item.get("trigger", ""),
        item.get("description", ""),
        item.get("result_claim", ""),
        item.get("pattern_template", ""),
        item.get("why_it_works", ""),
    ]
    return normalize_spaces(" ".join(part for part in parts if part))


def informative_terms(text: str, limit: int = 6) -> list[str]:
    tokens = tokenize(text)
    chosen = []
    for tok in tokens:
        if tok in GENERIC_WORKFLOW_TERMS:
            continue
        if tok not in chosen:
            chosen.append(tok)
        if len(chosen) >= limit:
            break
    return chosen


def make_signature(parts: list[str], fallback: str) -> str:
    cleaned = [part for part in parts if part and part != "unknown"]
    return " | ".join(cleaned[:3]) if cleaned else fallback


def quote_coverage(source_quotes: list[str]) -> float:
    if not source_quotes:
        return 0.0
    useful = sum(1 for q in source_quotes if len(q.split()) >= 8)
    return min(useful / 2.0, 1.0)


def specificity_score(item: dict) -> float:
    terms = informative_terms(workflow_text(item), limit=8)
    tools = item.get("tools_mentioned", []) or []
    trigger = item.get("trigger", "")
    result_claim = item.get("result_claim", "")
    vertical = item.get("vertical_signature", "unknown")

    score = 0.0
    score += min(len(terms) / 6.0, 1.0) * 0.30
    score += min(len(tools) / 3.0, 1.0) * 0.20
    score += (1.0 if trigger else 0.0) * 0.15
    score += (1.0 if result_claim else 0.0) * 0.10
    score += quote_coverage(item.get("source_quotes", [])) * 0.10
    score += (0.0 if is_generic_vertical(vertical) else 1.0) * 0.15
    return round(min(score, 1.0), 4)


def genericity_score(item: dict) -> float:
    workflow_terms = informative_terms(workflow_text(item), limit=8)
    if not workflow_terms:
        return 1.0
    generic_hits = sum(1 for term in workflow_terms if term in GENERIC_WORKFLOW_TERMS)
    vertical_penalty = 0.35 if is_generic_vertical(item.get("vertical_signature", "unknown")) else 0.0
    ratio = generic_hits / max(len(workflow_terms), 1)
    return round(min(1.0, ratio + vertical_penalty), 4)


def weighted_tokens(item: dict) -> list[str]:
    tokens: list[str] = []
    field_weights = {
        "name": 3,
        "pattern_template": 4,
        "trigger": 3,
        "description": 2,
        "result_claim": 1,
        "why_it_works": 1,
        "business_type": 1,
    }
    for field_name, weight in field_weights.items():
        for token in tokenize(str(item.get(field_name, ""))):
            if token not in STOPWORDS:
                tokens.extend([token] * weight)
    for tool in item.get("tools_mentioned", []) or []:
        tool_token = tool.replace(" ", "_")
        tokens.extend([tool_token] * 4)
    return tokens


def build_idf(items: list[dict]) -> dict[str, float]:
    doc_counts = collections.Counter()
    total = len(items)
    for item in items:
        for token in set(weighted_tokens(item)):
            doc_counts[token] += 1
    return {
        token: math.log((total + 1) / (count + 1)) + 1.0
        for token, count in doc_counts.items()
    }


def tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = collections.Counter(tokens)
    total = sum(counts.values()) or 1
    return {token: (count / total) * idf.get(token, 1.0) for token, count in counts.items()}


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    dot = sum(left[k] * right[k] for k in shared)
    left_mag = math.sqrt(sum(v * v for v in left.values()))
    right_mag = math.sqrt(sum(v * v for v in right.values()))
    if left_mag == 0 or right_mag == 0:
        return 0.0
    return dot / (left_mag * right_mag)


@dataclass
class Cluster:
    id: str
    items: list[dict] = field(default_factory=list)
    centroid_sum: collections.Counter = field(default_factory=collections.Counter)
    count: int = 0

    def add(self, item: dict):
        self.items.append(item)
        self.count += 1
        self.centroid_sum.update(item["_vector"])

    def centroid(self) -> dict[str, float]:
        if self.count == 0:
            return {}
        return {token: value / self.count for token, value in self.centroid_sum.items()}


def load_frontier_items() -> tuple[list[dict], dict[str, int]]:
    paths = sorted(glob.glob(str(EXTRACTED / "*.json")))
    known_tools = tool_lexicon_from_extracts(paths)
    model_counts = collections.Counter()
    items: list[dict] = []

    for path in paths:
        data = clean_json_file(path)
        if not data:
            continue

        video_id = data.get("video_id", "")
        title = data.get("title", "")
        model_used = data.get("model_used", "unknown")
        model_counts[model_used] += 1

        for tactic in data.get("tactics", []):
            normalized_tools = sorted({
                normalize_tool_name(tool)
                for tool in (tactic.get("tools_mentioned") or [])
                if normalize_tool_name(tool)
            })
            vertical = canonical_vertical(tactic.get("business_type", ""))
            terms = informative_terms(workflow_text(tactic))
            item = {
                "source_type": "tactic",
                "video_id": video_id,
                "title": title,
                "model_used": model_used,
                "name": normalize_spaces(tactic.get("name", "")),
                "first_party": tactic.get("first_party"),
                "business_type": tactic.get("business_type", ""),
                "tools_mentioned": normalized_tools,
                "trigger": normalize_spaces(tactic.get("trigger", "")),
                "description": normalize_spaces(tactic.get("description", "")),
                "result_claim": normalize_spaces(tactic.get("result_claim", "")),
                "source_quotes": [normalize_spaces(q) for q in (tactic.get("source_quotes") or []) if q.strip()],
                "pattern_template": "",
                "why_it_works": "",
                "evidence_class": evidence_class(tactic.get("first_party")),
                "vertical_signature": vertical,
                "tool_signature": ", ".join(normalized_tools[:3]) if normalized_tools else "no named tools",
                "workflow_signature": make_signature(
                    [
                        " / ".join(terms[:4]) if terms else "",
                        vertical if not is_generic_vertical(vertical) else "",
                        ", ".join(normalized_tools[:2]),
                    ],
                    fallback=tactic.get("name", "").strip() or "unnamed workflow",
                ),
            }
            item["specificity_score"] = specificity_score(item)
            item["genericity_score"] = genericity_score(item)
            item["_tokens"] = weighted_tokens(item)
            items.append(item)

        for pattern in data.get("niche_patterns", []):
            pattern_template = normalize_spaces(pattern.get("pattern_template", ""))
            vertical = infer_vertical_from_pattern(pattern_template)
            normalized_tools = infer_tools(
                [pattern_template, pattern.get("description", ""), pattern.get("why_it_works", "")],
                known_tools,
            )
            terms = informative_terms(
                " ".join([pattern_template, pattern.get("description", ""), pattern.get("why_it_works", "")])
            )
            item = {
                "source_type": "niche_pattern",
                "video_id": video_id,
                "title": title,
                "model_used": model_used,
                "name": "",
                "first_party": None,
                "business_type": vertical if vertical != "unknown" else "",
                "tools_mentioned": normalized_tools,
                "trigger": "",
                "description": normalize_spaces(pattern.get("description", "")),
                "result_claim": "",
                "source_quotes": [normalize_spaces(q) for q in (pattern.get("source_quotes") or []) if q.strip()],
                "pattern_template": pattern_template,
                "why_it_works": normalize_spaces(pattern.get("why_it_works", "")),
                "evidence_class": "unknown",
                "vertical_signature": vertical,
                "tool_signature": ", ".join(normalized_tools[:3]) if normalized_tools else "no named tools",
                "workflow_signature": make_signature(
                    [
                        " / ".join(terms[:4]) if terms else "",
                        vertical if not is_generic_vertical(vertical) else "",
                        ", ".join(normalized_tools[:2]),
                    ],
                    fallback=pattern_template or "unnamed pattern",
                ),
            }
            item["specificity_score"] = specificity_score(item)
            item["genericity_score"] = genericity_score(item)
            item["_tokens"] = weighted_tokens(item)
            items.append(item)

    return items, dict(model_counts)


def attach_vectors(items: list[dict]) -> None:
    idf = build_idf(items)
    for item in items:
        item["_vector"] = tfidf_vector(item["_tokens"], idf)


def cluster_items(items: list[dict], threshold: float = 0.50) -> list[Cluster]:
    sorted_items = sorted(
        items,
        key=lambda item: (
            item["source_type"] == "tactic",
            item["specificity_score"],
            -item["genericity_score"],
        ),
        reverse=True,
    )

    clusters: list[Cluster] = []
    for idx, item in enumerate(sorted_items, 1):
        best_cluster = None
        best_score = 0.0
        for cluster in clusters:
            score = cosine_similarity(item["_vector"], cluster.centroid())
            if score > best_score:
                best_score = score
                best_cluster = cluster
        if best_cluster and best_score >= threshold:
            best_cluster.add(item)
        else:
            cluster = Cluster(id=f"cluster-{idx:04d}")
            cluster.add(item)
            clusters.append(cluster)
    return clusters


def cluster_name(cluster: Cluster) -> str:
    named = [item["name"] for item in cluster.items if item.get("name")]
    if named:
        top = collections.Counter(named).most_common(1)[0][0]
        return top[:80]
    templates = [item["pattern_template"] for item in cluster.items if item.get("pattern_template")]
    if templates:
        top = collections.Counter(templates).most_common(1)[0][0]
        return top[:80]
    terms = collections.Counter()
    for item in cluster.items:
        for term in informative_terms(workflow_text(item), limit=6):
            terms[term] += 1
    top_terms = [term for term, _ in terms.most_common(4)]
    return " / ".join(top_terms)[:80] if top_terms else cluster.id


def cluster_combo_key(item: dict) -> str:
    return " :: ".join([
        item.get("vertical_signature", "unknown"),
        item.get("tool_signature", "no named tools"),
        item.get("workflow_signature", "unknown"),
    ])


def score_items(items: list[dict]) -> None:
    combo_counts = collections.Counter(cluster_combo_key(item) for item in items)
    workflow_counts = collections.Counter(item.get("workflow_signature", "") for item in items)

    for item in items:
        combo_count = combo_counts[cluster_combo_key(item)]
        workflow_count = workflow_counts[item.get("workflow_signature", "")]
        novelty = 1.0 / math.sqrt(combo_count)
        support_bonus = min(workflow_count / 4.0, 1.0)
        evidence_strength = {
            "operator": 1.0,
            "unknown": 0.65,
            "guru": 0.35,
        }[item["evidence_class"]]
        item["novelty_score"] = round(novelty, 4)
        item["item_frontier_score"] = round(
            min(
                1.0,
                (novelty * 0.45)
                + (item["specificity_score"] * 0.25)
                + (evidence_strength * 0.20)
                + (support_bonus * 0.10)
                - (item["genericity_score"] * 0.15),
            ),
            4,
        )


def why_interesting(cluster_payload: dict) -> str:
    verticals = cluster_payload["business_types"]
    tools = cluster_payload["tools"]
    section = cluster_payload["cluster_type"]
    support = cluster_payload["support_videos"]

    if section == "Emerging Cross-Vertical Motifs":
        return (
            f"Shows the same workflow shape across {len(verticals)} business contexts"
            f"{' using ' + ', '.join(tools[:2]) if tools else ''}."
        )
    if section == "Operator Patterns":
        return (
            f"Operator-backed pattern with {support} supporting videos and concrete implementation detail."
        )
    if section == "Guru Frontier":
        return (
            "Expands the search space beyond expected workflows, but evidence is mostly guru or speculative."
        )
    if section == "Expected / Saturated":
        return (
            "Widely repeated pattern that looks like a baseline market behavior rather than a frontier edge."
        )
    return (
        "Specific, lower-frequency workflow with enough tools, triggers, or quotes to be worth investigating."
    )


def build_cluster_payload(cluster: Cluster) -> dict:
    items = cluster.items
    source_counts = collections.Counter(item["source_type"] for item in items)
    evidence_counts = collections.Counter(item["evidence_class"] for item in items)
    video_ids = []
    titles = {}
    for item in items:
        if item["video_id"] not in titles:
            titles[item["video_id"]] = item["title"]
        video_ids.append(item["video_id"])

    support_videos = len(set(video_ids))
    verticals = sorted({
        item["vertical_signature"]
        for item in items
        if item["vertical_signature"] != "unknown"
    })
    tools = [
        tool for tool, _ in collections.Counter(
            tool for item in items for tool in item.get("tools_mentioned", [])
        ).most_common(8)
    ]
    quotes = []
    for item in sorted(items, key=lambda row: row["item_frontier_score"], reverse=True):
        for quote in item.get("source_quotes", []):
            if quote and quote not in quotes:
                quotes.append(quote)
            if len(quotes) >= 2:
                break
        if len(quotes) >= 2:
            break

    representative_items = sorted(items, key=lambda row: row["item_frontier_score"], reverse=True)
    representative_examples = [
        {
            "video_id": item["video_id"],
            "title": item["title"],
            "source_type": item["source_type"],
            "name": item.get("name") or item.get("pattern_template") or item["workflow_signature"],
            "evidence_class": item["evidence_class"],
        }
        for item in representative_items[:5]
    ]

    operator_ratio = evidence_counts["operator"] / max(len(items), 1)
    guru_ratio = evidence_counts["guru"] / max(len(items), 1)
    unknown_ratio = evidence_counts["unknown"] / max(len(items), 1)
    avg_specificity = sum(item["specificity_score"] for item in items) / max(len(items), 1)
    avg_genericity = sum(item["genericity_score"] for item in items) / max(len(items), 1)
    avg_novelty = sum(item["novelty_score"] for item in items) / max(len(items), 1)
    distinct_verticals = len(verticals)
    cross_vertical = min(max(distinct_verticals - 1, 0) / 3.0, 1.0)
    quote_strength = sum(quote_coverage(item["source_quotes"]) for item in items) / max(len(items), 1)
    pattern_support = min(support_videos / 5.0, 1.0)
    evidence_strength = min(1.0, (operator_ratio * 1.0) + (unknown_ratio * 0.55) + (guru_ratio * 0.25) + (quote_strength * 0.25))
    guru_delta = 1.0 if operator_ratio >= 0.35 else (0.55 if guru_ratio >= 0.5 else 0.75)

    frontier_score = (
        (avg_novelty * 0.28)
        + (cross_vertical * 0.18)
        + (evidence_strength * 0.20)
        + (avg_specificity * 0.18)
        + (pattern_support * 0.10)
        + (guru_delta * 0.06)
        - (avg_genericity * 0.12)
    )
    frontier_score = round(max(0.0, min(1.0, frontier_score)) * 100, 1)

    payload = {
        "cluster_id": cluster.id,
        "cluster_name": cluster_name(cluster),
        "cluster_type": "",
        "frontier_score": frontier_score,
        "evidence_class": (
            "operator" if operator_ratio >= 0.5 else
            "guru" if guru_ratio >= 0.5 else
            "mixed" if operator_ratio > 0 and guru_ratio > 0 else
            "unknown"
        ),
        "support_videos": support_videos,
        "support_items": len(items),
        "business_types": verticals[:10],
        "tools": tools,
        "workflow_signature": representative_items[0]["workflow_signature"],
        "representative_examples": representative_examples,
        "quotes": quotes[:2],
        "source_type_mix": dict(source_counts),
        "evidence_mix": dict(evidence_counts),
        "operator_ratio": round(operator_ratio, 3),
        "guru_ratio": round(guru_ratio, 3),
        "avg_specificity": round(avg_specificity, 3),
        "avg_genericity": round(avg_genericity, 3),
        "cross_vertical_score": round(cross_vertical, 3),
        "why_interesting": "",
    }
    return payload


def choose_anchor(candidates: list[dict], predicate, used: set[str]) -> dict | None:
    for cluster in candidates:
        if cluster["cluster_id"] in used:
            continue
        if predicate(cluster):
            used.add(cluster["cluster_id"])
            return cluster
    return None


def assign_sections(clusters: list[dict]) -> dict[str, list[dict]]:
    ordered = sorted(clusters, key=lambda row: (row["frontier_score"], row["support_videos"]), reverse=True)
    used: set[str] = set()
    sections: dict[str, list[dict]] = {name: [] for name in SECTIONS}

    anchors = {
        "Operator Patterns": choose_anchor(
            ordered,
            lambda c: c["operator_ratio"] >= 0.45 and c["support_videos"] >= 2 and c["avg_specificity"] >= 0.45,
            used,
        ),
        "Emerging Cross-Vertical Motifs": choose_anchor(
            ordered,
            lambda c: c["support_videos"] >= 2 and c["cross_vertical_score"] >= 0.66 and len(c["business_types"]) >= 3,
            used,
        ),
        "Weird but Plausible": choose_anchor(
            ordered,
            lambda c: c["frontier_score"] >= 45 and c["support_videos"] <= 3 and c["avg_specificity"] >= 0.55,
            used,
        ),
        "Guru Frontier": choose_anchor(
            ordered,
            lambda c: c["guru_ratio"] >= 0.5 and c["operator_ratio"] < 0.3,
            used,
        ),
        "Expected / Saturated": choose_anchor(
            ordered,
            lambda c: c["support_videos"] >= 3 and (c["support_items"] >= 5 or c["avg_genericity"] >= 0.15),
            used,
        ),
    }
    for section, cluster in anchors.items():
        if cluster:
            cluster["cluster_type"] = section
            sections[section].append(cluster)

    for cluster in ordered:
        if cluster["cluster_id"] in used:
            continue
        if cluster["support_videos"] >= 3 and (cluster["support_items"] >= 5 or cluster["avg_genericity"] >= 0.15):
            target = "Expected / Saturated"
        elif cluster["guru_ratio"] >= 0.5 and cluster["operator_ratio"] < 0.3:
            target = "Guru Frontier"
        elif cluster["support_videos"] >= 2 and cluster["cross_vertical_score"] >= 0.66 and len(cluster["business_types"]) >= 3:
            target = "Emerging Cross-Vertical Motifs"
        elif cluster["operator_ratio"] >= 0.45 and cluster["support_videos"] >= 2:
            target = "Operator Patterns"
        else:
            target = "Weird but Plausible"
        cluster["cluster_type"] = target
        cluster["why_interesting"] = why_interesting(cluster)
        sections[target].append(cluster)
        used.add(cluster["cluster_id"])

    for section_name in SECTIONS:
        for cluster in sections[section_name]:
            if not cluster["why_interesting"]:
                cluster["why_interesting"] = why_interesting(cluster)
        sections[section_name].sort(
            key=lambda row: (row["frontier_score"], row["support_videos"], row["avg_specificity"]),
            reverse=True,
        )
    return sections


def board_markdown(metadata: dict, sections: dict[str, list[dict]]) -> str:
    lines = [
        "# Frontier Analysis Board",
        "",
        f"- Generated: `{metadata['generated_at']}`",
        f"- Extracted videos analyzed: `{metadata['video_count']}`",
        f"- Frontier items analyzed: `{metadata['item_count']}`",
        f"- Models included: `{', '.join(f'{k} ({v})' for k, v in metadata['model_counts'].items())}`",
        "",
        "This board emphasizes novel, viable automation patterns over simple popularity counts.",
        "",
    ]

    for section_name in SECTIONS:
        lines.append(f"## {section_name}")
        lines.append("")
        if not sections[section_name]:
            lines.append("_No clusters matched this section._")
            lines.append("")
            continue
        for cluster in sections[section_name][:8]:
            lines.append(f"### {cluster['cluster_name']}")
            lines.append("")
            lines.append(
                f"- Frontier score: `{cluster['frontier_score']}`"
                f" | Evidence: `{cluster['evidence_class']}`"
                f" | Support videos: `{cluster['support_videos']}`"
                f" | Support items: `{cluster['support_items']}`"
            )
            if cluster["business_types"]:
                lines.append(f"- Business types: {', '.join(cluster['business_types'][:6])}")
            if cluster["tools"]:
                lines.append(f"- Tools: {', '.join(cluster['tools'][:6])}")
            lines.append(f"- Why it is interesting: {cluster['why_interesting']}")
            if cluster["quotes"]:
                for quote in cluster["quotes"][:2]:
                    lines.append(f"> {quote}")
            if cluster["representative_examples"]:
                lines.append("- Example sources:")
                for example in cluster["representative_examples"][:5]:
                    label = example["name"] or example["title"]
                    lines.append(
                        f"  - `{example['evidence_class']}` | {label[:90]} | {example['title'][:90]}"
                    )
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def strip_private_fields(items: list[dict]) -> list[dict]:
    cleaned = []
    for item in items:
        public = {k: v for k, v in item.items() if not k.startswith("_")}
        cleaned.append(public)
    return cleaned


def main():
    items, model_counts = load_frontier_items()
    if not items:
        raise SystemExit("No frontier items found in data/youtube/extracted.")

    attach_vectors(items)
    score_items(items)
    clusters = cluster_items(items)
    cluster_payloads = [build_cluster_payload(cluster) for cluster in clusters]
    sections = assign_sections(cluster_payloads)

    metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "video_count": len({item["video_id"] for item in items}),
        "item_count": len(items),
        "cluster_count": len(cluster_payloads),
        "model_counts": model_counts,
        "source_type_counts": dict(collections.Counter(item["source_type"] for item in items)),
    }

    OUT_ITEMS.write_text(json.dumps({
        "metadata": metadata,
        "items": strip_private_fields(items),
    }, indent=2), encoding="utf-8")

    board_payload = {
        "metadata": metadata,
        "sections": sections,
        "clusters": sorted(cluster_payloads, key=lambda row: row["frontier_score"], reverse=True),
    }
    OUT_BOARD.write_text(json.dumps(board_payload, indent=2), encoding="utf-8")
    OUT_MD.write_text(board_markdown(metadata, sections), encoding="utf-8")

    print(f"Wrote {OUT_ITEMS}")
    print(f"Wrote {OUT_BOARD}")
    print(f"Wrote {OUT_MD}")
    print(f"Videos: {metadata['video_count']} | Items: {metadata['item_count']} | Clusters: {metadata['cluster_count']}")
    print("Sections:")
    for section_name in SECTIONS:
        print(f"  {section_name}: {len(sections[section_name])}")


if __name__ == "__main__":
    main()
