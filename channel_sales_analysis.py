"""
channel_sales_analysis.py
=========================
Reusable full-channel analysis for creators who teach AI service sales.

This script:
1. Collects transcripts for every video on a channel
2. Extracts niches, acquisition tactics, offer/delivery tactics, and tips
3. Aggregates recurring findings across the full corpus
4. Writes both structured JSON and a readable markdown report

It intentionally lives outside the main queue-based pipeline so channel-specific
analysis can evolve without disturbing the broader research flow.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from utils import AgentError, call_llm, current_llm_provider, infer_llm_provider
from youtube_collect import _discover_channel_ids, collect_channel


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "youtube" / "raw"
OUT_DIR = DATA_DIR / "channel_analysis"
REPORTS_DIR = ROOT / "reports"
CACHE_DIR = OUT_DIR / "cache"

DEFAULT_MODEL = "qwen3:8b"
MAX_TRANSCRIPT_CHARS = 24000
MIN_TRANSCRIPT_WORDS = 150
POSITIVE_TERMS = [
    "ai",
    "automation",
    "automations",
    "agency",
    "services business",
    "service business",
    "lead",
    "leads",
    "appointment",
    "seminar",
    "client",
    "clients",
    "local business",
    "outreach",
    "cold email",
    "cold call",
    "follow up",
    "sales",
    "offer",
    "n8n",
    "zapier",
    "make.com",
    "gohighlevel",
]
NEGATIVE_TERMS = [
    "amazon wholesale",
    "amazon fba",
    "supplier",
    "test order",
    "profitable accounts",
    "wholesaler",
    "wholesale",
    "inventory",
    "fba",
]


VIDEO_ANALYSIS_PROMPT = """You are extracting operational knowledge from a YouTube transcript.
The creator teaches how to sell AI services to businesses.

Your job is to pull out:
1. Which niches or business types the creator presents as especially good to sell AI to
2. The concrete client acquisition tactics they recommend
3. The concrete offer / fulfillment / delivery tactics they recommend
4. The concrete tips, tricks, and heuristics they recommend

Be strict:
- Only include findings explicitly supported by this transcript.
- Prefer concrete tactics over generic motivation.
- If a niche/tactic is mentioned only vaguely, leave it out.
- Every item must include one short verbatim source quote from the transcript.
- Return empty arrays when the transcript is fluffy or off-topic.

VIDEO TITLE: {title}
CHANNEL: {channel}

TRANSCRIPT:
\"\"\"{transcript}\"\"\"

Return ONLY valid JSON with this shape:
{{
  "niches": [
    {{
      "niche": "<business type or segment>",
      "why_good_for_ai": "<why this niche is attractive>",
      "best_offer_angles": ["<lead gen>", "<follow-up>", "<appointment setting>"],
      "confidence": "<high|medium|low>",
      "source_quote": "<short verbatim quote>"
    }}
  ],
  "acquisition_tactics": [
    {{
      "technique": "<short tactic name>",
      "category": "<in_person|seminar|cold_outreach|referral|partnership|content|paid|follow_up|other>",
      "description": "<what the creator recommends doing>",
      "why_it_works": "<why they say it works>",
      "source_quote": "<short verbatim quote>"
    }}
  ],
  "offer_delivery_tactics": [
    {{
      "technique": "<short tactic name>",
      "description": "<what to sell or how to fulfill it>",
      "tools_mentioned": ["<tool names only if stated>"],
      "source_quote": "<short verbatim quote>"
    }}
  ],
  "tips_and_tricks": [
    {{
      "tip": "<specific recommendation or heuristic>",
      "theme": "<niche_selection|lead_gen|sales_call|pricing|offer_design|fulfillment|mindset|other>",
      "source_quote": "<short verbatim quote>"
    }}
  ],
  "summary": {{
    "video_takeaway": "<1-2 sentence plain summary>"
  }}
}}
"""


CORPUS_SUMMARY_PROMPT = """You are summarizing channel-wide findings from a creator who teaches selling AI services.

Use the supplied evidence bundle to produce a clean synthesis focused on:
1. Best niches for selling AI
2. Best acquisition tactics
3. Best offer / delivery approaches
4. Repeated tips and heuristics

Be concrete and evidence-led. Do not invent findings outside the bundle.
Prefer ranked findings that recur across multiple videos.

EVIDENCE BUNDLE:
{bundle}

Return ONLY valid JSON:
{{
  "executive_summary": [
    "<bullet-style sentence>",
    "<bullet-style sentence>"
  ],
  "best_niches": [
    {{
      "niche": "<name>",
      "why_it_stands_out": "<why>",
      "recurrence_notes": "<how it shows up>",
      "best_offer_angles": ["<angle>"]
    }}
  ],
  "best_acquisition_tactics": [
    {{
      "technique": "<name>",
      "why_it_matters": "<why>",
      "when_to_use": "<when>"
    }}
  ],
  "best_offer_delivery_tactics": [
    {{
      "technique": "<name>",
      "why_it_matters": "<why>"
    }}
  ],
  "key_tips_and_tricks": [
    "<tip sentence>",
    "<tip sentence>"
  ]
}}
"""


def slugify(value: str) -> str:
    text = re.sub(r"^https?://(www\.)?youtube\.com/", "", value.strip(), flags=re.I)
    text = text.replace("@", "")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "channel"


def ensure_dirs():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_video_artifacts(video_id: str) -> tuple[dict, str] | tuple[None, None]:
    raw_dir = RAW_DIR / video_id
    meta_file = raw_dir / "meta.json"
    transcript_file = raw_dir / "transcript.txt"
    if not meta_file.exists() or not transcript_file.exists():
        return None, None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    transcript = transcript_file.read_text(encoding="utf-8", errors="replace").strip()
    if len(transcript.split()) < MIN_TRANSCRIPT_WORDS:
        return None, None
    return meta, transcript[:MAX_TRANSCRIPT_CHARS]


def is_relevant_video(meta: dict, transcript: str) -> tuple[bool, str]:
    haystack = " ".join(
        [
            meta.get("title", ""),
            meta.get("description", ""),
            transcript[:3000],
        ]
    ).lower()
    positive_hits = [term for term in POSITIVE_TERMS if term in haystack]
    negative_hits = [term for term in NEGATIVE_TERMS if term in haystack]
    if len(positive_hits) >= 2 and not negative_hits:
        return True, f"positive_hits={positive_hits[:5]}"
    if len(positive_hits) >= 3 and len(negative_hits) <= 1:
        return True, f"positive_hits={positive_hits[:5]} negatives={negative_hits[:2]}"
    return False, f"positive_hits={positive_hits[:5]} negatives={negative_hits[:3]}"


def parse_json_object(raw: str) -> dict:
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def analyze_video(meta: dict, transcript: str, model: str, provider: str | None) -> dict:
    prompt = VIDEO_ANALYSIS_PROMPT.format(
        title=meta.get("title", ""),
        channel=meta.get("channel", ""),
        transcript=transcript,
    )
    raw = call_llm(
        prompt,
        model=model,
        provider=provider,
        timeout=240,
        options={"temperature": 0.1, "num_ctx": 32768, "max_output_tokens": 2200},
    )
    data = parse_json_object(raw)
    data["video_id"] = meta.get("video_id", "")
    data["title"] = meta.get("title", "")
    data["url"] = meta.get("url", "")
    data["channel"] = meta.get("channel", "")
    return data


def cache_path_for(slug: str, video_id: str) -> Path:
    return CACHE_DIR / slug / f"{video_id}.json"


def load_cached_analysis(slug: str, video_id: str) -> dict | None:
    path = cache_path_for(slug, video_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cached_analysis(slug: str, video_id: str, payload: dict):
    path = cache_path_for(slug, video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def aggregate_findings(analyses: list[dict]) -> dict:
    niche_groups: dict[str, dict] = {}
    acquisition_groups: dict[str, dict] = {}
    delivery_groups: dict[str, dict] = {}
    tip_groups: dict[str, dict] = {}
    category_counts = Counter()
    theme_counts = Counter()

    def _touch(group_map: dict[str, dict], key: str, seed: dict):
        if key not in group_map:
            group_map[key] = seed
        return group_map[key]

    for item in analyses:
        video_ref = {
            "video_id": item.get("video_id", ""),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
        }

        for niche in item.get("niches", []):
            name = niche.get("niche", "").strip()
            if not name:
                continue
            key = normalize_phrase(name)
            entry = _touch(
                niche_groups,
                key,
                {
                    "niche": name,
                    "why_good_for_ai": [],
                    "best_offer_angles": Counter(),
                    "confidence": Counter(),
                    "source_quotes": [],
                    "videos": [],
                },
            )
            if niche.get("why_good_for_ai"):
                entry["why_good_for_ai"].append(niche["why_good_for_ai"])
            for angle in niche.get("best_offer_angles", []):
                if angle:
                    entry["best_offer_angles"][angle] += 1
            if niche.get("confidence"):
                entry["confidence"][niche["confidence"]] += 1
            if niche.get("source_quote"):
                entry["source_quotes"].append(niche["source_quote"])
            entry["videos"].append(video_ref)

        for tactic in item.get("acquisition_tactics", []):
            name = tactic.get("technique", "").strip()
            if not name:
                continue
            key = normalize_phrase(name)
            entry = _touch(
                acquisition_groups,
                key,
                {
                    "technique": name,
                    "category": tactic.get("category", "other"),
                    "descriptions": [],
                    "why_it_works": [],
                    "source_quotes": [],
                    "videos": [],
                },
            )
            category_counts[entry["category"]] += 1
            if tactic.get("description"):
                entry["descriptions"].append(tactic["description"])
            if tactic.get("why_it_works"):
                entry["why_it_works"].append(tactic["why_it_works"])
            if tactic.get("source_quote"):
                entry["source_quotes"].append(tactic["source_quote"])
            entry["videos"].append(video_ref)

        for tactic in item.get("offer_delivery_tactics", []):
            name = tactic.get("technique", "").strip()
            if not name:
                continue
            key = normalize_phrase(name)
            entry = _touch(
                delivery_groups,
                key,
                {
                    "technique": name,
                    "descriptions": [],
                    "tools_mentioned": Counter(),
                    "source_quotes": [],
                    "videos": [],
                },
            )
            if tactic.get("description"):
                entry["descriptions"].append(tactic["description"])
            for tool in tactic.get("tools_mentioned", []):
                if tool:
                    entry["tools_mentioned"][tool] += 1
            if tactic.get("source_quote"):
                entry["source_quotes"].append(tactic["source_quote"])
            entry["videos"].append(video_ref)

        for tip in item.get("tips_and_tricks", []):
            text = tip.get("tip", "").strip()
            if not text:
                continue
            key = normalize_phrase(text)
            entry = _touch(
                tip_groups,
                key,
                {
                    "tip": text,
                    "theme": tip.get("theme", "other"),
                    "source_quotes": [],
                    "videos": [],
                },
            )
            theme_counts[entry["theme"]] += 1
            if tip.get("source_quote"):
                entry["source_quotes"].append(tip["source_quote"])
            entry["videos"].append(video_ref)

    def finalize(group_map: dict[str, dict], kind: str) -> list[dict]:
        items = []
        for entry in group_map.values():
            video_ids = []
            seen = set()
            for video in entry["videos"]:
                vid = video.get("video_id", "")
                if vid and vid not in seen:
                    seen.add(vid)
                    video_ids.append(video)
            result = {
                k: v
                for k, v in entry.items()
                if k not in {"videos"}
            }
            result["support_count"] = len(video_ids)
            result["videos"] = video_ids[:8]
            result["source_quotes"] = list(dict.fromkeys(entry.get("source_quotes", [])))[:8]
            if kind == "niche":
                result["best_offer_angles"] = [x for x, _ in entry["best_offer_angles"].most_common(5)]
                result["confidence"] = entry["confidence"].most_common(1)[0][0] if entry["confidence"] else "medium"
                result["why_good_for_ai"] = list(dict.fromkeys(entry["why_good_for_ai"]))[:4]
            elif kind == "delivery":
                result["tools_mentioned"] = [x for x, _ in entry["tools_mentioned"].most_common(8)]
                result["descriptions"] = list(dict.fromkeys(entry["descriptions"]))[:4]
            elif kind == "acquisition":
                result["descriptions"] = list(dict.fromkeys(entry["descriptions"]))[:4]
                result["why_it_works"] = list(dict.fromkeys(entry["why_it_works"]))[:4]
            elif kind == "tip":
                result["theme"] = entry.get("theme", "other")
            items.append(result)
        items.sort(key=lambda x: (-x["support_count"], x.get("niche") or x.get("technique") or x.get("tip", "")))
        return items

    return {
        "niches": finalize(niche_groups, "niche"),
        "acquisition_tactics": finalize(acquisition_groups, "acquisition"),
        "offer_delivery_tactics": finalize(delivery_groups, "delivery"),
        "tips_and_tricks": finalize(tip_groups, "tip"),
        "counts": {
            "acquisition_categories": dict(category_counts),
            "tip_themes": dict(theme_counts),
        },
    }


def build_summary_bundle(aggregated: dict) -> str:
    bundle = {
        "top_niches": aggregated.get("niches", [])[:12],
        "top_acquisition_tactics": aggregated.get("acquisition_tactics", [])[:16],
        "top_offer_delivery_tactics": aggregated.get("offer_delivery_tactics", [])[:16],
        "top_tips_and_tricks": aggregated.get("tips_and_tricks", [])[:20],
    }
    return json.dumps(bundle, indent=2)


def summarize_corpus(aggregated: dict, model: str, provider: str | None) -> dict:
    prompt = CORPUS_SUMMARY_PROMPT.format(bundle=build_summary_bundle(aggregated))
    raw = call_llm(
        prompt,
        model=model,
        provider=provider,
        timeout=240,
        options={"temperature": 0.1, "num_ctx": 32768, "max_output_tokens": 2400},
    )
    return parse_json_object(raw)


def write_markdown_report(
    report_path: Path,
    channel: str,
    ids: list[str],
    analyses: list[dict],
    aggregated: dict,
    summary: dict,
):
    lines = [
        f"# Channel Analysis — {channel}",
        "",
        f"- Videos discovered: {len(ids)}",
        f"- Videos analyzed: {len(analyses)}",
        "",
        "## Executive Summary",
        "",
    ]
    for item in summary.get("executive_summary", []):
        lines.append(f"- {item}")

    def add_ranked_section(title: str, items: list[dict], kind: str):
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append("- No strong recurring findings extracted.")
            return
        for idx, item in enumerate(items, 1):
            name = item.get("niche") or item.get("technique") or item.get("tip", "")
            lines.append(f"### {idx}. {name}")
            lines.append(f"- Support: {item.get('support_count', 0)} videos")
            if kind == "niche":
                why = item.get("why_good_for_ai", [])
                if why:
                    lines.append(f"- Why it stands out: {why[0]}")
                angles = item.get("best_offer_angles", [])
                if angles:
                    lines.append(f"- Best offer angles: {', '.join(angles[:5])}")
            elif kind == "acquisition":
                if item.get("category"):
                    lines.append(f"- Category: {item['category']}")
                descs = item.get("descriptions", [])
                if descs:
                    lines.append(f"- What he recommends: {descs[0]}")
                why = item.get("why_it_works", [])
                if why:
                    lines.append(f"- Why it works: {why[0]}")
            elif kind == "delivery":
                descs = item.get("descriptions", [])
                if descs:
                    lines.append(f"- Delivery angle: {descs[0]}")
                tools = item.get("tools_mentioned", [])
                if tools:
                    lines.append(f"- Tools mentioned: {', '.join(tools[:8])}")
            elif kind == "tip":
                if item.get("theme"):
                    lines.append(f"- Theme: {item['theme']}")
            quotes = item.get("source_quotes", [])
            if quotes:
                lines.append(f"- Example quote: \"{quotes[0]}\"")
            videos = item.get("videos", [])
            if videos:
                titles = [v.get("title", "").strip() for v in videos if v.get("title")]
                if titles:
                    lines.append(f"- Seen in: {', '.join(titles[:3])}")

    add_ranked_section("Best Niches For Selling AI", aggregated.get("niches", [])[:15], "niche")
    add_ranked_section("Best Client Acquisition Tactics", aggregated.get("acquisition_tactics", [])[:20], "acquisition")
    add_ranked_section("Best Offer And Fulfillment Tactics", aggregated.get("offer_delivery_tactics", [])[:20], "delivery")
    add_ranked_section("Key Tips And Tricks", aggregated.get("tips_and_tricks", [])[:25], "tip")

    lines += [
        "",
        "## LLM Summary",
        "",
    ]
    for item in summary.get("best_niches", []):
        lines.append(f"- Niche: {item.get('niche', '')} — {item.get('why_it_stands_out', '')}")
    for item in summary.get("best_acquisition_tactics", []):
        lines.append(f"- Acquisition: {item.get('technique', '')} — {item.get('why_it_matters', '')}")
    for item in summary.get("best_offer_delivery_tactics", []):
        lines.append(f"- Delivery: {item.get('technique', '')} — {item.get('why_it_matters', '')}")
    for item in summary.get("key_tips_and_tricks", []):
        lines.append(f"- Tip: {item}")

    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def run_channel_analysis(
    channel: str,
    max_videos: int | None,
    workers: int,
    model: str,
    provider: str | None,
    skip_collect: bool,
):
    ensure_dirs()
    provider = infer_llm_provider(model=model, provider=provider)
    slug = slugify(channel)

    print(f"📺 Discovering videos for {channel}...")
    ids = _discover_channel_ids(channel, max_videos=max_videos)
    print(f"   Found {len(ids)} videos.")
    if not ids:
        raise SystemExit("No videos discovered for this channel.")

    if not skip_collect:
        print("📥 Collecting channel transcripts...")
        collect_channel(channel, max_videos=max_videos, workers=workers)

    analyses = []
    skipped = []
    for idx, video_id in enumerate(ids, 1):
        meta, transcript = load_video_artifacts(video_id)
        if not meta or not transcript:
            skipped.append({"video_id": video_id, "reason": "missing_or_short_transcript"})
            print(f"[{idx}/{len(ids)}] ⏭️  {video_id} missing usable transcript")
            continue

        relevant, reason = is_relevant_video(meta, transcript)
        if not relevant:
            skipped.append({"video_id": video_id, "reason": f"off_topic_heuristic:{reason}"})
            print(f"[{idx}/{len(ids)}] ⏭️  {meta.get('title', video_id)[:70]} | {reason}")
            continue

        cached = load_cached_analysis(slug, video_id)
        if cached:
            analyses.append(cached)
            print(f"[{idx}/{len(ids)}] ♻️  Cached {meta.get('title', video_id)[:70]}")
            continue

        print(f"[{idx}/{len(ids)}] 🤖 Analyzing {meta.get('title', video_id)[:70]}")
        try:
            analyzed = analyze_video(meta, transcript, model=model, provider=provider)
            analyses.append(analyzed)
            save_cached_analysis(slug, video_id, analyzed)
        except Exception as exc:
            skipped.append({"video_id": video_id, "reason": str(exc)})
            print(f"    ⚠️  Skipped {video_id}: {exc}")

    aggregated = aggregate_findings(analyses)
    summary = summarize_corpus(aggregated, model=model, provider=provider) if analyses else {}

    json_path = OUT_DIR / f"{slug}.json"
    report_path = REPORTS_DIR / f"channel_analysis_{slug}.md"

    payload = {
        "channel": channel,
        "slug": slug,
        "video_ids": ids,
        "videos_analyzed": len(analyses),
        "videos_skipped": skipped,
        "model": model,
        "provider": provider,
        "per_video": analyses,
        "aggregated": aggregated,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown_report(report_path, channel, ids, analyses, aggregated, summary)

    print("")
    print(f"✅ Structured output: {json_path}")
    print(f"✅ Markdown report:   {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze a YouTube channel for AI-sales niches and tactics")
    parser.add_argument("--channel", required=True, help="YouTube channel URL or handle")
    parser.add_argument("--max", type=int, help="Optional cap on videos to analyze")
    parser.add_argument("--workers", type=int, default=3, help="Transcript collection workers")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM model name")
    parser.add_argument("--provider", choices=["ollama", "gemini"], help="Override LLM provider")
    parser.add_argument("--skip-collect", action="store_true", help="Reuse already collected transcripts")
    args = parser.parse_args()

    try:
        run_channel_analysis(
            channel=args.channel,
            max_videos=args.max,
            workers=args.workers,
            model=args.model,
            provider=args.provider,
            skip_collect=args.skip_collect,
        )
    except AgentError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
