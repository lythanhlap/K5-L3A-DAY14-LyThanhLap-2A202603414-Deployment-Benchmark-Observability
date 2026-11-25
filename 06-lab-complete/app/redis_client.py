"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

• Đọc REDIS_URL từ config
• Khởi tạo lazy (không ping lúc import) để container khởi động được khi chưa có Redis
• redis_healthy() kiểm tra kết nối thật cho GET /ready
"""
import redis

from app.config import settings

r: redis.Redis | None = (
    redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )
    if settings.redis_url
    else None
)


def redis_healthy() -> bool:
    """Ping Redis; trả False nếu không cấu hình hoặc mất kết nối."""
    if r is None:
        return False
    try:
        return bool(r.ping())
    except (redis.RedisError, OSError):
        return False


def close_redis() -> None:
    """Đóng connection pool khi shutdown."""
    if r is not None:
        try:
            r.close()
        except (redis.RedisError, OSError):
            pass
