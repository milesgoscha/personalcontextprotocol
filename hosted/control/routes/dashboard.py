"""Dashboard routes for HTML pages.

These routes serve the web UI using Jinja2 templates.
Authentication is handled via session cookies.
"""

from datetime import datetime, timedelta, UTC
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.jwt import create_token_pair, decode_token, TokenError
from ..auth.password import hash_password, verify_password
from ..config import get_settings
from ..database import get_db
from ..models import User, Node, Session as DBSession, NodeStatus, AuditLog
from ..services.encryption import decrypt_token, DecryptionError
from ..services.node_client import NodeClient, NodeClientError

router = APIRouter()

# Template configuration
templates = Jinja2Templates(directory="control/templates")


# --- Cookie Helpers ---

ACCESS_TOKEN_COOKIE = "pcp_access_token"
REFRESH_TOKEN_COOKIE = "pcp_refresh_token"


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set authentication cookies."""
    settings = get_settings()

    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear authentication cookies."""
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE)


async def get_current_user_from_cookie(
    request: Request,
    db: AsyncSession,
) -> User | None:
    """Get the current user from session cookies."""
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)

    if not access_token:
        return None

    try:
        token_data = decode_token(access_token, expected_type="access")
    except TokenError:
        return None

    result = await db.execute(select(User).where(User.id == token_data.user_id))
    return result.scalar_one_or_none()


async def require_auth(request: Request, db: AsyncSession) -> User:
    """Require authentication, redirect to login if not authenticated."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


# --- Auth Pages ---


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    next: str | None = None,
):
    """Show login page."""
    user = await get_current_user_from_cookie(request, db)
    if user:
        # If already logged in, redirect to next URL or dashboard
        redirect_url = next if next else "/dashboard"
        return RedirectResponse(url=redirect_url, status_code=303)

    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "next": next or ""},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    """Handle login form submission."""
    # Find user
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid email or password", "email": email, "next": next},
            status_code=401,
        )

    # Create tokens
    access_token, refresh_token = create_token_pair(user.id)

    # Store session
    settings = get_settings()
    import hashlib
    session = DBSession(
        id=str(uuid4()),
        user_id=user.id,
        refresh_token_hash=hashlib.sha256(refresh_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    await db.commit()

    # If there's a next URL (e.g., from OAuth flow), use that
    if next:
        redirect_url = next
    else:
        # Check if user has completed onboarding
        result = await db.execute(select(Node).where(Node.user_id == user.id))
        node = result.scalar_one_or_none()

        redirect_url = "/dashboard"
        if node and node.status == NodeStatus.RUNNING:
            redirect_url = "/dashboard"
        elif node:
            redirect_url = "/onboarding/2"

    response = RedirectResponse(url=redirect_url, status_code=303)
    set_auth_cookies(response, access_token, refresh_token)
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show signup page."""
    settings = get_settings()
    user = await get_current_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "auth/signup.html",
        {"request": request, "domain": settings.pcp_domain},
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Handle signup form submission."""
    settings = get_settings()
    errors = []

    # Validate
    if password != password_confirm:
        errors.append("Passwords do not match")

    if len(password) < 8:
        errors.append("Password must be at least 8 characters")

    username = username.lower().strip()
    if len(username) < 3:
        errors.append("Username must be at least 3 characters")

    if errors:
        return templates.TemplateResponse(
            "auth/signup.html",
            {
                "request": request,
                "error": errors[0],
                "email": email,
                "username": username,
                "domain": settings.pcp_domain,
            },
            status_code=400,
        )

    # Check for existing user
    result = await db.execute(select(User).where(User.email == email.lower()))
    if result.scalar_one_or_none():
        return templates.TemplateResponse(
            "auth/signup.html",
            {
                "request": request,
                "error": "Email already registered",
                "username": username,
                "domain": settings.pcp_domain,
            },
            status_code=409,
        )

    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        return templates.TemplateResponse(
            "auth/signup.html",
            {
                "request": request,
                "error": "Username already taken",
                "email": email,
                "domain": settings.pcp_domain,
            },
            status_code=409,
        )

    # Create user
    user = User(
        id=str(uuid4()),
        email=email.lower(),
        username=username,
        password_hash=hash_password(password),
    )
    db.add(user)

    # Create pending node
    node = Node(
        id=str(uuid4()),
        user_id=user.id,
        status=NodeStatus.PENDING,
    )
    db.add(node)

    await db.commit()

    # Create tokens
    access_token, refresh_token = create_token_pair(user.id)

    # Store session
    import hashlib
    session = DBSession(
        id=str(uuid4()),
        user_id=user.id,
        refresh_token_hash=hashlib.sha256(refresh_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    await db.commit()

    # Go directly to dashboard (node provisioned in background)
    response = RedirectResponse(url="/dashboard", status_code=303)
    set_auth_cookies(response, access_token, refresh_token)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Handle logout."""
    user = await get_current_user_from_cookie(request, db)

    if user:
        # Delete all sessions
        from sqlalchemy import delete
        await db.execute(delete(DBSession).where(DBSession.user_id == user.id))
        await db.commit()

    response = RedirectResponse(url="/login", status_code=303)
    clear_auth_cookies(response)
    return response


# --- Dashboard Pages ---


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show dashboard home page."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    # Auto-provision pending nodes (replaces onboarding flow)
    if node and node.status == NodeStatus.PENDING:
        from ..routes.nodes import _provision_node_multi_tenant, _provision_node_legacy_task
        import asyncio

        prov_settings = get_settings()
        if prov_settings.multi_tenant:
            await _provision_node_multi_tenant(db, node, user.id, user.username)
            await db.refresh(node)
        else:
            asyncio.create_task(_provision_node_legacy_task(node.id, user.id, user.username))

    # Get stats if node is running
    settings = get_settings()
    stats = {"active_grants": 0, "pending_grants": 0}
    if node and node.status == NodeStatus.RUNNING:
        try:
            # Use shared node with X-User-Id header for multi-tenant mode
            node_url = settings.shared_node_url
            extra_headers = {"X-User-Id": str(user.id)}
            async with NodeClient(node_url, None, extra_headers) as client:
                grants = await client.get_grants()
                stats["active_grants"] = len([g for g in grants if g.get("status") == "approved"])
                stats["pending_grants"] = len([g for g in grants if g.get("status") == "pending"])
        except NodeClientError:
            pass
    return templates.TemplateResponse(
        "dashboard/home.html",
        {
            "request": request,
            "user": user,
            "node": node,
            "stats": stats,
            "active_page": "home",
            "is_admin": settings.is_admin(user.username),
        },
    )


@router.get("/dashboard/grants", response_class=HTMLResponse)
async def dashboard_grants(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
):
    """Show grants management page."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    grants = []
    if node and node.status == NodeStatus.RUNNING:
        try:
            # Use shared node with X-User-Id header for multi-tenant mode
            node_url = settings.shared_node_url
            extra_headers = {"X-User-Id": str(user.id)}
            async with NodeClient(node_url, None, extra_headers) as client:
                all_grants = await client.get_grants()
                # Filter out revoked grants (no longer actionable)
                active_grants = [g for g in all_grants if g.get("status") != "revoked"]
                if status_filter:
                    grants = [g for g in active_grants if g.get("status") == status_filter]
                else:
                    grants = active_grants
        except NodeClientError:
            pass

    return templates.TemplateResponse(
        "dashboard/grants.html",
        {
            "request": request,
            "user": user,
            "grants": grants,
            "status_filter": status_filter,
            "active_page": "grants",
            "is_admin": settings.is_admin(user.username),
        },
    )


@router.get("/dashboard/tokens", response_class=HTMLResponse)
async def dashboard_tokens(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show token management page."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    return templates.TemplateResponse(
        "dashboard/tokens.html",
        {
            "request": request,
            "user": user,
            "active_page": "tokens",
            "is_admin": settings.is_admin(user.username),
        },
    )


@router.get("/dashboard/audit", response_class=HTMLResponse)
async def dashboard_audit(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 50,
    offset: int = 0,
):
    """Show audit log page."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    settings = get_settings()
    entries = []
    if node and node.status == NodeStatus.RUNNING:
        try:
            # Use shared node with X-User-Id header for multi-tenant mode
            node_url = settings.shared_node_url
            extra_headers = {"X-User-Id": str(user.id)}
            async with NodeClient(node_url, None, extra_headers) as client:
                response = await client.get_audit_log(limit=limit, offset=offset)
                # API returns {"events": [...], ...} - extract the events list
                entries = response.get("events", []) if isinstance(response, dict) else response
        except NodeClientError:
            pass

    return templates.TemplateResponse(
        "dashboard/audit.html",
        {
            "request": request,
            "user": user,
            "entries": entries,
            "limit": limit,
            "offset": offset,
            "active_page": "audit",
            "is_admin": settings.is_admin(user.username),
        },
    )


@router.get("/dashboard/settings", response_class=HTMLResponse)
async def dashboard_settings(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show settings page."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    settings = get_settings()
    return templates.TemplateResponse(
        "dashboard/settings.html",
        {
            "request": request,
            "user": user,
            "node": node,
            "active_page": "settings",
            "is_admin": settings.is_admin(user.username),
        },
    )


# --- Onboarding Pages ---


@router.get("/onboarding/1", response_class=HTMLResponse)
async def onboarding_step1(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show onboarding step 1."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "onboarding/step1.html",
        {"request": request, "user": user},
    )


@router.get("/onboarding/2", response_class=HTMLResponse)
async def onboarding_step2(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show onboarding step 2."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    # Trigger provisioning if node is pending
    if node and node.status == NodeStatus.PENDING:
        from ..routes.nodes import _provision_node_multi_tenant, _provision_node_legacy_task
        import asyncio

        prov_settings = get_settings()
        if prov_settings.multi_tenant:
            # Multi-tenant: instant provisioning
            await _provision_node_multi_tenant(db, node, user.id, user.username)
            await db.refresh(node)
        else:
            # Legacy: background task
            asyncio.create_task(_provision_node_legacy_task(node.id, user.id, user.username))

    return templates.TemplateResponse(
        "onboarding/step2.html",
        {"request": request, "user": user, "node": node},
    )


@router.get("/onboarding/3", response_class=HTMLResponse)
async def onboarding_step3(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show onboarding step 3."""
    user = await get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(Node).where(Node.user_id == user.id))
    node = result.scalar_one_or_none()

    return templates.TemplateResponse(
        "onboarding/step3.html",
        {"request": request, "user": user, "node": node},
    )


# --- Landing Page ---


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show docs landing page or redirect to dashboard if logged in."""
    user = await get_current_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "docs/home.html",
        {"request": request},
    )


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Show docs page (accessible to everyone, logged in or not)."""
    user = await get_current_user_from_cookie(request, db)

    if user:
        # Get the user's node for personalized URLs
        result = await db.execute(select(Node).where(Node.user_id == user.id))
        node = result.scalar_one_or_none()

        settings = get_settings()
        return templates.TemplateResponse(
            "dashboard/docs.html",
            {
                "request": request,
                "user": user,
                "node": node,
                "active_page": "docs",
                "is_admin": settings.is_admin(user.username),
            },
        )

    # Not logged in - show standalone docs page
    return templates.TemplateResponse(
        "docs/home.html",
        {"request": request, "user": None, "node": None},
    )
