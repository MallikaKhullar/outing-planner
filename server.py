"""Main Tornado web server for the Outing Planner application."""
import json
import os
from dotenv import load_dotenv
import tornado.ioloop
import tornado.web
import tornado.httpclient
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

import database as db
from ai_engine import parse_intent, build_search_query, summarize_results, generate_clarifying_question_rules
from places import search_places
from ranking import rank_places, estimate_outing_cost, estimate_travel_time, haversine_distance
from opportunities import detect_opportunities

PORT = int(os.environ.get("PORT", 8080))

# In-memory cache for fast access
_memory_cache = {}


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self, *args):
        self.set_status(204)
        self.finish()

    def get_json_body(self):
        try:
            return json.loads(self.request.body)
        except:
            return {}


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html")
        with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), "r") as f:
            html = f.read()

        # Inject Mapbox token from environment
        mapbox_token = os.environ.get("MAPBOX_TOKEN", "")
        token_script = f'<script>window.MAPBOX_TOKEN = "{mapbox_token}";</script>'
        html = html.replace("</head>", f"{token_script}\n</head>")

        self.write(html)


class SettingsHandler(BaseHandler):
    def get(self):
        settings = db.get_settings()
        self.write(json.dumps(settings or {}))

    def put(self):
        data = self.get_json_body()
        db.update_settings(data)
        self.write(json.dumps({"status": "ok"}))


class ChatHandler(BaseHandler):
    def post(self):
        data = self.get_json_body()
        user_message = data.get("message", "")
        city = data.get("city", "San Francisco")
        lat = data.get("lat", 37.7749)
        lng = data.get("lng", -122.4194)
        conversation_id = data.get("conversation_id")

        if not user_message:
            self.write(json.dumps({"error": "No message provided"}))
            return

        settings = db.get_settings() or {}

        # Create conversation if needed
        if not conversation_id:
            conversation_id = db.create_conversation(city)

        # Save user message
        db.add_message(conversation_id, "user", user_message)

        # Parse intent
        intent = parse_intent(user_message)
        print(f"\n{'='*60}")
        print(f"[CHAT] Message: '{user_message}'")
        print(f"[CHAT] Browser sent: lat={lat}, lng={lng}, city={city}")
        print(f"[INTENT] Parsed: {intent}")

        # Check for clarifying question
        clarifying = generate_clarifying_question_rules(intent, user_message)

        # If intent is clear enough, search
        places = []
        cost_estimate = None
        search_query = ""

        if intent.get("activity_type") or intent.get("cuisine") or intent.get("category"):
            search_query = build_search_query(intent, user_message)
            print(f"[SEARCH] Query: '{search_query}'")

            # Check cache
            cache_key = f"{search_query}_{city}".lower().replace(" ", "_")
            print(f"[CACHE] Key: '{cache_key}'")
            cached = db.get_cached_places(city, cache_key)

            # Always prefer browser-provided GPS
            search_lat = lat if lat != 37.7749 else (settings.get("home_lat") or lat)
            search_lng = lng if lng != -122.4194 else (settings.get("home_lng") or lng)
            print(f"[SEARCH] Using coords: lat={search_lat}, lng={search_lng}")

            if cached:
                print(f"[CACHE] HIT — returning {len(cached)} cached places (DELETE outing_planner.db to clear)")
                places = cached
            else:
                print(f"[CACHE] MISS — calling Google Places API...")
                places = search_places(search_query, search_lat, search_lng, city, intent)
                print(f"[SEARCH] Got {len(places)} places from API")
                for p in places[:5]:
                    print(f"  - {p['name']} | signals={p.get('review_signals',{})} | lat={p.get('lat'):.4f}, lng={p.get('lng'):.4f}")
                if places:
                    db.cache_places(city, cache_key, places)

            # Rank places
            user_lat = settings.get("home_lat", lat) or lat
            user_lng = settings.get("home_lng", lng) or lng
            places = rank_places(places, intent, settings, user_lat, user_lng)
            print(f"[RANK] Top 3 after ranking:")
            for p in places[:3]:
                print(f"  #{places.index(p)+1} {p['name']} score={p.get('score')} wifi={p.get('review_signals',{}).get('wifi','?')} dist={p.get('distance_miles')}mi")

            # Estimate cost
            cost_estimate = estimate_outing_cost(places, intent, settings, city, user_lat, user_lng)
        print(f"{'='*60}\n")

        # Generate response
        if clarifying and not places:
            response_text = clarifying
        elif places:
            response_text = summarize_results(places, intent, user_message)
        else:
            response_text = ("I'd love to help you plan an outing! Try something like:\n"
                           "• \"Find a cozy coffee shop to work in\"\n"
                           "• \"Cheap Chinese food nearby\"\n"
                           "• \"Hiking trails near me\"\n"
                           "• \"Nice date night restaurant under $50 per person\"")

        # Save assistant message
        db.add_message(conversation_id, "assistant", response_text, {
            "intent": intent,
            "place_count": len(places),
        })

        self.write(json.dumps({
            "conversation_id": conversation_id,
            "message": response_text,
            "intent": intent,
            "places": places[:10],
            "cost_estimate": cost_estimate,
            "search_query": search_query,
            "clarifying": clarifying if not places else None,
        }))


class OpportunitiesHandler(BaseHandler):
    def get(self):
        city = self.get_argument("city", "San Francisco")
        lat = float(self.get_argument("lat", 37.7749))
        lng = float(self.get_argument("lng", -122.4194))

        print(f"\n[OPPS] Received request: city={city}, lat={lat}, lng={lng}")

        # Check cache with location-specific key (includes lat/lng)
        cached = db.get_cached_opportunities(city, lat, lng)
        if cached:
            print(f"[OPPS] Cache HIT for {city} at ({lat}, {lng})")
            print(f"[OPPS] Returning cached weather: {cached.get('weather_data', {}).get('temp_f')}°F")
            self.write(json.dumps(cached))
            return

        print(f"[OPPS] Cache MISS for {city} at ({lat}, {lng}), fetching fresh opportunities...")
        result = detect_opportunities(lat, lng, city)
        print(f"[OPPS] Got fresh weather: {result.get('weather', {}).get('temp_f')}°F for {city}")
        db.cache_opportunities(city, lat, lng, result["suggestions"], result.get("weather", {}))

        self.write(json.dumps(result))


class ClearCacheHandler(BaseHandler):
    def post(self):
        data = self.get_json_body()
        city = data.get("city", "")
        if city:
            db.clear_city_cache(city)
            _memory_cache.pop(city, None)
        self.write(json.dumps({"status": "ok", "cleared": city}))


class HistoryHandler(BaseHandler):
    def get(self):
        conv_id = self.get_argument("conversation_id", None)
        if conv_id:
            messages = db.get_messages(int(conv_id))
            self.write(json.dumps({"messages": messages}))
        else:
            self.write(json.dumps({"messages": []}))


class ReverseGeocodeHandler(BaseHandler):
    def get(self):
        """Reverse geocode coordinates to city name using Nominatim."""
        lat = self.get_argument("lat", "")
        lng = self.get_argument("lng", "")

        if not lat or not lng:
            self.write(json.dumps({"city": "San Francisco", "source": "default"}))
            return

        try:
            import urllib.request
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=10"
            req = urllib.request.Request(url, headers={"User-Agent": "OutingPlanner/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                address = data.get("address", {})
                city = (address.get("city") or address.get("town") or
                        address.get("village") or address.get("county") or "Unknown")
                self.write(json.dumps({
                    "city": city,
                    "display_name": data.get("display_name", ""),
                    "source": "geocode",
                }))
        except Exception as e:
            self.write(json.dumps({"city": "San Francisco", "source": "fallback", "error": str(e)}))


class DebugHandler(BaseHandler):
    """Debug endpoint to test Google Places API queries."""
    def get(self):
        """Run debug search for target cafes."""
        from places import search_target_cafes_debug
        import io
        import sys

        # Capture print output
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()

        try:
            search_target_cafes_debug()
            output = buffer.getvalue()
        finally:
            sys.stdout = old_stdout

        self.write(json.dumps({
            "status": "debug_complete",
            "output": output
        }))


def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application([
        (r"/", IndexHandler),
        (r"/api/settings", SettingsHandler),
        (r"/api/chat", ChatHandler),
        (r"/api/opportunities", OpportunitiesHandler),
        (r"/api/clear-cache", ClearCacheHandler),
        (r"/api/history", HistoryHandler),
        (r"/api/reverse-geocode", ReverseGeocodeHandler),
        (r"/api/debug", DebugHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": static_path}),
    ], debug=True)


def main():
    db.init_db()
    app = make_app()
    app.listen(PORT)
    print(f"🗺️  Outing Planner running at http://localhost:{PORT}")
    print(f"   API keys configured: Google={'✓' if os.environ.get('GOOGLE_PLACES_API_KEY') else '✗'} "
          f"Yelp={'✓' if os.environ.get('YELP_API_KEY') else '✗'} "
          f"Foursquare={'✓' if os.environ.get('FOURSQUARE_API_KEY') else '✗'} "
          f"Claude={'✓' if os.environ.get('ANTHROPIC_API_KEY') else '✗'}")
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
