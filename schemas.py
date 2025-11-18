"""
Database Schemas for Telegram 2.0

Each Pydantic model below represents a MongoDB collection. The collection
name is the lowercase of the class name (e.g., User -> "user").
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

# Core user/auth
class User(BaseModel):
    phone: str = Field(..., description="E.164 formatted phone number")
    name: str = Field("", description="Display name")
    username: Optional[str] = Field(None, description="Unique handle")
    avatar_url: Optional[str] = None
    status: str = Field("", description="Status line")
    privacy_mode: bool = Field(False)
    creator_mode: bool = Field(False)
    notifications_enabled: bool = Field(True)

class Session(BaseModel):
    phone: str
    otp_code: str
    verified: bool = False
    user_id: Optional[str] = None

# Chats & messaging
class Chat(BaseModel):
    type: Literal["personal", "group", "channel"]
    title: str
    participants: List[str] = Field(default_factory=list)
    last_message_at: Optional[datetime] = None
    pinned: bool = False

class Message(BaseModel):
    chat_id: str
    sender_id: str
    text: str = ""
    attachments: List[Dict[str, Any]] = Field(default_factory=list)  # {type: image|video|file|voice, url}
    thread_root_id: Optional[str] = None
    reactions: Dict[str, List[str]] = Field(default_factory=dict)  # emoji -> [user_id]

# Channels & content
class Channel(BaseModel):
    title: str
    description: str = ""
    owner_id: str
    members: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    trending_score: float = 0.0

class Post(BaseModel):
    channel_id: str
    author_id: str
    content_text: str = ""
    media: List[Dict[str, Any]] = Field(default_factory=list)
    scheduled_at: Optional[datetime] = None

class Story(BaseModel):
    author_id: str
    background: str = "#FAF9F7"
    text: str = ""

# Automation
class AutomationFlow(BaseModel):
    owner_id: str
    name: str
    nodes: List[Dict[str, Any]]  # [{id, type, x, y, config}]
    edges: List[Dict[str, Any]]  # [{from, to}]

# Analytics
class AnalyticsEvent(BaseModel):
    user_id: str
    event: str
    meta: Dict[str, Any] = Field(default_factory=dict)
