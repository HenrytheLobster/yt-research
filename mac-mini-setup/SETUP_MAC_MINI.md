# Mac mini — Frontier/Discovery Corpus Setup

Run a **second, independent corpus** on the Mac mini in parallel with the Windows run.
Windows = operator/vertical corpus (what's deployed). Mac mini = **discovery + frontier**
corpus (new verticals via generic/task queries, novel automations via guru/frontier queries).

They must stay **fully independent** — separate machines, separate local `data/` folders.
Never point both at one shared network folder (the queue lock only works within one machine
and they'd corrupt each other). Merge at the end by copying extracted JSON.

---

## 1. Copy the project over
Copy the whole `yt-research` folder to the Mac mini (USB, git, or `scp`), then **start its
data fresh** so it's a clean separate corpus:

```bash
cd ~/yt-research
rm -rf data/queue data/youtube data/quarantine data/knowledge
mkdir -p data/queue data/youtube/raw data/youtube/extracted data/quarantine data/knowledge
: > data/queue/seen_video_ids.txt
```

## 2. Install dependencies
```bash
python3 -m pip install --upgrade yt-dlp requests flask portalocker
```

Install Ollama (https://ollama.com/download), then pull the models:
```bash
ollama pull qwen3:8b
ollama pull phi3:mini
```

## 3. Set Ollama concurrency (Apple Silicon)
```bash
launchctl setenv OLLAMA_NUM_PARALLEL 2
```
Then quit and reopen the Ollama app so it picks up the setting. (2 is safe on a Mac mini's
unified memory unless it's 32GB+, in which case you can try 3.)

## 4. Use the frontier query config
Replace the search config with the one in this folder:
```bash
cp mac-mini-setup/search_config_frontier.json config/search_config.json
```

## 5. Run it — IMPORTANT: 2 collect workers, not 3
Both machines share your home IP, and **YouTube rate-limits by IP**. Keep this machine at
**2 collect workers** so the two boxes combined stay under YouTube's ceiling:

```bash
python3 run_agent.py --mode full --workers 2
```

(Or use the dashboard: `python3 app.py` → http://localhost:5000 → Full Run. But the dashboard
buttons default to 3 workers, so the terminal command above is the safer way to pin it at 2.)

If you see a wave of `no_transcript` / HTTP 429 in the log, that's throttling — drop to
`--workers 1` here and let Windows keep its 3.

---

## 6. Merge the two corpora (back on Windows, when both finish)
The durable artifact is the per-video extraction JSON. Copy this machine's results over:

```bash
# from the Mac mini:
scp data/youtube/extracted/*.json  <windows>:/path/to/yt-research/data/youtube/extracted/
```

Then on Windows, rebuild the combined view:
```bash
python build_taxonomy.py        # taxonomy dashboard now reflects BOTH corpora
```

That's enough for the taxonomy + vertical scan (they read `extracted/` directly). The
`first_party` flag in each extraction lets you separate guru-pitched from owner-deployed when
you analyze the frontier results.
