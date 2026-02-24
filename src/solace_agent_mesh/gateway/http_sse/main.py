from __future__ import annotations

import logging
import os
import time as _time
from pathlib import Path

import httpx
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi import status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from typing import TYPE_CHECKING

from a2a.types import InternalError, InvalidRequestError, JSONRPCError
from a2a.types import JSONRPCResponse as A2AJSONRPCResponse

from ...common import a2a
from ...gateway.http_sse import dependencies
from ...shared.auth.middleware import create_oauth_middleware
from .routers import (
    agent_cards,
    artifacts,
    auth,
    config,
    feedback,
    people,
    sse,
    speech,
    version,
    visualization,
    projects,
    prompts,
)
from .routers.sessions import router as session_router
from .routers.tasks import router as task_router
from .routers.users import router as user_router


if TYPE_CHECKING:
    from .component import WebUIBackendComponent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate Limiting Middleware (P1 Item 9)
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Simple token bucket for per-session rate limiting."""

    __slots__ = ("rate", "burst", "tokens", "last_refill")

    def __init__(self, rate: float, burst: int):
        self.rate = rate  # tokens per second
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = _time.monotonic()

    def consume(self) -> bool:
        now = _time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> int:
        """Seconds until next token is available."""
        if self.tokens >= 1.0:
            return 0
        return max(1, int((1.0 - self.tokens) / self.rate) + 1)


class RateLimitMiddleware:
    """Per-session token-bucket rate limiter for task creation endpoints."""

    RATE_LIMITED_PREFIXES = ("/api/v1/tasks",)

    def __init__(self, app, *, rate_per_minute: int = 30, burst: int = 5):
        self.app = app
        self.rate = rate_per_minute / 60.0  # tokens per second
        self.burst = burst
        self._buckets: dict[str, _TokenBucket] = {}
        self._last_cleanup = _time.monotonic()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Only rate-limit POST requests to task endpoints
        if method == "POST" and any(path.startswith(p) for p in self.RATE_LIMITED_PREFIXES):
            # Extract session ID from cookies
            session_key = self._extract_session_key(scope)
            bucket = self._buckets.setdefault(
                session_key, _TokenBucket(self.rate, self.burst)
            )

            if not bucket.consume():
                retry_after = bucket.retry_after
                response = JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": "Too many requests. Please wait before submitting another query.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

            # Periodic cleanup of stale buckets (every 5 min)
            now = _time.monotonic()
            if now - self._last_cleanup > 300:
                self._last_cleanup = now
                cutoff = now - 600  # Remove buckets idle for 10 min
                stale = [k for k, b in self._buckets.items() if b.last_refill < cutoff]
                for k in stale:
                    del self._buckets[k]

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_session_key(scope) -> str:
        """Extract a session identifier from cookies or fall back to client IP."""
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode("utf-8", errors="ignore")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return f"session:{part[8:40]}"  # First 32 chars of session token
        # Fallback to client IP
        client = scope.get("client")
        return f"ip:{client[0]}" if client else "unknown"


# OAuth helper functions - delegate to enterprise package if available
async def _validate_token(
    auth_service_url: str,
    auth_provider: str,
    access_token: str,
) -> bool:
    """
    Validate an access token against SAM's OAuth2 service.

    This function delegates to the enterprise package's OAuth utilities.

    Args:
        auth_service_url: Base URL of the OAuth2 service
        auth_provider: Provider name configured in OAuth2 service
        access_token: The access token to validate

    Returns:
        True if token is valid, False otherwise
    """
    try:
        from solace_agent_mesh_enterprise.gateway.auth.internal.oauth_utils import (
            validate_token_with_oauth_service,
        )
        return await validate_token_with_oauth_service(
            auth_service_url, auth_provider, access_token
        )
    except ImportError:
        log.error("Enterprise package not available for OAuth token validation")
        return False


async def _get_user_info(
    auth_service_url: str,
    auth_provider: str,
    access_token: str,
) -> dict | None:
    """
    Retrieve user information from SAM's OAuth2 service.

    This function delegates to the enterprise package's OAuth utilities.

    Args:
        auth_service_url: Base URL of the OAuth2 service
        auth_provider: Provider name configured in OAuth2 service
        access_token: The validated access token

    Returns:
        Dictionary containing user claims, or None if request failed
    """
    try:
        from solace_agent_mesh_enterprise.gateway.auth.internal.oauth_utils import (
            get_user_info_from_oauth_service,
        )
        return await get_user_info_from_oauth_service(
            auth_service_url, auth_provider, access_token
        )
    except ImportError:
        log.error("Enterprise package not available for OAuth user info retrieval")
        return None


def _extract_user_identifier(user_info: dict, preferred_claim: str | None = None) -> str | None:
    """
    Extract the primary user identifier from OAuth user info.

    This function delegates to the enterprise package's OAuth utilities,
    with a fallback to "sam_dev_user" for development when identifier is invalid.

    Args:
        user_info: Dictionary of user claims from OAuth provider
        preferred_claim: OAuth claim to prioritize as user ID

    Returns:
        The user's primary identifier, or "sam_dev_user" if not found/invalid
    """
    try:
        from solace_agent_mesh_enterprise.gateway.auth.internal.oauth_utils import (
            extract_user_identifier,
        )
        # Only pass preferred_claim if it's not None to match test expectations
        if preferred_claim is not None:
            result = extract_user_identifier(user_info, preferred_claim)
        else:
            result = extract_user_identifier(user_info)
        # Fallback to sam_dev_user if enterprise returns None (invalid/unknown identifier)
        if result is None:
            return "sam_dev_user"
        return result
    except ImportError:
        log.error("Enterprise package not available for user identifier extraction")
        return "sam_dev_user"


app = FastAPI(
    title="A2A Web UI Backend",
    version="1.0.0",  # Updated to reflect simplified architecture
    description="Backend API and SSE server for the A2A Web UI, hosted by MedExpert AI Connector.",
)




def _setup_alembic_config(database_url: str) -> Config:
    alembic_cfg = Config()
    alembic_cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "alembic"),
    )
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    return alembic_cfg


def _run_community_migrations(database_url: str) -> None:
    """
    Run Alembic migrations for the community database schema.
    This includes sessions, chat_messages tables and their indexes.
    """
    try:
        from sqlalchemy import create_engine

        log.info("[WebUI Gateway] Starting community migrations...")
        engine = create_engine(database_url)
        inspector = sa.inspect(engine)
        existing_tables = inspector.get_table_names()

        alembic_cfg = _setup_alembic_config(database_url)
        if not existing_tables or "sessions" not in existing_tables:
            log.info("[WebUI Gateway] Running initial database setup")
        else:
            log.info("[WebUI Gateway] Checking for schema updates")

        command.upgrade(alembic_cfg, "head")
        log.info("[WebUI Gateway] Community migrations completed")
    except Exception as e:
        log.warning("[WebUI Gateway] Migration check failed: %s - attempting to run migrations", e)
        try:
            alembic_cfg = _setup_alembic_config(database_url)
            command.upgrade(alembic_cfg, "head")
            log.info("[WebUI Gateway] Community migrations completed")
        except Exception as migration_error:
            log.error("[WebUI Gateway] Migration failed: %s", migration_error)
            log.error("[WebUI Gateway] Check database connectivity and permissions")
            raise RuntimeError(
                f"Community database migration failed: {migration_error}"
            ) from migration_error




def _setup_database(database_url: str) -> None:
    """Initialize database and run migrations."""
    from ...common.middleware.registry import MiddlewareRegistry

    dependencies.init_database(database_url)
    log.info("[WebUI Gateway] Running community database migrations...")
    _run_community_migrations(database_url)
    log.info("[WebUI Gateway] Community migrations completed")

    # Run any registered post-migration hooks (e.g., enterprise migrations)
    MiddlewareRegistry.run_post_migration_hooks(database_url)
    log.info("[WebUI Gateway] Database setup complete")


def _get_app_config(component: "WebUIBackendComponent") -> dict:
    webui_app = component.get_app()
    app_config = {}
    if webui_app:
        app_config = getattr(webui_app, "app_config", {})
        if app_config is None:
            log.warning("webui_app.app_config is None, using empty dict.")
            app_config = {}
    else:
        log.warning("Could not get webui_app from component. Using empty app_config.")
    return app_config


def _create_api_config(app_config: dict, database_url: str) -> dict:
    return {
        "external_auth_service_url": app_config.get(
            "external_auth_service_url", "http://localhost:8080"
        ),
        "external_auth_callback_uri": app_config.get(
            "external_auth_callback_uri", "http://localhost:8000/api/v1/auth/callback"
        ),
        "external_auth_provider": app_config.get("external_auth_provider", "azure"),
        "frontend_use_authorization": app_config.get(
            "frontend_use_authorization", False
        ),
        "frontend_redirect_url": app_config.get(
            "frontend_redirect_url", "http://localhost:3000"
        ),
        "persistence_enabled": database_url is not None,
    }


def setup_dependencies(component: "WebUIBackendComponent"):
    """
    Initialize FastAPI dependencies (middleware, routers, static files).
    Database migrations are handled in component.__init__().

    Args:
        component: WebUIBackendComponent instance
    """
    dependencies.set_component_instance(component)

    app_config = _get_app_config(component)
    api_config_dict = _create_api_config(app_config, component.database_url)
    dependencies.set_api_config(api_config_dict)

    _setup_middleware(component)
    _setup_routers()
    _setup_static_files()


def _setup_middleware(component: "WebUIBackendComponent") -> None:
    allowed_origins = component.get_cors_origins()
    allow_credentials = getattr(component, "cors_allow_credentials", False)
    allow_methods = getattr(component, "cors_allow_methods", ["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    allow_headers = getattr(component, "cors_allow_headers", ["Content-Type", "Authorization", "X-Requested-With"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )
    log.info(
        "CORSMiddleware added with origins: %s, credentials: %s",
        allowed_origins,
        allow_credentials,
    )

    session_manager = component.get_session_manager()
    session_secure = component.get_config("session_cookie_secure", False)
    session_samesite = component.get_config("session_cookie_samesite", "lax")
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_manager.secret_key,
        https_only=session_secure,
        same_site=session_samesite,
    )
    log.info(
        "SessionMiddleware added (secure=%s, samesite=%s).",
        session_secure,
        session_samesite,
    )

    # Rate limiting (applied after session/auth in request flow due to ASGI LIFO ordering)
    rate_limit_enabled = component.get_config("rate_limit_enabled", False)
    if rate_limit_enabled:
        rpm = component.get_config("rate_limit_requests_per_minute", 30)
        burst = component.get_config("rate_limit_burst", 5)
        app.add_middleware(RateLimitMiddleware, rate_per_minute=rpm, burst=burst)
        log.info("RateLimitMiddleware added (%d req/min, burst %d)", rpm, burst)

    auth_middleware_class = create_oauth_middleware(component)
    app.add_middleware(auth_middleware_class, component=component)

    api_config = dependencies.get_api_config()
    use_auth = api_config.get("frontend_use_authorization", False) if api_config else False
    if use_auth:
        log.info("OAuth middleware added (real token validation enabled)")
    else:
        log.info("OAuth middleware added (development mode - community/dev user)")


def _setup_routers() -> None:
    api_prefix = "/api/v1"

    app.include_router(session_router, prefix=api_prefix, tags=["Sessions"])
    app.include_router(user_router, prefix=f"{api_prefix}/users", tags=["Users"])
    app.include_router(config.router, prefix=api_prefix, tags=["Config"])
    app.include_router(version.router, prefix=api_prefix, tags=["Version"])
    app.include_router(agent_cards.router, prefix=api_prefix, tags=["Agent Cards"])
    app.include_router(task_router, prefix=api_prefix, tags=["Tasks"])
    app.include_router(sse.router, prefix=f"{api_prefix}/sse", tags=["SSE"])
    app.include_router(
        artifacts.router, prefix=f"{api_prefix}/artifacts", tags=["Artifacts"]
    )
    app.include_router(
        visualization.router,
        prefix=f"{api_prefix}/visualization",
        tags=["Visualization"],
    )
    app.include_router(people.router, prefix=api_prefix, tags=["People"])
    app.include_router(auth.router, prefix=api_prefix, tags=["Auth"])
    app.include_router(projects.router, prefix=api_prefix, tags=["Projects"])
    app.include_router(feedback.router, prefix=api_prefix, tags=["Feedback"])
    app.include_router(prompts.router, prefix=f"{api_prefix}/prompts", tags=["Prompts"])
    app.include_router(speech.router, prefix=f"{api_prefix}/speech", tags=["Speech"])
    log.info("Legacy routers mounted for endpoints not yet migrated")

    # Register shared exception handlers
    from solace_agent_mesh.shared.exceptions.exception_handlers import register_exception_handlers

    register_exception_handlers(app)
    log.info("Registered shared exception handlers")


def _setup_static_files() -> None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = Path(os.path.normpath(os.path.join(current_dir, "..", "..")))
    static_files_dir = Path.joinpath(root_dir, "client", "webui", "frontend", "static")

    if not os.path.isdir(static_files_dir):
        log.warning(
            "Static files directory '%s' not found. Frontend may not be served.",
            static_files_dir,
        )
    # try to mount static files directory anyways, might work for enterprise
    try:
        app.mount(
            "/", StaticFiles(directory=static_files_dir, html=True), name="static"
        )
        log.info("Mounted static files directory '%s' at '/'", static_files_dir)
    except Exception as static_mount_err:
        log.error(
            "Failed to mount static files directory '%s': %s",
            static_files_dir,
            static_mount_err,
        )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: FastAPIRequest, exc: HTTPException):
    """
    HTTP exception handler with automatic format detection.
    Returns JSON-RPC format for tasks/SSE endpoints, REST format for others.
    """
    log.warning(
        "HTTP Exception Handler triggered: Status=%s, Detail=%s, Request: %s %s",
        exc.status_code,
        exc.detail,
        request.method,
        request.url,
    )

    # Check if this is a JSON-RPC endpoint (tasks and SSE endpoints use JSON-RPC)
    is_jsonrpc_endpoint = request.url.path.startswith(
        "/api/v1/tasks"
    ) or request.url.path.startswith("/api/v1/sse")

    if is_jsonrpc_endpoint:
        # Use JSON-RPC format for tasks and SSE endpoints
        error_data = None
        error_code = InternalError().code
        error_message = str(exc.detail)

        if isinstance(exc.detail, dict):
            if "code" in exc.detail and "message" in exc.detail:
                error_code = exc.detail["code"]
                error_message = exc.detail["message"]
                error_data = exc.detail.get("data")
            else:
                error_data = exc.detail
        elif isinstance(exc.detail, str):
            if exc.status_code == status.HTTP_400_BAD_REQUEST:
                error_code = -32600
            elif exc.status_code == status.HTTP_404_NOT_FOUND:
                error_code = -32601
                error_message = "Resource not found"

        error_obj = JSONRPCError(
            code=error_code, message=error_message, data=error_data
        )
        response = A2AJSONRPCResponse(error=error_obj)
        return JSONResponse(
            status_code=exc.status_code, content=response.model_dump(exclude_none=True)
        )
    else:
        # Use standard REST format for sessions and other REST endpoints
        if isinstance(exc.detail, dict):
            error_response = exc.detail
        elif isinstance(exc.detail, str):
            error_response = {"detail": exc.detail}
        else:
            error_response = {"detail": str(exc.detail)}

        return JSONResponse(status_code=exc.status_code, content=error_response)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: FastAPIRequest, exc: RequestValidationError
):
    """
    Handles Pydantic validation errors with format detection.
    """
    log.warning(
        "Validation Exception Handler triggered: %s, Request: %s %s",
        exc.errors(),
        request.method,
        request.url,
    )
    response = a2a.create_invalid_request_error_response(
        message="Invalid request parameters", data=exc.errors(), request_id=None
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=response.model_dump(exclude_none=True),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: FastAPIRequest, exc: Exception):
    """
    Handles any other unexpected exceptions with format detection.
    """
    log.exception(
        "Generic Exception Handler triggered: %s, Request: %s %s",
        exc,
        request.method,
        request.url,
    )
    error_obj = a2a.create_internal_error(
        message="An unexpected server error occurred: %s" % type(exc).__name__
    )
    response = a2a.create_error_response(error=error_obj, request_id=None)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=response.model_dump(exclude_none=True),
    )


@app.get("/health", tags=["Health"])
async def read_root():
    """Basic health check endpoint."""
    log.debug("Health check endpoint '/health' called")
    return {"status": "A2A Web UI Backend is running"}