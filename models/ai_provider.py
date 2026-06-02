"""
AIProvider & AppSetting models — konfigurasi provider AI untuk tahap analisis video.

Tiap baris `ai_providers` = satu "profil" yang bisa dipilih (mode tunggal) atau
diputar (mode rotasi) dari panel admin, sehingga provider/model bisa diganti
tanpa edit kode/redeploy. `app_settings` = penyimpanan key-value generik untuk
toggle global (mis. rotasi on/off).
"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from models.base import Base, TimestampMixin


class AIProvider(TimestampMixin, Base):
    """
    Profil provider AI untuk step_a_video_understanding.

    adapter:
      - "openai_video"  -> SDK OpenAI-compatible, kirim video langsung (Qwen). [aktif]
      - "openai_frames" -> OpenAI-compatible, kirim frame gambar (Gemini/Groq). [nanti]
      - "gemini_native" -> native google-genai, video penuh. [nanti]

    is_active : provider terpilih saat MODE TUNGGAL (dijaga hanya satu True).
    is_enabled: boleh dipilih / ikut rotasi.
    """

    __tablename__ = "ai_providers"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    label = Column(String(100), nullable=False)
    adapter = Column(String(30), nullable=False, default="openai_video")
    base_url = Column(String(255), nullable=False)
    model = Column(String(100), nullable=False)
    api_key = Column(Text, nullable=False)
    input_type = Column(String(20), nullable=False, default="video")

    is_enabled = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<AIProvider id={self.id} label={self.label!r} model={self.model!r} active={self.is_active}>"


class AppSetting(Base):
    """Key-value setting global aplikasi (mis. ai_rotation_enabled, ai_rotation_cursor)."""

    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<AppSetting {self.key}={self.value!r}>"
