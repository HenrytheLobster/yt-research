"""
Streaming overnight runner for research.

Starts stage workers repeatedly for a fixed time window so later stages can keep
working while discovery and collection continue filling the queue.
"""

import argparse
import os
import shutil
import time
from types import SimpleNamespace
from pathlib import Path

from parallel_collect import run_parallel_collect
from parallel_extract import run_parallel
from parallel_triage import run_parallel_triage
from run_agent import run_discover, run_merge, run_policy, run_report, run_status
from utils import QueueLock, load_pending_unlocked


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CLEAR_ON_FRESH = [
    DATA_DIR / "queue",
    DATA_DIR / "youtube" / "raw",
    DATA_DIR / "youtube" / "extracted",
    DATA_DIR / "knowledge",
    DATA_DIR / "quarantine",
]


def clear_research_state():
    """Clear topic-specific run artifacts before starting a new corpus."""
    for path in CLEAR_ON_FRESH:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def queue_counts() -> dict[str, int]:
    with QueueLock():
        entries = load_pending_unlocked()
    counts = {}
    for entry in entries:
        status = entry.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(entries)
    return counts


def print_counts(label: str):
    counts = queue_counts()
    interesting = [
        "total", "queued", "collecting", "collected", "triaging",
        "pending_extract", "extracting", "extracted", "merged",
        "skipped", "no_transcript", "extract_failed",
    ]
    parts = [f"{k}={counts[k]}" for k in interesting if counts.get(k)]
    print(f"{label}: " + ", ".join(parts))


def main():
    parser = argparse.ArgumentParser(description="Stream the pipeline overnight")
    parser.add_argument("--hours", type=float, default=18.0)
    parser.add_argument("--discover-max", type=int, default=40)
    parser.add_argument("--process-max", type=int, default=2000)
    parser.add_argument("--collect-workers", type=int, default=3)
    parser.add_argument("--triage-workers", type=int, default=6)
    parser.add_argument("--extract-workers", type=int, default=6)
    parser.add_argument("--extract-model", default=os.environ.get("EXTRACT_MODEL", "grok"))
    parser.add_argument("--cycle-sleep", type=int, default=60)
    parser.add_argument("--topic", help="Plain-language research topic for triage/extraction prompts")
    parser.add_argument("--skip-discover", action="store_true")
    parser.add_argument("--fresh", action="store_true",
                        help="Clear queue, transcripts, extractions, knowledge, and quarantine before running")
    args = parser.parse_args()

    os.environ["EXTRACT_MODEL"] = args.extract_model
    deadline = time.monotonic() + args.hours * 3600

    if args.fresh:
        clear_research_state()

    print("Streaming overnight run")
    print(f"  time box:     {args.hours:.1f} hours")
    print(f"  discover-max: {args.discover_max} per query")
    print(f"  process-max:  {args.process_max}")
    print(f"  collect:      {args.collect_workers} workers")
    print(f"  triage:       {args.triage_workers} workers")
    print(f"  extract:      {args.extract_workers} workers via {args.extract_model}")

    discover_args = SimpleNamespace(query=None, max=args.discover_max, dry_run=False)
    process_args = SimpleNamespace(max=args.process_max, reset=False, include_low=False)

    if args.skip_discover:
        print("\nStage 1: Discover skipped; using existing/live queue")
        print_counts("Initial queue")
    else:
        print("\nStage 1: Discover")
        run_discover(discover_args)
        print_counts("After discovery")

    cycle = 1
    while time.monotonic() < deadline:
        remaining = (deadline - time.monotonic()) / 3600
        print(f"\nCycle {cycle} | {remaining:.2f}h remaining")
        print_counts("Before cycle")

        counts = queue_counts()
        if counts.get("queued", 0):
            run_parallel_collect(workers=args.collect_workers, total=min(args.process_max, counts["queued"]))

        counts = queue_counts()
        if counts.get("collected", 0):
            run_parallel_triage(workers=args.triage_workers, total=min(args.process_max, counts["collected"]), topic=args.topic)

        counts = queue_counts()
        if counts.get("pending_extract", 0):
            run_parallel(workers=args.extract_workers, total=min(args.process_max, counts["pending_extract"]), topic=args.topic)

        print_counts("After cycle")
        counts = queue_counts()
        has_work = any(counts.get(s, 0) for s in ("queued", "collected", "pending_extract", "collecting", "triaging", "extracting"))
        if not has_work:
            print("No active queue work remains.")
            break

        cycle += 1
        time.sleep(args.cycle_sleep)

    print("\nFinal merge/report")
    run_merge(process_args)
    run_policy(process_args)
    report_file = run_report()
    print(f"Report: {report_file}")
    run_status()


if __name__ == "__main__":
    main()
