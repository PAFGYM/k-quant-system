"""Chat memory management - stores conversation history in SQLite.

Provides a thin wrapper around the SQLiteStore chat_history table
for the AI chat handler. Supports adding messages, retrieving recent
history, cleanup of old messages, and full history clearing.

Section 55 of K-Quant system architecture.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


class ChatMemory:
    """Manages conversation history for AI chat.

    Stores recent messages in the SQLite chat_history table via the
    provided db (SQLiteStore) instance. Auto-cleans messages older
    than 24 hours when cleanup() is called.

    Typical usage::

        memory = ChatMemory(db)
        memory.add("user", "에코프로 어떻게 보여?")
        memory.add("assistant", "주호님, 에코프로는 현재...")
        history = memory.get_recent(limit=10)
        memory.cleanup(hours=24)

    Attributes:
        db: SQLiteStore instance backing the chat history storage.
    """

    def __init__(self, db) -> None:
        """Initialize ChatMemory with a database connection.

        Args:
            db: SQLiteStore instance that implements add_chat_message(),
                get_recent_chat_messages(), cleanup_old_chat_messages(),
                and clear_chat_history() methods.
        """
        self.db = db

    def add(self, role: str, content: str) -> None:
        """Add a message to conversation history.

        Args:
            role: Message role, either 'user' or 'assistant'.
            content: Message text content.
        """
        if role not in ("user", "assistant"):
            logger.warning("Invalid chat role '%s'; expected 'user' or 'assistant'", role)
        self.db.add_chat_message(role, content)

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent messages, ordered oldest to newest.

        Args:
            limit: Maximum number of messages to return. Defaults to 10.

        Returns:
            List of dicts with keys: role, content, created_at.
            Ordered chronologically (oldest first, newest last).
        """
        return self.db.get_recent_chat_messages(limit=limit)

    def cleanup(self, hours: int = 24) -> int:
        """Delete messages older than the specified number of hours.

        This should be called periodically (e.g., daily) to prevent
        the chat_history table from growing unbounded.

        Args:
            hours: Age threshold in hours. Messages older than this
                   will be deleted. Defaults to 24.

        Returns:
            Number of messages deleted.
        """
        deleted = self.db.cleanup_old_chat_messages(hours=hours)
        if deleted > 0:
            logger.info("Cleaned up %d old chat messages (older than %dh)", deleted, hours)
        return deleted

    def clear(self) -> None:
        """Clear all chat history.

        Removes every message from the chat_history table. Use with
        caution; this is irreversible.
        """
        self.db.clear_chat_history()
        logger.info("Chat history cleared")

    def message_count(self) -> int:
        """Return total number of messages in history.

        Returns:
            Integer count of all stored chat messages.
        """
        messages = self.db.get_recent_chat_messages(limit=9999)
        return len(messages)
