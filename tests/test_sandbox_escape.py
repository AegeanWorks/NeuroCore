"""Regression tests for M3: sandbox escape paths.

The sandbox previously passed through every ``modules.*`` import, so tool code could
``import modules.tools.sandbox`` and read that module's real ``os``/``sys`` globals. It also let
dunder traversal (``().__class__.__mro__[1].__subclasses__()``) reach the real interpreter.
Both are now refused; the one first-party module tools actually need still imports.
"""

import pytest

from modules.tools.sandbox import RestrictedImport, SecurityError, ToolSandbox


def test_bare_modules_package_is_not_passthrough():
    importer = RestrictedImport()
    with pytest.raises(ImportError):
        importer("modules")


def test_sandbox_module_cannot_be_imported():
    importer = RestrictedImport()
    with pytest.raises(ImportError):
        importer("modules.tools.sandbox")


def test_allowlisted_first_party_module_imports():
    importer = RestrictedImport()
    assert importer("modules.calendar.events") is not None


def test_calendar_passthrough_still_works_in_sandbox():
    sandbox = ToolSandbox()
    code = """
from modules.calendar.events import event_manager
result = event_manager is not None
"""
    assert sandbox.execute(code, {})["result"] is True


def test_importing_sandbox_at_runtime_is_refused():
    sandbox = ToolSandbox()
    with pytest.raises(Exception) as exc_info:
        sandbox.execute("import modules.tools.sandbox\nresult = 1", {})
    assert "not permitted" in str(exc_info.value)


def test_dunder_class_attribute_is_blocked_statically():
    sandbox = ToolSandbox()
    with pytest.raises(SecurityError) as exc_info:
        sandbox._check_code_safety("result = ().__class__.__mro__")
    assert "attribute" in str(exc_info.value)


def test_dunder_traversal_is_blocked_at_runtime():
    sandbox = ToolSandbox()
    with pytest.raises(SecurityError):
        sandbox.execute("result = ().__class__.__bases__", {})


def test_getattr_dunder_is_blocked():
    sandbox = ToolSandbox()
    with pytest.raises(SecurityError):
        sandbox._check_code_safety("getattr(object, '__globals__')")


def test_subscript_dunder_is_blocked():
    sandbox = ToolSandbox()
    with pytest.raises(SecurityError):
        sandbox._check_code_safety("x = vars(type)['__subclasses__']")


def test_double_underscore_name_attribute_still_allowed():
    """``__name__`` is not an escape; tools rely on it (e.g. type(x).__name__)."""
    sandbox = ToolSandbox()
    sandbox._check_code_safety("result = type(object).__name__")  # must not raise