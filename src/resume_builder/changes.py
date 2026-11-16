"""Change notifications: how services tell input adapters that stored data changed.

Services never write user files. After a write they call `space.changed(kind, key, ...)`, and every
`ChangeListener` on the App gets a `Change`. Locally the YAML/Markdown adapter is the listener and
writes the matching working copy back (R7.12). A host without user files passes
`App(change_listeners=[])`, or its own listeners (for example to notify a web client).

Kinds: `settings`, `library`, `registry`, `profile` (key: `public` or a job id), `application`
(key: job id), `job_created` (key: job id), `posting` (key: job id), `document` (key: job id, with
`doc_kind` and `slug`).

`hold_changes()` holds notifications instead of sending them, for a caller that runs operations
inside its own transaction (the sync adapter): it sends them once that transaction commits, and
drops them on a rollback or dry run, so no listener hears of a change that never happened.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Change:
    kind: str
    key: str | None = None
    doc_kind: str | None = None
    slug: str = ""


class ChangeListener(Protocol):
    def __call__(self, space: Any, change: Change) -> bool:
        """React to a change. Return True if it updated a copy the user can see (a file, a page)."""
        ...


_held: ContextVar[list | None] = ContextVar("rb_held_changes", default=None)


@contextlib.contextmanager
def hold_changes():
    """Collect `(space, Change)` pairs instead of notifying listeners, until the block ends.

    Yields the list. The caller decides which held changes to send (`space.notify(change)`).
    """
    token = _held.set([])
    try:
        yield _held.get()
    finally:
        _held.reset(token)


def held() -> list | None:
    """The list changes are being held in, or None when they go straight to the listeners."""
    return _held.get()
