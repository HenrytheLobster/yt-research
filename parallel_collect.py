"""
parallel_collect.py — Parallel Transcript Collector
===================================================
Collect is the slowest stage: it's serial and fires ~3 yt-dlp processes per video
(manual subs, auto subs, metadata). This runs a small worker pool that each claim
`queued` videos from the queue, and folds subs + metadata into ONE yt-dlp call.

IMPORTANT: YouTube rate-limits transcript pulls per IP. 3 workers is the sweet spot;
pushing higher triggers throttling/backoff that loses you the speedup. Default is 3.

Exposes run_parallel_collect() so the orchestrator / dashboard can call it.

Usage:
    python parallel_collect.py                      # all queued, default workers
    python parallel_collect.py --workers 3 --max 500
"""

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    QueueLock, load_pending_unlocked, save_pending_unlocked,
    iso_now, run_ytdlp, AgentError,
)
from youtube_collect import vtt_to_text, save_collected, quarantine, RAW_DIR, setup_dirs

DEFAULT_WORKERS = 3


def fetch_transcript_and_meta(url: str, video_id: str) -> tuple[str, str, dict]:
    """
    ONE yt-dlp call gets manual+auto subs AND metadata (info.json) — instead of the
    2-3 separate calls the serial collector makes. Returns (text, lang, meta).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cmd = [
            "yt-dlp", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-lang", "en", "--sub-format", "vtt", "--convert-subs", "vtt",
            "--write-info-json", "--no-playlist", "--no-warnings",
            "-o", str(tmp / "%(id)s.%(ext)s"), url,
        ]
        try:
            run_ytdlp(cmd, timeout=90, label="subs+meta")
        except AgentError:
            pass  # fall through; we still check whatever landed in tmp

        # transcript
        vtt_files = list(tmp.glob(f"{video_id}*.vtt")) or list(tmp.glob("*.vtt"))
        text, lang = "", "none"
        if vtt_files:
            # prefer a manual track (filename without 'auto') if both exist
            vtt_files.sort(key=lambda p: ("auto" in p.name.lower()))
            text = vtt_to_text(vtt_files[0])
            lang = "manual" if "auto" not in vtt_files[0].name.lower() else "auto"

        # metadata
        meta = {}
        info_files = list(tmp.glob(f"{video_id}*.info.json")) or list(tmp.glob("*.info.json"))
        if info_files:
            try:
                meta = json.loads(info_files[0].read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        return text, lang, meta


def collect_one(video_id: str, url: str) -> tuple[bool, str]:
    out_dir = RAW_DIR / video_id
    if (out_dir / "transcript.txt").exists():
        return True, ""
    text, lang, meta = fetch_transcript_and_meta(url, video_id)
    if not text or len(text.split()) < 100:
        reason = f"No usable transcript (lang={lang}, words={len(text.split())})"
        quarantine(video_id, reason)
        return False, reason
    if not meta:
        meta = {"title": video_id, "webpage_url": url}
    saved = save_collected(video_id, meta, text, lang)
    print(f"  collected: {saved['title'][:55]} ({saved['transcript_word_count']} words, {lang})")
    return True, ""


def worker_process(worker_id: int, max_per_worker: int):
    processed = 0
    while processed < max_per_worker:
        # claim one queued video (fast, locked)
        video_id, url = None, None
        with QueueLock():
            entries = load_pending_unlocked()
            queued = [e for e in entries if e.get("status") == "queued"]
            if not queued:
                break
            video_id = queued[0]["video_id"]
            url = queued[0].get("url", f"https://www.youtube.com/watch?v={video_id}")
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = "collecting"  # prevent double-claim
                    e["worker_id"] = worker_id
                    e["last_attempt_at"] = iso_now()
                    break
            save_pending_unlocked(entries)

        if not video_id:
            break

        print(f"[Collect {worker_id}] {video_id}")
        try:
            success, err = collect_one(video_id, url)
        except Exception as e:
            success, err = False, f"collect error: {e}"
            print(f"[Collect {worker_id}] ERROR {video_id}: {e}")

        with QueueLock():
            entries = load_pending_unlocked()
            for e in entries:
                if e["video_id"] == video_id:
                    e["status"] = "collected" if success else "no_transcript"
                    e["last_error"] = None if success else err
                    e.pop("worker_id", None)
                    break
            save_pending_unlocked(entries)

        processed += 1
        print(f"[Collect {worker_id}] done {processed}/{max_per_worker}")
    print(f"[Collect {worker_id}] Finished ({processed} videos)")


def count_queued() -> int:
    with QueueLock():
        entries = load_pending_unlocked()
    return sum(1 for e in entries if e.get("status") == "queued")


def run_parallel_collect(workers: int = DEFAULT_WORKERS, total=None):
    """Spawn `workers` collect workers that drain the `queued` queue."""
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    setup_dirs()
    if total is None:
        total = count_queued()
    if total <= 0:
        print("No queued videos to collect.")
        return

    workers = max(1, int(workers))
    if workers > 4:
        print("WARNING: >4 collect workers risks YouTube rate-limiting; capping at 4.")
        workers = 4
    per_worker = (total + workers - 1) // workers

    print(f"\nStarting {workers} parallel collect worker(s)")
    print(f"   Target: {total} videos (~{per_worker} per worker)")
    print(f"   Note: YouTube throttles per IP — 3 workers is the safe sweet spot.\n")

    start = time.time()
    procs = []
    for i in range(workers):
        p = mp.Process(target=worker_process, args=(i, per_worker))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()

    elapsed = time.time() - start
    print(f"\nAll collect workers done in {elapsed/60:.1f} minutes")
    if total:
        print(f"   Average: {elapsed/total:.1f}s per video")


def main():
    parser = argparse.ArgumentParser(description="Parallel transcript collector")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="Parallel collect workers (3 recommended; >4 risks rate-limiting)")
    parser.add_argument("--max", type=int, default=None,
                        help="Total videos to collect (default: all queued)")
    args = parser.parse_args()
    run_parallel_collect(workers=args.workers, total=args.max)


if __name__ == "__main__":
    main()
