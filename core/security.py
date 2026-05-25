"""
Security utilities — JWT token management and password hashing.
Stateless auth: JWT contains token_version for Force Logout support.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
import bcrypt

from core.config import get_settings

settings = get_settings()

# --- Password Hashing (Native Bcrypt) ---

def hash_password(password: str) -> str:
    """Hash a plaintext password using native bcrypt."""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a native bcrypt hash."""
    plain_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    try:
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


# --- JWT Token Management ---

def create_access_token(
    user_id: int,
    email: str,
    token_version: int,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.
    
    Payload includes:
    - sub: user ID (string)
    - email: user email
    - tv: token_version — for Force Logout validation
    - exp: expiration timestamp
    - iat: issued-at timestamp
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))

    payload = {
        "sub": str(user_id),
        "email": email,
        "tv": token_version,
        "exp": expire,
        "iat": now,
    }

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT access token.
    
    Returns the payload dict if valid, None if invalid/expired.
    Payload keys: sub (user_id), email, tv (token_version), exp, iat
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        # Ensure required fields exist
        if "sub" not in payload or "tv" not in payload:
            return None
        return payload
    except JWTError:
        return None


def parse_user_agent(ua_string: str) -> tuple[str, str]:
    """
    Lightweight, high-performance parser for client User-Agents.
    Returns: (device_brand, device_os)
    """
    if not ua_string:
        return "Unknown Device", "Unknown OS"

    ua_lower = ua_string.lower()

    # 1. Determine Operating System
    os = "Unknown OS"
    if "windows" in ua_lower:
        os = "Windows"
    elif "macintosh" in ua_lower or "mac os x" in ua_lower:
        if "ipad" in ua_lower or "iphone" in ua_lower:
            os = "iOS"
        else:
            os = "macOS"
    elif "android" in ua_lower:
        os = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        os = "iOS"
    elif "linux" in ua_lower:
        os = "Linux"

    # 2. Determine Brand / Manufacture / Browser Engine
    brand = "PC / Generic Browser"

    if "iphone" in ua_lower:
        brand = "Apple iPhone"
    elif "ipad" in ua_lower:
        brand = "Apple iPad"
    elif "samsung" in ua_lower or "sm-" in ua_lower:
        brand = "Samsung Mobile"
    elif "xiaomi" in ua_lower or "mi " in ua_lower or "redmi" in ua_lower or "poco" in ua_lower:
        brand = "Xiaomi Mobile"
    elif "oppo" in ua_lower or "cph" in ua_lower:
        brand = "Oppo Mobile"
    elif "vivo" in ua_lower or "v2" in ua_lower:
        brand = "Vivo Mobile"
    elif "realme" in ua_lower or "rmx" in ua_lower:
        brand = "Realme Mobile"
    elif "huawei" in ua_lower:
        brand = "Huawei Mobile"
    elif "pixel" in ua_lower:
        brand = "Google Pixel"
    elif "android" in ua_lower:
        brand = "Android Device"
    else:
        # Fallback to Browser detection on PC
        if "chrome" in ua_lower and "safari" in ua_lower and "edge" not in ua_lower and "edg" not in ua_lower and "opr" not in ua_lower:
            brand = "Google Chrome"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            brand = "Apple Safari"
        elif "firefox" in ua_lower:
            brand = "Mozilla Firefox"
        elif "edg" in ua_lower or "edge" in ua_lower:
            brand = "Microsoft Edge"
        elif "opr" in ua_lower or "opera" in ua_lower:
            brand = "Opera Browser"

    return brand, os
