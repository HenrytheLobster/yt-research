PASTE THIS INTO A FRESH COWORK AGENT ON THE MAC MINI
(after you've copied the yt-research folder onto the Mac and given the agent access to it)
=========================================================================================

I've copied a Python project called `yt-research` onto this Mac mini. It's a YouTube
research pipeline (discover → collect → triage → extract → merge) that uses local Ollama
models to extract structured "automation" knowledge from YouTube transcripts. My Windows
machine is already running one corpus; I want this Mac mini to run a SECOND, INDEPENDENT
corpus in parallel with a different set of search queries.

Please set it up and start it:

1. Start the data fresh so this is a clean, separate corpus (don't reuse any existing data):
   wipe and recreate data/queue, data/youtube/raw, data/youtube/extracted,
   data/quarantine, data/knowledge, and create an empty data/queue/seen_video_ids.txt.

2. Install deps: `python3 -m pip install --upgrade yt-dlp requests flask portalocker`.
   Confirm Ollama is installed and pull `qwen3:8b` and `phi3:mini`. Set
   OLLAMA_NUM_PARALLEL=2 (launchctl setenv, then restart Ollama).

3. Replace `config/search_config.json` with `mac-mini-setup/search_config_frontier.json`
   (the frontier/discovery query set already in the project).

4. Run the full pipeline with TWO workers (important — this Mac shares my home IP with the
   Windows box and YouTube rate-limits by IP, so keep it at 2):
   `python3 run_agent.py --mode full --workers 2`

5. Watch for throttling: if the log fills with `no_transcript` or HTTP 429, drop to
   `--workers 1`.

Context on the goal (so you understand what we're doing): the Windows machine runs an
"operator / named-vertical" corpus. This Mac runs a "discovery + frontier" corpus — generic
and task-based queries to surface NEW business verticals we haven't thought of, plus
guru/novel-automation queries to surface weird/innovative builds. The extraction schema has
a `first_party` flag that separates "owner actually running it" from "guru pitching it" —
that's how we'll keep the guru noise useful instead of misleading. When this finishes I'll
copy this machine's `data/youtube/extracted/*.json` back to Windows and merge both corpora.

Read the project's `SETUP_MAC_MINI.md` and `README.md` first if you need more detail, then
proceed.
