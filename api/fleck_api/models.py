from datetime import datetime
from typing import Any

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, false, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB, datetime: DateTime(timezone=True)}


def _id() -> Mapped[int]:
    return mapped_column(Integer, primary_key=True)


def _fk(table: str, nullable: bool = False):
    return mapped_column(Integer, ForeignKey(f"{table}.id", ondelete="CASCADE" if not nullable else "SET NULL"), nullable=nullable)


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = _id()
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str | None]
    created_at: Mapped[datetime] = _now()


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users")
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = _now()


class GoogleToken(Base):
    __tablename__ = "google_tokens"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    token_enc: Mapped[str] = mapped_column(Text)  # Fernet(FLECK_SECRET_KEY) of the token JSON
    updated_at: Mapped[datetime] = _now()


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users")
    name: Mapped[str | None]
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    last_seen: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users")
    code: Mapped[str] = mapped_column(String, unique=True)
    expires_at: Mapped[datetime]  # created + 10 min
    used_at: Mapped[datetime | None]


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = _id()
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(String, unique=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = _id()
    client_id: Mapped[int | None] = _fk("clients", True)
    kind: Mapped[str]  # client|firm|reference
    title: Mapped[str]
    filename: Mapped[str]
    blob_key: Mapped[str]
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|indexing|indexed|failed
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None]  # sha256 of file + client name; the seed re-indexes when it changes
    source_path: Mapped[str | None]  # path under clients/ for seed-created documents
    created_at: Mapped[datetime] = _now()


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "google_event_id"),)
    id: Mapped[int] = _id()
    user_id: Mapped[int] = _fk("users")
    google_event_id: Mapped[str]
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    attendees: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    meeting_link: Mapped[str | None]
    client_id: Mapped[int | None] = _fk("clients", True)
    recurring_event_id: Mapped[str | None]  # Google's series id for instances of a recurring event
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # gone from Google but kept


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[int] = _id()
    event_id: Mapped[int | None] = _fk("calendar_events", True)
    title: Mapped[str]
    client_id: Mapped[int | None] = _fk("clients", True)
    recorded_by: Mapped[int | None] = _fk("users", True)
    status: Mapped[str] = mapped_column(String, default="recording")  # recording|processing|ready|failed
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None]
    recording_key: Mapped[str | None]


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("meeting_id", "segment_id"),)
    id: Mapped[int] = _id()
    meeting_id: Mapped[int] = _fk("meetings")
    segment_id: Mapped[str]
    stream: Mapped[str]  # you|client
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    gate: Mapped[str | None]


class AudioChunk(Base):
    __tablename__ = "audio_chunks"
    __table_args__ = (UniqueConstraint("meeting_id", "seq"),)
    id: Mapped[int] = _id()
    meeting_id: Mapped[int] = _fk("meetings")
    seq: Mapped[int] = mapped_column(Integer)
    blob_key: Mapped[str]


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = _id()
    meeting_id: Mapped[int] = mapped_column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), unique=True)
    text: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[int | None] = _fk("users", True)
    updated_at: Mapped[datetime] = _now()


class Assist(Base):
    __tablename__ = "assists"
    id: Mapped[int] = _id()
    meeting_id: Mapped[int] = _fk("meetings")
    assist_id: Mapped[str] = mapped_column(String, unique=True)
    segment_id: Mapped[str | None]
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str | None]  # library|reference|general|not_in_material
    from_brief: Mapped[bool] = mapped_column(Boolean, default=False)
    late: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # found by the late recheck
    citations: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    first_words_ms: Mapped[int | None] = mapped_column(Integer)
    full_ms: Mapped[int | None] = mapped_column(Integer)
    rejected: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    used: Mapped[bool | None]
    used_by: Mapped[str | None]  # auto|manual
    used_score: Mapped[float | None] = mapped_column(Float)


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (UniqueConstraint("event_id"),)
    id: Mapped[int] = _id()
    event_id: Mapped[int] = _fk("calendar_events")
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|building|ready|failed
    context: Mapped[str | None] = mapped_column(Text)  # "What we know going in", built locally
    doc_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()  # the page's "Prepared HH:MM"


class BriefItem(Base):
    __tablename__ = "brief_items"
    id: Mapped[int] = _id()
    brief_id: Mapped[int] = _fk("briefs")
    topic: Mapped[str]  # commercial|proof|delivery|risk|technical
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    kind: Mapped[str]
    citations: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    question_vector: Mapped[list[float] | None] = mapped_column(ARRAY(Float))


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (UniqueConstraint("meeting_id", "version"),)
    id: Mapped[int] = _id()
    meeting_id: Mapped[int] = _fk("meetings")
    version: Mapped[int] = mapped_column(Integer, default=1)
    items: Mapped[list[Any]] = mapped_column(JSONB, default=list)


class ChatThread(Base):
    __tablename__ = "chat_threads"
    id: Mapped[int] = _id()
    meeting_id: Mapped[int | None] = _fk("meetings", True)  # null = Ask-all thread
    created_at: Mapped[datetime] = _now()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = _id()
    thread_id: Mapped[int] = _fk("chat_threads")
    role: Mapped[str]
    text: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _now()


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = _id()
    purpose: Mapped[str]
    model: Mapped[str]
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    meeting_id: Mapped[int | None] = _fk("meetings", True)
    event_id: Mapped[int | None] = _fk("calendar_events", True)  # a brief's calls belong to its event
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _now()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = _now()
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None]  # set on claim; a stale one means the worker died
    created_at: Mapped[datetime] = _now()
