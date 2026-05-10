"""Logic layer: pure business logic functions and handler strategies.

This package contains the core classification and handler logic that is
independent of Neo4j queries. All functions take pre-fetched data parameters
instead of database connections.
"""

from .graph_helpers import (
    format_method_fqn,
    is_internal_reference,
    member_display_name,
    sort_entries_by_location,
    sort_entries_by_priority,
)
from .handlers import (
    USED_BY_HANDLERS,
    EdgeContext,
    EntryBucket,
    ExtendsHandler,
    ImplementsHandler,
    InstantiationHandler,
    MethodCallHandler,
    ParamReturnHandler,
    PropertyAccessHandler,
    PropertyTypeHandler,
)
from .reference_types import (
    CHAINABLE_REFERENCE_TYPES,
    REF_TYPE_PRIORITY,
    infer_reference_type,
)

__all__ = [
    "CHAINABLE_REFERENCE_TYPES",
    "REF_TYPE_PRIORITY",
    "USED_BY_HANDLERS",
    "EdgeContext",
    "EntryBucket",
    "ExtendsHandler",
    "ImplementsHandler",
    "InstantiationHandler",
    "MethodCallHandler",
    "ParamReturnHandler",
    "PropertyAccessHandler",
    "PropertyTypeHandler",
    "format_method_fqn",
    "infer_reference_type",
    "is_internal_reference",
    "member_display_name",
    "sort_entries_by_location",
    "sort_entries_by_priority",
]
