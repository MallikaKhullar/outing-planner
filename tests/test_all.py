"""Automated tests for the Outing Planner application."""
import sys
import os
import json
import unittest

# Add parent dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ai_engine import parse_intent_rules, build_search_query, generate_clarifying_question_rules
from ranking import (
    haversine_distance, estimate_travel_time, estimate_parking_cost,
    estimate_meal_cost, score_place, rank_places,
    estimate_outing_cost, extract_review_signals
)
from opportunities import check_day_context, check_season, get_demo_weather
import database as db


class TestIntentParsing(unittest.TestCase):
    """Test intent parsing for various user queries."""

    def test_cheap_chinese_food(self):
        intent = parse_intent_rules("cheap Chinese food")
        self.assertEqual(intent.get("cuisine"), "chinese")
        self.assertEqual(intent.get("price_level"), 1)
        self.assertIn(intent.get("category"), ["food"])

    def test_coffee_shop_to_work(self):
        intent = parse_intent_rules("I want a coffee shop where I can work on my laptop")
        self.assertEqual(intent.get("activity_type"), "work_cafe")
        self.assertEqual(intent.get("category"), "coffee")

    def test_nearby_coffee_to_work(self):
        intent = parse_intent_rules("nearby coffee shop to work in")
        self.assertEqual(intent.get("activity_type"), "work_cafe")
        # Nearby work_cafe should have tight distance limit (~15 min walk = 0.75 mi)
        self.assertEqual(intent.get("distance_limit"), 0.75)

    def test_hiking_near_me(self):
        intent = parse_intent_rules("I want to go hiking nearby")
        self.assertEqual(intent.get("activity_type"), "hiking")
        self.assertEqual(intent.get("category"), "outdoor")
        self.assertIsNotNone(intent.get("distance_limit"))

    def test_date_night_budget(self):
        intent = parse_intent_rules("Nice sit-down place for a date under $50 per person")
        self.assertEqual(intent.get("activity_type"), "date")
        self.assertIsNotNone(intent.get("budget_per_person"))
        self.assertLessEqual(intent.get("budget_per_person", 0), 50)

    def test_warm_weather_kayaking(self):
        intent = parse_intent_rules("It's warm today and I want to go kayaking somewhere not too far away")
        self.assertEqual(intent.get("activity_type"), "kayaking")
        self.assertTrue(intent.get("weather_dependency"))

    def test_sushi_restaurant(self):
        intent = parse_intent_rules("I want sushi for dinner tonight")
        self.assertEqual(intent.get("cuisine"), "japanese")
        self.assertTrue(intent.get("open_now"))

    def test_budget_bar(self):
        intent = parse_intent_rules("cheap drinks at a bar nearby")
        self.assertEqual(intent.get("activity_type"), "bar")
        self.assertEqual(intent.get("price_level"), 1)

    def test_vague_input(self):
        intent = parse_intent_rules("I'm hungry")
        self.assertEqual(intent.get("activity_type"), "restaurant")

    def test_outdoor_generic(self):
        intent = parse_intent_rules("I want to do something outside today")
        self.assertEqual(intent.get("activity_type"), "outdoor")


class TestSearchQueryBuilding(unittest.TestCase):

    def test_chinese_restaurant_query(self):
        intent = {"cuisine": "chinese", "activity_type": "restaurant", "price_level": 1}
        query = build_search_query(intent)
        self.assertIn("chinese", query)
        self.assertIn("cheap", query)

    def test_work_cafe_query(self):
        intent = {"activity_type": "work_cafe", "category": "coffee"}
        query = build_search_query(intent)
        # Search query is simple ("coffee shop"), wifi/laptop filtering happens in ranking via review signals
        self.assertIn("coffee", query.lower())

    def test_hiking_query(self):
        intent = {"activity_type": "hiking"}
        query = build_search_query(intent)
        self.assertIn("hiking", query.lower())


class TestRankingEngine(unittest.TestCase):

    def setUp(self):
        self.settings = {
            "transportation": "car",
            "walking_threshold_min": 15,
            "budget_preference": "moderate",
            "toll_preference": "avoid",
        }
        self.user_lat = 37.7749
        self.user_lng = -122.4194

    def test_haversine_distance(self):
        # SF to Oakland ~ 8 miles
        dist = haversine_distance(37.7749, -122.4194, 37.8044, -122.2712)
        self.assertGreater(dist, 5)
        self.assertLess(dist, 15)

    def test_travel_time_car(self):
        time = estimate_travel_time(10, "car")
        self.assertGreater(time, 15)
        self.assertLess(time, 30)

    def test_travel_time_walk(self):
        time = estimate_travel_time(1, "walk")
        self.assertGreater(time, 15)

    def test_parking_cost(self):
        sf_parking = estimate_parking_cost("San Francisco")
        self.assertGreater(sf_parking, 0)
        ny_parking = estimate_parking_cost("New York")
        self.assertGreater(ny_parking, sf_parking)

    def test_meal_cost_levels(self):
        cheap = estimate_meal_cost(1)
        expensive = estimate_meal_cost(4)
        self.assertLess(cheap["avg"], expensive["avg"])


    def test_scoring(self):
        place = {
            "name": "Test Place",
            "rating": 4.5,
            "price_level": 2,
            "lat": 37.78,
            "lng": -122.41,
            "review_count": 200,
            "open_now": True,
            "photos": ["img1", "img2", "img3", "img4"],
            "types": ["cafe", "establishment"],
            "review_signals": {"wifi": "high", "laptop_friendly": "high"},
        }
        intent = {"activity_type": "work_cafe", "price_level": 2}
        score = score_place(place, intent, self.settings, self.user_lat, self.user_lng)
        self.assertGreater(score, 50)

    def test_ranking_order(self):
        places = [
            {"name": "Low", "rating": 2.0, "price_level": 3, "lat": 37.7, "lng": -122.5,
             "review_count": 10, "open_now": False, "photos": [], "review_signals": {}},
            {"name": "High", "rating": 4.8, "price_level": 2, "lat": 37.78, "lng": -122.41,
             "review_count": 500, "open_now": True, "photos": ["a","b","c","d"], "review_signals": {}},
        ]
        intent = {"activity_type": "restaurant", "price_level": 2}
        ranked = rank_places(places, intent, self.settings, self.user_lat, self.user_lng)
        self.assertEqual(ranked[0]["name"], "High")

    def test_cost_estimation(self):
        places = [{"name": "Cafe", "price_level": 2, "lat": 37.78, "lng": -122.41}]
        intent = {"activity_type": "cafe"}
        cost = estimate_outing_cost(places, intent, self.settings, "San Francisco",
                                      self.user_lat, self.user_lng)
        self.assertGreater(cost["total"], 0)
        self.assertGreater(len(cost["items"]), 0)

    def test_joe_the_juice_cafe_primary(self):
        """Test that JOE & THE JUICE is NOT filtered out when cafe is primary type."""
        # JOE & THE JUICE has types: ['cafe', ..., 'restaurant', 'meal_takeaway']
        # With the fix, it should pass because 'cafe' is primary
        place = {
            "name": "JOE & THE JUICE",
            "rating": 4.0,
            "review_count": 500,
            "price_level": 2,
            "types": ["cafe", "establishment", "food", "meal_takeaway", "point_of_interest", "restaurant", "store"],
            "lat": 37.7851,
            "lng": -122.3967,
            "open_now": True,
            "photos": [],
            "review_signals": {"wifi": "medium", "laptop_friendly": "medium"},
        }
        intent = {"activity_type": "work_cafe", "price_level": 2}
        score = score_place(place, intent, self.settings, self.user_lat, self.user_lng)
        # Score should NOT be -1 (which would filter it out)
        self.assertGreaterEqual(score, 0, "JOE & THE JUICE should NOT be filtered out (score -1)")
        self.assertGreater(score, 50, "JOE & THE JUICE should have a reasonable score for work_cafe")

    def test_nearby_coffee_both_premium_cafes(self):
        """Test that Andytown and JOE & THE JUICE both score well for nearby work_cafe."""
        intent = {"activity_type": "work_cafe", "price_level": 2}

        # Andytown Coffee Roasters
        andytown = {
            "name": "Andytown Coffee Roasters",
            "rating": 4.4,
            "review_count": 500,
            "price_level": 2,
            "types": ["cafe", "establishment", "food", "point_of_interest", "store"],
            "lat": 37.7890,
            "lng": -122.3958,
            "open_now": True,
            "photos": [],
            "review_signals": {"wifi": "medium", "laptop_friendly": "medium"},
        }

        # JOE & THE JUICE
        joe = {
            "name": "JOE & THE JUICE",
            "rating": 4.0,
            "review_count": 500,
            "price_level": 2,
            "types": ["cafe", "establishment", "food", "meal_takeaway", "point_of_interest", "restaurant", "store"],
            "lat": 37.7851,
            "lng": -122.3967,
            "open_now": True,
            "photos": [],
            "review_signals": {"wifi": "medium", "laptop_friendly": "medium"},
        }

        andytown_score = score_place(andytown, intent, self.settings, self.user_lat, self.user_lng)
        joe_score = score_place(joe, intent, self.settings, self.user_lat, self.user_lng)

        # Both should score well (> 50) for work_cafe
        self.assertGreater(andytown_score, 50, f"Andytown should score well, got {andytown_score}")
        self.assertGreater(joe_score, 50, f"JOE & THE JUICE should score well, got {joe_score}")

    def test_mcdonalds_excluded_for_work_cafe(self):
        """Test that McDonald's is excluded from work_cafe queries."""
        mcdonalds = {
            "name": "McDonald's",
            "rating": 3.3,
            "review_count": 1000,
            "price_level": 1,
            "types": ["cafe", "establishment", "meal_takeaway", "restaurant", "point_of_interest", "store", "food"],
            "lat": 37.7890,
            "lng": -122.3958,
            "open_now": True,
            "photos": [],
            "review_signals": {},
        }
        intent = {"activity_type": "work_cafe", "price_level": 2}
        score = score_place(mcdonalds, intent, self.settings, self.user_lat, self.user_lng)
        # Score should be -1 (excluded)
        self.assertEqual(score, -1, f"McDonald's should be excluded from work_cafe, but got score {score}")

    def test_mcdonalds_allowed_for_restaurant(self):
        """Test that McDonald's is NOT excluded for regular restaurant queries."""
        mcdonalds = {
            "name": "McDonald's",
            "rating": 3.3,
            "review_count": 1000,
            "price_level": 1,
            "types": ["meal_takeaway", "restaurant", "point_of_interest", "store", "food"],
            "lat": 37.7890,
            "lng": -122.3958,
            "open_now": True,
            "photos": [],
            "review_signals": {},
        }
        intent = {"activity_type": "restaurant", "price_level": 1}
        score = score_place(mcdonalds, intent, self.settings, self.user_lat, self.user_lng)
        # Score should NOT be -1 (not excluded)
        self.assertNotEqual(score, -1, f"McDonald's should NOT be excluded for restaurant queries, but got score {score}")


class TestReviewSignals(unittest.TestCase):

    def test_wifi_signal(self):
        reviews = ["Great wifi here", "Fast wifi connection", "Good internet"]
        signals = extract_review_signals(reviews)
        self.assertEqual(signals["wifi"], "high")

    def test_quiet_signal(self):
        reviews = ["Very quiet place", "Peaceful atmosphere"]
        signals = extract_review_signals(reviews)
        self.assertIn(signals["quiet"], ["high", "medium"])

    def test_no_signals(self):
        reviews = ["Nice food", "Good service"]
        signals = extract_review_signals(reviews)
        self.assertEqual(signals.get("wifi", "none"), "none")


class TestClarifyingQuestions(unittest.TestCase):

    def test_vague_input_triggers_question(self):
        intent = {}
        q = generate_clarifying_question_rules(intent, "hi")
        self.assertIsNotNone(q)

    def test_clear_input_no_question(self):
        intent = {"activity_type": "hiking", "category": "outdoor"}
        q = generate_clarifying_question_rules(intent, "I want to go hiking nearby on a trail")
        self.assertIsNone(q)

    def test_food_without_cuisine(self):
        intent = {"activity_type": "restaurant"}
        q = generate_clarifying_question_rules(intent, "I'm hungry, want to eat food")
        self.assertIsNotNone(q)


class TestOpportunities(unittest.TestCase):

    def test_day_context(self):
        ctx = check_day_context()
        self.assertIn(ctx["day_of_week"], [
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
        ])
        self.assertIn(ctx["time_of_day"], ["morning", "afternoon", "evening", "night"])

    def test_season(self):
        season = check_season()
        self.assertIn(season["season"], ["spring", "summer", "fall", "winter"])

    def test_demo_weather(self):
        weather = get_demo_weather()
        self.assertIn("temp_f", weather)
        self.assertIn("is_warm", weather)


class TestCityAndCache(unittest.TestCase):

    def setUp(self):
        # Use test database
        db.DB_PATH = "/tmp/test_outing_planner.db"
        db.init_db()

    def tearDown(self):
        import os
        try:
            os.remove("/tmp/test_outing_planner.db")
        except:
            pass

    def test_settings_crud(self):
        settings = db.get_settings()
        self.assertIsNotNone(settings)

        db.update_settings({"city": "Seattle", "transportation": "walk"})
        updated = db.get_settings()
        self.assertEqual(updated["city"], "Seattle")
        self.assertEqual(updated["transportation"], "walk")

    def test_cache_places(self):
        db.cache_places("San Francisco", "test_query", [{"name": "Test"}])
        cached = db.get_cached_places("San Francisco", "test_query")
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0]["name"], "Test")

    def test_clear_cache(self):
        db.cache_places("Portland", "test", [{"name": "A"}])
        db.clear_city_cache("Portland")
        cached = db.get_cached_places("Portland", "test")
        self.assertIsNone(cached)

    def test_conversation(self):
        conv_id = db.create_conversation("San Francisco")
        self.assertIsNotNone(conv_id)
        db.add_message(conv_id, "user", "test message")
        msgs = db.get_messages(conv_id)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "test message")


class TestExampleQueries(unittest.TestCase):
    """Test the full pipeline with example queries from the spec."""

    def _run_query(self, text):
        intent = parse_intent_rules(text)
        query = build_search_query(intent)
        return intent, query

    def test_warm_hiking(self):
        intent, query = self._run_query(
            "It's warm today and I want to go kayaking or hiking somewhere not too far away."
        )
        self.assertIn(intent.get("activity_type"), ["kayaking", "hiking"])
        self.assertTrue(intent.get("weather_dependency"))

    def test_date_restaurant(self):
        intent, query = self._run_query(
            "I want to go on a date to a nice sit-down restaurant but spend less than $50 per person."
        )
        self.assertIn(intent.get("activity_type"), ["date", "restaurant"])
        self.assertIsNotNone(intent.get("budget_per_person"))

    def test_cheap_chinese(self):
        intent, query = self._run_query(
            "I'm in the mood for Chinese food but not expensive."
        )
        self.assertEqual(intent.get("cuisine"), "chinese")

    def test_laptop_cafe(self):
        intent, query = self._run_query(
            "I want a coffee shop where it's normal to sit with a laptop and work."
        )
        self.assertEqual(intent.get("activity_type"), "work_cafe")

    def test_fun_things_to_do(self):
        """Test that vague 'fun things to do' queries map to entertainment, not parks."""
        intent, query = self._run_query(
            "Fun things to do this weekend nearby"
        )
        self.assertEqual(intent.get("activity_type"), "entertainment")
        self.assertIn("entertainment", query.lower())
        self.assertNotIn("park", query.lower())  # Should NOT search for parks

    def test_things_to_do_vague(self):
        """Test that 'things to do' queries map to entertainment."""
        intent, query = self._run_query(
            "What things to do are open now?"
        )
        self.assertEqual(intent.get("activity_type"), "entertainment")
        self.assertTrue(intent.get("open_now"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
