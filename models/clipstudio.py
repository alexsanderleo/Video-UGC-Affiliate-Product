"""
Clip Studio (Auto Klip VIP) models — Opus Clip style pipeline.

Tables (sesuai spesifikasi menubaru.md bagian 4):
- clip_projects : satu video sumber (YouTube / upload) + status pipeline (jobs digabung di sini:
                  kolom stage/percent/error_message — Tahap F).
- clip_transcripts : transkrip word-level JSON dari faster-whisper.
- clips : hasil potongan AI (start/end/title/score/...) + edit_state editor.
- clip_exports : riwayat render final per klip.

Kolom JSON memakai sqlalchemy JSON (portable: TEXT di SQLite, JSON di MySQL).
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from models.base import Base, TimestampMixin


class ClipProject(TimestampMixin, Base):
    """Satu proses 'Get clips': sumber video + status pipeline keseluruhan."""

    __tablename__ = "clip_projects"

    id = Column(String(36), primary_key=True)  # uuid4
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    source_url = Column(Text, nullable=True)
    source_type = Column(String(20), default="youtube", nullable=False)  # youtube | upload
    title = Column(String(500), nullable=True)
    duration = Column(Float, default=0.0, nullable=False)
    fps = Column(Float, default=30.0, nullable=False)
    width = Column(Integer, default=0, nullable=False)
    height = Column(Integer, default=0, nullable=False)

    # Opsi user sebelum proses (bagian 2 alur user)
    options = Column(JSON, nullable=True)
    # {clip_length: "auto"|"<30"|"30-60"|"60-90", range_start, range_end, language: "auto"|"id"|"en",
    #  max_clips, aspect_ratio: "9:16"|"1:1"|"16:9", caption_template}

    # Status pipeline (Tahap F — tabel jobs digabung ke project)
    status = Column(String(30), default="queued", nullable=False)
    # queued | downloading | transcribing | analyzing | reframing | rendering | done | error
    percent = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    credits_used = Column(Integer, default=0, nullable=False)  # 1 menit sumber = 1 credit

    user = relationship("User", backref="clip_projects")
    clips = relationship("Clip", back_populates="project", cascade="all, delete-orphan")
    transcript = relationship("ClipTranscript", back_populates="project", uselist=False,
                              cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ClipProject id={self.id} status={self.status} {self.percent}%>"


class ClipTranscript(TimestampMixin, Base):
    """Transkrip word-level (Tahap B). words = [{word,start,end,conf,is_filler}, ...]."""

    __tablename__ = "clip_transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(36), ForeignKey("clip_projects.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    language = Column(String(10), default="id", nullable=False)
    words = Column(JSON, nullable=True)

    project = relationship("ClipProject", back_populates="transcript")


class Clip(TimestampMixin, Base):
    """Satu klip hasil AI curation + seluruh state editor."""

    __tablename__ = "clips"

    id = Column(String(36), primary_key=True)  # uuid4
    project_id = Column(String(36), ForeignKey("clip_projects.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    start = Column(Float, default=0.0, nullable=False)
    end = Column(Float, default=0.0, nullable=False)
    title = Column(String(500), nullable=True)
    score = Column(Integer, default=0, nullable=False)        # virality score 0-100
    score_breakdown = Column(JSON, nullable=True)             # {hook, flow, value, trend} ala Opus
    reason = Column(Text, nullable=True)                       # alasan singkat skor
    hashtags = Column(JSON, nullable=True)                     # ["#a", "#b", "#c"]

    aspect_ratio = Column(String(10), default="9:16", nullable=False)  # 9:16 | 1:1 | 16:9
    layout_mode = Column(String(10), default="fill", nullable=False)   # fill | fit | split
    tracker_on = Column(Boolean, default=True, nullable=False)
    crop_keyframes = Column(JSON, nullable=True)               # [{t, cx, cy}] relatif ke video sumber
    caption_style = Column(JSON, nullable=True)                # template + override user
    edit_state = Column(JSON, nullable=True)
    # {cut_ranges:[[s,e]], word_edits:{idx:"teks"}, deleted_words:[idx], overlays:[...],
    #  texts:[...], broll:[...], music:{...}, transitions:[...], caption_pos_pct, extend:{start,end}}

    status = Column(String(30), default="ready", nullable=False)  # processing | ready | error
    thumbnail = Column(String(500), nullable=True)             # path relatif storage
    sprite = Column(String(500), nullable=True)                # strip thumbnail timeline

    project = relationship("ClipProject", back_populates="clips")
    exports = relationship("ClipExport", back_populates="clip", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Clip id={self.id} {self.start:.1f}-{self.end:.1f}s score={self.score}>"


class ClipExport(TimestampMixin, Base):
    """Riwayat export final per klip."""

    __tablename__ = "clip_exports"

    id = Column(String(36), primary_key=True)  # uuid4
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)

    resolution = Column(String(10), default="1080p", nullable=False)  # 720p | 1080p
    watermark = Column(Boolean, default=False, nullable=False)
    file_path = Column(String(500), nullable=True)             # path relatif storage
    file_size = Column(BigInteger, default=0, nullable=False)

    status = Column(String(30), default="queued", nullable=False)  # queued|rendering|done|error
    percent = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    clip = relationship("Clip", back_populates="exports")
