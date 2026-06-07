"""
parallel_extract.py — Parallel Extraction Runner
=================================================
Runs multiple extract.py workers in parallel to maximize Ollama throughput.

On an 8GB GPU (e.g. RTX 4060 Ti) ~3 concurrent qwen3:8b requests fit at num_ctx 4096.
Ollama serves the concurrency (set OLLAMA_NUM_PARALLEL); we spawn workers that each
claim videos from the queue.

Exposes run_parallel() so the orchestrator (run_agent.py) can call it directly —
parallel extraction is now the default extract path.

Usage:
    python parallel_extract.py                      # all pending, default workers
    python parallel_extract.py --workers 3 --max 1800
    python parallel_extract.py --reset --workers 3  # full re-extract
"""

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import QueueLock, load_pending_unlocked, save_pending_unlocked, iso_now
from extract import extract_video  # import the core function

DEFAULT_WORKERS = 3


def worker_process(worker_id: int, max_per_worker: int):
    """
    Each worker:
    1. Locks queue
    2. Claims next pending_extract video
    3. Unlocks queue
    4. Extracts (slow LLM call outside lock)
    5. Locks queue, updates status
    6. Repeat
    """
    processed = 0

    while processed < max_per_worker:
        # Claim one video from queue (fast, locked)
        video_id = None
        with QueueLock():
            entries = load_pending_unlocked()
            pending = [e for e in entries if e.get("status") == "pending_extract"]
            if not pending:
                break  # queue empty

            # Claim the first one
            video_id = pending[0]["video_id"]
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = "extracting"  # temporary status to prevent double-claim
                    e["worker_id"] = worker_id
                    e["last_attempt_at"] = iso_now()
                    break
            save_pending_unlocked(entries)

        if not video_id:
            break

        print(f"[Worker {worker_id}] Extracting {video_id}")

        # Extract (SLOW - happens outside lock). force=True so a re-extract overwrites
        # any cached JSON instead of silently returning the old version.
        try:
            result = extract_video(video_id, force=True)
            success = result is not None
        except Exception as e:
            print(f"[Worker {worker_id}] ERROR {video_id}: {e}")
            success = False

        # Update status (fast, locked)
        with QueueLock():
            entries = load_pending_unlocked()
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = "extracted" if success else "extract_failed"
                    e["last_error"] = None if success else "extraction error"
                    e.pop("worker_id", None)
                    break
            save_pending_unlocked(entries)

        processed += 1
        print(f"[Worker {worker_id}] done {processed}/{max_per_worker}")

    print(f"[Worker {worker_id}] Finished ({processed} videos)")


def reset_for_reextract() -> int:
    """Flip every video with a transcript (except already-extracted) back to pending_extract."""
    from extract import RAW_DIR
    with QueueLock():
        entries = load_pending_unlocked()
        n = 0
        for e in entries:
            vid = e.get("video_id")
            # skip ones already finished cleanly; reclaim everything else
            # (incl. stale "extracting" left behind by a Ctrl+C)
            if e.get("status") == "extracted":
                continue
            if vid and (RAW_DIR / vid / "transcript.txt").exists():
                e["status"] = "pending_extract"
                e["last_error"] = None
                e.pop("worker_id", None)
                n += 1
        save_pending_unlocked(entries)
    print(f"Reset {n} videos to pending_extract")
    return n


def count_pending() -> int:
    with QueueLock():
        entries = load_pending_unlocked()
    return sum(1 for e in entries if e.get("status") == "pending_extract")


def run_parallel(workers: int = DEFAULT_WORKERS, total=None):
    """
    Spawn `workers` worker processes that drain the pending_extract queue.
    Callable from run_agent.py or this file's CLI. If `total` is None, processes
    every pending_extract video. Falls back to single worker if workers <= 1.
    """
    # Required for multiprocessing 'spawn' on Windows; safe to call repeatedly.
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    if total is None:
        total = count_pending()
    if total <= 0:
        print("No videos pending extraction.")
        return

    workers = max(1, int(workers))
    per_worker = (total + workers - 1) // workers  # ceiling division

    print(f"\nStarting {workers} parallel extraction worker(s)")
    print(f"   Target: {total} videos (~{per_worker} per worker)")
    print(f"   Ensure Ollama allows {workers} concurrent requests (OLLAMA_NUM_PARALLEL).\n")

    start_time = time.time()
    processes = []
    for i in range(workers):
        p = mp.Process(target=worker_process, args=(i, per_worker))
        p.start()
        processes.append(p)
    for p in processes:
        p.join()

    elapsed = time.time() - start_time
    print(f"\nAll workers done in {elapsed/60:.1f} minutes")
    if total:
        print(f"   Average: {elapsed/total:.1f}s per video")


def main():
    parser = argparse.ArgumentParser(description="Parallel extraction runner")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="Number of parallel workers (2-3 recommended for 8GB VRAM)")
    parser.add_argument("--max", type=int, default=None,
                        help="Total videos to process (default: all pending)")
    parser.add_argument("--reset", action="store_true",
                        help="Flip every video with a transcript back to pending_extract first")
    args = parser.parse_args()

    if args.reset:
        reset_for_reextract()
    run_parallel(workers=args.workers, total=args.max)


if __name__ == "__main__":
    main()
