import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .api import admin, agent, auth, bookings, checkouts, health, platform, search, tenant, trips, whatsapp
from .core.config import settings
from .core.errors import DomainError
from .core.observability import HTTP_LATENCY, HTTP_REQUESTS, configure_logging, request_id_var
from .core.ratelimit import build_limiter
from .db import Base, SessionLocal, engine
from .providers.registry import build_providers
from .services.agent import LLMClient
from .services.payments import build_gateway
from .services.whatsapp import WhatsAppClient
from .tenancy import ensure_platform, tenant_slug

log = logging.getLogger("ute")
_EXEMPT = ("/health", "/metrics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.validate_runtime()  # refuse to start misconfigured
    if settings.create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        await ensure_platform(db)  # default tenant + optional super admin; idempotent
    app.state.limiter = build_limiter(settings.redis_url)
    app.state.providers = build_providers(settings)
    app.state.gateway = build_gateway(settings)
    app.state.llm = LLMClient.from_settings()
    app.state.whatsapp = WhatsAppClient(settings, limiter=app.state.limiter) if settings.whatsapp_enabled else None
    log.info("started env=%s providers=%s gateway=%s agent=%s whatsapp=%s", settings.app_env, list(app.state.providers),
             app.state.gateway.name, app.state.llm is not None, app.state.whatsapp is not None)
    yield
    for p in app.state.providers.values():
        await p.aclose()
    for c in (app.state.gateway, app.state.llm, app.state.whatsapp, app.state.limiter):
        if c:
            await c.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Universal Travel Ecosystem API", version="0.2.0", lifespan=lifespan)

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        """Fixed window per (tenant, client IP). Shared across replicas when REDIS_URL is set."""
        if request.url.path.startswith(_EXEMPT) or settings.rate_limit_per_minute <= 0:
            return await call_next(request)
        ip = request.client.host if request.client else "?"
        ok, retry = await request.app.state.limiter.hit(f"{tenant_slug(request)}:{ip}", settings.rate_limit_per_minute)
        if not ok:
            return JSONResponse({"detail": "Too many requests", "code": "rate_limited"}, status_code=429,
                                headers={"Retry-After": str(retry)})
        return await call_next(request)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "Internal server error", "code": "internal"}, status_code=500)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        dt = time.perf_counter() - t0
        HTTP_REQUESTS.labels(request.method, route, response.status_code).inc()
        HTTP_LATENCY.labels(request.method, route).observe(dt)
        response.headers["X-Request-ID"] = rid
        if not request.url.path.startswith(_EXEMPT):
            log.info("%s %s -> %s %.0fms", request.method, request.url.path, response.status_code, dt * 1000)
        request_id_var.reset(token)
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_, e: DomainError):
        return JSONResponse({"detail": e.detail, "code": e.code}, status_code=e.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, e: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'][1:])}: {err['msg']}" for err in e.errors())
        return JSONResponse({"detail": msg, "code": "validation_error"}, status_code=422)

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health.router)
    for r in (auth, tenant, search, checkouts, bookings, trips, agent, whatsapp, admin, platform):
        app.include_router(r.router, prefix="/v1")
    return app


app = create_app()
