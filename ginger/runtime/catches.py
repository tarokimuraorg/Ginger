"""Event processing shared by current and future try statement evaluators."""

from collections.abc import Callable, Iterable
from typing import Any

from .context import RuntimeContext
from .failures import FailureStatus


def handle_try_events(context: RuntimeContext, event_ids: Iterable[int],
                      handlers: Iterable[tuple[str, Callable[[], Any]]]) -> None:
    """Process a fixed, occurrence-ordered snapshot, never handler-born events."""
    targets = tuple(event_ids)
    first_handlers = {}
    for name, handler in handlers:
        first_handlers.setdefault(name, handler)
    for event_id in targets:
        event = context.get_event(event_id)
        if event.status is not FailureStatus.UNRESOLVED:
            continue
        handler = first_handlers.get(event.failure_id.value)
        if handler is not None:
            context.run_handler(event_id, handler)
