"""
FastAPI SaaS Entry Point — Video Affiliate AI Generator.

Optimized for 10,000 mobile users on VPS aaPanel:
- Full async I/O (SQLAlchemy async + uvicorn)
- Stateless JWT auth (no server-side sessions)
- Connection pooling for PostgreSQL
- CORS enabled for mobile app access

Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from api.v1.router import router as v1_router
from api.v1.generate import generate_video
from core.config import get_settings
from core.database import close_db, init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    # Startup: create tables (dev only — use Alembic for production)
    print("[STARTUP] Starting SaaS backend...")
    print(f"   Database: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
    await init_db()
    print("[DB] Database tables ready.")
    yield
    # Shutdown: close DB connections
    await close_db()
    print("[SHUTDOWN] SaaS backend stopped.")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "SaaS API untuk Video Affiliate AI Generator.\n\n"
        "**Fitur:**\n"
        "- 🔐 Register & Login (JWT)\n"
        "- 🚪 Force Logout All Devices\n"
        "- 📊 Daily Quota Management\n"
        "- 🎬 Video Generation (Protected)\n"
    ),
    docs_url="/docs",      # Swagger UI
    redoc_url="/redoc",    # ReDoc
    lifespan=lifespan,
)

# --- CORS Middleware ---
# Allow mobile apps and browsers to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include API v1 Router ---
app.include_router(v1_router, prefix=settings.API_V1_PREFIX)


# --- Legacy / PoC Frontend API Compatibility ---
# This ensures that frontend calls to `/api/generate` map seamlessly to our new JWT protected route
@app.post(
    "/api/generate",
    tags=["Video Generation"],
    summary="Legacy compatibility route for frontend video generation",
)
async def legacy_generate_video(response=Depends(generate_video)):
    """Deletes to the modern, JWT-protected generate endpoint for backward compatibility."""
    return response


# --- Serve Frontend and Outputs ---
# Ensure directories exist
os.makedirs("static", exist_ok=True)
os.makedirs("outputs", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


@app.get("/", tags=["UI"], summary="Serve frontend homepage")
async def serve_index():
    """Serve index.html at root url."""
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h3>Static folder index.html not found.</h3>", status_code=404)


@app.get("/mimin", tags=["UI"], summary="Serve admin dashboard")
async def serve_admin():
    """Serve admin.html at /mimin url."""
    admin_path = os.path.join("static", "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return HTMLResponse("<h3>Static folder admin.html not found.</h3>", status_code=404)


@app.get("/admin", tags=["UI"], summary="Serve admin dashboard")
async def serve_admin_dashboard_root():
    """Serve admin.html at /admin url."""
    admin_path = os.path.join("static", "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return HTMLResponse("<h3>Static folder admin.html not found.</h3>", status_code=404)


# --- Health Check ---
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": "1.0.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Auto-reload in dev
        workers=1,     # Use 1 for dev, 2-4 for production
    )
