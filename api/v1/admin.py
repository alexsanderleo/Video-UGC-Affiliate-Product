"""
Admin router — Dashboard and platform monitoring endpoints.
Optimized for aaPanel low-RAM VPS using HTMX partial rendering.
"""

import os
from datetime import datetime, timezone
import redis.asyncio as async_redis

from fastapi import APIRouter, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_current_admin
from models.user import User
from models.generation_log import GenerationLog
from core.config import get_settings
from core.security import hash_password

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])
settings = get_settings()


def format_bytes(b: int) -> str:
    """Format bytes into human-readable MB or GB."""
    if b < 1024 * 1024:
        return f"{(b / 1024):.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{(b / (1024 * 1024)):.1f} MB"
    else:
        return f"{(b / (1024 * 1024 * 1024)):.2f} GB"


@router.get(
    "/mimin",
    response_class=HTMLResponse,
    summary="Serve the Admin Dashboard page",
)
async def serve_admin_dashboard():
    """Serve the static admin.html dashboard."""
    admin_path = os.path.join("static", "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return HTMLResponse("<h3>Admin folder admin.html not found.</h3>", status_code=404)


@router.get(
    "/stats",
    response_class=HTMLResponse,
    summary="Get HTML fragments for dashboard stats cards",
)
async def get_stats_cards(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Fetch metrics and return modern Tailind-styled HTML card grid."""
    # 1. Total Generations (Success vs Total)
    success_res = await db.execute(
        select(func.count(GenerationLog.id)).where(GenerationLog.status == "success")
    )
    success_count = success_res.scalar() or 0

    total_res = await db.execute(
        select(func.count(GenerationLog.id))
    )
    total_count = total_res.scalar() or 0

    # 2. Total Bandwidth
    bw_res = await db.execute(
        select(func.sum(GenerationLog.bandwidth_bytes))
    )
    total_bw_bytes = bw_res.scalar() or 0
    formatted_bw = format_bytes(total_bw_bytes)

    # 3. Active Queue (pending + processing)
    queue_res = await db.execute(
        select(func.count(GenerationLog.id)).where(GenerationLog.status.in_(["pending", "processing"]))
    )
    active_queue = queue_res.scalar() or 0

    # 4. Online Users in Redis
    online_count = 0
    try:
        r = async_redis.from_url(settings.REDIS_URL)
        keys = await r.keys("user_active:*")
        online_count = len(keys)
        await r.close()
    except Exception:
        pass

    # Return elegant HTMX cards
    html = f"""
    <!-- Card 1: Online Users -->
    <div class="glass-card p-6 flex items-center justify-between transition duration-300 hover:scale-105 border border-indigo-500/20">
        <div>
            <p class="text-sm font-medium text-slate-400 uppercase tracking-wider">User Online (Redis)</p>
            <h3 class="text-3xl font-extrabold text-white mt-1">{online_count}</h3>
        </div>
        <div class="p-3 bg-emerald-500/10 rounded-xl border border-emerald-500/20 text-emerald-400">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
        </div>
    </div>

    <!-- Card 2: Total Generations -->
    <div class="glass-card p-6 flex items-center justify-between transition duration-300 hover:scale-105 border border-purple-500/20">
        <div>
            <p class="text-sm font-medium text-slate-400 uppercase tracking-wider">Total Video Sukses</p>
            <h3 class="text-3xl font-extrabold text-white mt-1">{success_count} <span class="text-sm font-normal text-slate-500">/ {total_count} total</span></h3>
        </div>
        <div class="p-3 bg-purple-500/10 rounded-xl border border-purple-500/20 text-purple-400">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
        </div>
    </div>

    <!-- Card 3: Total Bandwidth -->
    <div class="glass-card p-6 flex items-center justify-between transition duration-300 hover:scale-105 border border-blue-500/20">
        <div>
            <p class="text-sm font-medium text-slate-400 uppercase tracking-wider">Bandwidth Keluar</p>
            <h3 class="text-3xl font-extrabold text-white mt-1">{formatted_bw}</h3>
        </div>
        <div class="p-3 bg-blue-500/10 rounded-xl border border-blue-500/20 text-blue-400">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
            </svg>
        </div>
    </div>

    <!-- Card 4: Queue Jobs -->
    <div class="glass-card p-6 flex items-center justify-between transition duration-300 hover:scale-105 border border-amber-500/20">
        <div>
            <p class="text-sm font-medium text-slate-400 uppercase tracking-wider">Antrean Render</p>
            <h3 class="text-3xl font-extrabold mt-1 {'text-amber-400 pulse' if active_queue > 0 else 'text-white'}">{active_queue}</h3>
        </div>
        <div class="p-3 bg-amber-500/10 rounded-xl border border-amber-500/20 text-amber-400">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
        </div>
    </div>
    """
    return HTMLResponse(html)


@router.get(
    "/logs",
    response_class=HTMLResponse,
    summary="Get HTML table rows for generation logs",
)
async def get_logs_rows(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Fetch latest 15 logs and return modern styled HTML table body."""
    result = await db.execute(
        select(GenerationLog, User.email)
        .join(User, GenerationLog.user_id == User.id)
        .order_by(GenerationLog.created_at.desc())
        .limit(15)
    )
    logs_data = result.all()

    if not logs_data:
        return HTMLResponse("""
        <tr>
            <td colspan="7" class="px-6 py-10 text-center text-slate-500 text-sm">
                Belum ada aktivitas rendering video.
            </td>
        </tr>
        """)

    html = ""
    for log, email in logs_data:
        # Style status badges
        status_colors = {
            "success": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
            "processing": "bg-amber-500/10 text-amber-400 border-amber-500/20 pulse",
            "pending": "bg-blue-500/10 text-blue-400 border-blue-500/20",
            "failed": "bg-rose-500/10 text-rose-400 border-rose-500/20",
        }
        badge_style = status_colors.get(log.status, "bg-slate-500/10 text-slate-400")
        
        formatted_time = log.created_at.strftime("%Y-%m-%d %H:%M")
        size_str = format_bytes(log.bandwidth_bytes) if log.status == "success" else "-"
        duration_str = f"{log.duration:.1f}s" if log.duration > 0 else "-"
        
        err_col = f'<span class="text-xs text-rose-400 block max-w-xs truncate" title="{log.error_message}">{log.error_message}</span>' if log.error_message else "-"

        html += f"""
        <tr class="border-b border-slate-800/50 hover:bg-slate-900/30 transition">
            <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-300">#{log.job_id}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">{email}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">{formatted_time}</td>
            <td class="px-6 py-4 whitespace-nowrap">
                <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border {badge_style}">
                    {log.status.upper()}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-300">{duration_str}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-300">{size_str}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">{err_col}</td>
        </tr>
        """
    return HTMLResponse(html)


def render_user_row(user: User) -> str:
    """Helper to render a single user row in the admin panel."""
    status_badge = (
        '<span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">AKTIF</span>'
        if user.is_active
        else '<span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">NONAKTIF</span>'
    )

    admin_badge = (
        '<span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">ADMIN</span>'
        if user.is_admin
        else ""
    )

    plan_names = {
        "monthly": "Bulanan (298k)",
        "6months": "6 Bulan (1295k)",
        "1year": "1 Tahun (1998k)"
    }
    plan_display = plan_names.get(user.price_plan, user.price_plan or "-")
    expired_str = user.expired_at.strftime("%Y-%m-%d") if user.expired_at else "-"

    # Standard edit and delete actions (switching delete to hx-post)
    common_actions = f"""
    <button 
        hx-get="/api/v1/admin/users/{user.id}/edit"
        hx-target="closest tr"
        hx-swap="outerHTML"
        class="px-2.5 py-1.5 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-semibold text-xs hover:bg-indigo-600 hover:text-white transition duration-200"
        title="Edit User"
    >
        📝 Edit
    </button>
    <button 
        hx-post="/api/v1/admin/users/{user.id}/delete"
        hx-confirm="Apakah Anda yakin ingin menghapus pengguna {user.email} secara permanen?"
        hx-target="closest tr"
        hx-swap="outerHTML"
        class="px-2.5 py-1.5 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20 font-semibold text-xs hover:bg-rose-500 hover:text-white transition duration-200"
        title="Hapus User"
    >
        🗑️ Hapus
    </button>
    """

    if user.is_active:
        specific_actions = f"""
        <button 
            hx-post="/api/v1/admin/users/{user.id}/force-logout"
            hx-confirm="Apakah Anda yakin ingin melakukan Force Logout untuk user ini?"
            hx-target="closest tr"
            hx-swap="outerHTML"
            class="px-2.5 py-1.5 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/20 font-semibold text-xs hover:bg-amber-500 hover:text-white transition duration-200"
            title="Force Logout"
        >
            🔑 Logout
        </button>
        <button 
            hx-post="/api/v1/admin/users/{user.id}/deactivate"
            hx-confirm="Apakah Anda yakin ingin menonaktifkan akun ini?"
            hx-target="closest tr"
            hx-swap="outerHTML"
            class="px-2.5 py-1.5 rounded-lg bg-slate-500/10 text-slate-400 border border-slate-500/20 font-semibold text-xs hover:bg-slate-500 hover:text-white transition duration-200"
            title="Nonaktifkan Akun"
        >
            🚫 Block
        </button>
        """
    else:
        specific_actions = f"""
        <button 
            hx-post="/api/v1/admin/users/{user.id}/approve"
            hx-confirm="Apakah Anda yakin ingin menyetujui (mengaktifkan) akun ini?"
            hx-target="closest tr"
            hx-swap="outerHTML"
            class="px-2.5 py-1.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-semibold text-xs hover:bg-emerald-500 hover:text-white transition duration-200"
            title="Setujui Akun"
        >
            ✅ ACC
        </button>
        """

    return f"""
    <tr class="border-b border-slate-800/50 hover:bg-slate-900/30 transition">
        <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-300">#{user.id}</td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-200">{user.full_name or '-'}</td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">
            <div class="flex items-center gap-1.5 flex-wrap">
                <span>{user.email}</span>
                {admin_badge}
            </div>
            <div class="text-[11px] text-indigo-400 font-semibold mt-0.5">Paket: {plan_display}</div>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-300">{user.quota_used} / {user.daily_quota}</td>
        <td class="px-6 py-4 whitespace-nowrap">
            <div class="flex flex-col gap-1 items-start">
                {status_badge}
                <div class="text-[10px] text-slate-400 mt-0.5">Expired: <span class="font-semibold text-slate-300">{expired_str}</span></div>
            </div>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm">
            <div class="flex gap-1.5 flex-wrap">
                {specific_actions}
                {common_actions}
            </div>
        </td>
    </tr>
    """


def render_user_edit_row(user: User) -> str:
    """Helper to render an inline edit form row in the admin panel table."""
    expired_str = user.expired_at.strftime("%Y-%m-%d") if user.expired_at else ""
    return f"""
    <tr class="bg-slate-900/60 border-b border-indigo-500/30">
        <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-300">#{user.id}</td>
        <td class="px-6 py-4 whitespace-nowrap">
            <input type="text" name="full_name" value="{user.full_name or ''}" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-sm text-slate-200 w-32 focus:border-indigo-500 focus:outline-none">
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            <input type="email" name="email" value="{user.email}" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-sm text-slate-200 w-44 focus:border-indigo-500 focus:outline-none">
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            <div class="flex flex-col gap-1">
                <div class="flex items-center gap-1.5">
                    <input type="number" name="daily_quota" value="{user.daily_quota}" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-sm text-slate-200 w-16 focus:border-indigo-500 focus:outline-none">
                    <span class="text-xs text-slate-500">video/hari</span>
                </div>
                <div class="flex flex-col gap-0.5">
                    <span class="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Paket</span>
                    <select name="price_plan" class="bg-slate-950 border border-slate-800 rounded-lg px-1.5 py-0.5 text-xs text-slate-300 focus:border-indigo-500 focus:outline-none">
                        <option value="monthly" {"selected" if user.price_plan == "monthly" else ""}>Bulanan (298k)</option>
                        <option value="6months" {"selected" if user.price_plan == "6months" else ""}>6 Bulan (1295k)</option>
                        <option value="1year" {"selected" if user.price_plan == "1year" else ""}>1 Tahun (1998k)</option>
                    </select>
                </div>
            </div>
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            <div class="flex flex-col gap-1">
                <div class="flex gap-1.5 flex-wrap">
                    <select name="is_active" class="bg-slate-950 border border-slate-800 rounded-lg px-1.5 py-0.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none" title="Status Akun">
                        <option value="1" {"selected" if user.is_active else ""}>AKTIF</option>
                        <option value="0" {"selected" if not user.is_active else ""}>NONAKTIF</option>
                    </select>
                    <select name="is_admin" class="bg-slate-950 border border-slate-800 rounded-lg px-1.5 py-0.5 text-xs text-slate-300 focus:border-indigo-500 focus:outline-none" title="Role Pengguna">
                        <option value="0" {"selected" if not user.is_admin else ""}>USER</option>
                        <option value="1" {"selected" if user.is_admin else ""}>ADMIN</option>
                    </select>
                </div>
                <div class="flex flex-col gap-0.5">
                    <span class="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Expired At</span>
                    <input type="date" name="expired_at" value="{expired_str}" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-0.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none">
                </div>
            </div>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm">
            <div class="flex gap-1.5">
                <button 
                    hx-post="/api/v1/admin/users/{user.id}/update"
                    hx-include="closest tr"
                    hx-target="closest tr"
                    hx-swap="outerHTML"
                    class="px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-bold text-xs hover:bg-emerald-700 transition"
                >
                    💾 Simpan
                </button>
                <button 
                    hx-get="/api/v1/admin/users/{user.id}/row"
                    hx-target="closest tr"
                    hx-swap="outerHTML"
                    class="px-3 py-1.5 rounded-lg bg-slate-850 text-slate-300 border border-slate-700 font-bold text-xs hover:bg-slate-700 transition"
                >
                    ❌ Batal
                </button>
            </div>
        </td>
    </tr>
    """


@router.get(
    "/users",
    response_class=HTMLResponse,
    summary="Get HTML table rows for SaaS users management",
)
async def get_users_rows(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Fetch all users and return modern HTML list with actions."""
    result = await db.execute(
        select(User).order_by(User.id.asc())
    )
    users = result.scalars().all()

    if not users:
        return HTMLResponse("""
        <tr>
            <td colspan="6" class="px-6 py-10 text-center text-slate-500 text-sm">
                Belum ada user terdaftar.
            </td>
        </tr>
        """)

    html = "".join(render_user_row(user) for user in users)
    return HTMLResponse(html)


@router.post(
    "/users/create",
    response_class=HTMLResponse,
    summary="Admin creates a new user manually",
)
async def admin_create_user(
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    price_plan: str = Form("monthly"),
    is_admin: int = Form(0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint for admin to manually add a new user."""
    # Check if email exists
    existing = await db.execute(
        select(User.id).where(User.email == email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar"
        )
        
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    
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
    
    plan = price_plan if price_plan in plan_prices else "monthly"
    price = plan_prices[plan]
    expired_at = now + timedelta(days=plan_durations[plan])

    user = User(
        email=email,
        hashed_pw=hash_password(password),
        full_name=full_name,
        is_active=True,  # Admin-created users are active by default
        token_version=0,
        quota_reset=now,
        price_plan=plan,
        price=price,
        expired_at=expired_at,
        is_admin=bool(is_admin),
    )
    db.add(user)
    await db.commit()
    
    # Return HTML representation to append
    return HTMLResponse(render_user_row(user))


@router.get(
    "/users/{user_id}/row",
    response_class=HTMLResponse,
    summary="Get simple HTML row representation of a user",
)
async def get_user_row_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to get a single row representation (for edit canceling)."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return HTMLResponse(render_user_row(user))


@router.get(
    "/users/{user_id}/edit",
    response_class=HTMLResponse,
    summary="Get HTML row edit form representation",
)
async def get_user_edit_row_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to swap standard row into an edit row form."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return HTMLResponse(render_user_edit_row(user))


@router.post(
    "/users/{user_id}/update",
    response_class=HTMLResponse,
    summary="Update a user's details inline",
)
async def admin_update_user(
    user_id: int,
    full_name: str = Form(...),
    email: str = Form(...),
    daily_quota: int = Form(...),
    is_active: int = Form(...),
    price_plan: str = Form(...),
    expired_at: str = Form(None),
    is_admin: int = Form(0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to update user details and return updated row."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
    # Check email uniqueness if changed
    if email != user.email:
        existing = await db.execute(
            select(User.id).where(User.email == email)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email sudah digunakan"
            )
            
    user.full_name = full_name
    user.email = email
    user.daily_quota = daily_quota
    user.is_active = bool(is_active)
    user.price_plan = price_plan
    user.is_admin = bool(is_admin)
    
    plan_prices = {
        "monthly": 298000,
        "6months": 1295000,
        "1year": 1998000
    }
    if price_plan in plan_prices:
        user.price = plan_prices[price_plan]
        
    if expired_at:
        try:
            dt = datetime.strptime(expired_at, "%Y-%m-%d")
            user.expired_at = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    
    db.add(user)
    await db.commit()
    
    return HTMLResponse(render_user_row(user))


@router.post(
    "/users/{user_id}/delete",
    response_class=HTMLResponse,
    summary="Permanently delete a user",
)
async def admin_delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to permanently delete a user from DB."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Anda tidak dapat menghapus akun admin Anda sendiri."
        )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
    await db.delete(user)
    await db.commit()
    
    # Return empty response to let HTMX remove the outerHTML row
    return HTMLResponse("")


@router.post(
    "/users/{user_id}/approve",
    response_class=HTMLResponse,
    summary="Approve and activate a registered user",
)
async def admin_approve_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to approve and activate a user."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
        
    user.is_active = True
    db.add(user)
    await db.commit()
    
    return HTMLResponse(render_user_row(user))


@router.post(
    "/users/{user_id}/deactivate",
    response_class=HTMLResponse,
    summary="Deactivate and block a registered user",
)
async def admin_deactivate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to deactivate (block) a user."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Anda tidak dapat menonaktifkan akun admin Anda sendiri."
        )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
        
    user.is_active = False
    db.add(user)
    await db.commit()
    
    return HTMLResponse(render_user_row(user))


@router.post(
    "/users/{user_id}/force-logout",
    response_class=HTMLResponse,
    summary="Increment token_version to force logout all devices",
)
async def admin_force_logout(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """API endpoint to execute force logout from the dashboard."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Anda tidak dapat melakukan Force Logout pada diri sendiri."
        )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
        
    # Increment token_version to invalidate JWT tokens
    user.token_version += 1
    db.add(user)
    await db.commit()
    
    return HTMLResponse(render_user_row(user))


@router.get(
    "/active-tasks",
    response_class=HTMLResponse,
    summary="Get HTML table rows for currently running FFmpeg processes",
)
async def get_active_tasks(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Fetch active tasks from Redis and match with database logs and users."""
    import redis
    r = redis.from_url(settings.REDIS_URL)
    
    # 1. Get all task_pid keys from Redis
    try:
        keys = r.keys("task_pid:*")
    except Exception as e:
        print(f"[ACTIVE TASKS ERROR] Error reading keys from Redis: {e}")
        keys = []
        
    active_jobs = []
    for key in keys:
        try:
            key_str = key.decode("utf-8")
            job_id = key_str.split(":")[1]
            pid_bytes = r.get(key_str)
            pid = int(pid_bytes.decode("utf-8")) if pid_bytes else None
            active_jobs.append({"job_id": job_id, "pid": pid})
        except Exception as ex:
            print(f"[ACTIVE TASKS ERROR] Parsing key error: {ex}")
            
    r.close()
    
    if not active_jobs:
        return HTMLResponse("""
        <tr>
            <td colspan="6" class="px-6 py-6 text-center text-slate-500 text-sm font-medium">
                Tidak ada proses rendering video (FFmpeg) yang sedang berjalan saat ini.
            </td>
        </tr>
        """)
        
    # 2. Query details from database for these job_ids
    job_ids = [j["job_id"] for j in active_jobs]
    result = await db.execute(
        select(GenerationLog, User.email)
        .join(User, GenerationLog.user_id == User.id)
        .where(GenerationLog.job_id.in_(job_ids))
    )
    logs_data = result.all()
    
    # Map job details
    job_details = {log.job_id: (log, email) for log, email in logs_data}
    
    html = ""
    for job in active_jobs:
        job_id = job["job_id"]
        pid = job["pid"]
        
        if job_id in job_details:
            log, email = job_details[job_id]
            video_name = log.video_name or "Unknown Video"
            start_time = log.created_at.strftime("%H:%M:%S (%d %b)")
            status_str = log.status.upper()
        else:
            # Fallback if not found in db yet
            email = "Unknown User"
            video_name = "Unknown Video"
            start_time = "Unknown Start Time"
            status_str = "RUNNING"
            
        html += f"""
        <tr class="border-b border-slate-800/50 hover:bg-slate-900/30 transition" id="active-task-{job_id}">
            <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-300">#{job_id}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-indigo-400 font-semibold">{email}</td>
            <td class="px-6 py-4 text-sm text-slate-300 max-w-xs truncate font-medium" title="{video_name}">{video_name}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">{start_time}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-300">
                <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20 pulse">
                    PID: {pid or 'N/A'} ({status_str})
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm">
                <button 
                    hx-post="/api/v1/admin/active-tasks/{job_id}/kill"
                    hx-confirm="Apakah Anda yakin ingin MEMBUNUH (Kill) paksa proses FFmpeg #{job_id} ini?"
                    hx-target="#active-task-{job_id}"
                    hx-swap="outerHTML"
                    class="px-2.5 py-1.5 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20 font-semibold text-xs hover:bg-rose-500 hover:text-white transition duration-200"
                    title="Kill Process"
                >
                    ⚡ Kill Paksa
                </button>
            </td>
        </tr>
        """
    return HTMLResponse(html)


@router.post(
    "/active-tasks/{job_id}/kill",
    response_class=HTMLResponse,
    summary="Admin kills a running FFmpeg task",
)
async def admin_kill_task(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Admin forcibly terminates an active FFmpeg process and revokes its Celery task."""
    import redis
    import signal
    import os
    import json
    from core.celery_app import celery_app
    from core.pipeline import UPLOAD_DIR, TEMP_DIR, OUTPUT_DIR
    
    # 1. Fetch job log
    result = await db.execute(
        select(GenerationLog).where(GenerationLog.job_id == job_id)
    )
    log = result.scalar_one_or_none()
    
    # 2. Retrieve child FFmpeg PID from Redis and kill it
    r_sync = redis.from_url(settings.REDIS_URL)
    pid_key = f"task_pid:{job_id}"
    pid_bytes = r_sync.get(pid_key)
    
    if pid_bytes:
        try:
            pid = int(pid_bytes.decode('utf-8'))
            print(f"[ADMIN KILL] Killing FFmpeg subprocess PID {pid} for job {job_id}")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as ex:
            print(f"[ADMIN KILL ERROR] Error killing PID: {ex}")
        finally:
            r_sync.delete(pid_key)
            
    # 3. Publish error/cancel event to SSE Pub/Sub
    channel = f"task_progress:{job_id}"
    cancel_event = {
        "step": "error",
        "status": "error",
        "message": "Proses dihentikan paksa oleh Administrator."
    }
    try:
        r_sync.publish(channel, json.dumps(cancel_event, ensure_ascii=False))
    except Exception:
        pass
    r_sync.close()
    
    # 4. Revoke the Celery task
    try:
        celery_app.control.revoke(job_id, terminate=True, signal='SIGKILL')
        print(f"[ADMIN CANCEL] Revoked Celery task {job_id}")
    except Exception as ce:
        print(f"[ADMIN CANCEL ERROR] Error revoking task: {ce}")
        
    # 5. Update DB log status to failed
    if log:
        log.status = "failed"
        log.error_message = "Dihentikan paksa oleh Administrator."
        db.add(log)
        await db.commit()
        
    # 6. Clean up temporary files
    for directory in [UPLOAD_DIR, TEMP_DIR, OUTPUT_DIR]:
        for filepath in directory.glob(f"{job_id}*"):
            try:
                if filepath.exists():
                    filepath.unlink()
            except Exception:
                pass
                
    return HTMLResponse(f"""
    <tr class="opacity-40 bg-rose-500/5" id="active-task-{job_id}">
        <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-slate-500">#{job_id}</td>
        <td colspan="4" class="px-6 py-4 text-sm text-rose-400 font-semibold text-center">
            ⚠️ Terhenti Paksa (Killed by Admin)
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm">
            <span class="text-rose-400 font-bold">TERBUNUH</span>
        </td>
    </tr>
    """)
