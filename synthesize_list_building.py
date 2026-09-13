"""
Create a second-pass Markdown synthesis for the phone-list research corpus.

This reads the merged knowledge base and writes a practical call-list playbook
without changing queue state.

Usage:
    python synthesize_list_building.py
"""

import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parent
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
REPORTS_DIR = ROOT / "reports"


CATEGORY_KEYWORDS = {
    "Google Maps and directories": [
        "google maps", "maps", "bbb", "yellow pages", "yelp", "directory",
        "google business", "business profile",
    ],
    "Owner and decision-maker lookup": [
        "owner", "founder", "ceo", "decision maker", "linkedin", "sales navigator",
        "apollo", "chrome extension",
    ],
    "Phone and data enrichment": [
        "phone", "enrich", "enrichment", "verified", "email finder",
        "skip tracing", "find people", "csv",
    ],
    "Qualification signals": [
        "revenue", "headcount", "employee", "hiring", "growth", "reviews",
        "rating", "capacity", "job post",
    ],
    "SEO, web, and AI triggers": [
        "seo", "website", "chat widget", "missed call", "text-back",
        "ai receptionist", "automation", "google maps", "ranking",
    ],
    "Tools and automation": [
        "apify", "instant data scraper", "phantom", "clay", "gohighlevel",
        "leadfinder", "n8n", "instantly", "closely", "give leads",
    ],
}


def load_jsonl(name: str) -> list[dict]:
    path = KNOWLEDGE_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


def text_for(item: dict) -> str:
    return " ".join(str(item.get(k, "")) for k in (
        "name", "description", "statement", "pattern_template", "why_it_works",
        "business_type", "result_claim",
    )).lower()


def label_for(item: dict) -> str:
    return item.get("name") or item.get("pattern_template") or item.get("statement") or "Untitled"


def desc_for(item: dict) -> str:
    return item.get("description") or item.get("why_it_works") or item.get("statement") or ""


def top(items: list[dict], n: int) -> list[dict]:
    return sorted(items, key=lambda x: (-x.get("support_count", 1), label_for(x).lower()))[:n]


def top_for_category(items: list[dict], keywords: list[str], n: int = 8) -> list[dict]:
    matches = [item for item in items if any(k in text_for(item) for k in keywords)]
    return top(matches, n)


def bullet(item: dict) -> str:
    label = label_for(item)
    desc = desc_for(item).strip()
    support = item.get("support_count", 1)
    if len(desc) > 220:
        desc = desc[:217].rstrip() + "..."
    return f"- **[{support}x] {label}**: {desc}"


def main():
    tactics = load_jsonl("tactics")
    patterns = load_jsonl("niche_patterns")
    claims = load_jsonl("claims")
    all_items = tactics + patterns + claims

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = REPORTS_DIR / f"list_building_second_pass_{stamp}.md"

    lines = [
        "# Phone List Building - Second-Pass Synthesis",
        f"**Generated:** {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Executive Summary",
        "",
        "The strongest repeatable path is to start with public local-business data, add owner or decision-maker context, then qualify each lead with visible buying signals before dialing.",
        "",
        "Best default workflow:",
        "",
        "```text",
        "niche + city search -> scrape/export listings -> enrich owner/contact data -> flag visible pain -> filter for size/capacity -> call with a trigger-specific opener",
        "```",
        "",
        "For a 1-5M owner-operated target, the best signals are not perfect revenue estimates. Use practical proxies: review volume, headcount, multiple locations, hiring activity, visible ad spend, website maturity, service pricing, and whether the owner still appears active in the business.",
        "",
        "## Top Repeated Findings",
        "",
    ]

    for item in top(tactics, 12):
        lines.append(bullet(item))

    lines += ["", "## Category Breakdown", ""]
    for category, keywords in CATEGORY_KEYWORDS.items():
        lines += [f"### {category}", ""]
        matches = top_for_category(all_items, keywords, n=8)
        if matches:
            lines.extend(bullet(item) for item in matches)
        else:
            lines.append("- No strong repeated item found.")
        lines.append("")

    lines += [
        "## Recommended Prospecting Workflow",
        "",
        "1. Pick one sellable niche and one metro area.",
        "2. Search Google Maps for `niche + city`, `niche near me`, and adjacent service keywords.",
        "3. Export name, phone, website, address, rating, review count, and category.",
        "4. Add trigger fields: no website, bad website, weak reviews, low review count, no chat widget, missing/weak Google profile, slow site, or likely missed-call problem.",
        "5. Find the owner or decision maker through the website, Google search, LinkedIn, Apollo, or Sales Navigator.",
        "6. Enrich phone/email only after the business passes the trigger check.",
        "7. Filter for likely 1-5M revenue using proxies: 3-50 employees, multiple trucks/chairs/locations, steady review volume, hiring posts, high-ticket services, and active paid/local marketing.",
        "8. Call with the specific trigger, not a generic service pitch.",
        "",
        "## Best Target Signals For Your ICP",
        "",
        "- Owner-operated local service business.",
        "- Enough volume or ticket size for SEO/automation to matter.",
        "- Owner name is findable.",
        "- Website exists but looks weak, or no website is attached to Google Maps.",
        "- Google Business Profile has reviews but weak optimization, low response rate, missing categories, or inconsistent listing data.",
        "- Business is hiring reception/front desk/admin help.",
        "- Business depends on inbound phone calls.",
        "- Business has signs of growth but still looks operationally manual.",
        "",
        "## Call Hooks To Attach To The List",
        "",
        "```text",
        "I found you on Google Maps for [service] in [city] and noticed [specific trigger].",
        "```",
        "",
        "```text",
        "I saw you may be hiring for front desk/reception. We help businesses cover missed calls and common questions without adding another full-time seat.",
        "```",
        "",
        "```text",
        "I noticed your site/Google profile may be leaking calls from people already looking for [service].",
        "```",
        "",
        "## What To Avoid",
        "",
        "- Buying a generic list before knowing the trigger you will call about.",
        "- Scraping thousands of weak-fit businesses with no qualification layer.",
        "- Calling without the owner name when it is findable.",
        "- Treating revenue estimates as exact truth.",
        "- Spending too much time enriching leads before they pass basic fit checks.",
        "- Pitching SEO or AI automation as categories instead of visible missed revenue.",
        "",
    ]

    out.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
