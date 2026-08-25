"""Compatibility shim: implementation moved to modules/security.py."""
from modules.security import (  # noqa: F401
    AppealPersistentView,
    AppealReviewView,
    AppealSystem,
    ApproveModal,
    BanAppealModal,
    DenyModal,
    RequestInfoModal,
    appeals_extension_setup,
    appeals_extension_setup as setup,
)
