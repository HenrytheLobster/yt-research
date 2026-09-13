"""
frontier_to_reddit.py — turn frontier_board.json into a paste-ready Reddit post.

Usage:
    python frontier_to_reddit.py                      # uses data/frontier_board.json
    python frontier_to_reddit.py path/to/board.json   # any board file
    python frontier_to_reddit.py --top 30             # how many rows in the table

Writes reports/frontier_reddit.md (Markdown table — renders on new Reddit) and also
prints a fixed-width version (works on old Reddit / any client) to the console.
"""
import json, sys, argparse
from pathlib import Path

ROOT = Path(__file__).parent

def clean(s, n=80):
    s = (str(s or "")).replace("|", "/").replace("\n", " ").strip()
    return s[:n]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(ROOT / "data" / "frontier_board.json"))
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    board = json.loads(Path(args.path).read_text(encoding="utf-8"))
    clusters = board.get("clusters") or []
    md = board.get("metadata", {})

    # rank by frontier score; keep the genuinely-interesting tiers near the top
    clusters = sorted(clusters, key=lambda c: -c.get("frontier_score", 0))[:args.top]

    rows = []
    for c in clusters:
        rows.append((
            clean(c.get("cluster_name"), 60),
            clean(c.get("cluster_type"), 28),
            clean(c.get("evidence_class"), 10),
            str(c.get("support_videos", 0)),
            clean(", ".join(c.get("tools", [])[:4]), 45),
        ))

    # ---- Markdown table (new Reddit) ----
    out = []
    out.append(f"*Analysis of {md.get('video_count','?')} YouTube automation videos "
               f"({md.get('item_count','?')} extracted workflows). "
               f"Ranked by a frontier score: novelty + how specific it is + whether a real "
               f"operator (not a guru) was shown running it.*\n")
    out.append("| # | Automation | Tier | Evidence | Videos | Main tools |")
    out.append("|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |")
    md_text = "\n".join(out)

    rep = ROOT / "reports"
    rep.mkdir(exist_ok=True)
    (rep / "frontier_reddit.md").write_text(md_text, encoding="utf-8")

    # ---- Fixed-width (old Reddit / code block) ----
    print(md_text)
    print("\n\n----- fixed-width version (paste inside a code block) -----\n")
    w = [3, 52, 26, 9, 6, 40]
    hdr = ["#", "Automation", "Tier", "Evidence", "Vids", "Tools"]
    line = "  ".join(h.ljust(w[i]) for i, h in enumerate(hdr))
    print(line); print("-" * len(line))
    for i, r in enumerate(rows, 1):
        cells = [str(i), r[0], r[1], r[2], r[3], r[4]]
        print("  ".join(cells[j].ljust(w[j])[:w[j]] for j in range(len(cells))))
    print(f"\nWrote {rep / 'frontier_reddit.md'}")

if __name__ == "__main__":
    main()
