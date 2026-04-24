"""Database models for multi-channel faceless video automation."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, enum.Enum):
    youtube = "youtube"
    tiktok = "tiktok"
    instagram = "instagram"


class JobStatus(str, enum.Enum):
    pending = "pending"
    scripting = "scripting"
    voicing = "voicing"
    fetching_media = "fetching_media"
    rendering = "rendering"
    uploading = "uploading"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Niche: content strategy for a family of channels
# ---------------------------------------------------------------------------
class Niche(Base):
    __tablename__ = "niches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    tone: Mapped[str] = mapped_column(String(64), default="neutral")
    emotions: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    prompt_additions: Mapped[str] = mapped_column(
        Text, default="",
        doc="Extra instructions appended to LLM prompts for this niche.",
    )
    language: Mapped[str] = mapped_column(String(8), default="en")
    video_length_seconds: Mapped[int] = mapped_column(Integer, default=25)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    channels: Mapped[list["Channel"]] = relationship(back_populates="niche")


# ---------------------------------------------------------------------------
# Channel: one social account on one platform
# ---------------------------------------------------------------------------
class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128),
                                      doc="Friendly name, e.g. 'Deep Facts NL'.")
    platform: Mapped[Platform] = mapped_column(String(16))
    account_handle: Mapped[str] = mapped_column(String(128), default="")
    niche_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("niches.id", ondelete="SET NULL"), nullable=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Style profile — JSON blob so we can evolve without migrations.
    style: Mapped[dict] = mapped_column(JSON, default=dict,
        doc="Keys: voice_id, voice_style, primary_color, accent_color, "
            "font_family, music_mood, caption_style.",
    )
    # Upload preferences (privacy, categories, tags template, etc.)
    upload_defaults: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    niche: Mapped[Optional[Niche]] = relationship(back_populates="channels")
    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan")
    tokens: Mapped[list["OAuthToken"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="channel")


# ---------------------------------------------------------------------------
# Schedule: when and how often to publish to a channel
# ---------------------------------------------------------------------------
class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"))
    # Cron expression in 5-field form; OR a simple "times_per_day" hint.
    cron: Mapped[str] = mapped_column(String(64), default="0 9,15,20 * * *",
        doc="Cron expression, timezone from server.",
    )
    videos_per_slot: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime,
                                                            nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="schedules")


# ---------------------------------------------------------------------------
# OAuthToken: platform credentials per channel
# ---------------------------------------------------------------------------
class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    __table_args__ = (
        UniqueConstraint("channel_id", "platform", name="uq_token_channel_platform"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"))
    platform: Mapped[Platform] = mapped_column(String(16))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime,
                                                           nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")
    account_id: Mapped[str] = mapped_column(String(128), default="",
        doc="Platform-assigned ID for the connected account (e.g. YT channel ID).")
    account_name: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow,
                                                 onupdate=_utcnow)

    channel: Mapped[Channel] = relationship(back_populates="tokens")


# ---------------------------------------------------------------------------
# Job: a single video production run, tracked through the pipeline
# ---------------------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"))
    niche_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("niches.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[JobStatus] = mapped_column(String(24), default=JobStatus.pending)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status_detail: Mapped[str] = mapped_column(Text, default="")

    idea: Mapped[dict] = mapped_column(JSON, default=dict)
    script: Mapped[dict] = mapped_column(JSON, default=dict)
    caption: Mapped[dict] = mapped_column(JSON, default=dict)

    voice_path: Mapped[str] = mapped_column(Text, default="")
    voice_duration: Mapped[float] = mapped_column(Float, default=0.0)
    media_urls: Mapped[list] = mapped_column(JSON, default=list)

    render_id: Mapped[str] = mapped_column(String(128), default="")
    render_url: Mapped[str] = mapped_column(Text, default="")
    video_path: Mapped[str] = mapped_column(Text, default="")

    platform_video_id: Mapped[str] = mapped_column(String(128), default="")
    platform_video_url: Mapped[str] = mapped_column(Text, default="")

    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow,
                                                 onupdate=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime,
                                                            nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="jobs")
    niche: Mapped[Optional[Niche]] = relationship()
