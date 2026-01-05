"""
PCP Hosted Service - Control Plane FastAPI Application.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import close_db
from .services import start_health_checker, stop_health_checker


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

    # Register API routes
    from .routes import auth_router, nodes_router, proxy_router, dashboard_router

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(nodes_router, prefix="/api/v1/node", tags=["nodes"])
    app.include_router(proxy_router, prefix="/api/v1/node", tags=["proxy"])

    # Register dashboard routes (HTML pages)
    app.include_router(dashboard_router, tags=["dashboard"])

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "service": "pcp-hosted-control-plane"}

    return app


# Default app instance for uvicorn
app = create_app()
