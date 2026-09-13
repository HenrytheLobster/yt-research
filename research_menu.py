"""Interactive text menu for topic-driven YouTube research runs."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from utils import AgentError, call_llm


ROOT = Path(__file__).parent
CONFIG = ROOT / "config" / "search_config.json"
RAW = ROOT / "data" / "youtube" / "raw"
OUT = ROOT / "data" / "topic_research"
REPORT = ROOT / "reports" / "topic_research_review.md"
LAST_IDS = OUT / "latest_video_ids.json"
LAST_RUN = OUT / "last_run.json"


STEP_CHOICES = {
    "discover": "discover videos",
    "collect": "download transcripts",
    "analyze": "analyze transcripts",
    "report": "write report",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        value = input(f"{text}{suffix}: ").strip()
    except EOFError:
        raise SystemExit("No input available; run this from an interactive terminal.")
    return value if value else (default or "")


def yes_no(text: str, default: bool = False) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        value = prompt(f"{text} ({default_text})").casefold()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter y or n.")


def choose_one(title: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    print(f"\n{title}")
    for index, (_, label) in enumerate(options, 1):
        marker = " default" if index - 1 == default_index else ""
        print(f"  {index}. {label}{marker}")
    while True:
        value = prompt("Choose a number", str(default_index + 1))
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1][0]
        print("Please choose one of the listed numbers.")


def parse_keywords(value: str) -> list[str]:
    if not value.strip():
        return []
    if "\n" in value:
        return [line.strip() for line in value.splitlines() if line.strip()]
    if ";" in value:
        return [part.strip() for part in value.split(";") if part.strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_json_object(text: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("No JSON object found")
    return json.loads(match.group())


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        cleaned = " ".join(item.strip().split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def previous_keywords() -> tuple[list[str], list[str], list[str]]:
    config = load_json(CONFIG, {})
    last_run = load_json(LAST_RUN, {})
    config_queries = config.get("queries", [])
    last_queries = last_run.get("queries", [])
    negative_keywords = last_run.get("negative_keywords") or config.get("negative_keywords", [])
    return config_queries, last_queries, negative_keywords


def print_previous_keywords() -> None:
    config_queries, last_queries, negative_keywords = previous_keywords()
    print("\nPrevious keyword context")
    if last_queries:
        print("  Last run queries:")
        for query in last_queries:
            print(f"    - {query}")
    else:
        print("  Last run queries: none recorded yet")
    if config_queries:
        print("  Config queries:")
        for query in config_queries:
            print(f"    - {query}")
    else:
        print("  Config queries: none")
    if negative_keywords:
        print("  Negative keywords:")
        print(f"    {', '.join(negative_keywords)}")


def transcript_dirs() -> list[Path]:
    if not RAW.exists():
        return []
    return sorted(path for path in RAW.iterdir() if path.is_dir())


def delete_previous_artifacts() -> None:
    for folder in transcript_dirs():
        shutil.rmtree(folder)
    if OUT.exists():
        shutil.rmtree(OUT)
    if REPORT.exists():
        REPORT.unlink()
    print("Deleted prior transcript folders, analysis cache, run markers, and report.")


def detect_ollama_models() -> list[str]:
    if shutil.which("ollama") is None:
        return []
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    models = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        lowered = name.casefold()
        if "embedding" in lowered or lowered.endswith(":cloud"):
            continue
        models.append(name)
    preferred = ["gemma4:12b", "gemma4:26b", "gemma4:12b-mlx", "qwen3:8b", "qwen3:8b-q4_K_M"]
    ordered = [name for name in preferred if name in models]
    ordered.extend(name for name in models if name not in ordered)
    return ordered


def model_options() -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for model in detect_ollama_models():
        options.append((model, f"Ollama local: {model}"))
    if shutil.which("claude"):
        options.append(("claude:haiku", "Claude CLI: haiku"))
    if shutil.which("codex"):
        options.append(("codex:gpt-5.6-terra", "Codex CLI: gpt-5.6-terra"))
    if not options:
        options.append(("gemma4:12b", "Ollama local: gemma4:12b"))
    return options


def choose_steps() -> list[str]:
    options = [
        ("full", "full run: discover, download transcripts, analyze, report"),
        ("collect", "discover and download transcripts only"),
        ("analyze", "analyze existing transcripts and write report"),
        ("report", "write report from cached analysis"),
        ("custom", "choose individual steps"),
    ]
    choice = choose_one("Run scope", options)
    if choice == "full":
        return ["discover", "collect", "analyze", "report"]
    if choice == "collect":
        return ["discover", "collect"]
    if choice == "analyze":
        return ["analyze", "report"]
    if choice == "report":
        return ["report"]
    print("\nChoose steps")
    selected = []
    for key, label in STEP_CHOICES.items():
        if yes_no(f"  Include {label}?", default=True):
            selected.append(key)
    return selected


def choose_count() -> int:
    while True:
        value = prompt("How many videos? Use 8 for a test run or 2000 for a full run", "8")
        try:
            count = int(value)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if count > 0:
            return count
        print("Please enter a positive number.")


def choose_timeout() -> int:
    while True:
        value = prompt("Seconds to allow each analysis segment before skipping that video", "900")
        try:
            timeout = int(value)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if timeout > 0:
            return timeout
        print("Please enter a positive number.")


def generate_keyword_ideas(model: str, negative_keywords: list[str]) -> list[str]:
    config_topic = load_json(CONFIG, {}).get("topic", "")
    seed = prompt("Seed topic for keyword ideas", config_topic or None)
    prompt_text = f"""Generate YouTube search queries for a transcript research run.
The goal is to find useful videos about: {seed}

Prefer queries that surface case studies, real examples, strategy breakdowns, implementation details, risks, and current best practices.
Avoid generic beginner courses, entertainment videos, podcasts, and sales funnels.
Do not include negative keyword syntax in the query strings; these filters will be appended separately:
{", ".join(negative_keywords)}

Return ONLY JSON in this shape:
{{"queries":["query one","query two"]}}

Give 8 to 12 concise search queries."""
    print(f"\nGenerating keyword ideas with {model}...")
    response = call_llm(
        prompt_text,
        model=model,
        timeout=180,
        retries=0,
        options={"temperature": 0.4, "num_ctx": 4096},
    )
    data = parse_json_object(response)
    queries = data.get("queries", [])
    if not isinstance(queries, list):
        raise ValueError("Generated JSON did not contain a queries list")
    return dedupe([str(query) for query in queries])[:12]


def choose_keywords(model: str, negative_keywords: list[str]) -> list[str]:
    config_queries, last_queries, _ = previous_keywords()
    default_queries = last_queries or config_queries

    mode = choose_one("Keyword setup", [
        ("manual", "enter keywords manually"),
        ("generate", "generate keyword ideas with the selected model"),
        ("generate_add", "generate ideas, then add manual keywords"),
    ], default_index=0)

    generated: list[str] = []
    if mode in {"generate", "generate_add"}:
        try:
            generated = generate_keyword_ideas(model, negative_keywords)
        except (AgentError, ValueError, json.JSONDecodeError) as exc:
            print(f"Keyword generation failed: {exc}")
            generated = []
        if generated:
            print("\nGenerated keyword ideas")
            for index, query in enumerate(generated, 1):
                print(f"  {index}. {query}")
            if not yes_no("Use these generated keywords?", default=True):
                generated = []

    if mode == "generate" and generated:
        return generated

    print("\nEnter positive keyword queries for this run.")
    print("Use commas or semicolons to separate multiple queries.")
    default_text = "; ".join(default_queries) if default_queries else None
    while True:
        queries = parse_keywords(prompt("Keywords", default_text))
        queries = dedupe(generated + queries)
        if queries:
            return queries
        print("Please enter at least one keyword query.")


def build_command(
    model: str,
    steps: list[str],
    count: int,
    queries: list[str],
    negatives: list[str],
    analysis_timeout: int = 900,
) -> list[str]:
    cmd = [sys.executable, "-u", str(ROOT / "research_run.py")]
    for step in steps:
        cmd.append(f"--{step}")
    if not any(step in steps for step in ["discover", "collect"]):
        cmd.append("--use-latest-ids")
    cmd += ["--model", model]
    if "analyze" in steps:
        cmd += ["--analysis-timeout", str(analysis_timeout)]
    if count:
        cmd += ["--max-videos", str(count)]
    if "discover" in steps:
        max_per_query = max(10, min(100, count))
        cmd += ["--max-per-query", str(max_per_query)]
        for query in queries:
            cmd += ["--query", query]
        for keyword in negatives:
            cmd += ["--negative-keyword", keyword]
    return cmd


def main() -> None:
    print("YouTube topic research setup")
    print_previous_keywords()

    dirs = transcript_dirs()
    if dirs or OUT.exists() or REPORT.exists():
        print(f"\nPrior artifacts: {len(dirs)} transcript folder(s), analysis cache/report may exist.")
        if yes_no("Delete previous run artifacts before continuing?", default=False):
            delete_previous_artifacts()

    models = model_options()
    default_index = next((i for i, (value, _) in enumerate(models) if value == "gemma4:12b"), 0)
    model = choose_one("Analysis model", models, default_index=default_index)
    steps = choose_steps()
    if not steps:
        raise SystemExit("No steps selected.")
    count = choose_count() if any(step in steps for step in ["discover", "collect", "analyze"]) else 0
    analysis_timeout = choose_timeout() if "analyze" in steps else 900
    _, _, negatives = previous_keywords()
    queries = choose_keywords(model, negatives) if "discover" in steps else []

    print("\nStarting run")
    print(f"  Steps: {', '.join(STEP_CHOICES[step] for step in steps)}")
    print(f"  Model: {model}")
    if count:
        print(f"  Max videos: {count}")
    if "analyze" in steps:
        print(f"  Analysis timeout: {analysis_timeout}s per segment")
    if queries:
        print(f"  Queries: {'; '.join(queries)}")
    if negatives and "discover" in steps:
        print(f"  Negative keywords: {', '.join(negatives)}")
    print("")

    cmd = build_command(model, steps, count, queries, negatives, analysis_timeout)
    completed = subprocess.run(cmd, cwd=ROOT)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
