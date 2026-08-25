"""Compatibility shim: implementation moved to modules/staff_management.py."""
from modules.staff_management import (  # noqa: F401
    ReviewModal,
    ReviewSelectionView,
    StaffReviewSystem,
    staff_reviews_extension_setup as setup,
    staff_reviews_extension_setup,
)
