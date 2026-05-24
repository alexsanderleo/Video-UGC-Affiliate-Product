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
