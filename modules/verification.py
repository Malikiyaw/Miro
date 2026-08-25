"""Compatibility shim: implementation moved to modules/member_management.py."""
from modules.member_management import (  # noqa: F401
    CaptchaModal,
    SetAccountAgeModal,
    SetUnverifiedRoleModal,
    SetVerifiedRoleModal,
    SetVerifyChannelModal,
    Verification,
    VerificationConfigPanel,
    VerificationSystem,
    VerificationView,
    VerifyView,
)
