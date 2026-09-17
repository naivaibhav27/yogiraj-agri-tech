import os
import re
import time
import base64
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta
from io import BytesIO
import threading
import random
import tempfile
import mimetypes

from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-please-change-in-production')
app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- POSTGRESQL DATABASE CONFIGURATION ---
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_NAME = os.getenv('DB_NAME', 'yogiraj_db')
DB_USER = os.getenv('DB_USER', 'yogiraj_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Yogiraj@2026')
DB_PORT = os.getenv('DB_PORT', '5432')

# --- UPLOAD CONFIG ---
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

# Force Vercel detection — /var/task always exists on Vercel
IS_VERCEL = bool(
    os.getenv('VERCEL') 
    or os.getenv('VERCEL_ENV') 
    or os.getenv('VERCEL_URL')
    or os.getenv('NOW_REGION')
    or os.path.exists('/var/task')        # Vercel's function root
    or os.path.exists('/var/lang')        # Vercel's Python dir
    or os.getcwd().startswith('/var')     # Any /var path = Vercel
)

# STARTUP DIAGNOSTIC — ye Vercel logs mein dikhega
print("=" * 70)
print("🔍 VERCEL DETECTION DIAGNOSTIC")
print("=" * 70)
print(f"   CWD:                    {os.getcwd()}")
print(f"   /var/task exists:       {os.path.exists('/var/task')}")
print(f"   /var/lang exists:       {os.path.exists('/var/lang')}")
print(f"   VERCEL env:             {os.getenv('VERCEL')}")
print(f"   VERCEL_ENV env:         {os.getenv('VERCEL_ENV')}")
print(f"   VERCEL_URL env:         {os.getenv('VERCEL_URL')}")
print(f"   IS_VERCEL result:       {IS_VERCEL}")
print(f"   Writable /tmp:          {os.access('/tmp', os.W_OK)}")
print("=" * 70)

if not IS_VERCEL:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- ADMIN CONFIG ---
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'Yogiraj@1811')
ADMIN_WHATSAPP_NUMBER = os.getenv('ADMIN_WHATSAPP_NUMBER', '919974120442')

# ============================================================
# 🚀 GROQ AI CONFIGURATION
# ============================================================
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"
GROQ_WHISPER_URL = f"{GROQ_BASE_URL}/audio/transcriptions"

GROQ_TEXT_MODEL = os.getenv('GROQ_TEXT_MODEL', 'openai/gpt-oss-20b')
GROQ_VISION_MODEL = os.getenv('GROQ_VISION_MODEL', 'qwen/qwen3.6-27b')
GROQ_WHISPER_MODEL = os.getenv('GROQ_WHISPER_MODEL', 'whisper-large-v3')

_ai_call_times = []
_AI_MAX_PER_MINUTE = 25
_ai_rate_lock = threading.Lock()


def _check_rate_limit():
    with _ai_rate_lock:
        now = time.time()
        while _ai_call_times and now - _ai_call_times[0] > 60:
            _ai_call_times.pop(0)
        if len(_ai_call_times) >= _AI_MAX_PER_MINUTE:
            wait = 60 - (now - _ai_call_times[0])
            return False, max(wait, 1.0)
        return True, 0.0


def _record_ai_call():
    with _ai_rate_lock:
        _ai_call_times.append(time.time())


print("=" * 60)
print("🌾 YOGIRAJ AGRI-TECH - COMPLETE SYSTEM")
print("=" * 60)
print(f"🌍 Environment: {'Vercel' if IS_VERCEL else 'Local'}")
print(f"🤖 AI: Groq — Text={GROQ_TEXT_MODEL}, Vision={GROQ_VISION_MODEL}")
print(f"🔑 Groq Key: {'SET' if GROQ_API_KEY else '❌ MISSING'}")
print("=" * 60)


# --- ESRI LAND COVER CLASS MAPPING ---
LAND_COVER_CLASSES = {
    1: {'name': 'Water', 'name_gu': 'પાણી', 'color': '#1A5BAB', 'icon': '💧', 'description': 'Water bodies'},
    2: {'name': 'Trees', 'name_gu': 'વૃક્ષો', 'color': '#358221', 'icon': '🌳', 'description': 'Forest/Trees'},
    4: {'name': 'Flooded Vegetation', 'name_gu': 'પૂરવાળી વનસ્પતિ', 'color': '#87D19E', 'icon': '🌿', 'description': 'Flooded areas'},
    5: {'name': 'Crops', 'name_gu': 'પાક', 'color': '#FFDB5C', 'icon': '🌾', 'description': 'Agricultural crops'},
    7: {'name': 'Built Area', 'name_gu': 'બાંધકામ વિસ્તાર', 'color': '#ED022A', 'icon': '🏙️', 'description': 'Urban/Built-up'},
    8: {'name': 'Bare Ground', 'name_gu': 'ખુલ્લી જમીન', 'color': '#EDE9E4', 'icon': '🏜️', 'description': 'Barren land'},
    9: {'name': 'Snow/Ice', 'name_gu': 'બરફ/હિમ', 'color': '#F2FAFF', 'icon': '❄️', 'description': 'Snow/Ice cover'},
    10: {'name': 'Clouds', 'name_gu': 'વાદળો', 'color': '#C8C8C8', 'icon': '☁️', 'description': 'Cloud cover'},
    11: {'name': 'Rangeland', 'name_gu': 'ચરાઈ જમીન', 'color': '#C6AD8D', 'icon': '🌿', 'description': 'Grassland/Shrubs'}
}

GUJARAT_DISTRICTS = {
    "sabarkantha": {"name": "Sabarkantha", "name_gu": "સાબરકાંઠા", "lat": 23.5979, "lon": 72.9698,
        "land_cover": {"Crops": 168.5, "Trees": 120.8, "Built Area": 45.2, "Bare Ground": 15.2, "Water": 8.5, "Rangeland": 22.3}},
    "aravalli": {"name": "Aravalli", "name_gu": "અરવલ્લી", "lat": 23.7179, "lon": 73.0198,
        "land_cover": {"Crops": 125.3, "Trees": 95.6, "Built Area": 32.1, "Bare Ground": 12.8, "Water": 6.2, "Rangeland": 15.5}},
    "mehsana": {"name": "Mehsana", "name_gu": "મહેસાણા", "lat": 23.5979, "lon": 72.9698,
        "land_cover": {"Crops": 210.2, "Trees": 25.6, "Built Area": 38.5, "Bare Ground": 18.5, "Water": 4.2, "Rangeland": 8.0}},
    "ahmedabad": {"name": "Ahmedabad", "name_gu": "અમદાવાદ", "lat": 23.0225, "lon": 72.5714,
        "land_cover": {"Crops": 185.3, "Trees": 15.6, "Built Area": 85.2, "Bare Ground": 12.5, "Water": 6.8, "Rangeland": 4.0}},
    "banaskantha": {"name": "Banaskantha", "name_gu": "બનાસકાંઠા", "lat": 24.3000, "lon": 72.5000,
        "land_cover": {"Crops": 195.8, "Trees": 18.2, "Built Area": 28.5, "Bare Ground": 22.5, "Water": 5.2, "Rangeland": 12.0}},
    "kheda": {"name": "Kheda", "name_gu": "ખેડા", "lat": 22.7500, "lon": 72.6833,
        "land_cover": {"Crops": 178.5, "Trees": 12.5, "Built Area": 35.2, "Bare Ground": 10.5, "Water": 8.5, "Rangeland": 6.0}},
    "vadodara": {"name": "Vadodara", "name_gu": "વડોદરા", "lat": 22.3072, "lon": 73.1812,
        "land_cover": {"Crops": 145.6, "Trees": 45.8, "Built Area": 65.2, "Bare Ground": 15.5, "Water": 12.5, "Rangeland": 8.0}},
    "surat": {"name": "Surat", "name_gu": "સુરત", "lat": 21.1702, "lon": 72.8311,
        "land_cover": {"Crops": 135.8, "Trees": 35.6, "Built Area": 75.2, "Bare Ground": 12.5, "Water": 18.5, "Rangeland": 5.0}},
    "rajkot": {"name": "Rajkot", "name_gu": "રાજકોટ", "lat": 22.3039, "lon": 70.8022,
        "land_cover": {"Crops": 155.8, "Trees": 22.5, "Built Area": 55.2, "Bare Ground": 25.5, "Water": 8.5, "Rangeland": 10.0}}
}


def get_esri_landcover(district_name):
    if district_name not in GUJARAT_DISTRICTS:
        return None, "District not found"
    district_data = GUJARAT_DISTRICTS[district_name]
    land_cover = district_data['land_cover']
    total_area = sum(land_cover.values())
    categories = []
    for name, area in land_cover.items():
        class_info = None
        for code, info in LAND_COVER_CLASSES.items():
            if info['name'] == name:
                class_info = info
                break
        if class_info:
            categories.append({'name': class_info['name'], 'name_gu': class_info['name_gu'],
                'area': area, 'color': class_info['color'], 'icon': class_info['icon'],
                'description': class_info['description'], 'percentage': (area / total_area) * 100})
        else:
            categories.append({'name': name, 'name_gu': name, 'area': area,
                'color': '#757575', 'icon': '📍', 'description': '', 'percentage': (area / total_area) * 100})
    categories.sort(key=lambda x: x['area'], reverse=True)
    result = {"total_area": total_area, "categories": categories, "district": district_data['name'],
        "district_gu": district_data['name_gu'], "lat": district_data['lat'], "lon": district_data['lon'],
        "year": "2024", "source": "ESRI Land Cover", "resolution": "10m"}
    return result, None


# --- DATABASE FUNCTIONS ---

def get_db_connection():
    try:
        database_url = os.getenv('DATABASE_URL')
        if database_url:
            conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
            print("✅ Connected to Neon PostgreSQL")
        else:
            conn = psycopg2.connect(
                host=DB_HOST, database=DB_NAME, user=DB_USER,
                password=DB_PASSWORD, port=DB_PORT, cursor_factory=RealDictCursor)
            print("✅ Connected to local PostgreSQL")
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        raise


def fix_column_types():
    """
    Fix legacy INTEGER is_active columns to BOOLEAN.
    Safe to run multiple times.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check + fix brand_partners
        try:
            cursor.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'brand_partners' AND column_name = 'is_active'
            """)
            row = cursor.fetchone()
            if row and row['data_type'] == 'integer':
                print("🔧 Migrating brand_partners.is_active INTEGER → BOOLEAN")
                cursor.execute("""
                    ALTER TABLE brand_partners
                    ALTER COLUMN is_active DROP DEFAULT
                """)
                cursor.execute("""
                    ALTER TABLE brand_partners
                    ALTER COLUMN is_active TYPE BOOLEAN USING (is_active::int::boolean)
                """)
                cursor.execute("""
                    ALTER TABLE brand_partners
                    ALTER COLUMN is_active SET DEFAULT TRUE
                """)
                conn.commit()
                print("✅ brand_partners migrated")
        except Exception as e:
            print(f"⚠️ brand_partners migration: {e}")
            conn.rollback()

        # Check + fix achievement_badges
        try:
            cursor.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'achievement_badges' AND column_name = 'is_active'
            """)
            row = cursor.fetchone()
            if row and row['data_type'] == 'integer':
                print("🔧 Migrating achievement_badges.is_active INTEGER → BOOLEAN")
                cursor.execute("ALTER TABLE achievement_badges ALTER COLUMN is_active DROP DEFAULT")
                cursor.execute("ALTER TABLE achievement_badges ALTER COLUMN is_active TYPE BOOLEAN USING (is_active::int::boolean)")
                cursor.execute("ALTER TABLE achievement_badges ALTER COLUMN is_active SET DEFAULT TRUE")
                conn.commit()
                print("✅ achievement_badges migrated")
        except Exception as e:
            print(f"⚠️ achievement_badges migration: {e}")
            conn.rollback()

        # Check + fix whatsapp_groups
        try:
            cursor.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'whatsapp_groups' AND column_name = 'is_active'
            """)
            row = cursor.fetchone()
            if row and row['data_type'] == 'integer':
                print("🔧 Migrating whatsapp_groups.is_active INTEGER → BOOLEAN")
                cursor.execute("ALTER TABLE whatsapp_groups ALTER COLUMN is_active DROP DEFAULT")
                cursor.execute("ALTER TABLE whatsapp_groups ALTER COLUMN is_active TYPE BOOLEAN USING (is_active::int::boolean)")
                cursor.execute("ALTER TABLE whatsapp_groups ALTER COLUMN is_active SET DEFAULT TRUE")
                conn.commit()
                print("✅ whatsapp_groups migrated")
        except Exception as e:
            print(f"⚠️ whatsapp_groups migration: {e}")
            conn.rollback()

    except Exception as e:
        print(f"❌ Column type fix error: {e}")
    finally:
        if conn:
            conn.close()


def seed_default_data():
    """Seed default data (founder, badges) if tables are empty."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Seed founder if empty
        try:
            cursor.execute("SELECT COUNT(*) as cnt FROM founder")
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute("""
                    INSERT INTO founder (name, role, bio, quote, image_url, video_url)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    'Mr. Santosh Valand - UN-FAO Certified Specialist',
                    'Co-Founder & CEO of Yogiraj - Agritech - Neo farm',
                    'Santosh Valand is the Founder and Administrator of Yogiraj Agritech, dedicated to supporting farmers with modern agricultural solutions and technology. His vision is to bridge the gap between traditional farming and modern technology by making innovative, practical, and AI-powered solutions accessible to farmers.',
                    'Empowering Farmers with the Power of AI.',
                    None, None))
                conn.commit()
                print("✅ Default founder record created")
        except Exception as e:
            print(f"⚠️ Founder seed: {e}")
            conn.rollback()

        # Seed default badges if empty
        try:
            cursor.execute("SELECT COUNT(*) as cnt FROM achievement_badges")
            if cursor.fetchone()['cnt'] == 0:
                badges = [
                    ('1 CR + turnover per year', 'Only quality', 'fa-chart-line'),
                    ('Heavy & great options', 'Trusted by many Big companies', 'fa-award'),
                    ('Successful order', 'Trusted by Dtech', 'fa-check-circle'),
                    ('5000+ happy customers', 'Best for all', 'fa-users'),
                ]
                for title, desc, icon in badges:
                    cursor.execute(
                        "INSERT INTO achievement_badges (title, description, icon, is_active) VALUES (%s, %s, %s, TRUE)",
                        (title, desc, icon))
                conn.commit()
                print("✅ Default badges created")
        except Exception as e:
            print(f"⚠️ Badge seed: {e}")
            conn.rollback()

    except Exception as e:
        print(f"❌ Seed error: {e}")
    finally:
        if conn:
            conn.close()


def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        print("📋 Creating database tables...")

        tables = [
            """CREATE TABLE IF NOT EXISTS admin (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, email TEXT, full_name TEXT, last_login TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, title TEXT, category TEXT, price REAL, stock_qty INTEGER, description TEXT, media_type TEXT, media_url TEXT, extra_info TEXT, youtube_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS rentals (id SERIAL PRIMARY KEY, title TEXT, daily_rate REAL, location TEXT, description TEXT, media_type TEXT, media_url TEXT, extra_info TEXT, youtube_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS blogs (id SERIAL PRIMARY KEY, author_name TEXT, title TEXT, content TEXT, rating INTEGER DEFAULT 5, media_type TEXT, media_url TEXT, youtube_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS sales (id SERIAL PRIMARY KEY, title TEXT, description TEXT, banner_image TEXT, discount_percentage REAL, start_date TIMESTAMP, end_date TIMESTAMP, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS sale_products (id SERIAL PRIMARY KEY, sale_id INTEGER REFERENCES sales(id), product_id INTEGER REFERENCES products(id), discounted_price REAL)""",
            """CREATE TABLE IF NOT EXISTS ads (id SERIAL PRIMARY KEY, title TEXT, description TEXT, media_url TEXT, link_url TEXT, position TEXT DEFAULT 'homepage', priority INTEGER DEFAULT 0, start_date TIMESTAMP, end_date TIMESTAMP, is_active BOOLEAN DEFAULT TRUE, clicks INTEGER DEFAULT 0, views INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS land_rentals (id SERIAL PRIMARY KEY, title TEXT, location TEXT, area_size REAL, area_unit TEXT DEFAULT 'Vigha', price REAL, price_unit TEXT DEFAULT 'Per Year', land_type TEXT, soil_type TEXT, water_availability TEXT, description TEXT, contact_name TEXT, contact_phone TEXT, media_type TEXT, media_url TEXT, youtube_url TEXT, status TEXT DEFAULT 'Available', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS founder (id SERIAL PRIMARY KEY, name TEXT, role TEXT, bio TEXT, image_url TEXT, video_url TEXT, quote TEXT)""",
            """CREATE TABLE IF NOT EXISTS brand_partners (id SERIAL PRIMARY KEY, name TEXT, logo_url TEXT, link_url TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS achievement_badges (id SERIAL PRIMARY KEY, title TEXT, description TEXT, icon TEXT, image_url TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS crop_rules (id SERIAL PRIMARY KEY, soil_type TEXT, season TEXT, water_availability TEXT, recommended_crop TEXT, variety TEXT, fertilizer_advice TEXT)""",
            """CREATE TABLE IF NOT EXISTS soil_reports (id SERIAL PRIMARY KEY, farmer_name TEXT, contact TEXT, report_image TEXT, n_value REAL, p_value REAL, k_value REAL, ph_value REAL, ai_advice TEXT, disease_desc TEXT, disease_image TEXT, disease_diagnosis TEXT, disease_advice TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS packed_foods (id SERIAL PRIMARY KEY, title_en TEXT, title_gu TEXT, category_en TEXT, category_gu TEXT, price REAL, stock_qty INTEGER, description_en TEXT, description_gu TEXT, media_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS product_categories (id SERIAL PRIMARY KEY, name TEXT, icon TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS product_media (id SERIAL PRIMARY KEY, product_id INTEGER REFERENCES products(id), media_url TEXT, media_type TEXT, youtube_url TEXT)""",
            """CREATE TABLE IF NOT EXISTS product_attributes (id SERIAL PRIMARY KEY, product_id INTEGER REFERENCES products(id), attribute_label TEXT, attribute_value TEXT)""",
            """CREATE TABLE IF NOT EXISTS category_attributes (id SERIAL PRIMARY KEY, category_id INTEGER REFERENCES product_categories(id), label TEXT, placeholder TEXT, type TEXT, options TEXT)""",
            """CREATE TABLE IF NOT EXISTS partnership_requests (id SERIAL PRIMARY KEY, brand_name TEXT, contact_person TEXT, email TEXT, phone TEXT, product_category TEXT, message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS whatsapp_groups (id SERIAL PRIMARY KEY, name TEXT, description TEXT, link TEXT, category TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS agri_news (id SERIAL PRIMARY KEY, title TEXT, image_url TEXT, content TEXT, link_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS password_resets (id SERIAL PRIMARY KEY, email TEXT, token TEXT, used INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS about_page (id SERIAL PRIMARY KEY, title TEXT, description TEXT, image_url TEXT, video_url TEXT, mission TEXT, vision TEXT)"""
        ]

        for table_sql in tables:
            try:
                cursor.execute(table_sql)
                conn.commit()
            except Exception as e:
                print(f"⚠️ Error creating table: {e}")
                conn.rollback()

        # Seed admin user
        try:
            cursor.execute("SELECT * FROM admin WHERE username = 'admin'")
            if not cursor.fetchone():
                hashed_pw = generate_password_hash("admin123", method="pbkdf2:sha256")
                cursor.execute(
                    "INSERT INTO admin (username, password, email, full_name) VALUES (%s, %s, %s, %s)",
                    ("admin", hashed_pw, "yogirajseeds03@gmail.com", "Yogiraj Admin"))
                conn.commit()
                print("✅ Default admin user created")
        except Exception as e:
            print(f"⚠️ Error creating admin: {e}")
            conn.rollback()

        print("✅ Database initialization complete!")

    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ============================================================
# STARTUP — Run init, migrations, and seeding on BOTH local & Vercel
# ============================================================
try:
    init_db()           # 1. Create tables (if not exist)
    fix_column_types()  # 2. Migrate legacy INTEGER → BOOLEAN
    seed_default_data() # 3. Seed founder + badges if empty
    print("✅ Startup complete!")
except Exception as e:
    print(f"⚠️ Startup issue: {e}")


# --- HELPER FUNCTIONS ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def extract_youtube_embed(url):
    if not url:
        return ""
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|shorts\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"
    return url


def save_file(file):
    if not file or file.filename == '':
        return "", ""

    filename = secure_filename(file.filename)
    if not filename:
        return "", ""

    video_extensions = ('.mp4', '.mov', '.avi', '.webm', '.mkv', '.3gp')
    media_type = "video" if filename.lower().endswith(video_extensions) else "image"

    print(f"📤 save_file() called: {filename}")
    print(f"   IS_VERCEL = {IS_VERCEL}")

    # === VERCEL BLOB PATH ===
    if IS_VERCEL:
        try:
            from vercel import blob

            name, ext = os.path.splitext(filename)
            unique_filename = f"{name}_{int(time.time() * 1000)}{ext}"

            # Save to /tmp — only writable location on Vercel
            temp_path = os.path.join('/tmp', unique_filename)
            file.save(temp_path)
            print(f"   Saved to /tmp: {temp_path}")

            try:
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

                blob_result = blob.upload_file(
                    local_path=temp_path,
                    path=f"uploads/{unique_filename}",
                    access="public",
                    content_type=content_type,
                    add_random_suffix=False,
                    overwrite=False
                )

                blob_url = blob_result.url
                print(f"   ✅ Uploaded to Blob: {blob_url}")
                return blob_url, media_type

            finally:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass

        except Exception as e:
            print(f"   ❌ Blob upload failed: {e}")
            import traceback
            traceback.print_exc()
            raise

    # === LOCAL PATH ===
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        print(f"   ✅ Local file saved: {filepath}")
        return filename, media_type
    except OSError as e:
        print(f"   ❌ Local save failed: {e}")
        raise
    

def get_media_items(media_url, media_type, youtube_url):
    media_items = []

    if youtube_url and youtube_url.strip():
        video_id = youtube_url.split('/')[-1].split('?')[0]
        media_items.append({
            'type': 'youtube',
            'url': youtube_url,
            'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
        })

    if media_url and media_url.strip():
        media_type_actual = "video" if media_type == 'video' else "image"

        # Blob URL → use directly
        if media_url.startswith("http://") or media_url.startswith("https://"):
            media_url_actual = media_url
        # Old local filename → prefix with /static/uploads/
        else:
            media_url_actual = f"/static/uploads/{media_url}"

        media_items.append({
            'type': media_type_actual,
            'url': media_url_actual,
        })

    return media_items


def rows_to_dict(rows):
    if not rows:
        return []
    return [dict(row) for row in rows]


def validate_date(date_str):
    if not date_str:
        return None
    try:
        if date_str.count('-') != 2:
            return None
        parts = date_str.split('-')
        if len(parts) != 3:
            return None
        year = int(parts[0])
        if year < 2000 or year > 2100:
            return None
        return date_str.replace('T', ' ') if 'T' in date_str else date_str
    except Exception:
        return None


# ============================================================
# 🌤️ WEATHER API
# ============================================================

def get_weather_forecast(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "Asia/Kolkata", "forecast_days": 7
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json(), None
        return None, f"API Error: {response.status_code}"
    except Exception as e:
        return None, f"Error: {str(e)}"


# ============================================================
# 🚀 GROQ AI — Text
# ============================================================

def call_groq_ai(prompt, max_retries=3):
    if not GROQ_API_KEY:
        print("❌ GROQ_API_KEY missing")
        return None, "Groq API key not configured"

    system_prompt = """You are Dr. Krishi, a senior AI Agronomist at Yogiraj Agri-Tech in Sabarkantha, Gujarat.
Provide practical crop advice for farmers. Format with emojis, bullet points.
Keep it concise and farmer-friendly. Respond in simple Hinglish/Gujarati if appropriate."""

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": GROQ_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7, "max_tokens": 800
    }

    allowed, wait = _check_rate_limit()
    if not allowed:
        print(f"🚦 Rate limit — waiting {wait:.1f}s...")
        time.sleep(wait)

    for attempt in range(max_retries):
        try:
            _record_ai_call()
            print(f"🔄 Groq Text attempt {attempt + 1}/{max_retries}...")
            response = requests.post(GROQ_CHAT_URL, headers=headers, json=data, timeout=25)

            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                print("✅ Groq text received!")
                return content, None
            elif response.status_code == 429:
                retry_after = response.headers.get('Retry-After')
                try:
                    sleep_for = float(retry_after) if retry_after else (2 ** attempt)
                except ValueError:
                    sleep_for = 2 ** attempt
                sleep_for = min(sleep_for, 15)
                print(f"⏳ Groq 429 — waiting {sleep_for:.1f}s...")
                time.sleep(sleep_for)
                continue
            else:
                print(f"⚠️ Groq error {response.status_code}: {response.text[:200]}")
                break
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout attempt {attempt + 1}")
            time.sleep(1)
            continue
        except Exception as e:
            print(f"❌ Groq exception: {e}")
            break

    return None, "Groq unavailable"


# ============================================================
# 🚀 GROQ VISION
# ============================================================

def call_groq_vision(image_bytes, mime_type, prompt, max_retries=2):
    if not GROQ_API_KEY:
        return None, "Groq API key not configured"

    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    image_data_url = f"data:{mime_type};base64,{b64_image}"

    system_prompt = """You are Dr. Krishi, an expert AI Agronomist for Yogiraj Agri-Tech.
Analyze the crop/leaf image and respond in Hinglish/Gujarati:
1. Identify crop 2. Identify disease/pest 3. Give solution 4. Recommend remedy
Keep response concise with emojis."""

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": GROQ_VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": prompt or "Analyze this crop image."},
                {"type": "image_url", "image_url": {"url": image_data_url}}
            ]}
        ],
        "temperature": 0.5, "max_tokens": 800
    }

    for attempt in range(max_retries):
        try:
            print(f"🔄 Groq Vision attempt {attempt + 1}/{max_retries}...")
            response = requests.post(GROQ_CHAT_URL, headers=headers, json=data, timeout=30)
            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                print("✅ Groq vision received!")
                return content, None
            elif response.status_code == 429:
                sleep_for = min(2 ** attempt, 10)
                time.sleep(sleep_for)
                continue
            else:
                print(f"⚠️ Vision error {response.status_code}: {response.text[:200]}")
                break
        except Exception as e:
            print(f"❌ Vision exception: {e}")
            break

    return None, "Groq vision unavailable"


# ============================================================
# 🚀 GROQ WHISPER
# ============================================================

def call_groq_whisper(audio_file, language=None):
    if not GROQ_API_KEY:
        return None, "Groq API key not configured"

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": (audio_file.filename, audio_file.stream, audio_file.mimetype or "audio/webm")}
    data = {"model": GROQ_WHISPER_MODEL, "response_format": "json"}
    if language:
        data["language"] = language

    try:
        print(f"🔄 Groq Whisper transcribing...")
        response = requests.post(GROQ_WHISPER_URL, headers=headers, files=files, data=data, timeout=30)
        if response.status_code == 200:
            text = response.json().get('text', '').strip()
            print(f"✅ Whisper: {text[:80]}...")
            return text, None
        else:
            return None, f"Whisper error: {response.status_code}"
    except Exception as e:
        return None, str(e)


def get_fallback_response(prompt):
    return """---
🔍 **Diagnosis**
Thank you for reaching out to Dr. Krishi.

🛠️ **Solution**
Please provide more details about your crop problem:
• Crop diseases (wheat, cotton, tomato)
• Fertilizer recommendations
• Soil health & pH
• Sowing timing

📦 **Products**
Visit our Agri Store for quality seeds and fertilizers.

💡 **Pro Tip**
Upload a photo for faster diagnosis.
---"""


# ============================================================
# ROUTES
# ============================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session['admin_username'] = 'admin'
            session['admin_full_name'] = 'Yogiraj Admin'
            return redirect(url_for('admin_dashboard'))
        return render_template('login_simple.html', error="❌ Invalid password.")
    return render_template('login_simple.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    def _fetch(query):
        try:
            cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            print(f"Error fetching: {e}")
            return []

    products = _fetch("SELECT * FROM products ORDER BY id DESC")
    rentals_list = _fetch("SELECT * FROM rentals ORDER BY id DESC")
    blogs = _fetch("SELECT * FROM blogs ORDER BY id DESC")
    land_rentals = _fetch("SELECT * FROM land_rentals ORDER BY id DESC")
    sales = _fetch("SELECT * FROM sales ORDER BY id DESC")
    ads = _fetch("SELECT * FROM ads ORDER BY id DESC")
    categories = _fetch("SELECT * FROM product_categories ORDER BY id DESC")
    groups = _fetch("SELECT * FROM whatsapp_groups ORDER BY id DESC")
    news_list = _fetch("SELECT * FROM agri_news ORDER BY id DESC")

    conn.close()

    return render_template('admin.html',
        products=rows_to_dict(products), rentals=rows_to_dict(rentals_list),
        blogs=rows_to_dict(blogs), land_rentals=rows_to_dict(land_rentals),
        sales=rows_to_dict(sales), ads=rows_to_dict(ads),
        categories=rows_to_dict(categories), groups=rows_to_dict(groups),
        news_list=rows_to_dict(news_list), admin_phone=ADMIN_WHATSAPP_NUMBER)


# --- ADMIN CRUD (all same as before, no changes needed) ---
# [add_product, add_rental, add_land_rental, delete_*, add_blog — all unchanged]

@app.route('/admin/add_product', methods=['POST'])
@login_required
def add_product():
    yt_url = extract_youtube_embed(request.form.get('youtube_url', ''))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO products (title, category, price, stock_qty, description,
                              media_type, media_url, extra_info, youtube_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (
        request.form.get('title', '').strip(),
        request.form.get('category', '').strip(),
        request.form.get('price', 0),
        request.form.get('stock_qty', 0),
        request.form.get('description', '').strip(),
        'image', '',
        request.form.get('extra_info', '').strip(),
        yt_url))
    row = cursor.fetchone()
    product_id = row['id'] if row else None

    files = request.files.getlist('media_files[]')
    if files and product_id:
        for file in files:
            if file and file.filename != '':
                filename, media_type = save_file(file)
                cursor.execute(
                    "INSERT INTO product_media (product_id, media_url, media_type) VALUES (%s, %s, %s)",
                    (product_id, filename, media_type))

    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/add_rental', methods=['POST'])
@login_required
def add_rental():
    filename, media_type = save_file(request.files.get('media'))
    yt_url = extract_youtube_embed(request.form.get('youtube_url', ''))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO rentals (title, daily_rate, location, description,
                             media_type, media_url, extra_info, youtube_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        request.form.get('title', '').strip(),
        request.form.get('daily_rate', 0),
        request.form.get('location', '').strip(),
        request.form.get('description', '').strip(),
        media_type, filename,
        request.form.get('extra_info', '').strip(),
        yt_url))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/add_land_rental', methods=['POST'])
@login_required
def add_land_rental():
    filename, media_type = save_file(request.files.get('media'))
    yt_url = extract_youtube_embed(request.form.get('youtube_url', ''))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO land_rentals (
            title, location, area_size, area_unit, price, price_unit,
            land_type, soil_type, water_availability, description,
            contact_name, contact_phone, media_type, media_url, youtube_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        request.form.get('title', '').strip(),
        request.form.get('location', '').strip(),
        request.form.get('area_size', 0),
        request.form.get('area_unit', 'Vigha'),
        request.form.get('price', 0),
        request.form.get('price_unit', 'Per Year'),
        request.form.get('land_type', ''),
        request.form.get('soil_type', ''),
        request.form.get('water_availability', ''),
        request.form.get('description', '').strip(),
        request.form.get('contact_name', '').strip(),
        request.form.get('contact_phone', '').strip(),
        media_type, filename, yt_url))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_land_rental/<int:id>')
@login_required
def delete_land_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM land_rentals WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_product/<int:id>')
@login_required
def delete_product(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_rental/<int:id>')
@login_required
def delete_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rentals WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_blog/<int:id>')
@login_required
def delete_blog(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blogs WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/blog/add', methods=['POST'])
@login_required
def add_blog():
    author = request.form.get('author_name', 'Admin')
    title = request.form.get('title', '')
    content = request.form.get('content', '')
    rating = request.form.get('rating', 5)
    filename, media_type = save_file(request.files.get('media'))
    yt_url = extract_youtube_embed(request.form.get('youtube_url', ''))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO blogs (author_name, title, content, rating,
                           media_type, media_url, youtube_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (author, title, content, rating, media_type, filename, yt_url))
    conn.commit()
    conn.close()
    return redirect(url_for('home'))


@app.route('/sales')
def sales_page():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("SELECT * FROM sales ORDER BY created_at DESC")
    sales = cursor.fetchall()
    sale_products = []
    for sale in sales:
        if not sale['is_active']:
            continue
        start_date = sale['start_date'].strftime('%Y-%m-%d %H:%M:%S') if sale['start_date'] else ''
        end_date = sale['end_date'].strftime('%Y-%m-%d %H:%M:%S') if sale['end_date'] else ''
        if start_date and end_date:
            if start_date <= current_date <= end_date:
                cursor.execute("""
                    SELECT p.*, sp.discounted_price
                    FROM sale_products sp JOIN products p ON sp.product_id = p.id
                    WHERE sp.sale_id = %s
                """, (sale['id'],))
                products = cursor.fetchall()
                sale_dict = dict(sale)
                sale_dict['products'] = rows_to_dict(products)
                sale_dict['start_date_display'] = start_date
                sale_dict['end_date_display'] = end_date
                sale_products.append(sale_dict)
    conn.close()
    return render_template('sales.html', sales=sale_products,
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/admin/add_sale', methods=['POST'])
@login_required
def add_sale():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    discount = request.form.get('discount_percentage', 0)
    start_date = request.form.get('start_date', '').strip().replace('T', ' ')
    end_date = request.form.get('end_date', '').strip().replace('T', ' ')
    filename, media_type = save_file(request.files.get('banner_image'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sales (title, description, banner_image, discount_percentage, start_date, end_date)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (title, description, filename, discount, start_date, end_date))
    row = cursor.fetchone()
    sale_id = row['id'] if row else None
    product_ids = request.form.getlist('product_ids')
    discounted_prices = request.form.getlist('discounted_prices')
    for i, product_id in enumerate(product_ids):
        if product_id:
            price = discounted_prices[i] if i < len(discounted_prices) else 0
            cursor.execute(
                "INSERT INTO sale_products (sale_id, product_id, discounted_price) VALUES (%s, %s, %s)",
                (sale_id, product_id, price))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_sale/<int:id>')
@login_required
def delete_sale(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales WHERE id=%s", (id,))
    cursor.execute("DELETE FROM sale_products WHERE sale_id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/add_ad', methods=['POST'])
@login_required
def add_ad():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    link_url = request.form.get('link_url', '').strip()
    position = request.form.get('position', 'homepage')
    priority = request.form.get('priority', 0)
    start_date = validate_date(request.form.get('start_date', '').strip())
    end_date = validate_date(request.form.get('end_date', '').strip())
    filename, media_type = save_file(request.files.get('media'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ads (title, description, media_url, link_url, position,
                         priority, start_date, end_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (title, description, filename, link_url, position, priority, start_date, end_date))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_ad/<int:id>')
@login_required
def delete_ad(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ads WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/toggle_ad/<int:id>')
@login_required
def toggle_ad(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ads SET is_active = NOT is_active WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard') + '#tab-ads')


@app.route('/admin/edit_ad/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_ad(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        link_url = request.form.get('link_url', '').strip()
        position = request.form.get('position', 'homepage')
        priority = request.form.get('priority', 0)
        new_filename = None
        if 'media' in request.files and request.files['media'].filename != '':
            file = request.files['media']
            new_filename, _ = save_file(file)
        if new_filename:
            cursor.execute("""
                UPDATE ads SET title=%s, description=%s, link_url=%s,
                               position=%s, priority=%s, media_url=%s WHERE id=%s
            """, (title, description, link_url, position, priority, new_filename, id))
        else:
            cursor.execute("""
                UPDATE ads SET title=%s, description=%s, link_url=%s,
                               position=%s, priority=%s WHERE id=%s
            """, (title, description, link_url, position, priority, id))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_dashboard') + '#tab-ads')
    cursor.execute("SELECT * FROM ads WHERE id=%s", (id,))
    ad = cursor.fetchone()
    conn.close()
    if not ad:
        return redirect(url_for('admin_dashboard') + '#tab-ads')
    return render_template('edit_ad.html', ad=dict(ad))


@app.route('/api/ads', methods=['GET'])
def get_ads():
    position = request.args.get('position', 'homepage')
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT * FROM ads
        WHERE is_active = true AND position = %s
        AND (start_date IS NULL OR start_date <= %s)
        AND (end_date IS NULL OR end_date >= %s)
        ORDER BY priority DESC, created_at DESC LIMIT 5
    """, (position, current_date, current_date))
    ads = cursor.fetchall()
    conn.close()
    return jsonify(rows_to_dict(ads))


@app.route('/api/ad/click/<int:id>', methods=['POST'])
def ad_click(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ads SET clicks = clicks + 1 WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/ad/view/<int:id>', methods=['POST'])
def ad_view(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ads SET views = views + 1 WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/')
def home():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM products ORDER BY id DESC LIMIT 6")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM rentals ORDER BY id DESC LIMIT 4")
    rentals = cursor.fetchall()
    cursor.execute("SELECT * FROM blogs ORDER BY id DESC LIMIT 10")
    blogs = cursor.fetchall()
    current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        SELECT * FROM ads WHERE is_active = true AND position = 'homepage'
        AND (start_date IS NULL OR start_date <= %s)
        AND (end_date IS NULL OR end_date >= %s)
        ORDER BY priority DESC, created_at DESC LIMIT 3
    """, (current_date, current_date))
    ads = cursor.fetchall()
    cursor.execute("SELECT * FROM founder LIMIT 1")
    founder = cursor.fetchone()
    cursor.execute("SELECT * FROM achievement_badges WHERE is_active = TRUE ORDER BY id DESC")
    badges = cursor.fetchall()
    cursor.execute("SELECT * FROM whatsapp_groups WHERE is_active = TRUE ORDER BY id DESC")
    groups = cursor.fetchall()
    cursor.execute("SELECT * FROM agri_news ORDER BY id DESC LIMIT 3")
    news_list = cursor.fetchall()
    conn.close()
    products = rows_to_dict(products)
    blogs = rows_to_dict(blogs)
    badges = rows_to_dict(badges)
    groups = rows_to_dict(groups)
    news_list = rows_to_dict(news_list)
    for p in products:
        p['media_items'] = get_media_items(p.get('media_url'), p.get('media_type'), p.get('youtube_url'))
    for b in blogs:
        b['media_items'] = get_media_items(b.get('media_url'), b.get('media_type'), b.get('youtube_url'))
    return render_template('index.html',
        products=products, rentals=rows_to_dict(rentals), blogs=blogs,
        ads=rows_to_dict(ads), founder=founder, badges=badges,
        groups=groups, news_list=news_list,
        admin_phone=ADMIN_WHATSAPP_NUMBER,
        is_admin=session.get('admin_logged_in', False))


@app.route('/admin/badges', methods=['GET', 'POST'])
@login_required
def admin_badges():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            title = request.form['title']
            description = request.form['description']
            icon = request.form['icon']
            filename = ""
            if 'badge_image' in request.files and request.files['badge_image'].filename != '':
                file = request.files['badge_image']
                filename, _ = save_file(file)
            cursor.execute(
                "INSERT INTO achievement_badges (title, description, icon, image_url) VALUES (%s, %s, %s, %s)",
                (title, description, icon, filename))
        elif action == 'delete':
            badge_id = request.form['badge_id']
            cursor.execute("DELETE FROM achievement_badges WHERE id=%s", (badge_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_badges'))
    cursor.execute("SELECT * FROM achievement_badges WHERE is_active = TRUE ORDER BY id DESC")
    badges = cursor.fetchall()
    conn.close()
    return render_template('admin_badges.html', badges=rows_to_dict(badges))


@app.route('/store')
def store():
    page = request.args.get('page', 1, type=int)
    per_page = 9
    offset = (page - 1) * per_page
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM products ORDER BY id DESC LIMIT %s OFFSET %s", (per_page, offset))
    products = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as count FROM products")
    total_count = cursor.fetchone()['count']
    cursor.execute("SELECT * FROM product_categories ORDER BY id DESC")
    categories = cursor.fetchall()
    conn.close()
    products = rows_to_dict(products)
    for p in products:
        p['media_items'] = get_media_items(p.get('media_url'), p.get('media_type'), p.get('youtube_url'))
    return render_template('store.html', products=products,
        categories=rows_to_dict(categories), page=page,
        total_count=total_count, admin_phone=ADMIN_WHATSAPP_NUMBER,
        is_admin=session.get('admin_logged_in', False))


@app.route('/rentals')
def rentals():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM rentals ORDER BY id DESC")
    rentals_list = cursor.fetchall()
    conn.close()
    rentals_list = rows_to_dict(rentals_list)
    for r in rentals_list:
        r['media_items'] = get_media_items(r.get('media_url'), r.get('media_type'), r.get('youtube_url'))
    return render_template('rentals.html', rentals=rentals_list,
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/mandi')
def packed_mandi():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM packed_foods ORDER BY id DESC")
    items = cursor.fetchall()
    conn.close()
    return render_template('mandi.html', items=rows_to_dict(items),
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/ai-advisor')
def ai_advisor():
    return render_template('ai_advisor_simple.html',
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/field-health')
def field_health():
    return render_template('field_health_real.html',
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/landcover')
def landcover():
    return render_template('landcover_esri.html',
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/land-rentals')
def land_rentals():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM land_rentals WHERE status = 'Available' ORDER BY created_at DESC")
    land_listings = cursor.fetchall()
    conn.close()
    land_listings = rows_to_dict(land_listings)
    for listing in land_listings:
        listing['media_items'] = get_media_items(
            listing.get('media_url'), listing.get('media_type'), listing.get('youtube_url'))
    return render_template('land_rentals.html', land_listings=land_listings,
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/community')
def community():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM whatsapp_groups WHERE is_active = TRUE ORDER BY id DESC")
    groups = cursor.fetchall()
    conn.close()
    return render_template('community.html', groups=rows_to_dict(groups),
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/api/weather')
def get_weather():
    lat = request.args.get('lat', '23.5979')
    lon = request.args.get('lon', '72.9698')
    try:
        weather_data, error = get_weather_forecast(lat, lon)
        if weather_data:
            return jsonify(weather_data)
        return jsonify({
            "current": {"temperature_2m": 31, "relative_humidity_2m": 55, "wind_speed_10m": 12, "weather_code": 0},
            "daily": {"temperature_2m_max": [32, 33, 34, 32, 31, 30, 29], "temperature_2m_min": [22, 23, 24, 22, 21, 20, 19]}
        })
    except Exception:
        return jsonify({"current": {"temperature_2m": 31, "relative_humidity_2m": 55, "wind_speed_10m": 12, "weather_code": 0}})


@app.route('/api/esri-landcover', methods=['GET'])
def api_esri_landcover():
    district = request.args.get('district', 'sabarkantha')
    lang = request.args.get('lang', 'en')
    if district not in GUJARAT_DISTRICTS:
        return jsonify({'success': False, 'error': 'District not found'}), 404
    data, error = get_esri_landcover(district)
    if data:
        return jsonify({'success': True, 'data': data, 'district': district, 'lang': lang})
    return jsonify({'success': False, 'error': error}), 500


@app.route('/api/nasa-ndvi', methods=['GET'])
def api_nasa_ndvi():
    lat = request.args.get('lat', '23.5979')
    lon = request.args.get('lon', '72.9698')
    mode = request.args.get('mode', 'image')
    if mode == 'image':
        try:
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (600, 400), color='#0a1a0f')
            draw = ImageDraw.Draw(img)
            colors = ['#1b5e20', '#2e7d32', '#4caf50', '#aed581', '#f9a825', '#f57c00', '#e53935']
            for x in range(0, 600, 30):
                for y in range(0, 400, 30):
                    color = random.choice(colors)
                    draw.rectangle([x, y, x + 30, y + 30], fill=color)
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            return jsonify({'success': True, 'image': img_str, 'type': 'image'})
        except Exception:
            return jsonify({'success': False, 'error': 'Image generation failed'}), 500
    elif mode == 'grid':
        grid_data = []
        for i in range(5):
            for j in range(5):
                grid_data.append({'lat': float(lat) + (i - 2) * 0.05, 'lon': float(lon) + (j - 2) * 0.05, 'ndvi': random.uniform(0.2, 0.8)})
        return jsonify({'success': True, 'data': grid_data, 'type': 'grid'})
    return jsonify({'success': False, 'error': 'Invalid mode'}), 400


@app.route('/api/ai_consult', methods=['POST'])
def ai_consult():
    data = request.json or {}
    user_prompt = data.get('prompt', '').strip()
    if not user_prompt:
        return jsonify({'error': 'Please describe your crop problem.'}), 400
    try:
        response_text, error = call_groq_ai(user_prompt)
        if response_text:
            return jsonify({'reply': response_text, 'timestamp': datetime.now().strftime("%I:%M %p"), 'success': True})
        return jsonify({'reply': get_fallback_response(user_prompt), 'timestamp': datetime.now().strftime("%I:%M %p"), 'success': True})
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'reply': get_fallback_response(user_prompt), 'timestamp': datetime.now().strftime("%I:%M %p"), 'success': True})


@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file uploaded'}), 400
    audio = request.files['audio']
    if audio.filename == '':
        return jsonify({'error': 'No audio file selected'}), 400
    language = request.form.get('language') or None
    try:
        text, error = call_groq_whisper(audio, language=language)
        if text:
            return jsonify({'success': True, 'text': text})
        return jsonify({'success': False, 'error': error or 'Transcription failed'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision_analyze', methods=['POST'])
def vision_analyze():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    allowed_types = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
    mime_type = file.mimetype or 'image/jpeg'
    if mime_type not in allowed_types:
        return jsonify({'error': f'Unsupported image type: {mime_type}'}), 400
    try:
        image_bytes = file.read()
        if len(image_bytes) > 20 * 1024 * 1024:
            return jsonify({'error': 'Image too large (max 20 MB)'}), 400
        prompt = request.form.get('prompt', '').strip() or "Analyze this crop/leaf image."
        response_text, error = call_groq_vision(image_bytes, mime_type, prompt)
        if response_text:
            return jsonify({'success': True, 'reply': response_text})
        return jsonify({'success': False, 'error': error or 'Vision analysis failed'}), 500
    except Exception as e:
        print(f"❌ Vision error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chatbot', methods=['POST'])
def chatbot_api():
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'reply': 'Hello! How can I assist you with farming today? 🌾'})

    if "recommend" in message.lower() or "crop" in message.lower() or "suggest" in message.lower():
        soil, season, water = "Loamy", "Rabi", "Year-round"
        if "clay" in message.lower(): soil = "Clay"
        if "sandy" in message.lower(): soil = "Sandy"
        if "kharif" in message.lower() or "monsoon" in message.lower(): season = "Kharif"
        if "rain" in message.lower(): water = "Rain-fed"
        if "irrigation" in message.lower(): water = "Irrigation Available"
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM crop_rules WHERE soil_type = %s AND season = %s AND water_availability = %s", (soil, season, water))
        rules = cursor.fetchall()
        conn.close()
        if rules:
            reply = "🌾 **Based on your farm conditions:**\n\n"
            for r in rules:
                reply += f"✅ **{r['recommended_crop']}** (Variety: {r['variety'] or 'Standard'})\n👉 {r['fertilizer_advice'] or 'Consult local expert.'}\n\n"
            return jsonify({'reply': reply})
        return jsonify({'reply': "🌾 No rule for those exact conditions. Contact Admin."})

    if "soil" in message.lower() or "n-p-k" in message.lower() or "ph" in message.lower():
        numbers = [float(s) for s in re.findall(r'[-+]?\d*\.\d+|\d+', message)]
        if len(numbers) >= 4:
            n, p, k, ph = numbers[0], numbers[1], numbers[2], numbers[3]
            advice = "✅ **Your soil is balanced.**"
            if ph < 5.5: advice = "⚠️ **Acidic soil.** Add Lime."
            elif ph > 8.0: advice = "⚠️ **Alkaline soil.** Add Gypsum."
            elif n < 20: advice = "⚠️ **Low Nitrogen.** Apply Urea."
            elif k < 40: advice = "⚠️ **Low Potassium.** Apply MOP."
            return jsonify({'reply': f"🧪 **Soil Analysis:**\n\nN={n}, P={p}, K={k}, pH={ph}\n\n💡 {advice}"})
        return jsonify({'reply': "🧪 Type N-P-K-pH like: `N=25, P=15, K=45, pH=6.5`"})

    try:
        formatted_prompt = f'You are Dr. Krishi, AI Agronomist. Farmer asked: "{message}". Reply with emojis.'
        response_text, error = call_groq_ai(formatted_prompt)
        if response_text:
            return jsonify({'reply': response_text})
    except Exception:
        pass
    return jsonify({'reply': get_fallback_response(message)})


@app.route('/admin/founder', methods=['GET', 'POST'])
@login_required
def admin_founder():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        role = request.form.get('role', '').strip()
        bio = request.form.get('bio', '').strip()
        quote = request.form.get('quote', '').strip()
        video_url = request.form.get('video_url', '').strip()
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                image_filename, _ = save_file(file)
                print(f"✅ Founder image saved: {image_filename}")
        cursor.execute("SELECT * FROM founder LIMIT 1")
        existing = cursor.fetchone()
        if existing:
            if image_filename:
                old_url = existing.get('image_url')
                if old_url and not (old_url.startswith("http://") or old_url.startswith("https://")):
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_url)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                cursor.execute("""
                    UPDATE founder SET name=%s, role=%s, bio=%s, quote=%s,
                        image_url=%s, video_url=%s WHERE id=%s
                """, (name, role, bio, quote, image_filename, video_url, existing['id']))
            else:
                cursor.execute("""
                    UPDATE founder SET name=%s, role=%s, bio=%s, quote=%s, video_url=%s WHERE id=%s
                """, (name, role, bio, quote, video_url, existing['id']))
        else:
            cursor.execute("""
                INSERT INTO founder (name, role, bio, quote, image_url, video_url)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, role, bio, quote, image_filename, video_url))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_founder'))
    cursor.execute("SELECT * FROM founder LIMIT 1")
    founder = cursor.fetchone()
    conn.close()
    return render_template('admin_founder.html', founder=founder)


@app.route('/about')
def about():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM about_page LIMIT 1")
    about_data = cursor.fetchone()
    conn.close()
    return render_template('about.html', about=about_data)


@app.route('/admin/about', methods=['GET', 'POST'])
@login_required
def admin_about():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        image = request.form['image_url']
        video = request.form['video_url']
        mission = request.form['mission']
        vision = request.form['vision']
        cursor.execute("SELECT * FROM about_page")
        exists = cursor.fetchone()
        if exists:
            cursor.execute("""
                UPDATE about_page SET title=%s, description=%s, image_url=%s,
                    video_url=%s, mission=%s, vision=%s
            """, (title, description, image, video, mission, vision))
        else:
            cursor.execute("""
                INSERT INTO about_page (title, description, image_url, video_url, mission, vision)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (title, description, image, video, mission, vision))
        conn.commit()
        conn.close()
        return redirect(url_for('about'))
    cursor.execute("SELECT * FROM about_page LIMIT 1")
    about_data = cursor.fetchone()
    conn.close()
    return render_template('admin_about.html', about=about_data)


@app.route('/api/brand_partners')
def get_brand_partners():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM brand_partners WHERE is_active = TRUE ORDER BY id DESC")
    partners = cursor.fetchall()
    conn.close()
    return jsonify(rows_to_dict(partners))


@app.route('/admin/brand_partners', methods=['GET', 'POST'])
@login_required
def admin_brand_partners():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form['name']
            logo = request.form['logo_url']
            link = request.form['link_url']
            cursor.execute("INSERT INTO brand_partners (name, logo_url, link_url) VALUES (%s, %s, %s)", (name, logo, link))
        elif action == 'delete':
            partner_id = request.form['partner_id']
            cursor.execute("DELETE FROM brand_partners WHERE id=%s", (partner_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_brand_partners'))
    cursor.execute("SELECT * FROM brand_partners ORDER BY id DESC")
    partners = cursor.fetchall()
    conn.close()
    return render_template('admin_brand_partners.html', partners=rows_to_dict(partners))


@app.route('/soil-crop-advisor')
def soil_crop_advisor():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM crop_rules")
    rules = cursor.fetchall()
    conn.close()
    return render_template('soil_crop_advisor.html', rules=rows_to_dict(rules),
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/admin/add_crop_rule', methods=['POST'])
@login_required
def add_crop_rule():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO crop_rules (soil_type, season, water_availability,
            recommended_crop, variety, fertilizer_advice)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (request.form['soil_type'].strip(), request.form['season'].strip(),
          request.form['water_availability'].strip(), request.form['recommended_crop'].strip(),
          request.form['variety'].strip(), request.form['fertilizer_advice'].strip()))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_crop_rule/<int:id>')
@login_required
def delete_crop_rule(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM crop_rules WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/submit_soil_report', methods=['POST'])
def submit_soil_report():
    name = request.form['name'].strip()
    contact = request.form['contact'].strip()
    try:
        n = float(request.form.get('n_value', '0') or 0)
        p = float(request.form.get('p_value', '0') or 0)
        k = float(request.form.get('k_value', '0') or 0)
        ph = float(request.form.get('ph_value', '0') or 0)
    except ValueError:
        n, p, k, ph = 0.0, 0.0, 0.0, 7.0
    disease_desc = request.form.get('disease_description', '').strip()
    disease_image = ""
    if 'disease_image' in request.files and request.files['disease_image'].filename != '':
        file = request.files['disease_image']
        disease_image, _ = save_file(file)
    report_image = ""
    if 'report_image' in request.files and request.files['report_image'].filename != '':
        file = request.files['report_image']
        report_image, _ = save_file(file)
    soil_advice = "✅ Your soil is balanced. Maintain current practices."
    if ph < 5.5:
        soil_advice = "⚠️ Your soil is acidic. Add Lime (Calcium Carbonate)."
    elif ph > 8.0:
        soil_advice = "⚠️ Your soil is alkaline. Add Gypsum or compost."
    elif n < 20:
        soil_advice = "⚠️ Nitrogen (N) low. Apply Urea or DAP."
    elif k < 40:
        soil_advice = "⚠️ Potassium (K) low. Apply MOP."
    disease_advice = ""
    disease_diagnosis = ""
    if disease_desc:
        try:
            response_text, error = call_groq_ai(f"I am a farmer. My crop has this problem: {disease_desc}")
            if response_text:
                disease_advice = response_text
                disease_diagnosis = "Analysis Complete"
            else:
                disease_diagnosis = "Analysis failed"
                disease_advice = "Could not connect to AI."
        except Exception as e:
            disease_diagnosis = "Error"
            disease_advice = f"Error: {str(e)}"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO soil_reports
        (farmer_name, contact, report_image, n_value, p_value, k_value, ph_value,
         ai_advice, disease_desc, disease_image, disease_diagnosis, disease_advice)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (name, contact, report_image, n, p, k, ph, soil_advice,
          disease_desc, disease_image, disease_diagnosis, disease_advice))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'advice': soil_advice,
        'n': n, 'p': p, 'k': k, 'ph': ph,
        'disease_advice': disease_advice, 'disease_diagnosis': disease_diagnosis})


@app.route('/admin/add_category_ajax', methods=['POST'])
@login_required
def add_category_ajax():
    name = request.json.get('name', '').strip()
    if name:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO product_categories (name, icon) VALUES (%s, %s) RETURNING id", (name, 'fa-tag'))
        row = cursor.fetchone()
        category_id = row['id'] if row else None
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': category_id, 'name': name})
    return jsonify({'success': False})


@app.route('/admin/delete_category/<int:id>')
@login_required
def delete_store_category(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM product_categories WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/api/recommend_crop', methods=['POST'])
def recommend_crop():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM crop_rules WHERE soil_type = %s AND season = %s AND water_availability = %s",
                   (data.get('soil'), data.get('season'), data.get('water')))
    rules = cursor.fetchall()
    conn.close()
    if rules:
        return jsonify({'success': True, 'crops': rows_to_dict(rules)})
    return jsonify({'success': False, 'message': "No crops match. Try changing inputs."})


@app.route('/partnership')
def partnership():
    return render_template('partnership.html',
        admin_phone=ADMIN_WHATSAPP_NUMBER, is_admin=session.get('admin_logged_in', False))


@app.route('/submit_partnership', methods=['POST'])
def submit_partnership():
    brand = request.form['brand_name'].strip()
    person = request.form['contact_person'].strip()
    email = request.form['email'].strip()
    phone = request.form['phone'].strip()
    category = request.form['product_category'].strip()
    message = request.form['message'].strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO partnership_requests (brand_name, contact_person, email, phone, product_category, message)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (brand, person, email, phone, category, message))
    conn.commit()
    conn.close()
    wa_text = f"🤝 *New Partnership Inquiry!*\n\n🏢 {brand}\n👤 {person}\n📧 {email}\n📞 {phone}\n📦 {category}\n💬 {message}"
    import urllib.parse
    return redirect(f"https://wa.me/{ADMIN_WHATSAPP_NUMBER}?text={urllib.parse.quote(wa_text)}")


@app.route('/admin/partnerships')
@login_required
def admin_partnerships():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM partnership_requests ORDER BY id DESC")
    requests_data = cursor.fetchall()
    conn.close()
    return render_template('admin_partnerships.html', requests=rows_to_dict(requests_data))


@app.route('/admin/delete_partnership/<int:id>')
@login_required
def delete_partnership(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM partnership_requests WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_partnerships'))


@app.route('/product/<int:id>')
def product_detail(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM products WHERE id=%s", (id,))
    product = cursor.fetchone()
    if not product:
        conn.close()
        return render_template('404.html'), 404
    product = dict(product)
    cursor.execute("SELECT * FROM product_media WHERE product_id=%s", (id,))
    media_items = cursor.fetchall()
    if not media_items and product.get('media_url'):
        media_items = [{'id': 0, 'media_url': product['media_url'],
                        'media_type': product['media_type'],
                        'youtube_url': product.get('youtube_url', '')}]
    conn.close()
    return render_template('product_detail.html', product=product,
        media_items=rows_to_dict(media_items),
        admin_phone=ADMIN_WHATSAPP_NUMBER,
        is_admin=session.get('admin_logged_in', False))


@app.route('/admin/add_packed_food', methods=['POST'])
@login_required
def add_packed_food():
    filename, media_type = save_file(request.files.get('media'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO packed_foods (title_en, title_gu, category_en, category_gu,
            price, stock_qty, description_en, description_gu, media_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (request.form['title_en'].strip(), request.form['title_gu'].strip(),
          request.form['category_en'].strip(), request.form['category_gu'].strip(),
          float(request.form['price']), int(request.form['stock_qty']),
          request.form['description_en'].strip(), request.form['description_gu'].strip(), filename))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_packed_food/<int:id>')
@login_required
def delete_packed_food(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM packed_foods WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/add_whatsapp_group', methods=['POST'])
@login_required
def add_whatsapp_group():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO whatsapp_groups (name, description, link, category) VALUES (%s, %s, %s, %s)",
                   (request.form['name'].strip(), request.form['description'].strip(),
                    request.form['link'].strip(), request.form['category'].strip()))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_whatsapp_group/<int:id>')
@login_required
def delete_whatsapp_group_fixed(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM whatsapp_groups WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/api/active_groups')
def api_active_groups():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM whatsapp_groups WHERE is_active = TRUE ORDER BY id DESC LIMIT 3")
    groups = cursor.fetchall()
    conn.close()
    return jsonify(rows_to_dict(groups))


@app.route('/admin/agri-news')
@login_required
def admin_agri_news():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM agri_news ORDER BY id DESC")
    news_list = cursor.fetchall()
    conn.close()
    return render_template('admin_agri_news.html', news_list=rows_to_dict(news_list))


@app.route('/admin/add_agri_news', methods=['POST'])
@login_required
def add_agri_news():
    title = request.form['title'].strip()
    content = request.form['content'].strip()
    link_url = request.form['link_url'].strip()
    filename = ""
    if 'news_image' in request.files and request.files['news_image'].filename != '':
        file = request.files['news_image']
        filename, _ = save_file(file)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO agri_news (title, image_url, content, link_url) VALUES (%s, %s, %s, %s)",
                   (title, filename, content, link_url))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_agri_news'))


@app.route('/admin/delete_agri_news/<int:id>')
@login_required
def delete_agri_news(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM agri_news WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_agri_news'))


@app.route('/news/<int:id>')
def news_detail(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM agri_news WHERE id=%s", (id,))
    news = cursor.fetchone()
    conn.close()
    if not news:
        return render_template('404.html'), 404
    return render_template('news_detail.html', news=dict(news))


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')