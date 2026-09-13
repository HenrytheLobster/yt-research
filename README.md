# YouTube Research Agent

A generic YouTube research tool: give it a topic, and it discovers relevant
videos, downloads transcripts, filters out junk, and extracts sourced,
concrete findings on that topic — not tied to any one subject.

There are two ways to run it:

1. **The interactive launcher** (`./research` or `research_menu.py`) — the
   simplest way in. It asks what you want researched, picks a model, and runs
   discover → collect → analyze → report as one pass. Good for "research this
   topic right now."
2. **The full pipeline** (`run_agent.py`) — an ongoing research program that
   builds a persistent knowledge base and scoring policy across many runs
   over time (discover → collect → triage → extract → merge → policy →
   report). Good for "keep researching this topic continuously."

Both pull the research topic from the same place: `config/search_config.json`
(its `topic` and `queries` fields), an explicit `--topic`/`--query` flag, or
the `RESEARCH_TOPIC` environment variable. Nothing about the extraction or
triage logic is hardcoded to a subject — set the topic and it researches
that.

---

## Quick start: the interactive launcher

```bash
./research
```

The menu shows the last run's keywords and the saved config keywords, offers
to delete prior transcripts/cache/report files, detects local Ollama models
and low-cost CLI models, then runs the selected steps. It can also ask the
selected model to generate keyword ideas from a seed topic. Keywords entered
in the menu apply only to that run; broad negative keywords (junk formats to
always skip) still come from `config/search_config.json`.

For automation, the worker script accepts direct flags:

```bash
.venv/bin/python -u research_run.py --discover --collect --analyze --report \
    --model gemma4:12b --max-videos 8 \
    --query "your topic here" --topic "your topic here"
```

The run prints progress during each model segment and can be resumed after an
interruption; completed segments are cached in `data/topic_research/`.
Collected transcripts are stored in `data/youtube/raw/`, and the
source-quoted review is written to `reports/topic_research_review.md`.

---

## Quick start: the full pipeline

```
DISCOVER → COLLECT → TRIAGE → EXTRACT → MERGE → POLICY → REPORT
   │           │         │        │         │        │
   │           │         │        │         │        └─ config/scoring_policy.json
   │           │         │        │         └────────── data/knowledge/*.jsonl
   │           │         │        └──────────────────── data/youtube/extracted/
   │           │         └───────────────────────────── triage.json per video
   │           └─────────────────────────────────────── data/youtube/raw/<id>/
   └─────────────────────────────────────────────────── data/queue/pending.jsonl
```

```bash
# Set the topic once in config/search_config.json ("topic" + "queries"),
# or pass --topic / --query on the command line each time.
python run_agent.py --mode full
python run_agent.py --mode full --topic "AI automation tools for small businesses"
```

Individual stages:

```bash
python run_agent.py --mode discover          # find new videos
python run_agent.py --mode collect --max 5   # download 5 transcripts
python run_agent.py --mode collect --channel https://www.youtube.com/@somechannel
python run_agent.py --mode triage             # filter with phi3:mini
python run_agent.py --mode extract            # extract knowledge
python run_agent.py --mode merge               # merge into knowledge base
python run_agent.py --mode policy              # regenerate scoring policy
python run_agent.py --mode report              # generate markdown report
python run_agent.py --mode status              # see pipeline state
```

### Parallel / overnight runs

Two runners keep multiple stage workers busy at once (worth tuning up or down
depending on your machine's GPU/RAM balance):

```bash
python streaming_overnight_run.py --hours 18 --discover-max 40 --process-max 2000 \
    --collect-workers 3 --triage-workers 6 --extract-workers 6 --topic "your topic"

python overnight_newsletter_run.py --discover-max 40 --process-max 2000 \
    --collect-workers 3 --triage-workers 6 --extract-workers 4 --topic "your topic"
```

`run_youtube_agent.bat` wraps `streaming_overnight_run.py` for Windows Task
Scheduler — edit the topic/queries in `config/search_config.json` before a
scheduled run picks them up.

---

## Setup

### 1. Install dependencies

```bash
pip install yt-dlp requests portalocker
```

### 2. Pull Ollama models (if using local models)

```bash
ollama pull qwen3:8b
ollama pull phi3:mini
ollama serve
```

### 3. Configure optional credentials

Copy `.env.example` to `.env` if you want to use Gemini, Claude CLI, Codex
CLI, or Grok CLI instead of local Ollama, or Reddit collection (legacy, see
below). Keep `.env` local; it is intentionally ignored by Git.

### 4. Set what to research

Edit `config/search_config.json`:

```json
{
  "topic": "a one-line description of what you're researching",
  "queries": ["search phrase one", "search phrase two"],
  "negative_keywords": ["shorts", "gaming", "..."],
  "channels": []
}
```

Or use `manage_search.py` to edit it from the CLI, or just pass `--topic` /
`--query` on any command above for a one-off run without touching the file.

---

## Models used

| Stage    | Default model | Notes |
|----------|----------------|-------|
| Triage   | `phi3:mini`    | Fast, cheap, filters junk |
| Extract  | `qwen3:8b`     | Structured extraction; also supports `grok`, `gemini-*`, `claude:*`, `codex:*` via `LLM_PROVIDER` / model-name prefix (see `utils.call_llm`) |

---

## File structure

```
run_agent.py             ← full-pipeline orchestrator (discover..report, knowledge base + policy)
research_menu.py         ← interactive launcher for the simpler one-shot pipeline
research_run.py          ← the one-shot pipeline itself (discover/collect/analyze/report)
research                 ← shell wrapper: ./research runs research_menu.py
youtube_discover.py      ← finds new videos (queries + channels)
youtube_collect.py       ← downloads transcripts (also: direct --channel collect)
triage.py                ← cheap pre-filter before expensive extraction
extract.py               ← structured knowledge extraction (topic-templated prompt)
merge.py                 ← knowledge base builder (full pipeline only)
policy.py                ← scoring policy generator (full pipeline only)
build_taxonomy.py        ← emergent (no hardcoded categories) taxonomy of what showed up
utils.py                 ← shared queue/lock/retry/LLM-provider helpers

parallel_collect.py      ← worker-pool variants of the stages above, for machines
parallel_triage.py         where running several LLM calls at once helps (e.g. a
parallel_extract.py        fast-GPU/tighter-RAM box)
streaming_overnight_run.py  ← time-boxed overnight runner, cycles stages until done
overnight_newsletter_run.py ← simpler single-pass overnight runner (misnamed historically —
                               nothing to do with newsletters, just an older overnight script)

config/
├── search_config.json    ← topic, queries, negative keywords, channels
└── scoring_policy.json   ← generated, not hand-edited

data/
├── queue/pending.jsonl
├── youtube/raw/<id>/{meta.json,transcript.txt,triage.json}
├── youtube/extracted/<id>.json
├── topic_research/       ← one-shot pipeline's cache
└── knowledge/*.jsonl     ← full pipeline's knowledge base

reports/                  ← markdown output from both pipelines
```

### Legacy / not part of the generic tool

These files exist from when this tool was built for narrower purposes and are
left in place but not actively developed — safe to ignore, and safe to delete
if you want a cleaner tree:

- **Reddit collection** (never really worked): `reddit_*.py`,
  `REDDIT_TAXONOMY.md`, `format_taxonomy_for_reddit.py`, `frontier_to_reddit.py`,
  the `--mode reddit-*` options in `run_agent.py`.
- **Cold-call/lead-gen specific analysis**: `count_local_tam.py`,
  `tam_debug.py`, `synthesize_list_building.py`, `search_sales_corpus.py`,
  `channel_sales_analysis.py`.
- **AI-automation-opportunity cataloging** (Signal17-specific, imposes a
  fixed "automations" lens rather than a topic-agnostic one):
  `build_frontier_board.py`, `report_automations.py`.
- **KDP pipeline integration** (hooks into a separate, external Kindle
  topic-discovery pipeline): `pipeline_hooks.py`.

---

## Recommended daily limits

To avoid rate limits and keep processing manageable:

| Setting        | Recommended |
|----------------|-------------|
| Videos/day     | 10–20 (ad hoc) / higher for overnight runs |
| Extract batch  | 5–10 |
| Queries        | 10–12 |
