"""pytest helpers for wand-backed test suites.

Kept with the shim so any project that replays through wand can reuse them.
Importing this module requires ``pytest``.
"""

import functools

import pytest

# Works both as a loose sibling module and when dropped into a package.
try:
    from client import WandFixtureMiss
except ImportError:  # pragma: no cover - depends on import context
    from .client import WandFixtureMiss


def skip_on_fixture_miss(func):
    """Skip a test (instead of failing) when its fixture hasn't been captured.

    In ci mode a call with no recorded fixture raises :class:`WandFixtureMiss`.
    Before you've captured, that's an expected "not recorded yet" state, not a
    failure — so we skip. In capture mode calls go live and never miss, so the
    test runs normally and records the fixture.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except WandFixtureMiss as miss:
            pytest.skip(f"fixture not captured yet — capture to record it ({miss})")

    return wrapper
