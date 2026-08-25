"""Compatibility shim: implementation moved to modules/security.py."""
from modules.security import (  # noqa: F401
    DEFAULT_GUARDIAN_CONFIG,
    GuardianSystem,
    _TOKEN_PATTERN,
    guardian_extension_setup,
    guardian_extension_setup as setup,
)
