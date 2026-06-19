"""
Smart Data Extractor - combines multiple sources automatically:

  1. ALWAYS tries Apple App Store first (if APP_STORE_ID is set)
  2. ALWAYS tries Google Maps via SerpAPI (nationwide coverage)
  3. Combines both into one reviews.csv

This gives maximum review coverage regardless of whether the brand has an app.
"""

import os, sys, csv, json, time, re
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    BRAND_NAME, KEYWORDS, APP_STORE_ID, APP_COUNTRY,
    MAX_REVIEW_PAGES, DATA_DIR, REVIEWS_CSV,
)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

FIELDNAMES = [
    "review_id", "stars", "date", "title", "text",
    "source", "product", "version", "vote_count",
    "place_name", "address", "city", "state",
    "latitude", "longitude", "google_rating", "total_reviews_at_location",
]

# US states for nationwide search coverage
US_STATES = [
    "California", "Texas", "Florida", "New York", "Pennsylvania",
    "Illinois", "Ohio", "Georgia", "North Carolina", "Michigan",
    "New Jersey", "Virginia", "Washington", "Arizona", "Massachusetts",
    "Tennessee", "Indiana", "Missouri", "Maryland", "Wisconsin",
    "Colorado", "Minnesota", "South Carolina", "Alabama", "Louisiana",
    "Nevada", "Oregon", "Connecticut",
]


def fetch_url(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def parse_relative_date(text):
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    text = text.lower().strip()
    now  = datetime.now()
    try:
        if "just now" in text or "moment" in text:
            return now.strftime("%Y-%m-%d")
        num = re.search(r'\d+', text)
        n   = int(num.group()) if num else 1
        if "year"  in text: return (now - timedelta(days=n*365)).strftime("%Y-%m-%d")
        if "month" in text: return (now - timedelta(days=n*30)).strftime("%Y-%m-%d")
        if "week"  in text: return (now - timedelta(days=n*7)).strftime("%Y-%m-%d")
        if "day"   in text: return (now - timedelta(days=n)).strftime("%Y-%m-%d")
        if "hour"  in text or "minute" in text: return now.strftime("%Y-%m-%d")
    except Exception:
        pass
    return now.strftime("%Y-%m-%d")


def parse_address(raw_address):
    city, state = "", ""
    if not raw_address:
        return city, state
    parts = [p.strip() for p in raw_address.split(",")]
    if len(parts) >= 3:
        city = parts[-3]
        state_zip = parts[-2].strip()
        m = re.match(r'^([A-Z]{2})', state_zip)
        if m:
            state = m.group(1)
    elif len(parts) == 2:
        city = parts[0]
    return city.strip(), state.strip()


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1 - Apple App Store (always tried if APP_STORE_ID is set)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_app_store():
    if not APP_STORE_ID.strip():
        print("\n📱 No APP_STORE_ID set - skipping App Store.")
        return []

    print(f"\n📱 Scraping Apple App Store (ID: {APP_STORE_ID})...")
    reviews = []
    for page in range(1, MAX_REVIEW_PAGES + 1):
        url = (f"https://itunes.apple.com/{APP_COUNTRY}/rss/customerreviews"
               f"/page={page}/id={APP_STORE_ID}/sortby=mostrecent/json")
        try:
            data    = json.loads(fetch_url(url))
            entries = data.get("feed", {}).get("entry", [])
            if page == 1 and entries:
                entries = entries[1:]
            if not entries:
                break
            for e in entries:
                reviews.append({
                    "review_id":  e.get("id",{}).get("label",""),
                    "stars":      e.get("im:rating",{}).get("label",""),
                    "date":       e.get("updated",{}).get("label","")[:10],
                    "title":      e.get("title",{}).get("label",""),
                    "text":       e.get("content",{}).get("label","").replace("\n"," ").strip(),
                    "source":     "app_store",
                    "product":    BRAND_NAME,
                    "version":    e.get("im:version",{}).get("label",""),
                    "vote_count": e.get("im:voteCount",{}).get("label","0"),
                    "place_name": "", "address": "", "city": "", "state": "",
                    "latitude": "", "longitude": "",
                    "google_rating": "", "total_reviews_at_location": "",
                })
            print(f"   Page {page}: {len(entries)} reviews (total: {len(reviews)})")
            time.sleep(0.5)
        except Exception as ex:
            print(f"   Page {page}: {ex} - stopping.")
            break

    print(f"   ✅ App Store: {len(reviews)} reviews")
    return reviews


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2 - Google Maps via SerpAPI (always tried, nationwide)
# ══════════════════════════════════════════════════════════════════════════════

def serpapi_get(params):
    params["api_key"] = SERPAPI_KEY
    url = f"https://serpapi.com/search?{urllib.parse.urlencode(params)}"
    return json.loads(fetch_url(url))


def scrape_location_reviews(keyword, max_locations=3, max_pages=2):
    """Search Google Maps for keyword, scrape reviews with full location metadata."""
    reviews = []
    try:
        data    = serpapi_get({"engine": "google_maps", "q": keyword, "type": "search"})
        results = data.get("local_results", [])

        if not results:
            return []

        for place in results[:max_locations]:
            data_id       = place.get("data_id", "")
            place_name    = place.get("title", keyword)
            raw_address   = place.get("address", "")

            gps  = place.get("gps_coordinates") or {}
            lat  = gps.get("latitude")  or place.get("latitude")  or place.get("lat") or ""
            lon  = gps.get("longitude") or place.get("longitude") or place.get("lng") or place.get("lon") or ""

            google_rating = place.get("rating", "")
            total_reviews = place.get("reviews", "")

            city, state = parse_address(raw_address)

            if not data_id:
                continue

            next_token = None
            location_reviews = []

            for page in range(max_pages):
                params = {"engine":"google_maps_reviews","data_id":data_id,"sort_by":"newestFirst","hl":"en"}
                if next_token:
                    params["next_page_token"] = next_token

                try:
                    rdata = serpapi_get(params)
                    raw   = rdata.get("reviews", [])
                    if not raw:
                        break

                    for r in raw:
                        text = r.get("snippet", "").replace("\n", " ").strip()
                        if not text:
                            continue
                        location_reviews.append({
                            "review_id":  r.get("review_id", f"{data_id}_{len(location_reviews)}"),
                            "stars":      str(r.get("rating", "")),
                            "date":       parse_relative_date(r.get("date", "")),
                            "title":      "",
                            "text":       text,
                            "source":     "google_maps",
                            "product":    place_name,
                            "version":    "",
                            "vote_count": str(r.get("likes", 0)),
                            "place_name": place_name,
                            "address":    raw_address,
                            "city":       city,
                            "state":      state,
                            "latitude":   str(lat),
                            "longitude":  str(lon),
                            "google_rating":              str(google_rating),
                            "total_reviews_at_location":  str(total_reviews),
                        })

                    next_token = rdata.get("serpapi_pagination", {}).get("next_page_token", "")
                    if not next_token:
                        break
                    time.sleep(0.3)

                except Exception:
                    break

            if location_reviews:
                print(f"      {place_name} ({city}, {state}): {len(location_reviews)} reviews")
            reviews.extend(location_reviews)
            time.sleep(0.3)

    except Exception as ex:
        print(f"   Error for '{keyword}': {ex}")

    return reviews


def scrape_serpapi():
    if not SERPAPI_KEY:
        print("\n🌍 SERPAPI_KEY not set - skipping Google Maps.")
        return []

    print(f"\n🌍 Scraping Google Maps reviews for: {BRAND_NAME} (nationwide)...")
    all_reviews = []
    seen_ids    = set()

    # Combine brand keywords with state names for nationwide coverage
    search_terms = []
    for kw in KEYWORDS:
        for state in US_STATES:
            search_terms.append(f"{kw} {state}")

    print(f"   Searching {len(search_terms)} state-targeted queries (capped for API budget)...")

    queries_run = 0
    max_queries = 40  # stay within free tier budget

    for term in search_terms:
        if queries_run >= max_queries:
            break
        if len(all_reviews) >= 500:
            break

        reviews = scrape_location_reviews(term, max_locations=2, max_pages=2)
        queries_run += 1

        for r in reviews:
            if r["review_id"] not in seen_ids:
                seen_ids.add(r["review_id"])
                all_reviews.append(r)

        time.sleep(0.3)

    print(f"\n   ✅ Google Maps: {len(all_reviews)} unique reviews from {queries_run} queries")

    if all_reviews:
        states_found = set(r["state"] for r in all_reviews if r["state"])
        print(f"   📍 States covered: {sorted(states_found)}")

    return all_reviews


# ══════════════════════════════════════════════════════════════════════════════
# MAIN - combines both sources
# ══════════════════════════════════════════════════════════════════════════════

def save_reviews(reviews):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REVIEWS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(reviews)
    print(f"\n   💾 Saved {len(reviews)} reviews → {REVIEWS_CSV}")


def main():
    print("=" * 55)
    print(f"  Smart Data Extractor - {BRAND_NAME}")
    print("  Combines App Store + Google Maps automatically")
    print("=" * 55)

    all_reviews = []

    # Always try App Store first
    app_reviews = scrape_app_store()
    all_reviews.extend(app_reviews)

    # Always try Google Maps (nationwide)
    maps_reviews = scrape_serpapi()
    all_reviews.extend(maps_reviews)

    if not all_reviews:
        print("\n⚠️  No reviews collected from any source.")
        print("   Check APP_STORE_ID and SERPAPI_KEY in config.py / GitHub Secrets")
        sys.exit(1)

    save_reviews(all_reviews)

    sources = {}
    for r in all_reviews:
        src = r.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    print("\n" + "=" * 55)
    print(f"  ✅ Done - {len(all_reviews)} total reviews")
    for src, count in sources.items():
        print(f"     {src}: {count}")
    print("=" * 55)


if __name__ == "__main__":
    main()
