"""
API v1 main router — aggregates all sub-routers.
"""

from fastapi import APIRouter

from api.v1.auth import router as auth_router
from api.v1.generate import router as generate_router
from api.v1.admin import router as admin_router
from api.v1.convert import router as convert_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(generate_router)
router.include_router(admin_router)
router.include_router(convert_router)

