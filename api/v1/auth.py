"""
Auth endpoints — Register, Login, Me, Force Logout.
All async for maximum concurrency on VPS.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from core.config import get_settings
from core.security import create_access_token, hash_password, verify_password, parse_user_agent
from models.user import User
from models.user_login import UserLogin
from schemas.auth import (
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    TokenResponse,
)
from schemas.user import UserBrief, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrasi user baru",
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Register a new user account.
    - Email must be unique
    - Password minimum 8 characters
    - Returns the created user profile
    """
    # Check if email already exists (efficient indexed query)
    existing = await db.execute(
        select(User.id).where(User.email == body.email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar. Gunakan email lain atau login.",
        )

    # Create user
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    
    plan_prices = {
        "monthly": 298000,
        "6months": 1295000,
        "1year": 1998000
    }
    plan_durations = {
        "monthly": 30,
        "6months": 180,
        "1year": 365
    }
    
    price_plan = body.price_plan if body.price_plan in plan_prices else "monthly"
    price = plan_prices[price_plan]
    expired_at = now + timedelta(days=plan_durations[price_plan])

    user = User(
        email=body.email,
        hashed_pw=hash_password(body.password),
        full_name=body.full_name,
        token_version=0,
        quota_reset=now,
        is_active=False,  # Requires admin approval
        price_plan=price_plan,
        price=price,
        expired_at=expired_at,
    )
    db.add(user)
    await db.commit()

    # Manually populate generated fields to avoid db.refresh issues with MySQL
    user.created_at = now

    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login dan dapatkan JWT token",
)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user and return JWT access token.
    - Validates email + password
    - Token includes token_version for Force Logout support
    """
    # Fetch user by email (indexed query)
    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email atau password salah.",
        )

    # Check for plan expiration
    if user.expired_at and datetime.utcnow() > user.expired_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Masa aktif akun Anda telah berakhir. Silakan hubungi Admin untuk melakukan perpanjangan.",
        )

    # Check if user is active / approved
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun Anda belum diaktifkan oleh Admin. Silakan hubungi Admin untuk konfirmasi pembayaran dan aktivasi.",
        )


    # Generate JWT with current token_version
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        token_version=user.token_version,
    )

    # Parse and save login log
    try:
        ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
        ua = request.headers.get("user-agent", "")
        brand, os = parse_user_agent(ua)

        login_log = UserLogin(
            user_id=user.id,
            ip_address=ip,
            user_agent=ua[:500],
            device_brand=brand,
            device_os=os
        )
        db.add(login_log)
        await db.commit()
    except Exception as e:
        # Don't block login if logging fails
        pass

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
        user=UserBrief.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get profil user yang sedang login",
)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the current authenticated user's profile."""
    return current_user


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    summary="Force logout dari semua device",
)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Force Logout — invalidate ALL existing JWT tokens.
    
    Increments token_version in DB. All JWTs issued before this
    moment will fail validation because their `tv` won't match.
    """
    current_user.token_version += 1
    db.add(current_user)
    await db.commit()

    return MessageResponse(
        message=f"Berhasil logout dari semua device. "
                f"Token version: {current_user.token_version}",
        success=True,
    )
