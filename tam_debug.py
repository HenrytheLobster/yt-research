"""One Places API call, full response printed. Run: python tam_debug.py  (key from env or arg)."""
import os, sys, json, urllib.request, urllib.error

key = os.environ.get("GOOGLE_MAPS_API_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
if not key:
    sys.exit("No key.")

body = {
    "textQuery": "med spa",
    "pageSize": 20,
    "locationRestriction": {"rectangle": {
        "low":  {"latitude": 38.70, "longitude": -77.25},
        "high": {"latitude": 38.95, "longitude": -76.95},
    }},
}
req = urllib.request.Request(
    "https://places.googleapis.com/v1/places:searchText",
    data=json.dumps(body).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.id,places.displayName,nextPageToken",
    },
    method="POST",
)
print("Requesting...")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("STATUS:", r.status)
        data = json.loads(r.read())
        print("places returned:", len(data.get("places", [])))
        print(json.dumps(data, indent=2)[:1500])
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print(e.read().decode()[:1500])
except Exception as e:
    print("OTHER ERROR:", repr(e))
