"""Pure-Python validation engine. No Django, no database, no model calls.

Inputs are plain dataclasses (``types.py``); output is a list of ``Issue``. The service
layer (``services/validation.py``) adapts database rows to these inputs and stores issues.
"""

from .engine import derive_statuses, validate
from .types import FieldInput, Issue, LineInput, RuleConfig

__all__ = ["FieldInput", "Issue", "LineInput", "RuleConfig", "derive_statuses", "validate"]
