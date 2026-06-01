"""
Integrasi auth terpusat agomart.

Customer app ini (video.agomart.com) tidak menyimpan password sendiri —
login diverifikasi ke API pusat agomart: email + password + akses tool 'video'.
Lihat endpoint pusat: POST {AGOMART_API_URL}/auth/app-login
  body : { email, password, tool }
  200  : { ok: true, user: { email, name, avatar } }
  401  : email/password salah
  403  : belum punya akses produk ini
"""

import httpx
from fastapi import HTTPException, status

from core.config import get_settings

settings = get_settings()


async def verify_with_agomart(email: str, password: str) -> dict:
    """Verifikasi kredensial ke agomart. Return data user agomart bila sukses,
    atau raise HTTPException sesuai balasan pusat."""
    url = f"{settings.AGOMART_API_URL.rstrip('/')}/auth/app-login"
    payload = {"email": email, "password": password, "tool": settings.AGOMART_TOOL_SLUG}

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=payload, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server autentikasi agomart sedang tidak dapat dihubungi. Coba beberapa saat lagi.",
        )

    if resp.status_code == 200:
        data = resp.json()
        return data.get("user") or {"email": email}

    if resp.status_code == 401:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Email atau password salah.")

    if resp.status_code == 403:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Anda belum memiliki akses ke produk ini. Silakan beli dulu di agomart.com.",
        )

    raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Gagal verifikasi ke server agomart.")
