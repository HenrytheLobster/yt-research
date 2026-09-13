"""
Overnight research runner.

Uses separate limits for discovery and processing so we can build a large queue
without accidentally asking YouTube for thousands of results per query.
"""

import argparse
import os
import shutil
from types import SimpleNamespace
from pathlib import Path

import run_agent


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


def main():
    parser = argparse.ArgumentParser(description="Run the research pipeline overnight")
    parser.add_argument("--discover-max", type=int, default=40,
                        help="YouTube results to inspect per configured query")
    parser.add_argument("--process-max", type=int, default=2000,
                        help="Max videos to process in collect/triage/extract stages")
    parser.add_argument("--collect-workers", type=int, default=3,
                        help="Transcript collection workers; 3 is usually best for YouTube")
    parser.add_argument("--triage-workers", type=int, default=6,
                        help="Ollama triage workers")
    parser.add_argument("--extract-workers", type=int, default=4,
                        help="Extraction workers; Grok CLI can usually handle more than local Ollama")
    parser.add_argument("--extract-model", default=os.environ.get("EXTRACT_MODEL", "grok"),
                        help="Use grok, a grok-* model id, a Gemini model, or a local Ollama model")
    parser.add_argument("--fresh", action="store_true",
                        help="Clear queue, transcripts, extractions, knowledge, and quarantine before running")
    parser.add_argument("--topic", help="Plain-language research topic for triage/extraction prompts")
    args = parser.parse_args()

    os.environ["EXTRACT_MODEL"] = args.extract_model
    if args.fresh:
        clear_research_state()

    print("Overnight full-pipeline run")
    print(f"  discover-max: {args.discover_max} per query")
    print(f"  process-max:  {args.process_max} videos per stage")
    print(f"  collect:      {args.collect_workers} workers")
    print(f"  triage:       {args.triage_workers} workers")
    print(f"  extract:      {args.extract_workers} workers via {args.extract_model}")

    discover_args = SimpleNamespace(
        query=None,
        max=args.discover_max,
        workers=args.collect_workers,
        dry_run=False,
    )
    process_args = SimpleNamespace(
        query=None,
        max=args.process_max,
        workers=args.collect_workers,
        dry_run=False,
        reprocess=None,
        reset=False,
        include_low=False,
        triage_workers=args.triage_workers,
        topic=args.topic,
    )
    extract_args = SimpleNamespace(**vars(process_args))
    extract_args.workers = args.extract_workers

    print("\nStage 1: Discover")
    run_agent.run_discover(discover_args)

    print("\nStage 2: Collect")
    run_agent.run_collect(process_args)

    print("\nStage 3: Triage")
    run_agent.run_triage(process_args)

    print("\nStage 4: Extract")
    run_agent.run_extract(extract_args)

    print("\nStage 5: Merge")
    run_agent.run_merge(process_args)

    print("\nStage 6: Policy")
    run_agent.run_policy(process_args)

    print("\nStage 7: Report")
    report_file = run_agent.run_report()
    print(f"\nDone. Report: {report_file}")
    run_agent.run_status()


if __name__ == "__main__":
    main()
