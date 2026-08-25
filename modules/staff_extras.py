"""Compatibility shim: implementation moved to modules/staff_management.py."""
from modules.staff_management import (  # noqa: F401
    StaffExtras,
    StaffExtrasCommands,
    staff_extras_extension_setup as setup,
    staff_extras_extension_setup,
)
