#!/usr/bin/env python3
"""Format automations_taxonomy.json into Reddit markdown tables."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
TAXONOMY_FILE = ROOT / "data" / "automations_taxonomy.json"

# Load with error recovery (truncation safety)
with open(TAXONOMY_FILE, encoding="utf-8", errors="ignore") as f:
    raw = f.read().split("\x00")[0]
    raw = raw[:raw.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group()) if m else {}

print("# Automation Categories")
print("*Data from {} videos, model: {}*\n".format(data['total_videos'], data['model']))

# Categories table
print("## By Category (Video Mentions)")
print("| Category | Videos | Top Tools |")
print("|----------|--------|-----------|")

for cat in data["categories"]:
    tools = ", ".join(t["tool"] for t in cat["top_tools"][:3])
    print("| {} | {} | {} |".format(cat['name'], cat['video_count'], tools))

print()

# Verticals table
print("## By Vertical (Industry)")
print("| Vertical | Videos |")
print("|----------|--------|")

if "verticals" in data:
    for vert in data["verticals"][:30]:  # top 30
        print("| {} | {} |".format(vert['name'], vert['video_count']))
