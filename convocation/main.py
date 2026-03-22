"""ConvocAItion — main FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from convocation.auth.models import Role, User
from convocation.auth.security import hash_password
from convocation.config import settings
from convocation.content.renderer import render_site
from convocation.content.store import ContentStore
from convocation.db import async_session, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database
    await init_db()

    # Bootstrap admin user if configured
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        async with async_session() as db:
            result = await db.execute(select(User).where(User.email == settings.bootstrap_admin_email))
            if not result.scalar_one_or_none():
                admin = User(
                    email=settings.bootstrap_admin_email,
                    display_name="Admin",
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role=Role.owner,
                )
                db.add(admin)
                await db.commit()

    # Initialize content repo
    store = ContentStore()

    # Initial site render
    try:
        render_site(store)
    except Exception:
        pass

    yield


app = FastAPI(
    title="ConvocAItion",
    description="Your community's site. Owned by everyone.",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Admin templates
admin_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates" / "admin"))

# Register API routers
from convocation.auth.routes import router as auth_router
from convocation.content.routes import router as content_router
from convocation.chat.routes import router as chat_router
from convocation.discord.webhook import router as discord_router
from convocation.notifications.push import router as push_router
from convocation.audit.routes import router as audit_router
from convocation.export.routes import router as export_router

app.include_router(auth_router)
app.include_router(content_router)
app.include_router(chat_router)
app.include_router(discord_router)
app.include_router(push_router)
app.include_router(audit_router)
app.include_router(export_router)


# --- Admin UI pages ---

@app.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    return admin_templates.TemplateResponse("index.html", {
        "request": request,
        "site_title": settings.site_title,
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return admin_templates.TemplateResponse("login.html", {
        "request": request,
        "site_title": settings.site_title,
    })


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, token: str = ""):
    return admin_templates.TemplateResponse("signup.html", {
        "request": request,
        "site_title": settings.site_title,
        "token": token,
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return admin_templates.TemplateResponse("chat.html", {
        "request": request,
        "site_title": settings.site_title,
    })


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    return admin_templates.TemplateResponse("history.html", {
        "request": request,
        "site_title": settings.site_title,
    })


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    return admin_templates.TemplateResponse("audit.html", {
        "request": request,
        "site_title": settings.site_title,
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return admin_templates.TemplateResponse("settings.html", {
        "request": request,
        "site_title": settings.site_title,
    })


# --- Public site serving ---

@app.get("/site/{path:path}")
async def serve_site(path: str):
    """Serve the generated static site."""
    output = settings.output_abs_path
    file_path = output / path

    if file_path.is_dir():
        file_path = file_path / "index.html"

    if not file_path.exists():
        file_path = output / "index.html"

    if file_path.exists():
        return FileResponse(file_path)

    return HTMLResponse("<h1>Site not generated yet</h1><p>Use the chat to create some content first.</p>", status_code=404)
