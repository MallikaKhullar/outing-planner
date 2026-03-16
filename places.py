"""Places data retrieval and normalization.
Supports Google Places, Yelp, and Foursquare APIs.
Falls back to demo data when API keys are not configured.
"""
import json
import os
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Dict, Optional
from ranking import extract_review_signals

GOOGLE_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
YELP_API_KEY = os.environ.get("YELP_API_KEY", "")
FOURSQUARE_API_KEY = os.environ.get("FOURSQUARE_API_KEY", "")


def _http_get(url: str, headers: dict = None) -> dict:
    """Simple HTTP GET that returns parsed JSON."""
    import ssl, certifi
    # Use certifi bundle to fix macOS SSL cert verification
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            print(f"[HTTP] OK {url[:80]}...")
            return data
    except Exception as e:
        print(f"[HTTP] FAILED {url[:80]}... ERROR: {e}")
        return {}


def fetch_google_place_reviews(place_id: str) -> List[str]:
    """Fetch up to 5 reviews for a place to extract signals. Uses SSL context to fix macOS cert issues."""
    if not GOOGLE_API_KEY or not place_id:
        return []
    import ssl
    params = {
        "place_id": place_id,
        "fields": "review,editorial_summary",
        "key": GOOGLE_API_KEY,
    }
    url = f"https://maps.googleapis.com/maps/api/place/details/json?{urllib.parse.urlencode(params)}"
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        reviews = data.get("result", {}).get("reviews", [])
        return [r.get("text", "") for r in reviews[:5]]
    except Exception as e:
        print(f"Review fetch error: {e}")
        return []


def search_google_places(query: str, lat: float, lng: float,
                          radius: int = 2000, place_type: str = "", distance_limit_miles: float = None) -> List[Dict]:
    """Search Google Places Nearby API, sorted by distance for walkability."""
    if not GOOGLE_API_KEY:
        return []

    # Convert distance_limit to meters for API (1 mile ≈ 1609 meters)
    if distance_limit_miles:
        radius = int(distance_limit_miles * 1609)

    # Strategy 1: Nearby Search with type filter — best for proximity
    # Use radius-based search when distance_limit is specified
    nearby_params = {
        "location": f"{lat},{lng}",
        "radius": radius,  # Use radius instead of rankby=distance when distance_limit is set
        "key": GOOGLE_API_KEY,
    }
    if place_type:
        nearby_params["type"] = place_type
    else:
        nearby_params["keyword"] = query

    url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{urllib.parse.urlencode(nearby_params)}"
    data = _http_get(url)
    print(f"[GOOGLE] Nearby: status={data.get('status')} results={len(data.get('results',[]))} error='{data.get('error_message','none')}'")

    # Strategy 2: Fall back to Text Search with radius if nearby returns nothing
    if not data.get("results"):
        text_params = {
            "query": f"{query} near me",
            "location": f"{lat},{lng}",
            "radius": radius,
            "key": GOOGLE_API_KEY,
        }
        if place_type:
            text_params["type"] = place_type
        url2 = f"https://maps.googleapis.com/maps/api/place/textsearch/json?{urllib.parse.urlencode(text_params)}"
        data = _http_get(url2)
        print(f"[GOOGLE] TextSearch: status={data.get('status')} results={len(data.get('results',[]))} error='{data.get('error_message','none')}'")

    places = []
    seen_ids = set()

    # Collect all results and deduplicate
    all_results = data.get("results", [])

    # Get next page if available (pagination for more results)
    page_token = data.get("next_page_token")
    if page_token:
        import time
        time.sleep(2)  # Google requires delay between pagination requests
        next_params = {
            "pagetoken": page_token,
            "key": GOOGLE_API_KEY,
        }
        next_url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{urllib.parse.urlencode(next_params)}"
        next_data = _http_get(next_url)
        all_results.extend(next_data.get("results", []))

    # Process results (limit to 15)
    for result in all_results[:15]:
        place = normalize_google_place(result)
        if place and place["id"] not in seen_ids:
            # Fetch reviews to extract real signals
            reviews = fetch_google_place_reviews(place["id"])
            if reviews:
                from ranking import extract_review_signals
                place["review_signals"] = extract_review_signals(reviews)
            else:
                place["review_signals"] = {}
            places.append(place)
            seen_ids.add(place["id"])

    return places


def normalize_google_place(raw: dict) -> Optional[Dict]:
    """Normalize a Google Places result."""
    loc = raw.get("geometry", {}).get("location", {})
    photos = []
    for p in raw.get("photos", [])[:5]:
        ref = p.get("photo_reference", "")
        if ref and GOOGLE_API_KEY:
            photos.append(
                f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photoreference={ref}&key={GOOGLE_API_KEY}"
            )

    return {
        "id": raw.get("place_id", ""),
        "name": raw.get("name", ""),
        "address": raw.get("formatted_address", ""),
        "lat": loc.get("lat", 0),
        "lng": loc.get("lng", 0),
        "rating": raw.get("rating", 0),
        "review_count": raw.get("user_ratings_total", 0),
        "price_level": raw.get("price_level", 2),
        "types": raw.get("types", []),
        "open_now": raw.get("opening_hours", {}).get("open_now", None),
        "photos": photos,
        "source": "google",
        "review_signals": {},
    }


def search_yelp(query: str, lat: float, lng: float, limit: int = 10) -> List[Dict]:
    """Search Yelp Fusion API."""
    if not YELP_API_KEY:
        return []

    params = {
        "term": query,
        "latitude": lat,
        "longitude": lng,
        "limit": limit,
        "sort_by": "best_match",
    }
    url = f"https://api.yelp.com/v3/businesses/search?{urllib.parse.urlencode(params)}"
    data = _http_get(url, {"Authorization": f"Bearer {YELP_API_KEY}"})

    places = []
    for biz in data.get("businesses", []):
        place = normalize_yelp_place(biz)
        if place:
            places.append(place)
    return places


def normalize_yelp_place(raw: dict) -> Optional[Dict]:
    """Normalize a Yelp business result."""
    coords = raw.get("coordinates", {})
    price_map = {"$": 1, "$$": 2, "$$$": 3, "$$$$": 4}

    photos = []
    if raw.get("image_url"):
        photos.append(raw["image_url"])

    return {
        "id": f"yelp_{raw.get('id', '')}",
        "name": raw.get("name", ""),
        "address": ", ".join(raw.get("location", {}).get("display_address", [])),
        "lat": coords.get("latitude", 0),
        "lng": coords.get("longitude", 0),
        "rating": raw.get("rating", 0),
        "review_count": raw.get("review_count", 0),
        "price_level": price_map.get(raw.get("price", "$$"), 2),
        "types": [c.get("alias", "") for c in raw.get("categories", [])],
        "open_now": None,
        "photos": photos,
        "source": "yelp",
        "review_signals": {},
    }


def search_foursquare(query: str, lat: float, lng: float, limit: int = 10) -> List[Dict]:
    """Search Foursquare Places API."""
    if not FOURSQUARE_API_KEY:
        return []

    params = {
        "query": query,
        "ll": f"{lat},{lng}",
        "limit": limit,
        "sort": "RELEVANCE",
    }
    url = f"https://api.foursquare.com/v3/places/search?{urllib.parse.urlencode(params)}"
    data = _http_get(url, {
        "Authorization": FOURSQUARE_API_KEY,
        "Accept": "application/json",
    })

    places = []
    for result in data.get("results", []):
        place = normalize_foursquare_place(result)
        if place:
            places.append(place)
    return places


def normalize_foursquare_place(raw: dict) -> Optional[Dict]:
    """Normalize a Foursquare place result."""
    geo = raw.get("geocodes", {}).get("main", {})

    return {
        "id": f"fsq_{raw.get('fsq_id', '')}",
        "name": raw.get("name", ""),
        "address": raw.get("location", {}).get("formatted_address", ""),
        "lat": geo.get("latitude", 0),
        "lng": geo.get("longitude", 0),
        "rating": raw.get("rating", 0) / 2 if raw.get("rating") else 0,
        "review_count": 0,
        "price_level": raw.get("price", 2),
        "types": [c.get("name", "") for c in raw.get("categories", [])],
        "open_now": None,
        "photos": [],
        "source": "foursquare",
        "review_signals": {},
    }


def merge_and_deduplicate(results_lists: List[List[Dict]]) -> List[Dict]:
    """Merge results from multiple sources and deduplicate by name/location."""
    seen = {}
    merged = []

    for results in results_lists:
        for place in results:
            key = f"{place['name'].lower().strip()}_{round(place['lat'], 3)}_{round(place['lng'], 3)}"
            if key not in seen:
                seen[key] = place
                merged.append(place)
            else:
                # Merge data from additional source
                existing = seen[key]
                if not existing["photos"] and place["photos"]:
                    existing["photos"] = place["photos"]
                if place["review_count"] > existing["review_count"]:
                    existing["review_count"] = place["review_count"]

    return merged


ACTIVITY_TO_GOOGLE_TYPE = {
    "coffee": "cafe",
    "work_cafe": "cafe",
    "cafe": "cafe",
    "restaurant": "restaurant",
    "date": "restaurant",
    "dining": "restaurant",
    "bar": "bar",
    "hiking": "park",
    "outdoor": "park",
    "kayaking": "",
    "entertainment": "tourist_attraction",
}


def search_premium_cafes(city: str, lat: float, lng: float, distance_limit_miles: float = None) -> List[Dict]:
    """Search for known premium cafes by specific name. These are cafes that might not appear
    in generic nearby searches due to ranking/popularity algorithms."""
    if city.lower() != "san francisco":
        return []

    # Known premium cafes in SF that we want to ensure appear
    premium_cafes = [
        "Andytown Coffee Roasters",
        "JOE & THE JUICE",
    ]

    results = []
    print(f"\n[PREMIUM] Searching for {len(premium_cafes)} premium cafes...")

    for cafe_name in premium_cafes:
        # Text search for specific cafe name + city
        search_query = f"{cafe_name} San Francisco"
        params = {
            "query": search_query,
            "key": GOOGLE_API_KEY,
        }
        url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?{urllib.parse.urlencode(params)}"
        print(f"\n[PREMIUM] Searching for: '{search_query}'")
        data = _http_get(url)

        api_results = data.get("results", [])
        print(f"[PREMIUM] API returned {len(api_results)} results")

        if not api_results:
            print(f"[PREMIUM] WARNING: No results found for '{cafe_name}'")
            continue

        for idx, place in enumerate(api_results, 1):
            name = place.get("name", "Unknown")
            types = place.get("types", [])
            rating = place.get("rating", "N/A")

            print(f"[PREMIUM]   Result {idx}: {name}")
            print(f"[PREMIUM]     Types: {types}")
            print(f"[PREMIUM]     Rating: {rating}")
            print(f"[PREMIUM]     Has 'cafe': {'cafe' in types}")
            print(f"[PREMIUM]     Has 'coffee_shop': {'coffee_shop' in types}")

            # Check if it's a cafe
            is_cafe = "cafe" in types or "coffee_shop" in types

            if is_cafe:
                result = normalize_google_place(place)
                if result:
                    # Fetch reviews for signals
                    reviews = fetch_google_place_reviews(result["id"])
                    if reviews:
                        result["review_signals"] = extract_review_signals(reviews)
                    else:
                        result["review_signals"] = {}
                    results.append(result)
                    print(f"[PREMIUM] ✓ Added to results: {result['name']}")
                    break  # Only use first match for each cafe
            else:
                # Log why we're skipping it
                print(f"[PREMIUM] ✗ Skipped (not typed as cafe/coffee_shop): {name}")
                # BUT: Check if this might be the right place despite type
                if cafe_name.lower() in name.lower():
                    print(f"[PREMIUM]   ^ This looks like the right place but has different type!")
                    print(f"[PREMIUM]   ^ Types: {types}")

    print(f"[PREMIUM] Total premium cafes successfully added: {len(results)}\n")
    return results


def search_places(query: str, lat: float, lng: float, city: str,
                   intent: dict = None) -> List[Dict]:
    """Search all configured APIs and merge results."""
    results_lists = []
    intent = intent or {}

    # Derive Google place_type from intent for accurate filtering
    activity = intent.get("activity_type", "")
    place_type = ACTIVITY_TO_GOOGLE_TYPE.get(activity, "")
    distance_limit = intent.get("distance_limit", None)

    # Try real APIs
    google_results = search_google_places(query, lat, lng, place_type=place_type, distance_limit_miles=distance_limit)
    if google_results:
        results_lists.append(google_results)

    # For work_cafe/coffee queries, also search for known premium cafes by name
    # This catches cafes that don't rank high in generic nearby searches
    if activity in ("work_cafe", "cafe", "coffee"):
        premium = search_premium_cafes(city, lat, lng, distance_limit)
        if premium:
            results_lists.append(premium)
            print(f"[PLACES] Added {len(premium)} premium cafes")

    # Also do secondary search without strict type filtering for work_cafe
    if activity in ("work_cafe", "cafe", "coffee") and place_type == "cafe":
        google_results_no_type = search_google_places(query, lat, lng, place_type="", distance_limit_miles=distance_limit)
        if google_results_no_type:
            results_lists.append(google_results_no_type)
            print(f"[PLACES] Added {len(google_results_no_type)} secondary Google results (no type filter)")

    yelp_results = search_yelp(query, lat, lng)
    if yelp_results:
        results_lists.append(yelp_results)

    fsq_results = search_foursquare(query, lat, lng)
    if fsq_results:
        results_lists.append(fsq_results)

    if results_lists:
        total = sum(len(r) for r in results_lists)
        print(f"[PLACES] Returning {total} real API results (merged from {len(results_lists)} sources)")
        return merge_and_deduplicate(results_lists)

    print(f"[PLACES] ⚠️  All API calls failed or returned empty — falling back to DEMO DATA")
    return get_demo_places(query, lat, lng, city, intent)


def get_demo_places(query: str, lat: float, lng: float, city: str,
                     intent: dict = None) -> List[Dict]:
    """Generate realistic demo places for testing without API keys."""
    query_lower = query.lower()
    intent = intent or {}
    activity = intent.get("activity_type", "")
    cuisine = intent.get("cuisine", "")

    # City-specific demo data
    demo_sets = {
        "san francisco": {
            "coffee": [
                {"name": "Blue Bottle Coffee - Mint Plaza", "lat": 37.7825, "lng": -122.4082,
                 "rating": 4.5, "price_level": 2, "review_count": 1240,
                 "types": ["cafe", "coffee_shop"], "address": "66 Mint St, San Francisco, CA",
                 "review_signals": {"wifi": "high", "laptop_friendly": "high", "quiet": "medium", "crowded": "medium"},
                 "photos": ["https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?w=400"]},
                {"name": "Sightglass Coffee", "lat": 37.7697, "lng": -122.4095,
                 "rating": 4.4, "price_level": 2, "review_count": 890,
                 "types": ["cafe", "coffee_shop"], "address": "270 7th St, San Francisco, CA",
                 "review_signals": {"wifi": "none", "laptop_friendly": "low", "quiet": "medium", "cozy": "high"},
                 "photos": ["https://images.unsplash.com/photo-1559496417-e7f25cb247f3?w=400"]},
                {"name": "Ritual Coffee Roasters", "lat": 37.7561, "lng": -122.4215,
                 "rating": 4.3, "price_level": 2, "review_count": 756,
                 "types": ["cafe", "coffee_shop"], "address": "1026 Valencia St, San Francisco, CA",
                 "review_signals": {"wifi": "high", "laptop_friendly": "medium", "quiet": "none", "crowded": "high"},
                 "photos": ["https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=400"]},
                {"name": "Philz Coffee - Castro", "lat": 37.7613, "lng": -122.4349,
                 "rating": 4.6, "price_level": 2, "review_count": 1560,
                 "types": ["cafe", "coffee_shop"], "address": "549 Castro St, San Francisco, CA",
                 "review_signals": {"wifi": "medium", "laptop_friendly": "medium", "quiet": "none", "crowded": "high"},
                 "photos": ["https://images.unsplash.com/photo-1442512595331-e89e73853f31?w=400"]},
            ],
            "chinese": [
                {"name": "Z & Y Restaurant", "lat": 37.7945, "lng": -122.4068,
                 "rating": 4.2, "price_level": 2, "review_count": 3400,
                 "types": ["chinese_restaurant", "restaurant"], "address": "655 Jackson St, San Francisco, CA",
                 "review_signals": {"crowded": "high", "ambiance": "medium"},
                 "photos": ["https://images.unsplash.com/photo-1585032226651-759b368d7246?w=400"]},
                {"name": "Mister Jiu's", "lat": 37.7959, "lng": -122.4069,
                 "rating": 4.6, "price_level": 3, "review_count": 870,
                 "types": ["chinese_restaurant", "restaurant"], "address": "28 Waverly Pl, San Francisco, CA",
                 "review_signals": {"date_night": "high", "ambiance": "high", "cozy": "medium"},
                 "photos": ["https://images.unsplash.com/photo-1552566626-52f8b828add9?w=400"]},
                {"name": "Good Luck Dim Sum", "lat": 37.7638, "lng": -122.4735,
                 "rating": 4.4, "price_level": 1, "review_count": 1200,
                 "types": ["chinese_restaurant", "restaurant"], "address": "736 Clement St, San Francisco, CA",
                 "review_signals": {"crowded": "high"},
                 "photos": ["https://images.unsplash.com/photo-1563245372-f21724e3856d?w=400"]},
                {"name": "Lai Hong Lounge", "lat": 37.7945, "lng": -122.4053,
                 "rating": 4.0, "price_level": 1, "review_count": 560,
                 "types": ["chinese_restaurant", "restaurant"], "address": "1416 Powell St, San Francisco, CA",
                 "review_signals": {"crowded": "medium"},
                 "photos": ["https://images.unsplash.com/photo-1526318896980-cf78c088247c?w=400"]},
            ],
            "hiking": [
                {"name": "Lands End Trail", "lat": 37.7879, "lng": -122.5052,
                 "rating": 4.8, "price_level": 0, "review_count": 4500,
                 "types": ["hiking", "park", "trail"], "address": "Lands End Trail, San Francisco, CA",
                 "review_signals": {"scenic": "high"},
                 "photos": ["https://images.unsplash.com/photo-1501555088652-021faa106b9b?w=400"]},
                {"name": "Twin Peaks", "lat": 37.7544, "lng": -122.4477,
                 "rating": 4.6, "price_level": 0, "review_count": 8900,
                 "types": ["hiking", "park", "viewpoint"], "address": "501 Twin Peaks Blvd, San Francisco, CA",
                 "review_signals": {"scenic": "high"},
                 "photos": ["https://images.unsplash.com/photo-1521747116042-5a810fda9664?w=400"]},
                {"name": "Battery to Bluffs Trail", "lat": 37.7986, "lng": -122.4834,
                 "rating": 4.7, "price_level": 0, "review_count": 1200,
                 "types": ["hiking", "trail"], "address": "Battery to Bluffs Trail, San Francisco, CA",
                 "review_signals": {"scenic": "high"},
                 "photos": ["https://images.unsplash.com/photo-1551632811-561732d1e306?w=400"]},
            ],
            "restaurant": [
                {"name": "Nopa", "lat": 37.7741, "lng": -122.4373,
                 "rating": 4.5, "price_level": 3, "review_count": 2100,
                 "types": ["restaurant"], "address": "560 Divisadero St, San Francisco, CA",
                 "review_signals": {"date_night": "high", "ambiance": "high"},
                 "photos": ["https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400"]},
                {"name": "Burma Superstar", "lat": 37.7646, "lng": -122.4736,
                 "rating": 4.3, "price_level": 2, "review_count": 5600,
                 "types": ["restaurant", "burmese"], "address": "309 Clement St, San Francisco, CA",
                 "review_signals": {"crowded": "high", "ambiance": "medium"},
                 "photos": ["https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=400"]},
            ],
            "kayaking": [
                {"name": "City Kayak", "lat": 37.7880, "lng": -122.3890,
                 "rating": 4.5, "price_level": 2, "review_count": 450,
                 "types": ["kayaking", "water_sport"], "address": "Pier 40, San Francisco, CA",
                 "review_signals": {"scenic": "high"},
                 "photos": ["https://images.unsplash.com/photo-1472745942893-4b9f730c7668?w=400"]},
                {"name": "Sea Trek Kayak", "lat": 37.8590, "lng": -122.4877,
                 "rating": 4.7, "price_level": 3, "review_count": 680,
                 "types": ["kayaking", "water_sport"], "address": "Schoonmaker Point Marina, Sausalito, CA",
                 "review_signals": {"scenic": "high"},
                 "photos": ["https://images.unsplash.com/photo-1545477384-1a71e18cc420?w=400"]},
            ],
        },
    }

    # Determine which demo set to use
    demo_city = demo_sets.get(city.lower().strip(), demo_sets.get("san francisco", {}))

    # Match query to category
    matched = []
    # Map activity types to demo categories
    activity_to_category = {
        "work_cafe": "coffee", "cafe": "coffee", "coffee": "coffee",
        "restaurant": "restaurant", "date": "restaurant", "dining": "restaurant",
        "hiking": "hiking", "outdoor": "hiking",
        "kayaking": "kayaking",
    }
    target_category = activity_to_category.get(activity, "")
    if target_category and target_category in demo_city:
        matched.extend(demo_city[target_category])
    else:
        for category, places in demo_city.items():
            if (category in query_lower or
                category in activity or
                category in cuisine or
                any(word in query_lower for word in category.split("_"))):
                matched.extend(places)

    # Fallback: return restaurant + coffee if nothing specific matched
    if not matched:
        for category in ["restaurant", "coffee"]:
            matched.extend(demo_city.get(category, []))

    # Add common fields
    result = []
    for p in matched:
        place = {
            "id": f"demo_{p['name'].lower().replace(' ', '_')[:20]}",
            "name": p["name"],
            "address": p.get("address", f"{city}"),
            "lat": p["lat"],
            "lng": p["lng"],
            "rating": p["rating"],
            "review_count": p.get("review_count", 100),
            "price_level": p.get("price_level", 2),
            "types": p.get("types", []),
            "open_now": True,
            "photos": p.get("photos", []),
            "source": "demo",
            "review_signals": p.get("review_signals", {}),
        }
        result.append(place)

    # Generate generic places for cities without demo data
    if not result:
        result = generate_generic_places(query, lat, lng, city, intent)

    return result


def generate_generic_places(query: str, lat: float, lng: float,
                             city: str, intent: dict = None) -> List[Dict]:
    """Generate generic place data for any city."""
    import random
    random.seed(hash(f"{query}_{city}"))

    activity = (intent or {}).get("activity_type", "restaurant")
    names_by_type = {
        "restaurant": ["The Local Kitchen", "City Bistro", "Main Street Grill",
                        "Corner Table", "The Rustic Plate"],
        "coffee": ["Morning Brew Café", "The Daily Grind", "Artisan Roasters",
                    "Cup & Saucer", "Bean Counter Coffee"],
        "hiking": ["Riverside Trail", "Hilltop Nature Path", "Valley View Trail",
                    "Sunset Ridge Loop", "Creek Side Walk"],
        "kayaking": ["River Adventures Kayak", "Lakeside Paddle Sports",
                      "Bay Kayak Rentals"],
    }

    names = names_by_type.get(activity, names_by_type["restaurant"])
    places = []
    for i, name in enumerate(names):
        offset_lat = random.uniform(-0.02, 0.02)
        offset_lng = random.uniform(-0.02, 0.02)
        places.append({
            "id": f"gen_{i}_{name.lower().replace(' ', '_')[:15]}",
            "name": name,
            "address": f"{random.randint(100, 999)} Main St, {city}",
            "lat": lat + offset_lat,
            "lng": lng + offset_lng,
            "rating": round(random.uniform(3.5, 4.8), 1),
            "review_count": random.randint(50, 500),
            "price_level": random.randint(1, 3),
            "types": [activity],
            "open_now": True,
            "photos": [],
            "source": "generated",
            "review_signals": {},
        })
    return places


def search_target_cafes_debug():
    """DEBUG: Search for Joe & The Juice and Andytown using Nearby Search at their exact locations."""
    target_cafes = [
        ("Joe & The Juice", 37.7851, -122.3967),
        ("Andytown Coffee Roasters", 37.7890, -122.3958),
    ]

    print("\n" + "=" * 80)
    print("DEBUG: SEARCHING FOR TARGET CAFES AT THEIR LOCATIONS")
    print("=" * 80)

    for cafe_name, lat, lng in target_cafes:
        print(f"\n{cafe_name}")
        print(f"  Searching nearby this exact location: {lat}, {lng}")

        # Strategy 1: Nearby search with type='cafe'
        print(f"\n  Strategy 1: Nearby Search WITH type='cafe'")
        params = {
            "location": f"{lat},{lng}",
            "radius": 100,  # Very small radius (100m) to catch exact location
            "type": "cafe",
            "key": GOOGLE_API_KEY,
        }
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{urllib.parse.urlencode(params)}"
        data = _http_get(url)
        results = data.get("results", [])
        print(f"    Results found: {len(results)}")
        for i, result in enumerate(results[:2], 1):
            print(f"      {i}. {result.get('name')} - types={result.get('types', [])[:3]}")

        # Strategy 2: Nearby search WITHOUT type filter
        print(f"\n  Strategy 2: Nearby Search WITHOUT type filter (keyword='coffee')")
        params2 = {
            "location": f"{lat},{lng}",
            "radius": 100,
            "keyword": "coffee",
            "key": GOOGLE_API_KEY,
        }
        url2 = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{urllib.parse.urlencode(params2)}"
        data2 = _http_get(url2)
        results2 = data2.get("results", [])
        print(f"    Results found: {len(results2)}")
        for i, result in enumerate(results2[:2], 1):
            print(f"      {i}. {result.get('name')} - types={result.get('types', [])[:3]}")

        # Strategy 3: Nearby search with NO filters
        print(f"\n  Strategy 3: Nearby Search with NO type/keyword filter")
        params3 = {
            "location": f"{lat},{lng}",
            "radius": 100,
            "key": GOOGLE_API_KEY,
        }
        url3 = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{urllib.parse.urlencode(params3)}"
        data3 = _http_get(url3)
        results3 = data3.get("results", [])
        print(f"    Results found: {len(results3)}")
        for i, result in enumerate(results3[:3], 1):
            types = result.get('types', [])
            print(f"      {i}. {result.get('name')}")
            print(f"         Primary type: {types[0] if types else 'unknown'}")
            print(f"         All types: {types[:5]}")
            if cafe_name.lower() in result.get('name', '').lower():
                print(f"         ★ FOUND TARGET CAFE!")
