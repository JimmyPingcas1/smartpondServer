import os
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import requests
from openai import OpenAI

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


# Load environment variables FIRST
# -------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(str(ROOT_DIR / ".env"))


# Import after .env is loaded
# ---------------------------
from .db import init_db
from .middleware.rateLimiter import limiter


# Application lifespan
# --------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Import routes after database initialization so their collection
    # bindings reference the initialized Motor collections.
    if not getattr(app.state, "routes_registered", False):
        from .routes import routers

        for router in routers:
            app.include_router(router)

        app.state.routes_registered = True

    yield


# FastAPI application
# -------------------
app = FastAPI(
    title="AI Water Quality System",
    lifespan=lifespan,
)


# Security Headers
# ----------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response


app.add_middleware(SecurityHeadersMiddleware)

 
# CORS
# ----
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Rate Limiting
# -------------
app.state.limiter = limiter

app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
)


# Health Check
# ------------
@app.get("/api/v1/health")
def api_health():
    return {"status": "ok"}

@app.get("/debug/ip")
def debug_ip():
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text
        return {"outbound_ip": ip}
    except Exception as e:
        return {"error": str(e)}


@app.get("/debug/openai")
def debug_openai():
    outbound_ip = None

    try:
        outbound_ip = requests.get(
            "https://api.ipify.org",
            timeout=10
        ).text.strip()

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

        response = client.responses.create(
            model="gpt-5-mini",
            input="Reply with the word OK."
        )

        return {
            "success": True,
            "outbound_ip": outbound_ip,
            "response": response.output_text
        }

    except Exception as e:
        return {
            "success": False,
            "outbound_ip": outbound_ip,
            "error_type": type(e).__name__,
            "error": str(e)
        }

# Local execution
# ---------------
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))

    uvicorn.run(
        "app.server:app",
        host=host,
        port=port,
        reload=False,
    )