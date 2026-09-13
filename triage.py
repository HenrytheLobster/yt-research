"""
triage.py — Content Router  (v3)
==================================
Filters collected transcripts before expensive extraction.

Changes from v2:
- No more hardcoded domain keyword list. The heuristic pre-filter now derives
  its "on-topic" terms from config/search_config.json's queries (or an
  explicit --topic), so this works for whatever subject you're researching,
  not just whatever the tool was last repurposed for.
- The LLM confirmation prompt is templated with the resolved topic instead of
  a hardcoded subject.

Usage:
    python triage.py
    python triage.py --max 10
    python triage.py --video_id VIDEO_ID
    python triage.py --topic "AI automation tools for small businesses"
"""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

from utils import (
    QueueLock, load_pending_unlocked, save_pending_unlocked,
    update_entry, call_llm, current_llm_provider, resolve_topic,
    load_search_config, iso_now, AgentError, OLLAMA_URL, StageTimer,
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "youtube" / "raw"
QUEUE_DIR = DATA_DIR / "queue"
PENDING_FILE = QUEUE_DIR / "pending.jsonl"

TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "phi3:mini")
FALLBACK_MODEL = os.environ.get("TRIAGE_FALLBACK_MODEL", "qwen3:4b")

MIN_WORDS = 300
TERM_THRESHOLD = 3
SKIP_THRESHOLD = 3

# Generic low-signal-format markers — these are about the VIDEO GENRE, not the
# research topic, so they stay hardcoded and apply no matter what you're
# researching. Add more in config/search_config.json's negative_keywords if a
# particular run needs to exclude other genres too.
SKIP_TERMS = [
    "vlog", "day in my life", "morning routine", "unboxing",
    "reaction", "challenge", "prank", "gaming",
    "faceless", "passive income", "side hustle", "make money online",
    "businesses to start", "businesses i'd build",
    "get rich", "one person business", "1-person business",
    "print on demand", "print-on-demand", "etsy", "dropship", "dropshipping",
    "crypto", "trading",
]


def domain_terms(topic: str) -> list[str]:
    """
    Derive an "on-topic" keyword list from the current run's queries/topic
    instead of a hardcoded list. Falls back to splitting the resolved topic
    string into significant words if no queries are configured.
    """
    config = load_search_config()
    queries = [q for q in (config.get("queries") or []) if q]
    terms: list[str] = []
    for q in queries:
        terms.append(q.strip().lower())
        terms.extend(w for w in re.findall(r"[a-z0-9']{4,}", q.lower()))
    if not terms:
        terms.extend(w for w in re.findall(r"[a-z0-9']{4,}", topic.lower()))
    seen = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ─── heuristic filter ─────────────────────────────────────────────────────────

def heuristic_triage(transcript: str, title: str, topic: str) -> tuple[str, str, list[str]]:
    text_lower = (transcript + " " + title).lower()
    words = transcript.split()

    if len(words) < MIN_WORDS:
        return "skip", f"Too short ({len(words)} words)", []

    terms = domain_terms(topic)
    skip_hits = [t for t in SKIP_TERMS if t in text_lower]

    # If we have no derived on-topic terms at all (empty queries and an
    # unresolvable topic), don't gate on keyword density — let the LLM stage
    # make the call instead of silently skipping everything.
    if not terms:
        if len(skip_hits) >= SKIP_THRESHOLD:
            return "skip", f"Off-topic signals: {skip_hits[:3]}", []
        return "extract", "no topic keywords configured; deferring to LLM triage", []

    topic_hits = [t for t in terms if t in text_lower]

    if len(topic_hits) < TERM_THRESHOLD:
        return "skip", f"Insufficient topic signal ({len(topic_hits)} terms)", topic_hits
    if len(skip_hits) >= SKIP_THRESHOLD:
        return "skip", f"Off-topic signals: {skip_hits[:3]}", topic_hits

    return "extract", f"{len(topic_hits)} topic terms matched", topic_hits


# ─── LLM confirmation ────────────────────────────────────────────────────────

def detect_model() -> str:
    if current_llm_provider() == "gemini" or TRIAGE_MODEL.lower().startswith("gemini"):
        return TRIAGE_MODEL
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        return TRIAGE_MODEL if "phi3" in models else FALLBACK_MODEL
    except Exception:
        return FALLBACK_MODEL


def llm_triage(transcript: str, title: str, model: str, topic: str) -> tuple[str, float]:
    prompt = f"""Filter this YouTube transcript for a research tool investigating: {topic}

Title: {title}
Transcript snippet: {transcript[:1500]}

Return ONLY valid JSON:
{{"decision": "extract", "confidence": 0.9, "reason": "..."}}

"extract" = contains specific, concrete information, tactics, tools, or examples related to {topic}
"skip" = motivational, vague, or unrelated to {topic}
"""
    try:
        raw = call_llm(prompt, model=model, timeout=60, options={"temperature": 0.1})
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            return result.get("decision", "extract"), float(result.get("confidence", 0.7))
    except Exception as e:
        print(f"    ⚠️  LLM triage error: {e}. Defaulting to extract.")
    return "extract", 0.5  # fail open


# ─── triage one video ────────────────────────────────────────────────────────

def triage_video(video_id: str, topic: str | None = None) -> dict:
    topic = resolve_topic(topic)
    raw_dir = RAW_DIR / video_id
    triage_file = raw_dir / "triage.json"

    if triage_file.exists():
        return json.loads(triage_file.read_text())

    transcript_file = raw_dir / "transcript.txt"
    if not transcript_file.exists():
        return {}

    transcript = transcript_file.read_text(encoding="utf-8")
    title = ""
    meta_file = raw_dir / "meta.json"
    if meta_file.exists():
        title = json.loads(meta_file.read_text()).get("title", "")

    word_count = len(transcript.split())

    # Stage 1: heuristic
    h_decision, h_reason, topic_hits = heuristic_triage(transcript, title, topic)

    if h_decision == "skip":
        result = {
            "video_id": video_id,
            "decision": "skip",
            "reason": h_reason,
            "confidence": 1.0,
            "stage": "heuristic",
            "topic_term_hits": topic_hits,
            "transcript_word_count": word_count,
            "triaged_at": iso_now(),
        }
    else:
        model = detect_model()
        print(f"    🤖 LLM triage ({model})...", end=" ", flush=True)
        llm_decision, llm_conf = llm_triage(transcript, title, model, topic)
        result = {
            "video_id": video_id,
            "decision": llm_decision,
            "reason": f"heuristic pass + {model} confirmed",
            "confidence": llm_conf,
            "stage": "llm",
            "model_used": model,
            "topic_term_hits": topic_hits,
            "transcript_word_count": word_count,
            "triaged_at": iso_now(),
        }
        print(f"{llm_decision} ({llm_conf:.2f})")

    triage_file.write_text(json.dumps(result, indent=2))
    icon = "✅" if result["decision"] == "extract" else "⏭️ "
    print(f"  {icon} {video_id[:12]}: {result['decision']} — {result['reason'][:65]}")
    return result


# ─── main ────────────────────────────────────────────────────────────────────

def triage_all(max_videos: int = None, topic: str | None = None):
    with QueueLock():
        entries = load_pending_unlocked()

    to_triage = [e for e in entries if e.get("status") == "collected"]
    if not to_triage:
        print("📭 No collected videos ready for triage.")
        return

    print(f"🔎 Triaging {len(to_triage)} videos...")
    if max_videos:
        to_triage = to_triage[:max_videos]
        print(f"   Processing first {max_videos}.")

    timer = StageTimer(len(to_triage))
    for i, entry in enumerate(to_triage, 1):
        vid = entry["video_id"]
        timer.start_item()
        result = triage_video(vid, topic=topic)
        timer.end_item()
        if result:
            decision = result.get("decision", "skip")
            update_entry(entries, vid, {
                "status": "pending_extract" if decision == "extract" else "skipped",
                "triage_decision": decision,
                "last_error": None,
            })
        print(timer.progress_line(i, label="video"))

    with QueueLock():
        save_pending_unlocked(entries)
    extract_count = sum(1 for e in entries if e.get("status") == "pending_extract")
    skip_count = sum(1 for e in entries if e.get("status") == "skipped")
    print(f"\n✨ Triage done — extract: {extract_count} | skipped: {skip_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video_id")
    parser.add_argument("--max", type=int, help="Max videos to triage")
    parser.add_argument("--topic", help="Override the research topic for this run")
    args = parser.parse_args()

    if args.video_id:
        triage_video(args.video_id, topic=args.topic)
    else:
        triage_all(max_videos=args.max, topic=args.topic)
