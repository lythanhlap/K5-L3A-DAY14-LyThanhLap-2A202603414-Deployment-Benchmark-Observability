"""
Rate limiting (tiêu chí 7).

• Sliding window 60 giây cho mỗi API key bằng Redis sorted set
• Vượt RATE_LIMIT_PER_MINUTE → HTTP 429 kèm header Retry-After
• State lưu trong Redis để chia sẻ giữa nhiều worker/instance và giữ được sau khi restart
"""
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def check_rate_limit(key: str) -> None:
    """Kiểm tra giới hạn request/phút cho API key hiện tại."""
    if r is None:
        raise HTTPException(status_code=503, detail="Redis client is not configured")

    now = time.time()
    limit = settings.rate_limit_per_minute
    redis_key = f"rate_limit:{key}"

    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - WINDOW_SECONDS)
        pipe.zcard(redis_key)
        _, current_count = pipe.execute()

        if current_count >= limit:
            oldest = r.zrange(redis_key, 0, 0, withscores=True)
            retry_after = (
                max(1, int(oldest[0][1] + WINDOW_SECONDS - now) + 1)
                if oldest
                else WINDOW_SECONDS
            )
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

        member = f"{now}:{uuid.uuid4().hex}"
        pipe = r.pipeline()
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, WINDOW_SECONDS)
        pipe.execute()
    except HTTPException:
        raise
    except (redis.RedisError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Rate limiter storage unavailable") from exc
