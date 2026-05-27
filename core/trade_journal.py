"""
Trade Journal module - re-exports from core.journal for backward compatibility
"""

from core.journal import TradeJournalDB as TradeJournal

__all__ = ["TradeJournal"]