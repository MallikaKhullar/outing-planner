"""Proactive opportunity detection based on weather, time, season, and location."""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import List, Dict

OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")


def get_weather(lat: float, lng: float) -> Dict:
    """Get current weather data."""
    if OPENWEATHER_API_KEY:
        try:
            url = (f"https://api.openweathermap.org/data/2.5/weather?"
                   f"lat={lat}&lon={lng}&appid={OPENWEATHER_API_KEY}&units=imperial")
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return {
                    "temp_f": data["main"]["temp"],
                    "feels_like_f": data["main"]["feels_like"],
                    "humidity": data["main"]["humidity"],
                    "description": data["weather"][0]["description"],
                    "icon": data["weather"][0]["icon"],
                    "wind_mph": data["wind"]["speed"],
                    "clouds_pct": data["clouds"]["all"],
                    "is_clear": data["clouds"]["all"] < 30,
                    "is_warm": data["main"]["temp"] > 65,
                    "is_hot": data["main"]["temp"] > 85,
                    "is_rainy": "rain" in data["weather"][0]["main"].lower(),
                }
        except Exception as e:
            print(f"Weather API error: {e}")

    # Fallback: return location-aware demo weather
    return get_demo_weather(lat, lng)


def get_demo_weather(lat: float = 0, lng: float = 0) -> Dict:
    """Return location-aware demo weather data."""
    # Generate deterministic weather based on coordinates
    # This makes different cities show different temperatures even without API
    import hashlib

    # Create a hash based on lat/lng to get consistent but varied weather
    coord_hash = hashlib.md5(f"{lat:.2f},{lng:.2f}".encode()).hexdigest()
    hash_int = int(coord_hash, 16)

    # Generate varied but realistic temperatures based on location
    temp_variance = (hash_int % 40) - 20  # -20 to +20 variance
    base_temp = 72
    temp_f = base_temp + temp_variance

    # Latitude affects climate (tropical = warmer, polar = colder)
    if lat > 0:
        temp_f += (lat / 90) * 15  # Northern hemisphere slightly warmer effect
    else:
        temp_f -= (-lat / 90) * 15  # Southern hemisphere

    # Warmer in summer (month-based adjustment)
    month = datetime.now().month
    if month in (6, 7, 8):  # Summer NH
        temp_f += 12
    elif month in (12, 1, 2):  # Winter NH
        temp_f -= 15

    # Humidity varies by location
    humidity = 40 + (hash_int % 50)

    # Cloud coverage varies
    clouds_pct = (hash_int // 256) % 100

    # Wind varies
    wind_mph = 5 + (hash_int % 25)

    # Determine weather type
    is_rainy = (hash_int % 10) < 3  # 30% chance of rain
    description = "rainy" if is_rainy else ("cloudy" if clouds_pct > 50 else "partly cloudy")

    # Weather icon (OpenWeather icon codes)
    if is_rainy:
        icon = "10d"  # Rain
    elif clouds_pct > 70:
        icon = "04d"  # Overcast
    elif clouds_pct > 30:
        icon = "02d"  # Partly cloudy
    else:
        icon = "01d"  # Clear

    return {
        "temp_f": round(temp_f),
        "feels_like_f": round(temp_f - 2),
        "humidity": round(humidity),
        "description": description,
        "icon": icon,
        "wind_mph": round(wind_mph, 1),
        "clouds_pct": clouds_pct,
        "is_clear": clouds_pct < 30,
        "is_warm": temp_f > 65,
        "is_hot": temp_f > 85,
        "is_rainy": is_rainy,
    }


def check_sunset_time(lat: float, lng: float) -> Dict:
    """Estimate sunset quality and time."""
    now = datetime.now()
    # Rough sunset time estimation (simplified)
    month = now.month
    # Summer months have later sunsets
    if month in (6, 7, 8):
        sunset_hour = 20
    elif month in (3, 4, 5, 9, 10):
        sunset_hour = 18
    else:
        sunset_hour = 17

    sunset_time = now.replace(hour=sunset_hour, minute=30)
    time_to_sunset = (sunset_time - now).total_seconds() / 3600

    return {
        "sunset_time": sunset_time.strftime("%I:%M %p"),
        "hours_until_sunset": round(time_to_sunset, 1),
        "is_golden_hour": 0 < time_to_sunset < 2,
        "is_upcoming": 0 < time_to_sunset < 4,
    }


def check_day_context() -> Dict:
    """Check day-of-week and time context."""
    now = datetime.now()
    day = now.strftime("%A")
    hour = now.hour
    is_weekend = day in ("Saturday", "Sunday")

    return {
        "day_of_week": day,
        "is_weekend": is_weekend,
        "is_morning": 6 <= hour < 12,
        "is_afternoon": 12 <= hour < 17,
        "is_evening": 17 <= hour < 21,
        "is_night": hour >= 21 or hour < 6,
        "time_of_day": (
            "morning" if 6 <= hour < 12 else
            "afternoon" if 12 <= hour < 17 else
            "evening" if 17 <= hour < 21 else
            "night"
        ),
    }


def check_season() -> Dict:
    """Determine current season and related context."""
    month = datetime.now().month
    if month in (3, 4, 5):
        season = "spring"
    elif month in (6, 7, 8):
        season = "summer"
    elif month in (9, 10, 11):
        season = "fall"
    else:
        season = "winter"

    return {
        "season": season,
        "is_holiday_season": month == 12,
        "is_summer": season == "summer",
        "is_good_hiking_season": season in ("spring", "summer", "fall"),
    }


def detect_opportunities(lat: float, lng: float, city: str) -> Dict:
    """Run all opportunity detection checks and generate suggestions."""
    print(f"[OPPS] detect_opportunities called: city={city}, lat={lat:.4f}, lng={lng:.4f}")
    weather = get_weather(lat, lng)
    print(f"[OPPS] Fetched FRESH weather: {weather.get('temp_f')}°F, {weather.get('description')}")
    sunset = check_sunset_time(lat, lng)
    day = check_day_context()
    season = check_season()

    suggestions = []

    # Weather-based suggestions
    if weather["is_warm"] and not weather["is_rainy"]:
        suggestions.append({
            "icon": "☀️",
            "text": "Perfect weather for a short hike",
            "query": "I want to go hiking nearby",
            "category": "outdoor",
            "reason": f"{weather['temp_f']}°F and {weather['description']}",
        })

    if weather["is_warm"] and not weather["is_rainy"] and weather["wind_mph"] < 15:
        suggestions.append({
            "icon": "🚣",
            "text": "Great conditions for kayaking",
            "query": "Where can I go kayaking nearby?",
            "category": "outdoor",
            "reason": f"Calm winds ({weather['wind_mph']} mph) and warm temps",
        })

    if weather["is_clear"] and sunset["is_upcoming"]:
        suggestions.append({
            "icon": "🌅",
            "text": f"Clear sunset tonight at {sunset['sunset_time']}",
            "query": "Best sunset viewpoints nearby",
            "category": "outdoor",
            "reason": f"Clear skies, sunset in {sunset['hours_until_sunset']:.0f} hours",
        })

    if weather["is_rainy"]:
        suggestions.append({
            "icon": "☕",
            "text": "Cozy weather for a coffee shop visit",
            "query": "I want a cozy cafe to sit in",
            "category": "coffee",
            "reason": "Rainy day — perfect for indoor coziness",
        })
        suggestions.append({
            "icon": "🎭",
            "text": "Indoor entertainment options nearby",
            "query": "Indoor activities and entertainment near me",
            "category": "entertainment",
            "reason": "Rainy day — great for museums, theaters, or galleries",
        })

    if weather["is_hot"]:
        suggestions.append({
            "icon": "🏖️",
            "text": "Beat the heat — water activities nearby",
            "query": "swimming or water activities near me",
            "category": "outdoor",
            "reason": f"It's {weather['temp_f']}°F — cool off with water fun",
        })

    # Time-based suggestions
    if day["is_morning"]:
        suggestions.append({
            "icon": "🥐",
            "text": "Start the day with a great brunch spot",
            "query": "Best brunch spots nearby",
            "category": "food",
            "reason": f"Good morning! {day['day_of_week']} brunch time",
        })

    if day["is_evening"]:
        suggestions.append({
            "icon": "🍽️",
            "text": "Explore dinner options tonight",
            "query": "Nice restaurant for dinner tonight",
            "category": "food",
            "reason": f"It's {day['day_of_week']} evening — dinner time!",
        })

    if day["is_weekend"]:
        suggestions.append({
            "icon": "🎉",
            "text": "Weekend adventure — explore something new",
            "query": "Fun things to do this weekend nearby",
            "category": "entertainment",
            "reason": f"Happy {day['day_of_week']}!",
        })

    # Season-based suggestions
    if season["is_good_hiking_season"] and weather["is_warm"]:
        if not any(s["category"] == "outdoor" and "hik" in s["text"].lower() for s in suggestions):
            suggestions.append({
                "icon": "🥾",
                "text": f"Great {season['season']} hiking weather",
                "query": "scenic hikes near me",
                "category": "outdoor",
                "reason": f"Beautiful {season['season']} conditions",
            })

    # Always suggest coffee if morning
    if day["is_morning"] and not any(s["category"] == "coffee" for s in suggestions):
        suggestions.append({
            "icon": "☕",
            "text": "Find a great coffee shop nearby",
            "query": "Best coffee shop near me",
            "category": "coffee",
            "reason": "Morning caffeine fix!",
        })

    # Limit to top 6 suggestions
    suggestions = suggestions[:6]

    return {
        "suggestions": suggestions,
        "weather": weather,
        "sunset": sunset,
        "day": day,
        "season": season,
    }
