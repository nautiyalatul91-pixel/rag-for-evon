import time
from typing import Dict, List
from fastapi import HTTPException, status, Depends
from app.services.auth_service import get_current_user
from app.config import logger

class RateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        # In-memory store: username -> list of timestamp floats
        self.requests: Dict[str, List[float]] = {}

    def check_limit(self, current_user: dict = Depends(get_current_user)) -> None:
        """
        FastAPI dependency to rate limit requests.
        Enforces a sliding window rate limit per authenticated user (20 requests per minute by default).
        Resets on server restart and is single-process only (known limitation).
        """
        username = current_user["username"]
        now = time.time()
        
        # Initialize user request history
        user_history = self.requests.setdefault(username, [])
        
        # Evict timestamps outside the sliding window
        cutoff = now - self.window_seconds
        user_history = [t for t in user_history if t > cutoff]
        self.requests[username] = user_history
        
        # Check limit breach
        if len(user_history) >= self.limit:
            logger.warning("Rate limit exceeded for user '%s'. Request blocked.", username)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {self.limit} requests per minute are allowed."
            )
            
        # Record new request
        user_history.append(now)

# Global rate limiter instance (defaults to 20 requests per 60 seconds)
chat_rate_limiter = RateLimiter(limit=20, window_seconds=60)
