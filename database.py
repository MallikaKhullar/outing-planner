"""Database schema and operations for the outing planner."""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "outing_planner.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            home_location TEXT DEFAULT '',
            home_lat REAL DEFAULT 0,
            home_lng REAL DEFAULT 0,
            city TEXT DEFAULT '',
            transportation TEXT DEFAULT 'car',
            walking_threshold_min INTEGER DEFAULT 15,
            budget_preference TEXT DEFAULT 'moderate',
            toll_preference TEXT DEFAULT 'avoid',
            other_notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );

        CREATE TABLE IF NOT EXISTS place_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            query_key TEXT NOT NULL,
            results TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            UNIQUE(city, query_key)
        );

        CREATE TABLE IF NOT EXISTS opportunity_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            date TEXT NOT NULL,
            suggestions TEXT NOT NULL,
            weather_data TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city, lat, lng, date)
        );

        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            query TEXT NOT NULL,
            intent_json TEXT DEFAULT '{}',
            result_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Insert default settings if not exists
    cursor.execute("SELECT COUNT(*) FROM user_settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO user_settings (id, home_location, city, transportation,
                walking_threshold_min, budget_preference, toll_preference, other_notes)
            VALUES (1, '', '', 'car', 15, 'moderate', 'avoid', '')
        """)

    conn.commit()
    conn.close()


def get_settings():
    conn = get_connection()
    row = conn.execute("SELECT * FROM user_settings WHERE id = 1").fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def update_settings(settings: dict):
    conn = get_connection()
    fields = []
    values = []
    allowed = [
        'home_location', 'home_lat', 'home_lng', 'city', 'transportation',
        'walking_threshold_min', 'budget_preference', 'toll_preference', 'other_notes'
    ]
    for key, val in settings.items():
        if key in allowed:
            fields.append(f"{key} = ?")
            values.append(val)
    if fields:
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(1)
        conn.execute(f"UPDATE user_settings SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    conn.close()


def create_conversation(city: str) -> int:
    conn = get_connection()
    cursor = conn.execute("INSERT INTO conversations (city) VALUES (?)", (city,))
    conv_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return conv_id


def add_message(conversation_id: int, role: str, content: str, metadata: dict = None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, json.dumps(metadata or {}))
    )
    conn.commit()
    conn.close()


def get_messages(conversation_id: int, limit: int = 50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
        (conversation_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cache_places(city: str, query_key: str, results: list, ttl_hours: int = 24):
    conn = get_connection()
    expires = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO place_cache (city, query_key, results, expires_at)
        VALUES (?, ?, ?, datetime('now', '+' || ? || ' hours'))
    """, (city, query_key, json.dumps(results), ttl_hours))
    conn.commit()
    conn.close()


def get_cached_places(city: str, query_key: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT results FROM place_cache
        WHERE city = ? AND query_key = ? AND expires_at > datetime('now')
    """, (city, query_key)).fetchone()
    conn.close()
    if row:
        return json.loads(row['results'])
    return None


def clear_city_cache(city: str):
    conn = get_connection()
    conn.execute("DELETE FROM place_cache WHERE city = ?", (city,))
    conn.execute("DELETE FROM opportunity_cache WHERE city = ?", (city,))
    conn.commit()
    conn.close()


def cache_opportunities(city: str, lat: float, lng: float, suggestions: list, weather_data: dict):
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    temp = weather_data.get('temp_f', '?')
    print(f"[DB] cache_opportunities: city={city}, lat={lat:.4f}, lng={lng:.4f}, temp={temp}°F")

    try:
        # Try new schema with lat/lng columns
        print(f"[DB]   Attempting new schema (with lat/lng)...")
        conn.execute("""
            INSERT OR REPLACE INTO opportunity_cache (city, lat, lng, date, suggestions, weather_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (city, lat, lng, today, json.dumps(suggestions), json.dumps(weather_data)))
        print(f"[DB]   ✓ Cached with new schema (location-specific)")
    except Exception as e:
        # Fall back to old schema if columns don't exist
        print(f"[DB]   New schema failed: {type(e).__name__}")
        print(f"[DB]   Falling back to old schema (city+date only)...")
        try:
            conn.execute("""
                INSERT OR REPLACE INTO opportunity_cache (city, date, suggestions, weather_data)
                VALUES (?, ?, ?, ?)
            """, (city, today, json.dumps(suggestions), json.dumps(weather_data)))
            print(f"[DB]   ✓ Cached with old schema (NOT location-specific!)")
        except Exception as e2:
            print(f"[DB]   ✗ Failed to cache: {e2}")

    conn.commit()
    conn.close()


def get_cached_opportunities(city: str, lat: float, lng: float):
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[DB] get_cached_opportunities: city={city}, lat={lat:.4f}, lng={lng:.4f}, date={today}")

    try:
        # Try new schema with lat/lng columns first
        print(f"[DB]   Trying new schema query (with lat/lng columns)...")
        row = conn.execute("""
            SELECT suggestions, weather_data FROM opportunity_cache
            WHERE city = ? AND lat = ? AND lng = ? AND date = ?
        """, (city, lat, lng, today)).fetchone()
        print(f"[DB]   New schema result: {'FOUND' if row else 'NOT FOUND'}")
    except Exception as e:
        # Fall back to old schema if lat/lng columns don't exist
        print(f"[DB]   New schema failed: {type(e).__name__}: {e}")
        print(f"[DB]   Attempting old schema fallback (city+date only)...")
        try:
            row = conn.execute("""
                SELECT suggestions, weather_data FROM opportunity_cache
                WHERE city = ? AND date = ?
            """, (city, today)).fetchone()
            print(f"[DB]   Old schema result: {'FOUND' if row else 'NOT FOUND'}")
            if row:
                print(f"[DB]   ⚠️  WARNING: OLD CACHED DATA WILL BE RETURNED (not location-specific)")
        except Exception as e2:
            print(f"[DB]   Old schema also failed: {type(e2).__name__}")
            row = None

    conn.close()
    if row:
        weather_data = json.loads(row['weather_data'])
        temp = weather_data.get('temp_f', '?')
        print(f"[DB]   Returning CACHED weather: {temp}°F")
        return {
            'suggestions': json.loads(row['suggestions']),
            'weather_data': weather_data
        }
    print(f"[DB]   No cache found, returning None (fresh data will be fetched)")
    return None


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
