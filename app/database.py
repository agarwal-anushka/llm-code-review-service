import psycopg2
from psycopg2 import pool
import os
from dotenv import load_dotenv
import redis

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")
if not REDIS_URL:
    raise ValueError("REDIS_URL is not set in .env")

try:
    redis_client = redis.from_url(REDIS_URL, socket_timeout=None, socket_connect_timeout=5)
    print("Redis client created successfully")
except Exception as e:
    print(f"Failed to create Redis client: {e}")
    redis_client = None

try:
    connection_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL
    )
    print("Database connection pool created successfully")
except Exception as e:
    print(f"Failed to create connection pool: {e}")
    connection_pool = None

def get_connection():
    if connection_pool is None:
        raise RuntimeError("Database connection pool is not available")
    return connection_pool.getconn()

def return_connection(conn):
    if connection_pool is not None:
        connection_pool.putconn(conn)

def get_redis():
    if redis_client is None:
        raise RuntimeError("Redis client is not available")
    return redis_client