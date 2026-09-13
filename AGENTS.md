# YouTube Research Agent — Handoff Notes

This repo is a generic YouTube research tool: point it at a topic and it
discovers videos, filters them, and extracts sourced findings on that topic.
It is not built around any one subject — it has been repurposed several times
(originally KDP/Kindle topic research, then cold-call/lead-gen research for
Signal17, then AI-automation research) and each time the domain got baked
into the code as hardcoded prompts and keyword lists. That's been undone:
the topic now lives in `config/search_config.json` (or `--topic`), and the
triage/extraction prompts are templated with it instead.

See `README.md` for the two ways to run it (the interactive one-shot launcher
vs. the full pipeline with a persistent knowledge base) and for which files
are legacy/domain-specific leftovers rather than part of the generic tool.

## Topic Configuration

- `config/search_config.json`'s `topic` + `queries` fields describe what
  you're researching. Leave `queries` empty and enter them per-run via
  `research_menu.py` for ad hoc topics, or fill them in for a standing
  research program run via `run_agent.py`.
- `triage.py` derives its cheap pre-filter keywords from those queries at
  run time — there's no hardcoded domain keyword list anymore. If no queries
  are configured, it skips the keyword gate and lets the LLM triage call
  decide.
- `extract.py` and `research_run.py` template their extraction prompts with
  the resolved topic (`utils.resolve_topic()`), instead of a hardcoded
  subject.
- `run_agent.py` writes the same Markdown report structure as before; the
  title is now generic ("YouTube Research Agent — Daily Report").

## Machine Notes (Windows box)

Use the full Windows Python path unless PATH has been fixed:

```powershell
C:\Users\naylo\AppData\Local\Programs\Python\Python313\python.exe
```

Ollama is expected at `http://localhost:11434`. Known installed local models:

```text
phi3:mini
qwen3:8b
qwen2.5:3b
gemma4:12b
gemma4-brain:latest
nomic-embed-text:latest
```

Grok CLI is installed at:

```text
C:\Users\naylo\.grok\bin\grok.exe
```

Grok may need to run under the real Windows user session. If it fails from a
sandboxed account, run the batch file or command from the authorized user
context.

## Model Split

- `yt-dlp`: YouTube search, metadata, subtitles, and transcripts.
- Ollama `phi3:mini`: fast local triage/filtering.
- `utils.call_llm()` routes extraction to whichever provider the model name
  implies: Ollama by default, or `grok`/`grok-*` (Grok CLI), `gemini*`
  (direct Gemini API), `claude:*` (Claude Code CLI), `codex:*` (Codex CLI).
  Set `EXTRACT_MODEL` (or pass `--extract-model` to the overnight runners) to
  pick one; a fast cloud/CLI model is usually far quicker than local GPU for
  extraction, leaving Ollama free for triage.
- Local code: merge, policy, and Markdown report generation.

## Main Run Commands

Interactive, one-shot, any topic:

```powershell
.\research
```

Full pipeline, scheduled/overnight:

```powershell
.\run_youtube_agent.bat
```

which runs:

```powershell
C:\Users\naylo\AppData\Local\Programs\Python\Python313\python.exe streaming_overnight_run.py --hours 18 --discover-max 40 --process-max 2000 --collect-workers 3 --triage-workers 6 --extract-workers 6 --extract-model grok --fresh
```

`--fresh` clears the old queue, raw transcripts, extracted files, knowledge
base, and quarantine before starting. Use it when switching topics or
starting a clean corpus. For continuing an existing queue without clearing
it, remove `--fresh`.

Do not use `run_agent.py --mode full --max 2000` for large crawls, because
that requests too many results per query. Use `streaming_overnight_run.py`
so discovery and processing limits stay separate.

## Parallelism

- Collection: `3` workers by default, to avoid pushing YouTube too hard.
- Triage: worth running with several workers (`parallel_triage.py`) on a
  machine with a fast GPU but tighter RAM — that's what this was originally
  built for on this Windows box, running several local Ollama triage calls
  concurrently while extraction runs separately via a cloud/CLI model.
- Extraction: parallel workers help most when using a cloud/CLI model
  (Grok, Gemini, Claude, Codex) rather than local Ollama.
- Discovery: `--discover-max 40` per query is a reasonable default.
- Processing: `--process-max 2000` per queue-draining cycle.

## Output

Reports are written to:

```text
reports/report_YYYY-MM-DD_HH-MM-SS.md          ← full pipeline
reports/topic_research_review.md                ← one-shot launcher
```

The full-pipeline report keeps these sections: Pipeline Status, Knowledge
Base, Tactics, Heuristics, Claims, Niche Patterns, Scoring Policy, Top
Tactics, Top Niche Patterns, Rule Telemetry.

## Things To Watch

- YouTube may return bot/cookie challenges during discovery. Optional
  browser-cookie support for `yt-dlp` is still a future improvement.
- Cloud/CLI model failures (Grok, Gemini, Claude, Codex) can happen on a
  chunk; add retry/backoff around a specific provider's calls in `utils.py`
  if repeated large runs show too many failures for that provider.
- Old report files remain in `reports/`; they are historical and don't
  affect a fresh run.
- See `README.md`'s "Legacy / not part of the generic tool" section for
  files that are kept but not actively developed (Reddit collection, the
  cold-call/TAM analysis scripts, the automation-opportunity cataloging
  tools, the KDP pipeline integration hook).
