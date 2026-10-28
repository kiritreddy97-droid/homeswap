import json
import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("homeswap.request")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so Render/Datadog/etc. can index fields."""

    _EXTRA = ("request_id", "method", "path", "status", "duration_ms", "client")

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname, "logger": record.name}
        out["msg"] = record.getMessage()
        for key in self._EXTRA:
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


class RateLimiter:
    """In-process sliding window per key. Per-instance only: with several instances the effective limit
    is multiplied, so move to Redis (or the host's edge limiter) when scaling out."""

    def __init__(self, per_minute: int, window: float = 60.0):
        self.per_minute = per_minute
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> tuple[bool, int]:
        t = time.monotonic()
        if t - self._last_prune > self.window:
            self._prune(t)
        q = self.hits[key]
        while q and t - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.per_minute:
            return False, max(1, int(self.window - (t - q[0])) + 1)
        q.append(t)
        return True, 0

    def _prune(self, t: float) -> None:
        for key in [k for k, q in self.hits.items() if not q or t - q[-1] >= self.window]:
            del self.hits[key]
        self._last_prune = t


class RequestMiddleware(BaseHTTPMiddleware):
    """Request id, rate limiting and one structured access-log line per request."""

    def __init__(self, app, limiter: RateLimiter, auth_limiter: RateLimiter):
        super().__init__(app)
        self.limiter = limiter
        self.auth_limiter = auth_limiter

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        client = request.client.host if request.client else "unknown"
        start = time.perf_counter()
        path = request.url.path
        is_auth = path.startswith("/auth/")
        limiter = self.auth_limiter if is_auth else self.limiter
        allowed, retry_after = (True, 0) if path == "/health" else limiter.allow(f"{client}:{is_auth}")
        if not allowed:
            response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
            response.headers["Retry-After"] = str(retry_after)
        else:
            try:
                response = await call_next(request)
            except Exception:
                log.exception(
                    "unhandled error", extra={"request_id": request_id, "method": request.method, "path": path}
                )
                response = JSONResponse({"detail": "internal server error"}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                "client": client,
            },
        )
        return response
