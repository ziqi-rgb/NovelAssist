"""数据模型定义 — 世界观核心实体"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class EventType(enum.Enum):
    MAIN = "MAIN"
    WORLD = "WORLD"
    RANDOM = "RANDOM"


class LocationScale(enum.Enum):
    REGION = "REGION"
    GRID_25 = "GRID_25"


class Outline(Base):
    __tablename__ = "outlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outlines.id"), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    parent: Mapped[Optional[Outline]] = relationship(
        back_populates="children", remote_side="Outline.id"
    )
    children: Mapped[list[Outline]] = relationship(back_populates="parent")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="outline")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outline_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outlines.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    outline: Mapped[Optional[Outline]] = relationship(back_populates="chapters")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    aliases: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="存活")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    scale: Mapped[LocationScale] = mapped_column(
        SAEnum(LocationScale), nullable=False, default=LocationScale.REGION
    )
    grid_x: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_y: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parent: Mapped[Optional[Location]] = relationship(
        back_populates="children", remote_side="Location.id"
    )
    children: Mapped[list[Location]] = relationship(back_populates="parent")
    events: Mapped[list[WorldEvent]] = relationship(back_populates="location")


class WorldEvent(Base):
    __tablename__ = "world_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_type: Mapped[EventType] = mapped_column(
        SAEnum(EventType), nullable=False, default=EventType.MAIN
    )
    timeline_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)

    location: Mapped[Optional[Location]] = relationship(back_populates="events")
