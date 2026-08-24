"""Durable world-model entity placeholders for Phase 01."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Account:
    name: str
    bot_name: str = ''
    bot_id: str = ''


@dataclass
class Guild:
    guild_id: str
    name: str = ''


@dataclass
class Channel:
    channel_id: str
    guild_id: str = ''
    name: str = ''


@dataclass
class User:
    user_id: str
    username: str = ''
    display_name: str = ''


@dataclass
class Message:
    message_id: str
    channel_id: str
    author_id: str
    content: str = ''


@dataclass
class Observation:
    observation_type: str
    channel_id: str = ''
    payload: dict = field(default_factory=dict)


@dataclass
class Task:
    task_type: str
    target_id: str = ''
    reason: str = ''


@dataclass
class PresenceState:
    account_name: str
    status: str = 'online'
    activity: str = ''


@dataclass
class RelationshipState:
    fondness: float = 0.5
    trust: float = 0.5
    patience: float = 0.5
    respect: float = 0.5
    interest: float = 0.5
    familiarity: float = 0.0


@dataclass
class AgentAffect:
    energy: float = 0.7
    sociability: float = 0.6
    irritability: float = 0.2
    playfulness: float = 0.5
    stress: float = 0.2
