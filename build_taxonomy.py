"""
build_taxonomy.py — EMERGENT automations taxonomy (LLM-clustered, NO hardcoded categories).

The old version forced every extraction into 16 categories hand-written in this file, so it
could never surface anything new — defeating the whole point of a discovery corpus. This
version does the opposite: it collects the actual automation names and business types from
the extractions and asks the LLM to INDUCE the category taxonomy + canonical verticals from
the data itself. Categories and verticals emerge from what's really there.

Model routing reuses extract.call_model, so it honors EXTRACT_MODEL:
    EXTRACT_MODEL=gemini-3.5-flash python build_taxonomy.py     # fast cloud (recommended)
    EXTRACT_MODEL=qwen3.5:cloud   python build_taxonomy.py      # Ollama cloud
    python build_taxonomy.py                                    # local default (slow)
Run after extraction (and after merging both machines' extracted/*.json into one folder).
"""
import glob, json, os, re, collections, time
from pathlib import Path
from datetime import datetime

from extract import call_model  # routes to Gemini / Ollama-cloud / local by model name

ROOT = Path(__file__).parent
EXTRACTED = ROOT / "data" / "youtube" / "extracted"
OUT = ROOT / "data" / "automations_taxonomy.json"
MODEL = os.environ.get("EXTRACT_MODEL", "qwen3:8b")
BATCH = 100

# Mike's "watch" ideas — used ONLY to post-tag emergent categories for highlight, never to
# constrain what gets discovered. Empty/extend freely; matching is fuzzy and cosmetic.
WATCH_HINTS = ["intake", "service plan", "seo", "content", "social", "linkedin",
               "operations", "scheduling pipeline", "dispatch"]


def load(p):
    t = open(p, encoding="utf-8", errors="ignore").read().split("\x00")[0]
    t = t[:t.rfind("}") + 1]
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        try:
            return json.loads(m.group()) if m else None
        except Exception:
            return None


def llm_json(prompt):
    """Call the model and parse a JSON object out of the response."""
    try:
        raw = call_model(prompt, MODEL)
    except Exception as e:
        print(f"    LLM call failed: {e}")
        return {}
    raw = re.sub(r"```(?:json)?", "", raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except Exception:
        return {}


def categorize_batch(items, kind):
    listing = "\n".join(f"- {n}" for n in items)
    noun = "automation/workflow names" if kind == "automation" else "business types / industries"
    unit = "the KIND of automation" if kind == "automation" else "the canonical industry/vertical"
    prompt = f"""You are clustering {noun} extracted from small-business AI/automation videos.
For each item, assign a SHORT label (2-4 words) describing {unit}.
Let the labels EMERGE from the items themselves — do not force any predefined list, but reuse
the same label for items that mean the same thing (e.g. "dental practice" and "dental office"
should both map to "Dental").

Return ONLY a JSON object mapping each exact item text to its label. No prose.

Items:
{listing}
"""
    return llm_json(prompt)


def consolidate(labels, kind):
    listing = "\n".join(f"- {l}" for l in labels)
    target = "20-40" if kind == "automation" else "15-30"
    prompt = f"""These are draft category labels for small-business {kind}s. Merge synonyms and
near-duplicates into a clean final taxonomy of roughly {target} categories that emerged from the
data. Return ONLY a JSON object mapping each draft label to its final canonical category name."""
    prompt += f"\n\nDraft labels:\n{listing}\n"
    return llm_json(prompt)


def cluster_items(items, kind):
    """Two-pass: per-batch labeling, then consolidate the labels. Returns item -> final label."""
    draft = {}
    for i in range(0, len(items), BATCH):
        b = items[i:i + BATCH]
        mapping = categorize_batch(b, kind)
        for name in b:
            draft[name] = (mapping.get(name) or mapping.get(name.strip()) or "Other").strip()
        print(f"  [{kind}] labeled {min(i + BATCH, len(items))}/{len(items)}")
    labels = sorted({v for v in draft.values() if v})
    final_map = consolidate(labels, kind) if len(labels) > 1 else {}
    print(f"  [{kind}] {len(labels)} draft labels -> {len(set(final_map.values()) or labels)} final categories")
    return {name: (final_map.get(draft[name], draft[name]) or "Other") for name in items}


def norm_vert(s):
    s = (s or "").strip().lower()
    s = re.sub(r"\b(small |local )?business(es)?\b", "", s).strip(" -+/")
    return s


# ─── 1. collect raw material from extractions ─────────────────────────────────
print(f"Loading extractions from {EXTRACTED} ...")
autos = {}            # name_lower -> {"raw":str, "videos":set, "tools":Counter, "example":dict, "first_party":[bool]}
vert_videos = collections.defaultdict(set)
total_videos = set()
files = 0
for f in glob.glob(str(EXTRACTED / "*.json")):
    d = load(f)
    if not d:
        continue
    m = str(d.get("model_used", ""))
    if not (m.startswith("qwen3") or m.startswith("gemini") or m.startswith("qwen2.5:7b")):
        continue
    files += 1
    vid = d.get("video_id", "")
    title = d.get("title", "")
    total_videos.add(vid)
    for t in d.get("tactics", []):
        nm = (t.get("name") or "").strip()
        if nm:
            key = nm.lower()
            a = autos.setdefault(key, {"raw": nm, "videos": set(), "tools": collections.Counter(),
                                       "example": None, "first_party": []})
            a["videos"].add(vid)
            for tl in (t.get("tools_mentioned") or []):
                if tl and tl.strip():
                    a["tools"][tl.strip()] += 1
            fp = t.get("first_party")
            if isinstance(fp, bool):
                a["first_party"].append(fp)
            elif isinstance(fp, str):
                a["first_party"].append(fp.strip().lower() in ("true", "yes", "1"))
            q = (t.get("source_quotes") or [""])[0]
            if a["example"] is None and len((t.get("description") or "")) > 40:
                a["example"] = {"name": nm, "desc": (t.get("description") or "")[:170],
                                "quote": (q or "")[:160], "source": title[:70], "video_id": vid}
        bt = norm_vert(t.get("business_type"))
        if bt and bt not in ("", "general", "n/a", "none", "various", "small", "any", "owner",
                             "business owner", "small- owner"):
            vert_videos[bt].add(vid)

print(f"  files: {files} | videos: {len(total_videos)} | distinct automations: {len(autos)} | distinct verticals(raw): {len(vert_videos)}")

# ─── 2. LLM-cluster the automation names into emergent categories ─────────────
print("Clustering automations into emergent categories (LLM)...")
auto_names = list(autos.keys())
auto_cat = cluster_items([autos[k]["raw"] for k in auto_names], "automation")
# map back via raw name
raw_to_key = {autos[k]["raw"]: k for k in auto_names}

cats = {}  # category -> {"videos":set, "tools":Counter, "members":[(count,key)], "fp_true":int,"fp_total":int}
for raw, cat in auto_cat.items():
    key = raw_to_key.get(raw)
    if not key:
        continue
    a = autos[key]
    c = cats.setdefault(cat, {"videos": set(), "tools": collections.Counter(), "members": [],
                              "fp_true": 0, "fp_total": 0})
    c["videos"] |= a["videos"]
    c["tools"].update(a["tools"])
    c["members"].append((len(a["videos"]), key))
    c["fp_true"] += sum(1 for x in a["first_party"] if x)
    c["fp_total"] += len(a["first_party"])

# ─── 3. LLM-cluster verticals ─────────────────────────────────────────────────
print("Clustering verticals (LLM)...")
vert_list = list(vert_videos.keys())
vert_cat = cluster_items(vert_list, "vertical")
vcats = collections.defaultdict(set)
for v, cat in vert_cat.items():
    vcats[cat] |= vert_videos[v]

# ─── 4. assemble payload (emergent — no hardcoded categories) ─────────────────
def is_watch(name):
    n = name.lower()
    return any(h in n for h in WATCH_HINTS)

categories = []
for cat, c in cats.items():
    members = sorted(c["members"], key=lambda x: -x[0])
    examples = []
    for _, key in members[:5]:
        ex = autos[key]["example"]
        if ex:
            examples.append(ex)
    top_names = ", ".join(autos[k]["raw"] for _, k in members[:4])
    fp_share = round(c["fp_true"] / c["fp_total"], 2) if c["fp_total"] else None
    categories.append({
        "name": cat,
        "desc": f"e.g. {top_names}",
        "watch": is_watch(cat),
        "video_count": len(c["videos"]),
        "distinct_automations": len(c["members"]),
        "first_party_share": fp_share,
        "top_tools": [{"tool": t, "n": n} for t, n in c["tools"].most_common(8)],
        "examples": examples,
    })
categories.sort(key=lambda x: -x["video_count"])

verticals = [{"name": k, "video_count": len(v)} for k, v in vcats.items()]
verticals.sort(key=lambda x: -x["video_count"])

payload = {
    "generated_at": datetime.now().isoformat(),
    "model": MODEL,
    "total_videos": len(total_videos),
    "method": "llm-emergent",
    "categories": categories,
    "verticals": verticals,
}
OUT.write_text(json.dumps(payload, indent=2))
OUT.with_suffix(".js").write_text("window.TAXONOMY = " + json.dumps(payload) + ";")

print(f"\nWrote {OUT} (+ .js)  [model: {MODEL}]")
print(f"Emergent automation categories: {len(categories)} | emergent verticals: {len(verticals)}")
print("\nTop emergent categories:")
for c in categories[:20]:
    print(f"  {c['video_count']:5d}  {c['name']}{'  *watch' if c['watch'] else ''}")
print("\nTop emergent verticals:")
for v in verticals[:20]:
    print(f"  {v['video_count']:5d}  {v['name']}")
