"""Vulture allowlist: suppress false positives from framework patterns."""

# Vulture is a static analyzer; it cannot see code that is referenced
# dynamically (string-form type annotations, decorator registration,
# attribute access driven by a framework). Each name below is genuinely
# used at runtime but invisible to vulture's AST walk. Vulture unions
# usages across every scanned file, so referencing a name here marks the
# matching definition elsewhere as "used" and keeps it out of the report.
#
# Keep this list minimal. Only add a symbol after confirming it is a real
# false positive, never to silence genuinely dead code. The note above
# each entry explains why the symbol is reachable.

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    # telemetry.py imports Event under TYPE_CHECKING and uses it only in
    # string-form annotations on _before_send (`event: "Event"`,
    # `-> "Event | None"`). Vulture does not resolve forward-reference
    # strings, so it reports that import as unused at 90% confidence.
    from sentry_sdk._types import Event

    _event_used = Event  # mark the telemetry.py import as used
