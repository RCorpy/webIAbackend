from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from .routes import router
from .auth import get_current_user

app = FastAPI()

# ⚡ Must be SYNC for SlowAPI
def get_user_or_ip_key(request: Request):
    try:
        # Try to extract token manually from headers
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            # ⚠️ Can't call async `get_current_user` here
            # Instead, just use the raw token string as the key
            token = auth_header.split(" ")[1]
            return token
    except Exception:
        pass

    # fallback to IP if not authenticated
    return get_remote_address(request)

# Configure limiter with global 20/minute
limiter = Limiter(key_func=get_user_or_ip_key, default_limits=["20/minute"])
app.state.limiter = limiter

# Add SlowAPI middleware FIRST
app.add_middleware(SlowAPIMiddleware)

# Handle 429 errors
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://95.217.233.118:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(router, prefix="/api")
