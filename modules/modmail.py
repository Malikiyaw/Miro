"""Compatibility shim: implementation moved to modules/communications.py."""
from modules.communications import (  # noqa: F401
    ModmailFileModal,
    ModmailReplyModal,
    ModmailSystem,
    ModmailThreadView,
    NoteModal,
    modmail_extension_setup,
    modmail_extension_setup as setup,
)
