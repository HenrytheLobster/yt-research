"""
app.py — AI Automation Research Dashboard
==========================================
Serves the dashboard and exposes pipeline controls via a simple Flask API.

Usage:
    python app.py
    Then open http://localhost:5000 in your browser.
"""

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
QUEUE_FILE = DATA_DIR / "queue" / "pending.jsonl"
POLICY_FILE = ROOT / "config" / "scoring_policy.json"
SEARCH_CONFIG_FILE = ROOT / "config" / "search_config.json"

app = Flask(__name__)

VALID_STAGES = {"discover", "collect", "collect-parallel", "triage", "extract", "extract-parallel", "merge", "policy", "report", "full", "build-taxonomy", "build-frontier"}

# ── Live run log ──────────────────────────────────────────────────────────────
# Background thread drains subprocess stdout into this deque.
# /api/log polls it. Keeps last 500 lines so the browser can catch up.
_run_log: deque = deque(maxlen=500)
_run_lock = threading.Lock()
_run_stage: str = ""
_run_active: bool = False
_run_proc: subprocess.Popen = None


def _drain(proc: subprocess.Popen, stage: str):
    """Read subprocess stdout line-by-line into the log buffer."""
    global _run_active, _run_stage, _run_proc
    with _run_lock:
        _run_log.clear()
        _run_log.append(f"▶  Starting {stage}…")
        _run_stage = stage
        _run_active = True
        _run_proc = proc
    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            with _run_lock:
                _run_log.append(line)
        proc.wait()
        rc = proc.returncode
        with _run_lock:
            if rc == -1 or rc is None:
                _run_log.append(f"\n⛔  Stage '{stage}' was interrupted.")
            else:
                _run_log.append(f"\n{'✅' if rc == 0 else '❌'}  Stage '{stage}' finished (exit {rc})")
    finally:
        with _run_lock:
            _run_active = False
            _run_proc = None


# ─── routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/status")
def status():
    """Return pipeline queue depths and knowledge item counts."""
    queue_counts = {"queued": 0, "collected": 0, "pending_extract": 0,
                    "extracted": 0, "skipped": 0, "extract_failed": 0}

    if QUEUE_FILE.exists():
        for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                s = entry.get("status", "queued")
                queue_counts[s] = queue_counts.get(s, 0) + 1
            except json.JSONDecodeError:
                continue

    knowledge_counts = {}
    for section in ["tactics", "heuristics", "claims", "niche_patterns"]:
        path = KNOWLEDGE_DIR / f"{section}.jsonl"
        if path.exists():
            knowledge_counts[section] = sum(
                1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        else:
            knowledge_counts[section] = 0

    policy_meta = {}
    if POLICY_FILE.exists():
        try:
            p = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
            policy_meta = {
                "version": p.get("version"),
                "generated_at": p.get("generated_at"),
                "based_on_video_count": p.get("based_on_video_count", 0),
                "boost_rules": len(p.get("boost_rules", [])),
                "penalty_rules": len(p.get("penalty_rules", [])),
            }
        except Exception:
            pass

    with _run_lock:
        running = _run_active
        current_stage = _run_stage

    return jsonify({
        "queue": queue_counts,
        "knowledge": knowledge_counts,
        "policy": policy_meta,
        "run": {"active": running, "stage": current_stage},
    })


@app.route("/api/log")
def get_log():
    """Return the live run log buffer."""
    with _run_lock:
        lines = list(_run_log)
        active = _run_active
        stage = _run_stage
    return jsonify({"lines": lines, "active": active, "stage": stage})


@app.route("/api/run/<stage>", methods=["POST"])
def run_stage(stage):
    """Kick off a pipeline stage; stream output to /api/log."""
    global _run_active
    if stage not in VALID_STAGES:
        return jsonify({"error": f"Unknown stage '{stage}'"}), 400

    with _run_lock:
        if _run_active:
            return jsonify({"error": "A stage is already running"}), 409

    def _count_status(target_status):
        n = 0
        if QUEUE_FILE.exists():
            for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("status") == target_status:
                        n += 1
                except json.JSONDecodeError:
                    continue
        return n

    # Optional extraction-model override from the UI (local Ollama / Ollama cloud / Gemini).
    model = (request.get_json(silent=True) or {}).get("model") or request.args.get("model")
    run_env = os.environ.copy()
    if model:
        run_env["EXTRACT_MODEL"] = model.strip()

    if stage == "extract-parallel":
        max_videos = max(_count_status("pending_extract"), 1)
        cmd = [sys.executable, str(ROOT / "parallel_extract.py"),
               "--workers", "3", "--max", str(max_videos)]
    elif stage == "collect-parallel":
        max_videos = max(_count_status("queued"), 1)
        cmd = [sys.executable, str(ROOT / "parallel_collect.py"),
               "--workers", "3", "--max", str(max_videos)]
    elif stage == "build-taxonomy":
        cmd = [sys.executable, str(ROOT / "build_taxonomy.py")]
    elif stage == "build-frontier":
        cmd = [sys.executable, str(ROOT / "build_frontier_board.py")]
    else:
        mode = "full" if stage == "full" else stage
        cmd = [sys.executable, str(ROOT / "run_agent.py"), "--mode", mode]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,          # line-buffered
        )
        t = threading.Thread(target=_drain, args=(proc, stage), daemon=True)
        t.start()
        return jsonify({"started": True, "stage": stage, "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def stop_run():
    """Kill the currently running pipeline stage."""
    global _run_proc
    with _run_lock:
        proc = _run_proc
    if proc is None:
        return jsonify({"stopped": False, "reason": "nothing running"})
    try:
        proc.terminate()          # SIGTERM — gives it a chance to clean up
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()           # SIGKILL if it didn't exit
        return jsonify({"stopped": True})
    except Exception as e:
        return jsonify({"stopped": False, "reason": str(e)}), 500


@app.route("/api/frontier")
def frontier():
    """Return the frontier board (novelty-ranked clusters in editorial sections)."""
    f = DATA_DIR / "frontier_board.json"
    if not f.exists():
        return jsonify({"sections": {}, "metadata": {}}), 200
    try:
        return jsonify(json.loads(f.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/taxonomy")
def taxonomy():
    """Return the automations taxonomy built by build_taxonomy.py (the core findings view)."""
    f = DATA_DIR / "automations_taxonomy.json"
    if not f.exists():
        return jsonify({"categories": [], "total_videos": 0, "generated_at": None}), 200
    try:
        return jsonify(json.loads(f.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/policy")
def policy():
    """Return the scoring policy JSON."""
    if not POLICY_FILE.exists():
        return jsonify({}), 404
    try:
        return jsonify(json.loads(POLICY_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/knowledge/<section>")
def knowledge_section(section):
    """Return top items from a knowledge section."""
    if section not in {"tactics", "heuristics", "claims", "niche_patterns"}:
        return jsonify({"error": "Invalid section"}), 400

    path = KNOWLEDGE_DIR / f"{section}.jsonl"
    if not path.exists():
        return jsonify([])

    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    items.sort(key=lambda x: -x.get("support_count", 1))
    return jsonify(items[:50])


@app.route("/api/config/search", methods=["GET"])
def get_search_config():
    """Return the current search config (queries + negative_keywords)."""
    if not SEARCH_CONFIG_FILE.exists():
        return jsonify({"queries": [], "negative_keywords": [], "channels": []}), 200
    try:
        return jsonify(json.loads(SEARCH_CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/search", methods=["POST"])
def save_search_config():
    """Validate and atomically write the search config."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    queries = data.get("queries", [])
    negative_keywords = data.get("negative_keywords", [])
    channels = data.get("channels", [])

    if not isinstance(queries, list) or not queries:
        return jsonify({"error": "queries must be a non-empty list"}), 400
    if not isinstance(negative_keywords, list):
        return jsonify({"error": "negative_keywords must be a list"}), 400
    if not all(isinstance(q, str) and q.strip() for q in queries):
        return jsonify({"error": "All queries must be non-empty strings"}), 400

    payload = {
        "_comment": "Edit this file to control what youtube_discover.py searches for.",
        "queries": [q.strip() for q in queries],
        "negative_keywords": [k.strip() for k in negative_keywords if k.strip()],
        "channels": channels,
    }

    try:
        tmp = SEARCH_CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(SEARCH_CONFIG_FILE)
        return jsonify({"saved": True, "query_count": len(payload["queries"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # use_reloader=False: Flask's reloader spawns a child process which
    # inherits the module-level state — turning it off keeps the log buffer
    # in one process and avoids double-starting pipeline runs.
    print("🚀 AI Automation Research Dashboard → http://localhost:5000")
    app.run(debug=True, port=5000, use_reloader=False)
