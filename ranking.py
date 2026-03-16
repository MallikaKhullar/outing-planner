"""Deterministic ranking engine for places. No LLM calls."""
import math
from typing import List, Dict, Optional


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance in miles between two coordinates."""
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def estimate_travel_time(distance_miles: float, mode: str = "car") -> int:
    """Estimate travel time in minutes."""
    speeds = {"car": 25, "walk": 3, "bike": 12, "transit": 15}
    speed = speeds.get(mode, 25)
    return max(1, round((distance_miles / speed) * 60))


def estimate_parking_cost(city: str, neighborhood: str = "") -> float:
    """Estimate parking cost by city heuristics."""
    city_costs = {
        "san francisco": 8.0, "new york": 15.0, "los angeles": 6.0,
        "seattle": 7.0, "chicago": 10.0, "boston": 12.0,
        "austin": 4.0, "portland": 5.0, "denver": 5.0,
        "bangalore": 1.0, "mumbai": 1.5, "delhi": 1.0,
    }
    city_lower = city.lower().strip()
    for key, cost in city_costs.items():
        if key in city_lower:
            return cost
    return 5.0


def estimate_meal_cost(price_level: int) -> Dict[str, float]:
    """Estimate meal cost per person based on price level (1-4)."""
    costs = {
        1: {"low": 8, "avg": 12, "high": 18},
        2: {"low": 15, "avg": 22, "high": 30},
        3: {"low": 25, "avg": 40, "high": 55},
        4: {"low": 50, "avg": 75, "high": 120},
    }
    return costs.get(price_level, costs[2])




def extract_review_signals(reviews: List[str]) -> Dict[str, str]:
    """Extract structured signals from review text. Handles negation for accuracy."""
    signals = {}
    text = " ".join(reviews).lower()

    keyword_signals = {
        "wifi": ["wifi", "wi-fi", "internet", "wireless"],
        "quiet": ["quiet", "peaceful", "calm", "serene"],
        "laptop_friendly": ["laptop", "work", "study", "outlet", "plug", "remote"],
        "crowded": ["crowded", "busy", "packed", "wait", "line"],
        "patio": ["patio", "outdoor", "terrace", "garden", "rooftop"],
        "cozy": ["cozy", "intimate", "warm", "comfortable"],
        "scenic": ["scenic", "view", "beautiful", "gorgeous", "vista"],
        "family_friendly": ["family", "kids", "children", "stroller"],
        "date_night": ["date", "romantic", "intimate", "candle"],
        "parking": ["parking", "lot", "garage", "valet"],
        "ambiance": ["ambiance", "atmosphere", "vibe", "decor"],
    }

    # Words that negate the following signal
    negation_words = ["no ", "not ", "don't ", "doesn't ", "didn't ", "lack ", "lacks ", "without ", "poor ", "bad ", "terrible "]

    for signal, keywords in keyword_signals.items():
        positive_count = 0
        negative_count = 0

        for keyword in keywords:
            # Count positive mentions
            positive_count += text.count(keyword)

            # Count negative mentions (e.g., "no wifi", "poor wifi")
            for neg in negation_words:
                negative_count += text.count(neg + keyword)

        # If more negative mentions than positive, mark as "none"
        if negative_count > positive_count:
            signals[signal] = "none"
        else:
            total_words = len(text.split())
            if total_words == 0:
                signals[signal] = "none"
            elif (positive_count - negative_count) > total_words * 0.01:
                signals[signal] = "high"
            elif (positive_count - negative_count) > 0:
                signals[signal] = "medium"
            else:
                signals[signal] = "none"

    return signals


def score_place(place: Dict, intent: Dict, user_settings: Dict,
                user_lat: float = 0, user_lng: float = 0) -> float:
    """Score a place based on intent match and user preferences. Returns 0-100."""
    is_debug = "andytown" in place.get("name", "").lower() or "joe" in place.get("name", "").lower()

    # CRITICAL: Filter out places with zero ratings/reviews early
    rating = place.get("rating", 0)
    review_count = place.get("review_count", 0)
    if rating == 0 or review_count == 0:
        if is_debug:
            print(f"[DEBUG] {place['name']}: FILTERED - rating={rating}, review_count={review_count}")
        return -1  # Signal to filter out completely

    # CRITICAL: Exclude fast food chains from coffee/cafe queries
    # McDonald's, Starbucks (when primarily fast food) don't meet quality standards for work_cafe
    activity_type = intent.get("activity_type", "")
    category = intent.get("category", "")
    place_name = place.get("name", "").lower()

    if activity_type in ("work_cafe", "cafe") or category in ("coffee", "cafe"):
        # Exclude McDonald's for coffee/cafe queries (not a real cafe)
        if "mcdonald" in place_name:
            if is_debug or "mcdonald" in place_name:
                print(f"[DEBUG] {place['name']}: FILTERED - excluded fast food for coffee/cafe query")
            return -1

    # CRITICAL: For work_cafe, filter out places that aren't cafes
    # Google Places API is too liberal with type assignments, so we need strict filtering
    if activity_type == "work_cafe":
        place_types = [t.lower() for t in place.get("types", [])]

        # Types that definitely disqualify a place (common primary types for non-cafes)
        disqualifying_types = ["restaurant", "bar", "bank", "florist", "car_rental",
                               "jewelry_store", "parking", "garage", "car_repair",
                               "meal_takeaway", "food_delivery", "meal_delivery"]

        # If ANY disqualifying type is present AND it's not ONLY a cafe, filter out
        # (The logic: "The Grove" is cafe + bar + restaurant → should be filtered)
        primary_type = place_types[0] if place_types else ""
        has_disqualifying = any(t in place_types for t in disqualifying_types)
        has_cafe = any(t in ["cafe", "coffee_shop", "coffee"] for t in place_types)

        # Debug for McDonald's or debug places
        if is_debug or "mcdonald" in place.get("name", "").lower():
            print(f"[DEBUG] {place['name']}: types={place_types}, primary={primary_type}, has_disqualifying={has_disqualifying}, has_cafe={has_cafe}")

        # FIXED: Don't rely on type ORDER - Google Places API doesn't guarantee order!
        # Simple rule: If it has "cafe" anywhere in types, ALLOW IT for work_cafe
        #            If it DOESN'T have "cafe", REJECT IT (it's purely a restaurant/bar/etc)
        if has_disqualifying:
            if is_debug or "mcdonald" in place.get("name", "").lower():
                print(f"[DEBUG] {place['name']}: has_cafe={has_cafe}, has_disqualifying={has_disqualifying}")

            # SIMPLE: If it has cafe type, allow it. If not, reject it.
            if not has_cafe:
                if is_debug or "mcdonald" in place.get("name", "").lower():
                    print(f"[DEBUG] {place['name']}: FILTERED - no cafe type (is pure restaurant/bar)")
                return -1
            else:
                if is_debug or "mcdonald" in place.get("name", "").lower():
                    print(f"[DEBUG] {place['name']}: ALLOWED - has cafe type")

        # Must have cafe or coffee in the types at all
        if not has_cafe:
            if is_debug:
                print(f"[DEBUG] {place['name']}: FILTERED - no cafe type")
            return -1

    score = 50.0  # Base score

    # Debug: Show distance_limit for problem places
    if is_debug or "mcdonald" in place.get("name", "").lower():
        dist_limit = intent.get("distance_limit", 20)
        print(f"[DEBUG SCORE] {place['name']}: distance_limit={dist_limit}, base_score={score}, rating={rating}")

    # Rating score (0-25 points)
    if rating > 0:
        score += (rating / 5.0) * 25
        if is_debug or "mcdonald" in place.get("name", "").lower():
            print(f"[DEBUG SCORE] {place['name']}: after rating (+{rating}): score={score}")

    # Price match (0-15 points)
    target_price = intent.get("price_level")
    place_price = place.get("price_level", 2)
    if target_price:
        price_diff = abs(place_price - target_price)
        score += max(0, 15 - price_diff * 5)
    else:
        budget_pref = user_settings.get("budget_preference", "moderate")
        if budget_pref == "budget" and place_price <= 2:
            score += 10
        elif budget_pref == "moderate" and place_price <= 3:
            score += 8
        elif budget_pref == "luxury" and place_price >= 3:
            score += 10

    # Distance score (0-20 points)
    place_lat = place.get("lat", 0)
    place_lng = place.get("lng", 0)
    if user_lat and user_lng and place_lat and place_lng:
        dist = haversine_distance(user_lat, user_lng, place_lat, place_lng)
        place["distance_miles"] = round(dist, 1)

        max_dist = intent.get("distance_limit", 20)
        if dist <= max_dist:
            dist_bonus = max(0, 20 * (1 - dist / max_dist))
            score += dist_bonus
            if is_debug or "mcdonald" in place.get("name", "").lower():
                print(f"[DEBUG SCORE] {place['name']}: dist={dist:.1f}mi, max_dist={max_dist}, dist_bonus={dist_bonus:.1f}, score={score:.1f}")
        else:
            score -= 10

        # Travel time
        mode = user_settings.get("transportation", "car")
        walk_threshold = user_settings.get("walking_threshold_min", 15)
        walk_time = estimate_travel_time(dist, "walk")
        if walk_time <= walk_threshold:
            place["travel_mode"] = "walk"
            place["travel_time_min"] = walk_time
        else:
            place["travel_mode"] = mode
            place["travel_time_min"] = estimate_travel_time(dist, mode)

    # Review signals match — only use signals relevant to the intent
    signals = place.get("review_signals", {})
    activity_type = intent.get("activity_type", "")

    if activity_type in ("work_cafe", "cafe", "coffee"):
        # For work cafes, wifi/laptop matter most, but don't filter aggressively
        # A cafe with no reviews mentioning wifi might still have it
        wifi_signal = signals.get("wifi", "none")
        laptop_signal = signals.get("laptop_friendly", "none")

        # Score wifi signal (don't filter out, just penalize if missing)
        if wifi_signal == "high": score += 15  # Strong bonus for confirmed good wifi
        elif wifi_signal == "medium": score += 8  # Good wifi detected in reviews
        elif wifi_signal == "none": score -= 5  # Mild penalty if not mentioned

        # Score laptop friendliness
        if laptop_signal == "high": score += 10
        elif laptop_signal == "medium": score += 5
        elif laptop_signal == "none": score -= 15  # Mild penalty if not mentioned as laptop friendly

        # Quiet is important for working
        if signals.get("quiet") in ("high", "medium"): score += 8
        elif signals.get("quiet") == "none": score -= 5  # Penalty if known to be crowded/loud

        # Crowded places are bad for working
        if signals.get("crowded") == "high": score -= 10  # Heavy penalty for busy places

    elif activity_type == "date" or intent.get("ambiance") == "romantic":
        if signals.get("date_night") in ("high", "medium"): score += 8
        if signals.get("cozy") in ("high", "medium"): score += 5
        if signals.get("ambiance") in ("high", "medium"): score += 5
        if signals.get("crowded") == "high": score -= 5   # Crowded = bad for dates
        if signals.get("patio") in ("high", "medium"): score += 3

    elif activity_type in ("hiking", "kayaking", "outdoor"):
        if signals.get("scenic") in ("high", "medium"): score += 15
        if signals.get("patio") in ("high", "medium"): score += 3

    elif activity_type == "restaurant":
        if signals.get("ambiance") in ("high", "medium"): score += 5
        if signals.get("crowded") == "high": score -= 3

    elif activity_type == "bar":
        if signals.get("ambiance") in ("high", "medium"): score += 5
        if signals.get("cozy") in ("high", "medium"): score += 3

    # Open now handling — critical for time-sensitive activities (dinner, lunch, coffee)
    open_now = place.get("open_now")
    activity_type = intent.get("activity_type", "")
    is_time_sensitive = activity_type in ("restaurant", "cafe", "work_cafe", "coffee", "bar", "dining")

    if is_time_sensitive:
        # For time-sensitive activities: HEAVY penalty if closed, BONUS if open
        if open_now is True:
            score += 15  # Strong bonus for confirmed open
        elif open_now is False:
            score -= 30  # Heavy penalty for confirmed closed
        # else: open_now is None (unknown) — no penalty or bonus
    else:
        # For other activities (hiking, parks, etc): lighter open_now bonus
        if open_now is True:
            score += 5
        elif open_now is False:
            score -= 10  # Lighter penalty for outdoor activities

    # Photo quality bonus
    if place.get("photos") and len(place.get("photos", [])) > 3:
        score += 3

    # Review count bonus
    if review_count > 100:
        score += 5
    elif review_count > 50:
        score += 3

    return min(100, max(0, round(score, 1)))


def rank_places(places: List[Dict], intent: Dict, user_settings: Dict,
                user_lat: float = 0, user_lng: float = 0) -> List[Dict]:
    """Rank places by score. Returns sorted list with scores, filtering out low-quality results."""
    scored_places = []

    for place in places:
        score = score_place(place, intent, user_settings, user_lat, user_lng)
        place["score"] = score

        # Debug: Show scores for premium cafes, McDonald's, and high-scoring places
        name_lower = place.get("name", "").lower()
        if "andytown" in name_lower or "joe" in name_lower or "mcdonald" in name_lower or score >= 90:
            print(f"[DEBUG RANK] {place['name']}: score={score}, types={place.get('types', [])}, rating={place.get('rating')}, activity_type={intent.get('activity_type')}")

        # Filter out places with score -1 (zero ratings/reviews)
        if score >= 0:
            scored_places.append(place)

    scored_places.sort(key=lambda p: p["score"], reverse=True)
    return scored_places


def estimate_outing_cost(places: List[Dict], intent: Dict,
                          user_settings: Dict, city: str,
                          user_lat: float = 0, user_lng: float = 0) -> Dict:
    """Estimate total outing cost for the TOP RECOMMENDATION ONLY (places[0]).

    Includes: activity cost, parking, transit. Does NOT include tolls.
    """
    cost_breakdown = {"items": [], "total": 0}

    for place in places[:1]:  # Primary destination
        # Meal / activity cost
        price_level = place.get("price_level", 2)
        activity = intent.get("activity_type", "restaurant")

        if activity in ("restaurant", "dining", "date"):
            meal = estimate_meal_cost(price_level)
            cost_breakdown["items"].append({
                "label": f"Meal ({place.get('name', 'Restaurant')})",
                "estimate": meal["avg"],
                "range": f"${meal['low']}-${meal['high']}"
            })
        elif activity in ("cafe", "work_cafe", "coffee"):
            cost_breakdown["items"].append({
                "label": "Coffee & snacks",
                "estimate": 8,
                "range": "$5-$12"
            })
        elif activity in ("hiking", "outdoor"):
            cost_breakdown["items"].append({
                "label": "Activity (entry/rental)",
                "estimate": 10,
                "range": "$0-$25"
            })
        elif activity == "kayaking":
            cost_breakdown["items"].append({
                "label": "Kayak rental",
                "estimate": 35,
                "range": "$25-$50"
            })

        # Parking — skip if walking distance
        is_walking = place.get("travel_mode") == "walk"
        if user_settings.get("transportation") == "car" and not is_walking:
            parking = estimate_parking_cost(city)
            if parking > 0:
                cost_breakdown["items"].append({
                    "label": "Parking",
                    "estimate": parking,
                    "range": f"${parking - 2}-${parking + 4}"
                })
        elif is_walking:
            cost_breakdown["items"].append({
                "label": "Walking distance — no parking needed",
                "estimate": 0,
                "range": "$0"
            })

    cost_breakdown["total"] = sum(item["estimate"] for item in cost_breakdown["items"])
    return cost_breakdown
