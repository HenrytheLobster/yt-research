"""
Benchmark extraction speed across models without changing queue state.

This samples already-collected YouTube transcripts, sends the same extraction
prompt to each requested model, and reports wall-clock timing plus JSON parse
success. It does not write extraction files, update the queue, merge, or report.

Usage:
    python benchmark_extract_speed.py --models grok qwen3:8b --videos 5 --chunks 1
"""

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from extract import EXTRACTION_PROMPT, call_model, chunk_transcript, parse_json_with_repair


ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "youtube" / "raw"


def load_samples(video_count: int, chunks_per_video: int, seed: int) -> list[dict]:
    candidates = []
    for raw_dir in RAW_DIR.iterdir() if RAW_DIR.exists() else []:
        transcript_file = raw_dir / "transcript.txt"
        if not transcript_file.exists():
            continue
        transcript = transcript_file.read_text(encoding="utf-8", errors="ignore")
        if len(transcript.split()) < 300:
            continue
        title = raw_dir.name
        meta_file = raw_dir / "meta.json"
        if meta_file.exists():
            try:
                title = json.loads(meta_file.read_text(encoding="utf-8")).get("title", title)
            except json.JSONDecodeError:
                pass
        candidates.append({
            "video_id": raw_dir.name,
            "title": title,
            "transcript": transcript,
        })

    random.Random(seed).shuffle(candidates)
    selected = candidates[:video_count]
    samples = []
    for item in selected:
        chunks = chunk_transcript(item["transcript"])[:chunks_per_video]
        for idx, chunk in enumerate(chunks, 1):
            samples.append({
                "video_id": item["video_id"],
                "title": item["title"],
                "chunk_num": idx,
                "total_chunks": len(chunks),
                "chunk": chunk,
            })
    return samples


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[idx]


def benchmark_model(model: str, samples: list[dict]) -> dict:
    timings = []
    successes = 0
    failures = 0

    print(f"\nModel: {model}")
    for i, sample in enumerate(samples, 1):
        prompt = EXTRACTION_PROMPT.format(
            title=sample["title"],
            video_id=sample["video_id"],
            chunk_num=sample["chunk_num"],
            total_chunks=sample["total_chunks"],
            transcript=sample["chunk"],
        )
        started = time.perf_counter()
        ok = False
        try:
            raw = call_model(prompt, model)
            parsed = parse_json_with_repair(raw, sample["video_id"])
            ok = parsed is not None
        except Exception as exc:
            print(f"  {i:02d}/{len(samples)} failed: {sample['video_id']} ({exc})")
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        if ok:
            successes += 1
            print(f"  {i:02d}/{len(samples)} ok     {elapsed:6.1f}s  {sample['video_id']}")
        else:
            failures += 1
            print(f"  {i:02d}/{len(samples)} bad    {elapsed:6.1f}s  {sample['video_id']}")

    return {
        "model": model,
        "samples": len(samples),
        "successes": successes,
        "failures": failures,
        "total_seconds": sum(timings),
        "avg_seconds": statistics.mean(timings) if timings else 0.0,
        "median_seconds": statistics.median(timings) if timings else 0.0,
        "p90_seconds": percentile(timings, 90),
        "min_seconds": min(timings) if timings else 0.0,
        "max_seconds": max(timings) if timings else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark extraction model speed")
    parser.add_argument("--models", nargs="+", default=["grok", "qwen3:8b"],
                        help="Models to benchmark, e.g. grok qwen3:8b")
    parser.add_argument("--videos", type=int, default=5,
                        help="Number of already-collected videos to sample")
    parser.add_argument("--chunks", type=int, default=1,
                        help="Chunks per sampled video")
    parser.add_argument("--seed", type=int, default=17,
                        help="Random seed for reproducible sampling")
    args = parser.parse_args()

    samples = load_samples(args.videos, args.chunks, args.seed)
    if not samples:
        print("No collected transcripts found to benchmark.")
        return

    print(f"Benchmarking {len(samples)} transcript chunk(s)")
    print("This does not update queue status or write extraction files.")

    results = [benchmark_model(model, samples) for model in args.models]

    print("\nSummary")
    print("model,samples,successes,failures,total_seconds,avg_seconds,median_seconds,p90_seconds")
    for result in results:
        print(
            "{model},{samples},{successes},{failures},{total_seconds:.1f},"
            "{avg_seconds:.1f},{median_seconds:.1f},{p90_seconds:.1f}".format(**result)
        )


if __name__ == "__main__":
    main()
