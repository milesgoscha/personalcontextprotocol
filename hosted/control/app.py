"""
PCP Hosted Service - Control Plane FastAPI Application.
"""

from collections import defaultdict
from contextlib import asynccontextmanager
from time import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings
from .database import close_db
from .services import start_health_checker, stop_health_checker


# Rate limiting
class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, limit: int | None = None) -> bool:
        """Check if request is allowed, returns False if rate limited."""
        now = time()
        window_start = now - 60
        max_requests = limit or self.rpm

        # Clean old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= max_requests:
            return False

        self.requests[key].append(now)
        return True


_rate_limiter = RateLimiter(requests_per_minute=60)

# Strict limits for auth endpoints (prevent brute force / spam)
STRICT_RATE_LIMIT_PATHS = {
    "/api/v1/auth/signup": 5,    # 5 signups per minute per IP
    "/api/v1/auth/login": 10,    # 10 login attempts per minute per IP
    "/api/v1/auth/refresh": 20,  # 20 refreshes per minute
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware."""

    async def dispatch(self, request: Request, call_next):
        # Use client IP as key for control plane (most requests are unauthenticated)
        client_ip = request.client.host if request.client else "unknown"

        # For authenticated requests, also include user info if available
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = f"token:{auth_header[7:23]}"
        else:
            key = f"ip:{client_ip}"

        # Check for stricter limits on auth paths
        path = request.url.path
        limit = STRICT_RATE_LIMIT_PATHS.get(path)

        if not _rate_limiter.is_allowed(key, limit):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = get_settings()
    print(f"Starting PCP Hosted Control Plane on {settings.host}:{settings.port}")
    print(f"Domain: {settings.pcp_domain}")

    # Start background services
    await start_health_checker()
    print("Health checker started")

    yield

    # Shutdown
    await stop_health_checker()
    await close_db()
    print("Control plane shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="PCP Hosted Service",
        description="Control plane for managed PCP nodes",
        version="0.3.0",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # Add rate limiting middleware
    app.add_middleware(RateLimitMiddleware)

    # Register API routes
    from .routes import (
        auth_router,
        nodes_router,
        proxy_router,
        dashboard_router,
        subdomain_proxy_router,
    )

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(nodes_router, prefix="/api/v1/node", tags=["nodes"])
    app.include_router(proxy_router, prefix="/api/v1/node", tags=["proxy"])

    # Register dashboard routes (HTML pages)
    app.include_router(dashboard_router, tags=["dashboard"])

    # Subdomain proxy for MCP access (catch-all, must be last)
    # This handles requests to {username}.pcp.bio/* and proxies to the shared node
    app.include_router(subdomain_proxy_router, tags=["subdomain-proxy"])

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "service": "pcp-hosted-control-plane"}

    return app


# Default app instance for uvicorn
app = create_app()
