"""API routes for PCP Hosted Service."""

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .nodes import router as nodes_router
from .proxy import router as proxy_router
from .subdomain_proxy import SubdomainProxyMiddleware

__all__ = [
    "auth_router",
    "dashboard_router",
    "nodes_router",
    "proxy_router",
    "SubdomainProxyMiddleware",
]
