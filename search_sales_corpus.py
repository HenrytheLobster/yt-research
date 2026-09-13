"""
search_sales_corpus.py
======================
Build a search-term corpus around selling AI services to businesses, then
extract repeated niches, acquisition tactics, offer structures, and tips
across multiple creators.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from channel_sales_analysis import (
    DEFAULT_MODEL,
    OUT_DIR,
    RAW_DIR,
    REPORTS_DIR,
    aggregate_findings,
    analyze_video,
    ensure_dirs,
    is_relevant_video,
    load_cached_analysis,
    load_video_artifacts,
    normalize_phrase,
    save_cached_analysis,
    slugify,
    summarize_corpus,
)
from utils import AgentError, infer_llm_provider
from youtube_collect import collect_video
from youtube_discover import discover_ids_from_query, hydrate_metadata


ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "search_config_ai_agency.json"
SEARCH_OUT_DIR = OUT_DIR / "search_corpus"
SEARCH_CACHE_DIR = SEARCH_OUT_DIR / "cache"
MAX_RESULTS_PER_QUERY = 12


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "queries": data.get("queries", []),
        "negative_keywords": data.get("negative_keywords", []),
    }


def build_queries(config: dict) -> list[str]:
    negatives = " ".join(f'-"{kw}"' for kw in config.get("negative_keywords", []))
    queries = []
    for q in config.get("queries", []):
        queries.append(f"{q} {negatives}".strip() if negatives else q)
    return queries


def corpus_slug(config_path: Path) -> str:
    return slugify(config_path.stem.replace("search-config-", "").replace("search_config_", ""))


def collect_search_candidates(queries: list[str], max_per_query: int) -> tuple[list[dict], dict[str, list[str]]]:
    videos: dict[str, dict] = {}
    query_hits: dict[str, list[str]] = {}

    for query in queries:
        print(f"\n🔍 Query: {query}")
        ids = discover_ids_from_query(query, max_per_query)
        query_hits[query] = ids
        print(f"   Found {len(ids)} candidates")
        for video_id in ids:
            if video_id in videos:
                videos[video_id]["matched_queries"].append(query)
                continue
            meta = hydrate_metadata(video_id)
            if not meta:
                continue
            videos[video_id] = {
                "video_id": video_id,
                "title": meta.get("title", ""),
                "channel": meta.get("channel") or meta.get("uploader", ""),
                "url": meta.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                "description": (meta.get("description") or "")[:1000],
                "matched_queries": [query],
                "meta": meta,
            }

    return list(videos.values()), query_hits


def search_cache_path(slug: str, video_id: str) -> Path:
    return SEARCH_CACHE_DIR / slug / f"{video_id}.json"


def load_search_cached_analysis(slug: str, video_id: str) -> dict | None:
    path = search_cache_path(slug, video_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return load_cached_analysis(slug, video_id)


def save_search_cached_analysis(slug: str, video_id: str, payload: dict):
    path = search_cache_path(slug, video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def augment_aggregated_with_creator_diversity(aggregated: dict, analyses: list[dict]) -> dict:
    by_video = {item.get("video_id", ""): item for item in analyses}

    def decorate(items: list[dict]) -> list[dict]:
        for item in items:
            creators = []
            seen = set()
            for video in item.get("videos", []):
                analysis = by_video.get(video.get("video_id", ""))
                creator = (analysis or {}).get("channel", "")
                if creator and creator not in seen:
                    seen.add(creator)
                    creators.append(creator)
            item["creator_diversity"] = len(creators)
            item["creators"] = creators[:8]
        items.sort(
            key=lambda x: (
                -x.get("creator_diversity", 0),
                -x.get("support_count", 0),
                x.get("niche") or x.get("technique") or x.get("tip", ""),
            )
        )
        return items

    return {
        **aggregated,
        "niches": decorate(aggregated.get("niches", [])),
        "acquisition_tactics": decorate(aggregated.get("acquisition_tactics", [])),
        "offer_delivery_tactics": decorate(aggregated.get("offer_delivery_tactics", [])),
        "tips_and_tricks": decorate(aggregated.get("tips_and_tricks", [])),
    }


def write_search_report(
    report_path: Path,
    corpus_name: str,
    query_count: int,
    candidates: list[dict],
    analyses: list[dict],
    aggregated: dict,
    summary: dict,
):
    lines = [
        f"# Search Corpus Analysis — {corpus_name}",
        "",
        f"- Queries used: {query_count}",
        f"- Unique candidate videos: {len(candidates)}",
        f"- Videos analyzed: {len(analyses)}",
        f"- Unique creators analyzed: {len({a.get('channel','') for a in analyses if a.get('channel')})}",
        "",
        "## Executive Summary",
        "",
    ]
    for item in summary.get("executive_summary", []):
        lines.append(f"- {item}")

    def add_section(title: str, items: list[dict], kind: str, limit: int):
        lines.extend(["", f"## {title}", ""])
        if not items:
            lines.append("- No strong recurring findings extracted.")
            return
        for idx, item in enumerate(items[:limit], 1):
            name = item.get("niche") or item.get("technique") or item.get("tip", "")
            lines.append(f"### {idx}. {name}")
            lines.append(f"- Creator diversity: {item.get('creator_diversity', 0)}")
            lines.append(f"- Support: {item.get('support_count', 0)} videos")
            creators = item.get("creators", [])
            if creators:
                lines.append(f"- Repeated by: {', '.join(creators[:5])}")
            if kind == "niche":
                why = item.get("why_good_for_ai", [])
                if why:
                    lines.append(f"- Why it stands out: {why[0]}")
            elif kind == "acquisition":
                if item.get("category"):
                    lines.append(f"- Category: {item['category']}")
                descs = item.get("descriptions", [])
                if descs:
                    lines.append(f"- Tactic: {descs[0]}")
            elif kind == "delivery":
                descs = item.get("descriptions", [])
                if descs:
                    lines.append(f"- Delivery angle: {descs[0]}")
            elif kind == "tip":
                if item.get("theme"):
                    lines.append(f"- Theme: {item['theme']}")
            quotes = item.get("source_quotes", [])
            if quotes:
                lines.append(f"- Example quote: \"{quotes[0]}\"")

    add_section("Best Niches Across Creators", aggregated.get("niches", []), "niche", 15)
    add_section("Best Acquisition Tactics Across Creators", aggregated.get("acquisition_tactics", []), "acquisition", 20)
    add_section("Best Offer And Fulfillment Tactics Across Creators", aggregated.get("offer_delivery_tactics", []), "delivery", 20)
    add_section("Key Tips And Tricks Across Creators", aggregated.get("tips_and_tricks", []), "tip", 25)

    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def run_search_corpus(
    config_path: Path,
    max_per_query: int,
    model: str,
    provider: str | None,
    skip_collect: bool,
):
    ensure_dirs()
    SEARCH_OUT_DIR.mkdir(parents=True, exist_ok=True)
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_config(config_path)
    queries = build_queries(cfg)
    provider = infer_llm_provider(model=model, provider=provider)
    slug = corpus_slug(config_path)

    candidates, query_hits = collect_search_candidates(queries, max_per_query)
    print(f"\n📚 Unique candidates across queries: {len(candidates)}")

    analyses = []
    skipped = []
    for idx, candidate in enumerate(candidates, 1):
        video_id = candidate["video_id"]
        title = candidate.get("title", "")
        url = candidate.get("url", "")
        if not skip_collect:
            success, err = collect_video(video_id, url)
            if not success:
                skipped.append({"video_id": video_id, "reason": err, "stage": "collect"})
                print(f"[{idx}/{len(candidates)}] ⏭️  {title[:70]} | collect failed: {err}")
                continue

        meta, transcript = load_video_artifacts(video_id)
        if not meta or not transcript:
            skipped.append({"video_id": video_id, "reason": "missing_or_short_transcript", "stage": "load"})
            print(f"[{idx}/{len(candidates)}] ⏭️  {title[:70]} | no usable transcript")
            continue

        relevant, reason = is_relevant_video(meta, transcript)
        if not relevant:
            skipped.append({"video_id": video_id, "reason": f"off_topic_heuristic:{reason}", "stage": "relevance"})
            print(f"[{idx}/{len(candidates)}] ⏭️  {title[:70]} | {reason}")
            continue

        cached = load_search_cached_analysis(slug, video_id)
        if cached:
            cached["matched_queries"] = candidate.get("matched_queries", [])
            analyses.append(cached)
            print(f"[{idx}/{len(candidates)}] ♻️  Cached {title[:70]}")
            continue

        print(f"[{idx}/{len(candidates)}] 🤖 Analyzing {title[:70]}")
        try:
            analyzed = analyze_video(meta, transcript, model=model, provider=provider)
            analyzed["matched_queries"] = candidate.get("matched_queries", [])
            analyses.append(analyzed)
            save_search_cached_analysis(slug, video_id, analyzed)
        except Exception as exc:
            skipped.append({"video_id": video_id, "reason": str(exc), "stage": "analysis"})
            print(f"    ⚠️  Skipped {video_id}: {exc}")

    aggregated = aggregate_findings(analyses)
    aggregated = augment_aggregated_with_creator_diversity(aggregated, analyses)
    summary = summarize_corpus(aggregated, model=model, provider=provider) if analyses else {}

    json_path = SEARCH_OUT_DIR / f"{slug}.json"
    report_path = REPORTS_DIR / f"search_corpus_{slug}.md"
    payload = {
        "config_path": str(config_path),
        "queries": queries,
        "query_hits": query_hits,
        "candidate_count": len(candidates),
        "videos_analyzed": len(analyses),
        "videos_skipped": skipped,
        "model": model,
        "provider": provider,
        "per_video": analyses,
        "aggregated": aggregated,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_search_report(report_path, slug, len(queries), candidates, analyses, aggregated, summary)
    print("")
    print(f"✅ Structured output: {json_path}")
    print(f"✅ Markdown report:   {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Build a search corpus for AI agency / AI sales research")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to search config JSON")
    parser.add_argument("--max-per-query", type=int, default=MAX_RESULTS_PER_QUERY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", choices=["ollama", "gemini"])
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    try:
        run_search_corpus(
            config_path=Path(args.config),
            max_per_query=args.max_per_query,
            model=args.model,
            provider=args.provider,
            skip_collect=args.skip_collect,
        )
    except AgentError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
