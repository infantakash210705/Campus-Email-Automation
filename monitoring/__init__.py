"""Scheduled website monitoring and change detection."""
from .changes import (
    OUTCOMES,
    classify_snapshot,
    diff_contacts,
    normalize_email,
)

__all__ = ["OUTCOMES", "classify_snapshot", "diff_contacts", "normalize_email"]
