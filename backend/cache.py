"""
Redis caching configuration for ZERO Terminal
"""
import json
import time
import logging
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger("ZERO_Cache")

try:
    import redis
except ImportError:
    redis = None
    logger.warning("Redis not installed. Cache functionality will be limited.")

# Redis configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_PASSWORD = None  # Set in production

# Cache TTLs (in seconds)
CACHE_TTLS = {
    "predictions": 300,      # 5 minutes
    "prices": 10,           # 10 seconds (near real-time)
    "news": 120,            # 2 minutes
    "user_session": 3600,   # 1 hour
    "strategy_results": 600, # 10 minutes
}

# Redis connection pool
redis_pool = None


def get_redis_connection():
    """Get Redis connection from pool."""
    global redis_pool
    if redis is None:
        logger.warning("Redis not available. Cache operations will fail.")
        return None
    
    if redis_pool is None:
        redis_pool = redis.ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True
        )
    return redis.Redis(connection_pool=redis_pool)


def set_cache(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Set value in cache."""
    try:
        r = get_redis_connection()
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        
        if ttl is None:
            ttl = CACHE_TTLS.get(key.split(':')[0], 300)
        
        r.setex(key, ttl, value)
        logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
        return True
    except Exception as e:
        logger.error(f"Cache set failed: {e}")
        return False


def get_cache(key: str) -> Any:
    """Get value from cache."""
    try:
        r = get_redis_connection()
        value = r.get(key)
        if value:
            logger.debug(f"Cache hit: {key}")
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        logger.debug(f"Cache miss: {key}")
        return None
    except Exception as e:
        logger.error(f"Cache get failed: {e}")
        return None


def delete_cache(key: str) -> bool:
    """Delete value from cache."""
    try:
        r = get_redis_connection()
        r.delete(key)
        logger.debug(f"Cache deleted: {key}")
        return True
    except Exception as e:
        logger.error(f"Cache delete failed: {e}")
        return False


def clear_cache(pattern: str = "*") -> int:
    """Clear cache matching pattern."""
    try:
        r = get_redis_connection()
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
            logger.info(f"Cache cleared: {len(keys)} keys matched pattern {pattern}")
            return len(keys)
        return 0
    except Exception as e:
        logger.error(f"Cache clear failed: {e}")
        return 0


# Cache key generators
class CacheKeys:
    """Cache key generators."""
    
    @staticmethod
    def predictions(index: str = "ALL") -> str:
        return f"predictions:{index}"
    
    @staticmethod
    def live_prices() -> str:
        return "prices:live"
    
    @staticmethod
    def news(limit: int = 20) -> str:
        return f"news:limit={limit}"
    
    @staticmethod
    def user_session(user_id: str) -> str:
        return f"user_session:{user_id}"
    
    @staticmethod
    def strategy_results(strategy_id: str) -> str:
        return f"strategy:{strategy_id}:results"


# Background task cache
TASK_QUEUE_KEY = "task_queue"


def add_to_queue(task_name: str, task_data: dict) -> bool:
    """Add task to queue."""
    try:
        r = get_redis_connection()
        task = {
            "name": task_name,
            "data": task_data,
            "timestamp": datetime.utcnow().isoformat(),
            "attempts": 0
        }
        r.lpush(TASK_QUEUE_KEY, json.dumps(task))
        logger.info(f"Task added to queue: {task_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to add task to queue: {e}")
        return False


def get_next_task() -> dict:
    """Get next task from queue."""
    try:
        r = get_redis_connection()
        task = r.brpop(TASK_QUEUE_KEY, timeout=5)
        if task:
            return json.loads(task[1])
        return None
    except Exception as e:
        logger.error(f"Failed to get task from queue: {e}")
        return None


def is_queue_empty() -> bool:
    """Check if queue is empty."""
    try:
        r = get_redis_connection()
        return r.llen(TASK_QUEUE_KEY) == 0
    except Exception as e:
        logger.error(f"Failed to check queue: {e}")
        return True


# Export cache functions for import
__all__ = ["set_cache", "get_cache", "delete_cache", "clear_cache", "CacheKeys", "add_to_queue", "get_next_task", "is_queue_empty", "get_redis_connection"]

# Simple in-memory cache as fallback
_in_memory_cache = {}


def cache_simple_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Simple in-memory cache set."""
    _in_memory_cache[key] = (value, time.time() + ttl)
    return True


def cache_simple_get(key: str) -> Any:
    """Simple in-memory cache get."""
    if key not in _in_memory_cache:
        return None
    value, expiry = _in_memory_cache[key]
    if time.time() > expiry:
        del _in_memory_cache[key]
        return None
    return value


def cache_simple_delete(key: str) -> bool:
    """Simple in-memory cache delete."""
    if key in _in_memory_cache:
        del _in_memory_cache[key]
    return True


# Fallback: use in-memory cache if Redis not available
def set_cache(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Set cache value (with Redis fallback)."""
    try:
        r = get_redis_connection()
        if r is None:
            # Fallback to in-memory cache
            return cache_simple_set(key, value, ttl or 300)
        
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        
        if ttl is None:
            ttl = CACHE_TTLS.get(key.split(':')[0], 300)
        
        r.setex(key, ttl, value)
        return True
    except Exception as e:
        logger.error(f"Cache set failed: {e}")
        return cache_simple_set(key, value, ttl or 300)


def get_cache(key: str) -> Any:
    """Get cache value (with Redis fallback)."""
    try:
        r = get_redis_connection()
        if r is None:
            # Fallback to in-memory cache
            return cache_simple_get(key)
        
        value = r.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return cache_simple_get(key)
    except Exception as e:
        logger.error(f"Cache get failed: {e}")
        return cache_simple_get(key)


def delete_cache(key: str) -> bool:
    """Delete cache value."""
    try:
        r = get_redis_connection()
        if r is None:
            return cache_simple_delete(key)
        
        r.delete(key)
        return cache_simple_delete(key)
    except Exception as e:
        logger.error(f"Cache delete failed: {e}")
        return cache_simple_delete(key)
