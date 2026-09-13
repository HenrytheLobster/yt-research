"""
count_local_tam.py — EXACT local business counts per vertical via Google Places API (New).
Adaptive quadtree tiling: searches a bounding box; wherever a box hits the 60-result API
cap, it splits into 4 sub-boxes and recurses, deduping every place by ID. This beats the
flat 60-cap and gets true counts with the fewest calls (only dense areas get subdivided).

Setup:
  - Enable "Places API (New)" on your key in Google Cloud Console.
  - PowerShell:  $env:GOOGLE_MAPS_API_KEY="..."   then:  python count_local_tam.py
  - or inline:   python count_local_tam.py YOUR_KEY

Tune the metro box + min cell size at the top. Dense verticals will take a few minutes and
a few hundred API calls — the script prints the running call count so you can see the cost.
"""
import os, sys, time, json, urllib.request, urllib.error

# Args: positional key (or GOOGLE_MAPS_API_KEY env), and optional --only=<substring>
# to run just one/some verticals (cheap targeted reruns instead of all 10).
ONLY = None
_pos = []
for _a in sys.argv[1:]:
    if _a.startswith("--only="):
        ONLY = _a.split("=", 1)[1].lower()
    elif not _a.startswith("--"):
        _pos.append(_a)
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or (_pos[0] if _pos else "")
if not API_KEY:
    sys.exit("No key. Set GOOGLE_MAPS_API_KEY or pass it as the first argument.")

# Metro bounding box — Alexandria + Arlington + Springfield + close-in Fairfax/Falls Church.
# Widen these if your seminar pulls from farther out (e.g. lower lo_lng toward DC/Maryland).
LO_LAT, HI_LAT = 38.70, 38.95
LO_LNG, HI_LNG = -77.25, -76.95
MIN_SPAN = 0.012   # deg (~1.3 km); stop subdividing below this even if still capped

VERTICALS = [
    ("Med spa",           "med spa"),
    ("Dental practice",   "dentist"),
    ("Law firm",          "law firm"),
    ("Chiropractor",      "chiropractor"),
    ("Aesthetic clinic",  "medical spa"),
    ("Day spa",           "day spa"),
    ("Financial advisor", "financial advisor"),
    ("Veterinary clinic", "veterinary clinic"),
    ("Optometrist",       "optometrist"),
    ("Physical therapy",  "physical therapy clinic"),
]

URL = "https://places.googleapis.com/v1/places:searchText"
_calls = 0
_hard_error_shown = False
_errored = False  # set if any box gave up after retries (counts may be undercounts)


def fetch_page(body):
    """One API call, with retry+backoff on rate-limit / transient errors.
    Returns (data, fatal_error_str_or_None)."""
    global _calls, _hard_error_shown
    for attempt in range(6):
        req = urllib.request.Request(
            URL, data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": "places.id,nextPageToken",
            },
            method="POST",
        )
        _calls += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            txt = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503):          # throttled / transient → back off
                time.sleep(1.5 * (attempt + 1))
                continue
            if not _hard_error_shown:                    # 400/403 = real bug, show it once
                print(f"\nAPI ERROR: HTTP {e.code}\n{txt}\n", file=sys.stderr)
                _hard_error_shown = True
            return None, f"HTTP {e.code}"
        except Exception:
            time.sleep(1.5 * (attempt + 1))
            continue
    return None, "rate-limited after retries"


def search_box(term, lo_lat, lo_lng, hi_lat, hi_lng):
    """Text Search restricted to a rectangle; paginate to the 60 cap. Returns (ids, capped).
    If a box errors mid-pagination we mark it 'capped' so the caller subdivides + re-queries
    it (smaller boxes succeed), instead of trusting a partial count."""
    global _errored
    ids, token, pages, errbox = set(), None, 0, False
    while pages < 3:  # 3 x 20 = 60 (API max per query)
        body = {
            "textQuery": term,
            "pageSize": 20,
            "locationRestriction": {"rectangle": {
                "low":  {"latitude": lo_lat, "longitude": lo_lng},
                "high": {"latitude": hi_lat, "longitude": hi_lng},
            }},
        }
        if token:
            body["pageToken"] = token
        time.sleep(0.06)  # gentle throttle to stay under QPS
        data, err = fetch_page(body)
        if err:
            _errored = True
            errbox = True
            break
        for p in data.get("places", []):
            if p.get("id"):
                ids.add(p["id"])
        token = data.get("nextPageToken")
        pages += 1
        if not token:
            break
        time.sleep(1.8)  # next-page token needs a moment to become valid
    return ids, (len(ids) >= 60) or errbox


def count_vertical(term):
    found = set()
    stack = [(LO_LAT, LO_LNG, HI_LAT, HI_LNG)]
    while stack:
        lo_lat, lo_lng, hi_lat, hi_lng = stack.pop()
        ids, capped = search_box(term, lo_lat, lo_lng, hi_lat, hi_lng)
        found |= ids
        if capped and (hi_lat - lo_lat) > MIN_SPAN and (hi_lng - lo_lng) > MIN_SPAN:
            mlat, mlng = (lo_lat + hi_lat) / 2, (lo_lng + hi_lng) / 2
            stack += [
                (lo_lat, lo_lng, mlat, mlng),
                (lo_lat, mlng, mlat, hi_lng),
                (mlat, lo_lng, hi_lat, mlng),
                (mlat, mlng, hi_lat, hi_lng),
            ]
    return len(found)


def main():
    rows = []
    for label, term in VERTICALS:
        if ONLY and ONLY not in label.lower() and ONLY not in term.lower():
            continue
        n = count_vertical(term)
        rows.append((label, term, n))
        print(f"  {label:18s} {n:4d}   (api calls so far: {_calls})", file=sys.stderr)

    print("\n| Category | Search term | Exact count |")
    print("|---|---|---|")
    for label, term, n in rows:
        print(f"| {label} | {term} | {n} |")
    print(f"\n_Exact de-duplicated counts in the box "
          f"[{LO_LAT},{LO_LNG}]–[{HI_LAT},{HI_LNG}]. {_calls} API calls total. "
          f"Med spa & aesthetic clinic overlap — don't sum them._")
    if _errored:
        print("\n⚠️  Some boxes gave up after retries — a few counts may be undercounts. "
              "Re-run (quota may have been temporarily exhausted), or raise the sleep values.")


if __name__ == "__main__":
    main()
