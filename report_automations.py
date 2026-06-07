"""
report_automations.py — Clean inventory of automations from existing extractions.
Reads data/youtube/extracted/*.json (no Ollama needed), strips prompt-contamination
and hustle-genre noise, and ranks automations by how many DISTINCT videos show them.
"""
import json, re, collections
from pathlib import Path

EXTRACTED = Path("data/youtube/extracted")

# --- contamination: text the 3B model copied verbatim from the prompt template ---
LEAK_STRINGS = {
    "short name", "what this automation workflow or strategy does",
    "if x then y (human readable)", "declarative fact about ai automation for small business",
    "what this automation opportunity pattern is", "business_type + pain_point + tool",
}
LEAK_EXAMPLES = {
    "real estate agent + lead follow-up + n8n", "plumber + lead follow-up + n8n",
}
# --- hustle / wrong-genre videos (start-a-business, make-money, POD) ---
HUSTLE_TITLE = re.compile(
    r"print on demand|\bpod\b|etsy|dropship|faceless|passive income|"
    r"start over|i'?d build|businesses to start|make money|side hustle|"
    r"\$\d|get rich|1-person|one person business|build a .* business with \$0",
    re.I)

def clean(s): return (s or "").strip().lower()

def is_leak(item):
    name = clean(item.get("name") or item.get("statement") or item.get("pattern_template"))
    desc = clean(item.get("description"))
    ex   = clean(item.get("example"))
    if name in LEAK_STRINGS or desc in LEAK_STRINGS: return True
    if ex in LEAK_EXAMPLES: return True
    if not name: return True
    return False

tool_videos   = collections.defaultdict(set)   # tool -> {video_ids}
tool_examples = collections.defaultdict(list)   # tool -> [(title, quote)]
biz_videos    = collections.defaultdict(set)    # business_type -> {video_ids}
autom_videos  = collections.defaultdict(set)    # automation tactic name -> {video_ids}
autom_quote   = {}

TOOL_CANON = {"make":"Make.com","make.com":"Make.com","makecom":"Make.com",
              "n8n":"n8n","zapier":"Zapier","chatgpt":"ChatGPT","openai":"OpenAI",
              "gohighlevel":"GoHighLevel","ghl":"GoHighLevel","claude":"Claude",
              "twilio":"Twilio","airtable":"Airtable","gemini":"Gemini",
              "power automate":"Power Automate","voiceflow":"Voiceflow","vapi":"Vapi"}

kept_videos, dropped_hustle = set(), set()
files = list(EXTRACTED.glob("*.json"))
for f in files:
    d = json.loads(f.read_text())
    vid, title = d.get("video_id",""), d.get("title","")
    if HUSTLE_TITLE.search(title):
        dropped_hustle.add(vid); continue
    kept_videos.add(vid)
    for t in d.get("tactics", []):
        if is_leak(t): continue
        nm = (t.get("name") or "").strip()
        if nm: 
            autom_videos[nm].add(vid)
            if nm not in autom_quote and t.get("source_quotes"):
                autom_quote[nm] = (title, t["source_quotes"][0])
        for raw in t.get("tools_mentioned", []) or []:
            c = TOOL_CANON.get(clean(raw), raw.strip())
            if c: tool_videos[c].add(vid)
    for p in d.get("niche_patterns", []):
        if is_leak(p): continue
        tmpl = (p.get("pattern_template") or "")
        biz = tmpl.split("+")[0].strip()
        if biz and len(biz) < 40: biz_videos[biz].add(vid)

def top(dct, n=25): return sorted(dct.items(), key=lambda kv:-len(kv[1]))[:n]

out = ["# Automations Actually Shown — Clean Inventory",
       f"\n_Source: {len(files)} extracted videos · kept {len(kept_videos)} · "
       f"dropped {len(dropped_hustle)} hustle/POD-genre · contamination filtered_\n",
       "\n## Tools people actually use (by # of distinct videos)\n"]
for tool, vids in top(tool_videos):
    out.append(f"- **{tool}** — {len(vids)} videos")
out.append("\n## Specific automations / tactics (deduped, real signal)\n")
for nm, vids in top(autom_videos, 30):
    q = autom_quote.get(nm)
    line = f"- **{nm}** — {len(vids)} video(s)"
    if q: line += f'  \n  _“{q[1][:140]}”_ — {q[0][:60]}'
    out.append(line)
out.append("\n## Business types these target\n")
for biz, vids in top(biz_videos, 20):
    out.append(f"- {biz} — {len(vids)}")

Path("reports/CLEAN_automations_inventory.md").write_text("\n".join(out))
print("WROTE reports/CLEAN_automations_inventory.md")
print(f"videos: {len(files)} | kept {len(kept_videos)} | dropped hustle {len(dropped_hustle)}")
print(f"distinct tools: {len(tool_videos)} | distinct tactics: {len(autom_videos)}")
