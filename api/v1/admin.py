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

from api.deps import get_db
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
async def get_stats_cards(db: AsyncSession = Depends(get_db)):
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
async def get_logs_rows(db: AsyncSession = Depends(get_db)):
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

    # Standard edit and delete actions
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
        hx-delete="/api/v1/admin/users/{user.id}/delete"
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
        <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-400">{user.email}</td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-300">{user.quota_used} / {user.daily_quota}</td>
        <td class="px-6 py-4 whitespace-nowrap">{status_badge}</td>
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
            <div class="flex items-center gap-1.5">
                <input type="number" name="daily_quota" value="{user.daily_quota}" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-sm text-slate-200 w-16 focus:border-indigo-500 focus:outline-none">
                <span class="text-xs text-slate-500">video/hari</span>
            </div>
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            <select name="is_active" class="bg-slate-950 border border-slate-800 rounded-lg px-2 py-1 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none">
                <option value="1" {"selected" if user.is_active else ""}>AKTIF</option>
                <option value="0" {"selected" if not user.is_active else ""}>NONAKTIF</option>
            </select>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm">
            <div class="flex gap-1.5">
                <button 
                    hx-put="/api/v1/admin/users/{user.id}/update"
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
async def get_users_rows(db: AsyncSession = Depends(get_db)):
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
    db: AsyncSession = Depends(get_db)
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
        
    user = User(
        email=email,
        hashed_pw=hash_password(password),
        full_name=full_name,
        is_active=True,  # Admin-created users are active by default
        token_version=0,
        quota_reset=datetime.utcnow(),
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
async def get_user_row_endpoint(user_id: int, db: AsyncSession = Depends(get_db)):
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
async def get_user_edit_row_endpoint(user_id: int, db: AsyncSession = Depends(get_db)):
    """API endpoint to swap standard row into an edit row form."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return HTMLResponse(render_user_edit_row(user))


@router.put(
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
    db: AsyncSession = Depends(get_db)
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
    
    db.add(user)
    await db.commit()
    
    return HTMLResponse(render_user_row(user))


@router.delete(
    "/users/{user_id}/delete",
    response_class=HTMLResponse,
    summary="Permanently delete a user",
)
async def admin_delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """API endpoint to permanently delete a user from DB."""
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
async def admin_approve_user(user_id: int, db: AsyncSession = Depends(get_db)):
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
async def admin_deactivate_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """API endpoint to deactivate (block) a user."""
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
async def admin_force_logout(user_id: int, db: AsyncSession = Depends(get_db)):
    """API endpoint to execute force logout from the dashboard."""
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
