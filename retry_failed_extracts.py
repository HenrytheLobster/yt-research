"""
Requeue failed or stale extraction items so the extractor can drain them again.

This is meant for transient provider failures, such as Grok spending-limit errors.

Usage:
    python retry_failed_extracts.py
    python retry_failed_extracts.py --include-stale-extracting
    python retry_failed_extracts.py --apply
"""

import argparse

from utils import QueueLock, load_pending_unlocked, save_pending_unlocked


RETRYABLE_STATUSES = {"extract_failed"}


def main():
    parser = argparse.ArgumentParser(description="Requeue failed extraction items")
    parser.add_argument("--apply", action="store_true",
                        help="Actually change queue statuses; without this, only preview")
    parser.add_argument("--include-stale-extracting", action="store_true",
                        help="Also reclaim items left in extracting, e.g. after stopping workers")
    args = parser.parse_args()

    statuses = set(RETRYABLE_STATUSES)
    if args.include_stale_extracting:
        statuses.add("extracting")

    with QueueLock():
        entries = load_pending_unlocked()
        targets = [e for e in entries if e.get("status") in statuses]

        print(f"Found {len(targets)} retryable extraction item(s):")
        counts = {}
        for entry in targets:
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        for status in sorted(counts):
            print(f"  {status}: {counts[status]}")

        if not args.apply:
            print("Dry run. Re-run with --apply to flip them to pending_extract.")
            return

        for entry in targets:
            entry["status"] = "pending_extract"
            entry["last_error"] = None
            entry.pop("worker_id", None)

        save_pending_unlocked(entries)

    print(f"Requeued {len(targets)} item(s) to pending_extract.")


if __name__ == "__main__":
    main()
