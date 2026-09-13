"""Discover videos on a topic, collect transcripts, and review them with an LLM.

This is the generic, topic-driven research pipeline: point it at whatever
you're researching with --query (or run it via research_menu.py / ./research
for an interactive prompt) and it extracts sourced findings on THAT topic,
not any one hardcoded subject.
"""

import argparse
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from utils import AgentError, call_llm, resolve_topic


ROOT = Path(__file__).parent
RAW = ROOT / "data" / "youtube" / "raw"
OUT = ROOT / "data" / "topic_research"
REPORT = ROOT / "reports" / "topic_research_review.md"
LAST_IDS = OUT / "latest_video_ids.json"
LAST_RUN = OUT / "last_run.json"


def build_search_queries(queries: list[str] | None, negative_keywords: list[str] | None) -> list[str]:
    from youtube_discover import build_queries, load_search_config

    config = load_search_config()
    base_queries = queries if queries else config.get("queries", [])
    negatives = negative_keywords if negative_keywords is not None else config.get("negative_keywords", [])
    return build_queries({"queries": base_queries, "negative_keywords": negatives, "channels": []})


def discover_video_ids(
    max_per_query: int,
    max_videos: int,
    queries: list[str] | None = None,
    negative_keywords: list[str] | None = None,
) -> list[str]:
    from youtube_collect import setup_dirs
    from youtube_discover import discover_ids_from_query

    setup_dirs()
    found = []
    for query in build_search_queries(queries, negative_keywords):
        print(f"Searching: {query}", flush=True)
        for video_id in discover_ids_from_query(query, max_per_query):
            if video_id not in found:
                found.append(video_id)
        if len(found) >= max_videos:
            break
    video_ids = found[:max_videos]
    LAST_IDS.parent.mkdir(parents=True, exist_ok=True)
    LAST_IDS.write_text(json.dumps(video_ids, indent=2), encoding="utf-8")
    print(f"Discovered {len(video_ids)} candidate videos", flush=True)
    return video_ids


def collect_video_ids(video_ids: list[str], max_videos: int | None = None) -> list[str]:
    from youtube_collect import collect_video, setup_dirs

    setup_dirs()
    collected = []
    selected = video_ids[:max_videos] if max_videos else video_ids
    for number, video_id in enumerate(selected, 1):
        print(f"Collecting {number}/{len(selected)}: {video_id}", flush=True)
        success, reason = collect_video(video_id, f"https://www.youtube.com/watch?v={video_id}")
        if success:
            collected.append(video_id)
        else:
            print(f"Skipping {video_id}: {reason}", flush=True)
    LAST_IDS.parent.mkdir(parents=True, exist_ok=True)
    LAST_IDS.write_text(json.dumps(collected, indent=2), encoding="utf-8")
    return collected


def discover_and_collect(max_per_query: int, max_videos: int) -> list[str]:
    return collect_video_ids(discover_video_ids(max_per_query, max_videos), max_videos)


def load_latest_ids() -> list[str]:
    if not LAST_IDS.exists():
        return []
    return json.loads(LAST_IDS.read_text(encoding="utf-8"))


def collected_video_ids() -> list[str]:
    if not RAW.exists():
        return []
    return sorted(
        path.name
        for path in RAW.iterdir()
        if path.is_dir() and (path / "transcript.txt").exists()
    )


def result_path(video_id: str, model: str) -> Path:
    return OUT / f"{video_id}_{model.replace(':', '-')}.json"


def chunks(text: str, words_per_chunk: int = 1500) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


def parse_json(response: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", response)
    if not match:
        raise ValueError("Model returned no JSON object")
    return json.loads(match.group())


def failed_result(
    *,
    video_id: str,
    model: str,
    meta: dict,
    segments: int,
    findings: list,
    rejected: list,
    error: Exception | str,
    failed_segment: int | None = None,
) -> dict:
    return {
        "video_id": video_id,
        "model": model,
        "meta": meta,
        "segments": segments,
        "findings": findings,
        "rejected": rejected,
        "error": f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error),
        "failed_segment": failed_segment,
    }


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def quote_in_transcript(quote: str, transcript: str) -> bool:
    needle = words(quote)
    haystack = words(transcript)
    if len(needle) < 5 or len(needle) > 35:
        return False
    return any(haystack[i:i + len(needle)] == needle
               for i in range(len(haystack) - len(needle) + 1))


def extract(video_id: str, model: str, topic: str, timeout: int = 900) -> dict:
    folder = RAW / video_id
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    transcript = (folder / "transcript.txt").read_text(encoding="utf-8")
    segments = chunks(transcript)
    findings = []
    rejected = []
    cache_dir = OUT / "segments" / f"{video_id}_{model.replace(':', '-')}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for number, segment in enumerate(segments, 1):
        cache = cache_dir / f"{number:03d}.json"
        if cache.exists():
            parsed = json.loads(cache.read_text(encoding="utf-8"))
            print(f"{video_id}: segment {number}/{len(segments)} cached", flush=True)
        else:
            print(f"{video_id}: segment {number}/{len(segments)} running on {model}...", flush=True)
            started = time.monotonic()
            prompt = f"""Extract concrete observations about the following topic from this YouTube transcript segment: {topic}
Treat the speaker's statements as claims, not established facts. Do not add outside knowledge.
Return ONLY JSON: {{"findings":[{{"type":"practice|case_study|risk|opinion","claim":"one specific point","quote":"an exact contiguous 5-30 word quote from the transcript proving the point"}}]}}.
Extract at most 6 useful findings. Leave findings empty when the segment has no concrete advice about {topic}.

Video: {meta.get('title', '')}
Transcript segment:
{segment}
"""
            stop_heartbeat = threading.Event()

            def heartbeat() -> None:
                while not stop_heartbeat.wait(20):
                    print(f"{video_id}: segment {number}/{len(segments)} still running "
                          f"({time.monotonic() - started:.0f}s)", flush=True)

            monitor = threading.Thread(target=heartbeat, daemon=True)
            monitor.start()
            try:
                response = call_llm(
                    prompt,
                    model=model,
                    timeout=timeout,
                    retries=0,
                    options={
                        "temperature": 0.1,
                        "num_ctx": 8192,
                        "num_predict": 900,
                        "format": "json",
                        "think": False,
                    },
                )
            except AgentError as exc:
                elapsed = time.monotonic() - started
                print(f"{video_id}: segment {number}/{len(segments)} failed after "
                      f"{elapsed:.0f}s: {exc}", flush=True)
                return {
                    "video_id": video_id,
                    "model": model,
                    "meta": meta,
                    "segments": len(segments),
                    "findings": findings,
                    "rejected": rejected,
                    "error": str(exc),
                    "failed_segment": number,
                }
            finally:
                stop_heartbeat.set()
                monitor.join(timeout=1)
            try:
                parsed = parse_json(response)
            except (ValueError, json.JSONDecodeError) as exc:
                elapsed = time.monotonic() - started
                print(f"{video_id}: segment {number}/{len(segments)} returned unusable JSON "
                      f"after {elapsed:.0f}s: {exc}", flush=True)
                return failed_result(
                    video_id=video_id,
                    model=model,
                    meta=meta,
                    segments=len(segments),
                    findings=findings,
                    rejected=rejected,
                    error=exc,
                    failed_segment=number,
                )
            cache.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
            print(f"{video_id}: segment {number}/{len(segments)} done in "
                  f"{time.monotonic() - started:.0f}s", flush=True)
        for finding in parsed.get("findings", []):
            quote = " ".join(str(finding.get("quote", "")).split())
            if not quote_in_transcript(quote, segment):
                rejected.append(finding)
                continue
            findings.append({**finding, "quote": quote, "segment": number})
    return {"video_id": video_id, "model": model, "meta": meta,
            "segments": len(segments), "findings": findings, "rejected": rejected}


def write_report(results: list[dict], model: str, topic: str) -> None:
    lines = [f"# {topic}: local transcript review", "",
             f"Model: `{model}`. Source: YouTube transcripts. Each item below is a speaker's claim, not an independently verified best practice. Only claims with a quote found in the transcript are included.", ""]
    for result in results:
        meta = result["meta"]
        lines += [f"## [{meta.get('title', result['video_id'])}]({meta.get('url', 'https://www.youtube.com/watch?v=' + result['video_id'])})", "",
                  f"Channel: {meta.get('channel', 'unknown')} · Published: {meta.get('publish_date', 'unknown')} · Verified observations: {len(result['findings'])}", ""]
        if result.get("error"):
            lines += [f"- Analysis stopped at segment {result.get('failed_segment', '?')}: `{result['error']}`", ""]
        for item in result["findings"]:
            lines += [f"- **{item.get('type', 'claim')}:** {item.get('claim', '')}",
                      f"  - Transcript: “{item['quote']}”"]
        if not result["findings"]:
            lines += ["- No source-verified observations extracted."]
        lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def analyze_video_ids(video_ids: list[str], model: str, topic: str, timeout: int = 900, update_report: bool = False) -> list[dict]:
    results = []
    for video_id in video_ids:
        path = result_path(video_id, model)
        if path.exists():
            print(f"{video_id}: cached at {path}", flush=True)
            result = json.loads(path.read_text(encoding="utf-8"))
        else:
            try:
                result = extract(video_id, model, topic, timeout=timeout)
            except Exception as exc:
                print(f"{video_id}: analysis failed unexpectedly: {exc}", flush=True)
                try:
                    folder = RAW / video_id
                    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                    transcript = (folder / "transcript.txt").read_text(encoding="utf-8")
                    segment_count = len(chunks(transcript))
                except Exception:
                    meta = {"title": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}
                    segment_count = 0
                result = failed_result(
                    video_id=video_id,
                    model=model,
                    meta=meta,
                    segments=segment_count,
                    findings=[],
                    rejected=[],
                    error=exc,
                )
            path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"{video_id}: {len(result['findings'])} verified quotes, "
              f"{len(result['rejected'])} rejected", flush=True)
        results.append(result)
        if update_report:
            write_report(results, model, topic)
            print(f"Report updated: {REPORT}", flush=True)
    return results


def load_analysis_results(video_ids: list[str], model: str) -> list[dict]:
    results = []
    for video_id in video_ids:
        path = result_path(video_id, model)
        if path.exists():
            results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def record_run(
    *,
    steps: list[str],
    model: str,
    queries: list[str],
    negative_keywords: list[str],
    max_videos: int,
    video_ids: list[str],
) -> None:
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps({
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "model": model,
        "queries": queries,
        "negative_keywords": negative_keywords,
        "max_videos": max_videos,
        "video_ids": video_ids,
    }, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_ids", nargs="*", help="Already known YouTube video IDs")
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--discover", action="store_true", help="Search for candidate video IDs")
    parser.add_argument("--collect", action="store_true", help="Download transcripts for selected video IDs")
    parser.add_argument("--analyze", action="store_true", help="Analyze collected transcripts")
    parser.add_argument("--report", action="store_true", help="Write the markdown report")
    parser.add_argument("--use-latest-ids", action="store_true", help="Use data/topic_research/latest_video_ids.json when no video IDs are passed")
    parser.add_argument("--query", action="append", dest="queries", help="Positive search query for this run; may be repeated")
    parser.add_argument("--negative-keyword", action="append", dest="negative_keywords", help="Negative keyword filter for this run; may be repeated")
    parser.add_argument("--max-per-query", type=int, default=5)
    parser.add_argument("--max-videos", type=int, default=8)
    parser.add_argument("--analysis-timeout", type=int, default=900, help="Seconds to allow each model segment before recording a failed video and continuing")
    parser.add_argument("--topic", help="Plain-language description of the research topic "
                                         "(defaults to the queries for this run, or config/search_config.json)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    topic = resolve_topic(args.topic, queries=args.queries)

    explicit_steps = any([args.discover, args.collect, args.analyze, args.report])
    if not explicit_steps:
        args.discover = True
        args.collect = True
        args.analyze = True
        args.report = True

    video_ids = list(args.video_ids)
    if args.use_latest_ids and not video_ids:
        video_ids = load_latest_ids()

    if args.discover:
        video_ids = discover_video_ids(
            args.max_per_query,
            args.max_videos,
            queries=args.queries,
            negative_keywords=args.negative_keywords,
        )

    if args.collect:
        if not video_ids:
            raise SystemExit("No video IDs available to collect")
        video_ids = collect_video_ids(video_ids, args.max_videos)

    if not video_ids and (args.analyze or args.report):
        video_ids = load_latest_ids() or collected_video_ids()
    if video_ids and not (args.discover or args.collect):
        video_ids = video_ids[:args.max_videos]

    results = []
    if args.analyze:
        if not video_ids:
            raise SystemExit("No collected video transcripts are available to analyze")
        results = analyze_video_ids(video_ids, args.model, topic, timeout=args.analysis_timeout, update_report=args.report)

    if args.report:
        if not results:
            results = load_analysis_results(video_ids, args.model)
        if not results:
            raise SystemExit("No analysis results are available to report")
        write_report(results, args.model, topic)
        print(f"Report updated: {REPORT}", flush=True)

    record_run(
        steps=[
            name for name, enabled in [
                ("discover", args.discover),
                ("collect", args.collect),
                ("analyze", args.analyze),
                ("report", args.report),
            ] if enabled
        ],
        model=args.model,
        queries=args.queries or [],
        negative_keywords=args.negative_keywords or [],
        max_videos=args.max_videos,
        video_ids=video_ids,
    )


if __name__ == "__main__":
    main()
