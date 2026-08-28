"""Compatibility shim: implementation moved to modules/staff_management.py."""
from modules.staff_management import (  # noqa: F401
    StaffShiftSystem,
    _SlashChannelShim,
    _SlashMessageShim,
    staff_shifts_extension_setup,
)
