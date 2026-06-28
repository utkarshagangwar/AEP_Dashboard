"""Shared rate limiter instance for SlowAPI.

Defined in its own module to avoid circular imports between `main.py`
(which imports routers) and route modules (which need the limiter).

Default limit:  100 requests/minute per IP — applied globally via SlowAPIMiddleware.
Auth override:  10 requests/minute per IP  — applied per-route with @limiter.limit("10/minute").
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# default_limits applies to every route that does NOT have its own @limiter.limit() decorator.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
)
