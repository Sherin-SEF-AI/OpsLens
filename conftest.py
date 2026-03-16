"""Root conftest: unregister ROS system pytest plugins that interfere."""


def pytest_configure(config):
    """Unregister problematic system-level plugins."""
    pm = config.pluginmanager
    # Unregister launch_testing_ros plugin which defines unknown hooks
    for plugin in list(pm.get_plugins()):
        mod = getattr(plugin, "__module__", "") or ""
        name = getattr(plugin, "__name__", "") or ""
        full = f"{mod} {name}"
        if "launch_testing" in full or "ament" in full:
            try:
                pm.unregister(plugin)
            except Exception:
                pass
