"""Logic layer: pure business logic functions and handler strategies.

This package contains the core classification and handler logic that is
independent of Neo4j queries. All functions take pre-fetched data parameters
instead of database connections.
"""

from .reference_types import (
    CHAINABLE_REFERENCE_TYPES,
    REF_TYPE_PRIORITY,
    infer_reference_type,
)
from .graph_helpers import (
    member_display_name,
    sort_entries_by_priority,
    sort_entries_by_location,
    format_method_fqn,
    is_internal_reference,
)
from .handlers import (
    EdgeContext,
    EntryBucket,
    USED_BY_HANDLERS,
    InstantiationHandler,
    ExtendsHandler,
    ImplementsHandler,
    PropertyTypeHandler,
    MethodCallHandler,
    PropertyAccessHandler,
    ParamReturnHandler,
)

__all__ = [
    "CHAINABLE_REFERENCE_TYPES",
    "REF_TYPE_PRIORITY",
    "infer_reference_type",
    "member_display_name",
    "sort_entries_by_priority",
    "sort_entries_by_location",
    "format_method_fqn",
    "is_internal_reference",
    "EdgeContext",
    "EntryBucket",
    "USED_BY_HANDLERS",
    "InstantiationHandler",
    "ExtendsHandler",
    "ImplementsHandler",
    "PropertyTypeHandler",
    "MethodCallHandler",
    "PropertyAccessHandler",
    "ParamReturnHandler",
]
