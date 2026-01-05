"""
PCP Hosted Service - Control Plane FastAPI Application.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = get_settings()
    print(f"Starting PCP Hosted Control Plane on {settings.host}:{settings.port}")
    print(f"Domain: {settings.pcp_domain}")

    yield

    # Shutdown
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

    # Register routes
    from .routes import auth_router

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "service": "pcp-hosted-control-plane"}

    @app.get("/")
    async def root():
        """Root endpoint - will redirect to dashboard or landing page."""
        return {
            "name": "PCP Hosted Service",
            "version": "0.3.0",
            "docs": "/docs",
        }

    return app


# Default app instance for uvicorn
app = create_app()
