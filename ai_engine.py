"""AI intent parsing and conversational engine using Claude Haiku.
Falls back to rule-based parsing when API key is not set."""
import json
import os
import urllib.request
import urllib.error
from typing import Dict, Optional, Tuple

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-3-haiku-20240307"
MAX_INPUT_TOKENS = 300
MAX_OUTPUT_TOKENS = 150


def call_claude(system_prompt: str, user_message: str) -> str:
    """Call Claude Haiku API with minimal tokens."""
    if not ANTHROPIC_API_KEY:
        return ""

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message[:MAX_INPUT_TOKENS]}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("content", [{}])[0].get("text", "")
    except Exception as e:
        print(f"Claude API error: {e}")
        return ""


PARSE_SYSTEM_PROMPT = """Convert the request into JSON constraints. Return ONLY valid JSON with scalar values (no arrays).
Allowed fields: category, cuisine, price_level(1-4), activity_type, distance_limit(miles), weather_dependency(bool), ambiance, open_now(bool), budget_per_person(number).
activity_type MUST be exactly ONE string from: restaurant, cafe, work_cafe, hiking, kayaking, outdoor, bar, shopping, date, entertainment.

CRITICAL DISTINCTIONS:
- hiking: mountain trails, elevation gain, challenging hikes, specific named trails
- outdoor: parks, casual walks, scenic areas, gardens, beaches, picnics, dog parks
- kayaking: water sports, kayak rental, paddle sports, boating

DISTANCE LIMITS:
- For work_cafe + "nearby"/"close"/coffee: distance_limit = 0.75 miles (~15 min walk)
- For other activities + "nearby"/"close": distance_limit = 1-3 miles
- "driving distance", "not too far": distance_limit = 15-20 miles
- If user mentions "walk" with parks: use 3 miles max
- If user mentions "drive" or "driving": use 15+ miles

Return JSON only. No arrays. No explanations."""


def _normalize_intent(intent: Dict) -> Dict:
    """Ensure all intent fields are scalars, not lists. Fixes LLM returning arrays."""
    for key, val in list(intent.items()):
        if isinstance(val, list):
            intent[key] = val[0] if val else None
    # If activity_type is work_cafe or cafe, prefer work_cafe when both implied
    if intent.get("activity_type") == "cafe":
        intent["activity_type"] = "work_cafe"
    return intent


def parse_intent_llm(user_input: str) -> Dict:
    """Parse user intent using Claude Haiku."""
    result = call_claude(PARSE_SYSTEM_PROMPT, f'User request: "{user_input}"')
    if result:
        try:
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(result)
            return _normalize_intent(parsed)
        except json.JSONDecodeError:
            pass
    return {}


CLARIFY_SYSTEM_PROMPT = """You help plan local outings. If the request is unclear, ask ONE short clarifying question. If clear, respond with "CLEAR". Be concise (under 30 words)."""


def get_clarifying_question(user_input: str, intent: Dict) -> Optional[str]:
    """Ask a clarifying question if needed."""
    result = call_claude(CLARIFY_SYSTEM_PROMPT, f'Request: "{user_input}"\nParsed: {json.dumps(intent)}')
    if result and "CLEAR" not in result.upper():
        return result
    return None


SUMMARY_SYSTEM_PROMPT = """You are a local outing assistant recommending places to visit. STRICT RULES:
1. Only mention wifi, seating, or amenities if they are explicitly in the data provided. NEVER invent or assume amenities.
2. If wifi data says "wifi:no" — warn the user. If wifi data is absent — do not mention wifi at all.
3. These are places to VISIT, not employment opportunities.
Summarize in 2-3 sentences: top pick name, its rating, and one concrete fact from the data."""


def summarize_results(places: list, intent: Dict, query: str) -> str:
    """Generate a brief summary of results."""
    if not places:
        return "I couldn't find any matching places. Try broadening your search or changing your location."

    # Build minimal summary for LLM — include wifi signal if relevant
    top_places = []
    for p in places[:3]:
        signals = p.get('review_signals', {})
        wifi_note = ""
        if intent.get("activity_type") in ("work_cafe", "coffee"):
            if signals.get("wifi") == "high":
                wifi_note = ", wifi:yes"
            elif signals.get("wifi") == "none":
                wifi_note = ", wifi:no"
        top_places.append(
            f"{p['name']} (rating:{p.get('rating',0)}, price:{'$'*p.get('price_level',1)}{wifi_note})"
        )

    summary_input = f"User wants: {query}\nNearby places to visit: {', '.join(top_places)}"

    result = call_claude(SUMMARY_SYSTEM_PROMPT, summary_input)
    if result:
        return result

    # Fallback: rule-based summary
    return generate_rule_summary(places, intent, query)


def generate_rule_summary(places: list, intent: Dict, query: str) -> str:
    """Generate summary without LLM."""
    top = places[0]
    name = top["name"]
    rating = top.get("rating", 0)
    count = len(places)

    parts = [f"I found {count} great option{'s' if count > 1 else ''} for you!"]
    parts.append(f"**{name}** stands out with a {rating}★ rating.")

    if top.get("travel_time_min"):
        mode = top.get("travel_mode", "car")
        parts.append(f"It's about {top['travel_time_min']} min by {mode}.")

    if top.get("review_signals"):
        signals = top["review_signals"]
        highlights = [k.replace("_", " ") for k, v in signals.items() if v == "high"]
        if highlights:
            parts.append(f"Reviewers highlight: {', '.join(highlights[:3])}.")

    return " ".join(parts)


# --- Rule-based intent parsing (no LLM needed) ---

KEYWORD_MAP = {
    "activity_type": {
        "work_cafe": ["work", "laptop", "study", "remote", "cowork", "coffee shop", "cafe"],
        "restaurant": ["restaurant", "eat", "dinner", "lunch", "brunch", "food", "meal", "hungry", "starving"],
        "hiking": ["hike", "hiking", "trail", "mountain", "elevation"],  # Removed "walk" — too generic
        "kayaking": ["kayak", "kayaking", "paddle", "canoe"],
        "outdoor": ["outdoor", "outside", "park", "beach", "picnic", "walk", "stroll", "nature", "scenic"],  # Added "walk" and "nature"
        "bar": ["bar", "drinks", "cocktail", "beer", "wine", "pub"],
        "date": ["date", "romantic", "date night", "anniversary"],
        "entertainment": ["movie", "show", "theater", "concert", "museum", "gallery", "fun things", "things to do"],  # Added "fun things" + "things to do"
    },
    "cuisine": {
        "chinese": ["chinese", "dim sum", "dumpling", "noodle", "wonton"],
        "japanese": ["japanese", "sushi", "ramen", "udon", "izakaya"],
        "italian": ["italian", "pasta", "pizza", "risotto"],
        "mexican": ["mexican", "taco", "burrito", "enchilada"],
        "indian": ["indian", "curry", "tandoori", "biryani", "naan"],
        "thai": ["thai", "pad thai", "curry"],
        "korean": ["korean", "bibimbap", "kimchi", "bbq"],
        "vietnamese": ["vietnamese", "pho", "banh mi"],
        "american": ["american", "burger", "steak", "bbq", "grill"],
    },
    "ambiance": {
        "romantic": ["romantic", "intimate", "date", "candle", "cozy"],
        "casual": ["casual", "laid back", "chill", "relaxed"],
        "upscale": ["upscale", "fancy", "fine dining", "elegant"],
        "lively": ["lively", "fun", "energetic", "bustling"],
    }
}

PRICE_KEYWORDS = {
    1: ["cheap", "budget", "inexpensive", "affordable", "dollar menu", "under $15"],
    2: ["moderate", "mid-range", "reasonable"],
    3: ["nice", "upscale", "sit-down"],
    4: ["expensive", "luxury", "high-end", "fine dining", "splurge"],
}

WEATHER_ACTIVITIES = ["hiking", "kayaking", "outdoor", "beach", "picnic", "biking"]


def parse_intent_rules(user_input: str) -> Dict:
    """Parse intent using keyword matching. No LLM required."""
    text = user_input.lower().strip()
    intent = {}

    # Detect activity type — check specific keywords first before generic ones
    # "park" + "walk" should be "outdoor", not "hiking"
    if any(kw in text for kw in ["park", "beach", "picnic"]):
        intent["activity_type"] = "outdoor"
    else:
        # Fall back to generic keyword matching
        for activity, keywords in KEYWORD_MAP["activity_type"].items():
            if any(kw in text for kw in keywords):
                intent["activity_type"] = activity
                break

    # Detect cuisine
    for cuisine, keywords in KEYWORD_MAP["cuisine"].items():
        if any(kw in text for kw in keywords):
            intent["cuisine"] = cuisine
            intent.setdefault("activity_type", "restaurant")
            break

    # Detect ambiance
    for ambiance, keywords in KEYWORD_MAP["ambiance"].items():
        if any(kw in text for kw in keywords):
            intent["ambiance"] = ambiance
            break

    # Detect price level
    for level, keywords in PRICE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            intent["price_level"] = level
            break

    # Budget per person
    import re
    budget_match = re.search(r'\$(\d+)\s*(?:per person|pp|each|/person)', text)
    if budget_match:
        budget = int(budget_match.group(1))
        intent["budget_per_person"] = budget
        if budget <= 15:
            intent["price_level"] = 1
        elif budget <= 30:
            intent["price_level"] = 2
        elif budget <= 60:
            intent["price_level"] = 3
        else:
            intent["price_level"] = 4

    # Under $X pattern
    under_match = re.search(r'under\s*\$(\d+)', text)
    if under_match and "budget_per_person" not in intent:
        budget = int(under_match.group(1))
        intent["budget_per_person"] = budget
        if budget <= 15:
            intent["price_level"] = 1
        elif budget <= 30:
            intent["price_level"] = 2
        elif budget <= 60:
            intent["price_level"] = 3

    # Less than $X pattern
    less_match = re.search(r'less than\s*\$(\d+)', text)
    if less_match and "budget_per_person" not in intent:
        budget = int(less_match.group(1))
        intent["budget_per_person"] = budget

    # Explicit transport mode — overrides user settings for this query
    if any(word in text for word in ["drive", "driving", "car", "by car", "drive to"]):
        intent["transport_override"] = "car"
        intent.setdefault("distance_limit", 20)   # driving = willing to go further
    elif any(word in text for word in ["walk", "walking", "on foot", "stroll"]):
        intent["transport_override"] = "walk"
        intent.setdefault("distance_limit", 2)

    # Citywide / anywhere scope
    if any(phrase in text for phrase in [
        "anywhere", "anywhere in", "in the city", "citywide", "best in",
        "best in sf", "all over", "across the city", "in san francisco",
        "in sf", "in new york", "in la", "in seattle"
    ]):
        intent["distance_limit"] = 30   # effectively: search the whole city
        intent["search_scope"] = "city"

    # Distance limit from explicit mileage
    dist_match = re.search(r'(\d+)\s*(?:miles?|mi)', text)
    if dist_match:
        intent["distance_limit"] = int(dist_match.group(1))

    # Near/nearby/close — short range
    if any(word in text for word in ["nearby", "near me", "close", "not far", "not too far"]):
        # For work_cafe, use tighter limit (walkable); for others, use 1 mile
        if intent.get("activity_type") == "work_cafe":
            intent["distance_limit"] = 0.75  # ~15 min walk (override any previous value)
        else:
            intent["distance_limit"] = 1  # ~20 min walk (override any previous value)

    # Weather dependency
    if any(word in text for word in ["warm", "sunny", "nice weather", "clear", "good weather"]):
        intent["weather_dependency"] = True

    # Open now
    if any(phrase in text for phrase in ["open now", "right now", "tonight", "today"]):
        intent["open_now"] = True

    # Default: if work-related keywords + coffee, set work_cafe
    if "work" in text and intent.get("activity_type") == "coffee":
        intent["activity_type"] = "work_cafe"

    # Category derivation
    activity = intent.get("activity_type", "")
    if activity in ("restaurant", "date") or intent.get("cuisine"):
        intent["category"] = "food"
    elif activity in ("coffee", "work_cafe"):
        intent["category"] = "coffee"
    elif activity in ("hiking", "kayaking", "outdoor"):
        intent["category"] = "outdoor"
    elif activity in ("bar",):
        intent["category"] = "nightlife"
    else:
        intent["category"] = "general"

    return intent


def parse_intent(user_input: str) -> Dict:
    """Parse user intent. Tries LLM first, falls back to rules."""
    # Try LLM
    llm_intent = parse_intent_llm(user_input)
    if llm_intent:
        return llm_intent

    # Fall back to rule-based
    return parse_intent_rules(user_input)


def build_search_query(intent: Dict, user_input: str = "") -> str:
    """Build a search query string from parsed intent.

    For 'outdoor' activity type, checks user input for specific types like beach, trail, etc.
    """
    parts = []

    if intent.get("cuisine"):
        parts.append(intent["cuisine"])

    activity = intent.get("activity_type", "")
    # Keep queries simple — Google Places matches on name/type, not descriptions.
    # Wifi/laptop filtering happens via review signals in the ranking engine.
    activity_queries = {
        "restaurant": "restaurant",
        "cafe": "coffee shop",
        "work_cafe": "coffee shop",   # NOT "wifi laptop friendly" — Google can't search that
        "coffee": "coffee shop",
        "hiking": "hiking trail",
        "kayaking": "water sports kayak paddle",  # Better search term for kayak locations
        "outdoor": "park",  # Default for outdoor
        "bar": "bar",
        "date": "restaurant",
        "entertainment": "entertainment",  # Museum, theater, concert, etc. — broader than "things to do"
    }

    # Get base activity query
    base_query = activity_queries.get(activity, "restaurant")

    # For outdoor activities, check if user specified a specific type (beach, trail, garden, etc.)
    if activity == "outdoor" and user_input:
        text_lower = user_input.lower()
        outdoor_types = {
            "beach": "beach",
            "waterfront": "waterfront",
            "trail": "hiking trail",
            "trail": "nature trail",
            "garden": "garden",
            "botanical": "botanical garden",
            "lake": "lake",
            "river": "river",
            "scenic overlook": "scenic overlook",
            "viewpoint": "viewpoint",
        }
        for keyword, replacement in outdoor_types.items():
            if keyword in text_lower:
                base_query = replacement
                break

    parts.append(base_query)

    if intent.get("ambiance"):
        parts.append(intent["ambiance"])

    # Don't add price terms for work/coffee activities — they're not useful for API searches
    # and just contaminate the results. Price matching happens in ranking.
    activity = intent.get("activity_type", "")
    if activity not in ("work_cafe", "cafe", "coffee"):
        price_terms = {1: "cheap", 2: "", 3: "nice", 4: "fine dining"}
        price_term = price_terms.get(intent.get("price_level", 0), "")
        if price_term:
            parts.append(price_term)

    return " ".join(parts).strip()


def generate_clarifying_question_rules(intent: Dict, user_input: str) -> Optional[str]:
    """Generate a clarifying question without LLM."""
    text = user_input.lower()

    # If very vague
    if len(text.split()) < 4 and not intent.get("activity_type") and not intent.get("cuisine"):
        return "Could you tell me more about what you're looking for? For example, are you looking for food, outdoor activities, or something else?"

    # Activity but no specifics
    if intent.get("activity_type") == "restaurant" and not intent.get("cuisine") and not intent.get("ambiance"):
        if "food" in text or "eat" in text or "hungry" in text:
            return "Any particular cuisine in mind, or are you open to anything?"

    # Coffee but unclear purpose
    if intent.get("activity_type") == "coffee" and "work" not in text and "laptop" not in text:
        return "Are you looking for a place to sit and work, or more of a grab-and-go spot?"

    # Outdoor but vague
    if intent.get("activity_type") == "outdoor" and not any(w in text for w in ["hike", "kayak", "bike", "beach"]):
        return "What kind of outdoor activity interests you? Hiking, kayaking, a park visit, or something else?"

    return None


def get_response(user_input: str, conversation_history: list = None) -> Tuple[Dict, Optional[str], str]:
    """
    Process user input and return (intent, clarifying_question, search_query).
    If clarifying_question is not None, ask it before searching.
    """
    intent = parse_intent(user_input)

    # Check if we need clarification
    clarifying = get_clarifying_question(user_input, intent)
    if not clarifying:
        clarifying = generate_clarifying_question_rules(intent, user_input)

    search_query = build_search_query(intent)

    return intent, clarifying, search_query
