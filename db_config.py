"""
Database Configuration - PostgreSQL
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db_connection():
    """Get PostgreSQL connection"""
    
    # Try environment variables first
    if os.getenv('DATABASE_URL'):
        # For cloud deployment (Railway, Supabase, etc.)
        import urllib.parse
        url = os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url)
    else:
        # Local development
        conn = psycopg2.connect(
            host="localhost",
            database="yogiraj_db",
            user="yogiraj_user",
            password="Yogiraj@2026",
            cursor_factory=RealDictCursor  # For dictionary-like rows
        )
    
    # Enable better performance
    conn.autocommit = False
    return conn

def get_db_cursor():
    """Get cursor for database operations"""
    conn = get_db_connection()
    return conn, conn.cursor()

# Optional: Connection pool for production
from psycopg2 import pool

if os.getenv('USE_POOL'):
    db_pool = pool.SimpleConnectionPool(
        1, 20,
        host="localhost",
        database="yogiraj_db",
        user="yogiraj_user",
        password="Yogiraj@2026"
    )
    
    def get_connection_from_pool():
        return db_pool.getconn()