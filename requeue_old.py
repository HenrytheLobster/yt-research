"""
requeue_old.py — Re-queue any video whose extracted JSON is NOT from qwen3.
Fixes the case where merge's queue-heal swept un-reprocessed 3b files into 'merged'.

Run this AFTER the current extraction run has drained, then re-run
parallel_extract.py to redo just the stragglers. Do NOT run merge again
until every extracted file is qwen3.

Usage:
    python requeue_old.py            # show what would be requeued
    python requeue_old.py --apply    # actually flip them to pending_extract
"""
import argparse, json, re, glob
from pathlib import Path
from utils import QueueLock, load_pending_unlocked, save_pending_unlocked

ROOT = Path(__file__).parent
EXTRACTED = ROOT / "data" / "youtube" / "extracted"
GOOD_MODEL = "qwen3"   # keep anything from qwen3; redo everything else

def model_of(path: Path) -> str:
    # read as text so even mount-padded files report their model
    txt = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'"model_used"\s*:\s*"([^"]+)"', txt)
    return m.group(1) if m else "?"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    stale = []
    for f in glob.glob(str(EXTRACTED / "*.json")):
        p = Path(f)
        if not model_of(p).startswith(GOOD_MODEL):
            stale.append(p.stem)
    stale = set(stale)
    print(f"Found {len(stale)} videos NOT from qwen3 (will be redone).")

    if not args.apply:
        print("Dry run. Re-run with --apply to flip them to pending_extract.")
        return

    with QueueLock():
        entries = load_pending_unlocked()
        n = 0
        for e in entries:
            if e.get("video_id") in stale:
                e["status"] = "pending_extract"
                e["last_error"] = None
                e.pop("worker_id", None)
                n += 1
        save_pending_unlocked(entries)
    print(f"Requeued {n} videos to pending_extract.")

if __name__ == "__main__":
    main()
