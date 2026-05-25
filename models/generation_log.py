"""
GenerationLog model — core table for tracking video generation tasks.
Includes tracking metrics like bandwidth, duration, and task execution status.
"""

from sqlalchemy import Column, Integer, String, Float, BigInteger, Text, ForeignKey
from sqlalchemy.orm import relationship

from models.base import Base, TimestampMixin


class GenerationLog(TimestampMixin, Base):
    """
    GenerationLogs table.
    
    Used to track Celery rendering tasks:
    - job_id: Unique identifier for the generation job.
    - status: pending, processing, success, failed.
    - bandwidth_bytes: Size of the rendered video file.
    - duration: Duration of the rendered video.
    - error_message: Text describing any failure.
    """

    __tablename__ = "generation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    job_id = Column(String(50), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Task state & settings
    status = Column(String(50), default="pending", nullable=False)
    voice = Column(String(50), nullable=True)
    watermark_mode = Column(String(50), nullable=True)
    watermark_text = Column(String(255), nullable=True)
    
    # Task metrics
    video_name = Column(String(255), nullable=True)
    duration = Column(Float, default=0.0, nullable=False)
    ingress_bytes = Column(BigInteger, default=0, nullable=False)
    bandwidth_bytes = Column(BigInteger, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", backref="generation_logs")

    def __repr__(self) -> str:
        return f"<GenerationLog id={self.id} job_id={self.job_id} status={self.status}>"
