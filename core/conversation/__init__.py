"""Conversation mode (v3) — continuous-listen, no-wakeword voice subsystem
("true speech mode"). System-level mode: manual activation, wakeword off for its
duration, fail-safe handoff so it can never break the wakeword pipeline.

  ConversationEngine  pure-logic turn-state machine
  ConversationDriver  bridges the engine to the proven STT/LLM/TTS pipeline
  SpeechGate          silero per-frame speech/silence decision
  LocalMicSource      local mic capture front-door (headphone tier)
  ConversationManager ties it all to the fail-safe handoff
"""
from .engine import ConversationEngine, IDLE, USER_SPEAKING, RESPONDING
from .driver import ConversationDriver
from .manager import ConversationManager

__all__ = [
    "ConversationEngine", "ConversationDriver", "ConversationManager",
    "IDLE", "USER_SPEAKING", "RESPONDING",
]
