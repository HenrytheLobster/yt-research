"""
profile_pipeline.py — Pipeline Profiler
========================================
Runs a small batch end-to-end and shows where time is spent.

Usage:
    python profile_pipeline.py --videos 10
"""

import argparse
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import QueueLock, load_pending_unlocked
from youtube_collect import collect_video
from triage import triage_video
from extract import extract_video
from merge import merge_extraction


def profile_stage(name: str, fn, *args, **kwargs):
    """Time a stage and return result."""
    print(f"\n⏱️  {name}...", end=" ", flush=True)
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - start
        print(f"✅ {elapsed:.1f}s")
        return elapsed, result, None
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌ {elapsed:.1f}s — {e}")
        return elapsed, None, str(e)


def profile_one_video(video_id: str, url: str) -> dict:
    """Profile all stages for one video."""
    timings = {}
    
    # Collect
    t, result, err = profile_stage("Collect", collect_video, video_id, url)
    timings["collect"] = t
    if err:
        return {"video_id": video_id, "error": err, "timings": timings}
    
    # Triage
    t, result, err = profile_stage("Triage", triage_video, video_id)
    timings["triage"] = t
    if err or (result and result.get("decision") == "skip"):
        return {"video_id": video_id, "skipped": True, "timings": timings}
    
    # Extract (slowest)
    t, result, err = profile_stage("Extract", extract_video, video_id)
    timings["extract"] = t
    if err:
        return {"video_id": video_id, "error": err, "timings": timings}
    
    # Merge
    t, result, err = profile_stage("Merge", merge_extraction, video_id)
    timings["merge"] = t
    
    return {"video_id": video_id, "timings": timings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", type=int, default=5, help="Number of videos to profile")
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  PIPELINE PROFILER — {args.videos} videos")
    print(f"{'='*60}\n")
    
    with QueueLock():
        entries = load_pending_unlocked()
    
    queued = [e for e in entries if e.get("status") == "queued"][:args.videos]
    if not queued:
        print("❌ No queued videos. Run discover first.")
        return
    
    results = []
    total_start = time.time()
    
    for i, entry in enumerate(queued, 1):
        print(f"\n{'─'*60}")
        print(f"Video {i}/{len(queued)}: {entry['video_id']}")
        print(f"{'─'*60}")
        
        result = profile_one_video(entry["video_id"], entry["url"])
        results.append(result)
    
    total_elapsed = time.time() - total_start
    
    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}\n")
    
    successful = [r for r in results if "error" not in r and not r.get("skipped")]
    
    if successful:
        avg_collect = sum(r["timings"]["collect"] for r in successful) / len(successful)
        avg_triage = sum(r["timings"]["triage"] for r in successful) / len(successful)
        avg_extract = sum(r["timings"]["extract"] for r in successful) / len(successful)
        avg_merge = sum(r["timings"]["merge"] for r in successful) / len(successful)
        avg_total = avg_collect + avg_triage + avg_extract + avg_merge
        
        print(f"Average timings per video ({len(successful)} successful):")
        print(f"  Collect:  {avg_collect:6.1f}s  ({avg_collect/avg_total*100:4.1f}%)")
        print(f"  Triage:   {avg_triage:6.1f}s  ({avg_triage/avg_total*100:4.1f}%)")
        print(f"  Extract:  {avg_extract:6.1f}s  ({avg_extract/avg_total*100:4.1f}%) ← BOTTLENECK")
        print(f"  Merge:    {avg_merge:6.1f}s  ({avg_merge/avg_total*100:4.1f}%)")
        print(f"  ────────────────────────")
        print(f"  TOTAL:    {avg_total:6.1f}s per video")
        
        print(f"\nProjected time for 1800 videos:")
        print(f"  Sequential: {avg_total * 1800 / 3600:.1f} hours")
        print(f"  2 workers:  {avg_total * 1800 / 3600 / 2:.1f} hours")
        print(f"  3 workers:  {avg_total * 1800 / 3600 / 3:.1f} hours")
    
    skipped = sum(1 for r in results if r.get("skipped"))
    errors = sum(1 for r in results if "error" in r)
    
    print(f"\nResults: {len(successful)} extracted | {skipped} skipped | {errors} errors")
    print(f"Wall time: {total_elapsed/60:.1f} minutes\n")


if __name__ == "__main__":
    main()
