"""Activate the wand boto3 bridge for the whole test session.

Scaffolded integration tests call boto3 directly and know nothing about wand —
"mocking is handled elsewhere". This is that elsewhere: it puts the tool-managed
shim on sys.path and enters boto3_shim.intercept() once for the session, so every
botocore call routes through wand.

The shim reads WAND_MODE itself (default ci):
  * ci           — replay committed fixtures; a miss raises WandFixtureMiss.
  * capture      — call real AWS and write the fixture pair.
  * passthrough  — call real AWS, write nothing.

Tool-managed by `wand init`, but safe to edit — init won't clobber an existing
conftest.py. If your project already has one, copy the _wand_intercept fixture
and the sys.path setup into it.
"""

import pathlib
import sys

import pytest

_SHIM_DIR = pathlib.Path(__file__).parent / ".wand" / "shims" / "python"

if _SHIM_DIR.is_dir() and str(_SHIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SHIM_DIR))

try:
    import boto3_shim
except ImportError:  # shim not installed — run tests without wand.
    boto3_shim = None


@pytest.fixture(scope="session", autouse=True)
def _wand_intercept():
    """Patch botocore for the session so capture/replay covers every test.

    Session-scoped and autouse so the patch is in place before any
    ``setUpClass`` fires the describe_* calls the tests assert on.
    """
    if boto3_shim is None:
        yield
        return
    with boto3_shim.intercept():
        yield
