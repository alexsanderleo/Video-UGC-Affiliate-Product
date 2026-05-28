import os
import sys
import asyncio
from pathlib import Path
from sqlalchemy import select

BASE_DIR = Path("/www/wwwroot/video.agomart.com")
sys.path.insert(0, str(BASE_DIR))

from core.config import get_settings
from core.database import async_session
from models.generation_log import GenerationLog

async def main():
    print("=" * 60)
    print(" FETCHING RECENT GENERATION LOGS FROM DATABASE")
    print("=" * 60)
    
    async with async_session() as session:
        # Get last 5 logs
        result = await session.execute(
            select(GenerationLog).order_by(GenerationLog.created_at.desc()).limit(5)
        )
        logs = result.scalars().all()
        
        for idx, log in enumerate(logs):
            print(f"\n[{idx+1}] Job ID: {log.job_id} | Status: {log.status} | Created: {log.created_at}")
            print(f"    User ID: {log.user_id} | Video: {log.video_name}")
            print(f"    Duration: {log.duration}s | Bandwidth: {log.bandwidth_bytes} bytes")
            print(f"    Error: {log.error_message}")
            
asyncio.run(main())
