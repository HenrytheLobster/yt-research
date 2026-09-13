"""
parallel_triage.py — Parallel Triage Runner
======================================================
Claims collected videos one at a time and triages them in worker processes.
This keeps Ollama busier than the original serial triage stage — most useful
on a machine with a fast GPU but tighter RAM, where running several triage
calls at once beats running extraction workers at max concurrency alone.
"""

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from triage import triage_video
from utils import QueueLock, load_pending_unlocked, save_pending_unlocked, iso_now

DEFAULT_WORKERS = 4


def worker_process(worker_id: int, max_per_worker: int, topic: str | None = None):
    processed = 0
    while processed < max_per_worker:
        video_id = None
        with QueueLock():
            entries = load_pending_unlocked()
            collected = [e for e in entries if e.get("status") == "collected"]
            if not collected:
                break
            video_id = collected[0]["video_id"]
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = "triaging"
                    e["worker_id"] = worker_id
                    e["last_attempt_at"] = iso_now()
                    break
            save_pending_unlocked(entries)

        if not video_id:
            break

        print(f"[Triage {worker_id}] {video_id}")
        try:
            result = triage_video(video_id, topic=topic)
            decision = result.get("decision", "skip") if result else "skip"
            success = True
        except Exception as e:
            print(f"[Triage {worker_id}] ERROR {video_id}: {e}")
            decision = "skip"
            success = False

        with QueueLock():
            entries = load_pending_unlocked()
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = (
                        "pending_extract" if success and decision == "extract"
                        else "skipped" if success
                        else "triage_failed"
                    )
                    e["triage_decision"] = decision if success else None
                    e["last_error"] = None if success else "triage error"
                    e.pop("worker_id", None)
                    break
            save_pending_unlocked(entries)

        processed += 1
        print(f"[Triage {worker_id}] done {processed}/{max_per_worker}")

    print(f"[Triage {worker_id}] Finished ({processed} videos)")


def reset_stale_triage():
    with QueueLock():
        entries = load_pending_unlocked()
        reset = 0
        for e in entries:
            if e.get("status") == "triaging":
                e["status"] = "collected"
                e.pop("worker_id", None)
                reset += 1
        save_pending_unlocked(entries)
    if reset:
        print(f"Reset {reset} stale triage claims")


def count_collected() -> int:
    with QueueLock():
        entries = load_pending_unlocked()
    return sum(1 for e in entries if e.get("status") == "collected")


def run_parallel_triage(workers: int = DEFAULT_WORKERS, total=None, topic: str | None = None):
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    reset_stale_triage()
    if total is None:
        total = count_collected()
    if total <= 0:
        print("No collected videos to triage.")
        return

    workers = max(1, int(workers))
    per_worker = (total + workers - 1) // workers
    print(f"\nStarting {workers} parallel triage worker(s)")
    print(f"   Target: {total} videos (~{per_worker} per worker)\n")

    start = time.time()
    procs = []
    for i in range(workers):
        p = mp.Process(target=worker_process, args=(i, per_worker, topic))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()

    elapsed = time.time() - start
    print(f"\nAll triage workers done in {elapsed/60:.1f} minutes")
    if total:
        print(f"   Average: {elapsed/total:.1f}s per video")


def main():
    parser = argparse.ArgumentParser(description="Parallel triage runner")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--topic", help="Override the research topic for this run")
    args = parser.parse_args()
    run_parallel_triage(workers=args.workers, total=args.max, topic=args.topic)


if __name__ == "__main__":
    main()
