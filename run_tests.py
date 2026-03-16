#!/usr/bin/env python
"""Run pytest with ROS system plugins filtered out.

The system has ROS Jazzy installed which registers pytest plugins via
system-level entry points. These plugins fail to load (missing `lark`)
and register unknown hooks. This runner patches importlib.metadata to
filter them out before pytest discovers them.

Usage: .venv/bin/python run_tests.py [pytest args...]
"""
import importlib.metadata
import sys

_BLOCKED_PLUGINS = frozenset({
    "launch_testing", "launch_testing_ros",
    "ament_lint", "ament_copyright", "ament_flake8",
    "ament_pep257", "ament_xmllint",
})

_orig_eps = importlib.metadata.entry_points


def _filtered_eps(**kwargs):
    result = _orig_eps(**kwargs)
    group = kwargs.get("group", "")
    if group == "pytest11":
        return [ep for ep in result if ep.name not in _BLOCKED_PLUGINS]
    return result


importlib.metadata.entry_points = _filtered_eps

# Also patch SelectableGroups.select for older API usage
try:
    from importlib.metadata import SelectableGroups
    _orig_select = SelectableGroups.select

    def _patched_select(self, **kwargs):
        result = _orig_select(self, **kwargs)
        if kwargs.get("group") == "pytest11":
            return [ep for ep in result if ep.name not in _BLOCKED_PLUGINS]
        return result

    SelectableGroups.select = _patched_select
except (ImportError, AttributeError):
    pass

# Patch pluggy's load_setuptools_entrypoints to filter before load
import pluggy._manager as _pm
_orig_load = _pm.PluginManager.load_setuptools_entrypoints


def _filtered_load(self, group, name=None):
    # Temporarily filter entry points
    for ep in importlib.metadata.entry_points().get(group, []) if isinstance(importlib.metadata.entry_points(), dict) else importlib.metadata.entry_points(group=group):
        if ep.name in _BLOCKED_PLUGINS:
            continue
        if name is not None and ep.name != name:
            continue
        if self.get_plugin(ep.name) or self.is_blocked(ep.name):
            continue
        try:
            plugin = ep.load()
        except Exception:
            continue
        self.register(plugin, name=ep.name)
    return len(self.list_plugin_distinfo())


_pm.PluginManager.load_setuptools_entrypoints = _filtered_load

if __name__ == "__main__":
    import pytest
    args = sys.argv[1:] if len(sys.argv) > 1 else ["tests/", "-v", "--tb=short"]
    sys.exit(pytest.main(args))
