"""
V10 §60 panel integrity — verifies capability registry, settings render, diagnostics, installer, audit.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_capabilities_registered():
    from core.capabilities import CAPABILITIES, all_capabilities
    assert len(CAPABILITIES) >= 30, f"expected >=30 capabilities, got {len(CAPABILITIES)}"
    for key in ("verification","tickets","auto_responder","economy","giveaways"):
        assert key in CAPABILITIES, f"missing {key}"
        cap = CAPABILITIES[key]
        assert cap.config_key, f"{key} no config_key"
        assert cap.runtime_owner, f"{key} no runtime_owner"
        assert isinstance(cap.actions, list) and cap.actions

def test_system_groups_tabs():
    from modules.system_panels import GroupPanelView
    assert "diagnostics" in GroupPanelView.TABS, f"TABS missing diagnostics: {GroupPanelView.TABS}"
    assert "settings" in GroupPanelView.TABS
    assert "actions" in GroupPanelView.TABS
    assert "history" in GroupPanelView.TABS
    assert "danger" in GroupPanelView.TABS
    assert "test" not in GroupPanelView.TABS or "diagnostics" in GroupPanelView.TABS  # test alias allowed

def test_resource_manager_exists():
    from core.resource_manager import ResourceManager
    assert hasattr(ResourceManager, "resolve_channel")
    assert hasattr(ResourceManager, "resolve_role")
    assert hasattr(ResourceManager, "create_channel")
    assert hasattr(ResourceManager, "post_panel")
    assert hasattr(ResourceManager, "repair_resource")

def test_installer_exists():
    from core.installer import SystemInstaller
    assert hasattr(SystemInstaller, "install")
    assert hasattr(SystemInstaller, "repair")
    assert hasattr(SystemInstaller, "test")
    # verification is fully V10
    from core.capabilities import get_capability
    v = get_capability("verification")
    assert v and len(v.resources) >= 3 and len(v.settings) >= 3

def test_deprecated_prefix():
    from modules.config_panels import ConfigPanels
    # ensure ConfigPanels.show_panel is deprecated (contains phrase)
    import inspect
    src = inspect.getsource(ConfigPanels.show_panel)
    assert "deprecated" in src.lower() or "Use /configpanel" in src

def test_global_health():
    from modules.system_panels import build_global_health_embed
    assert callable(build_global_health_embed)

def test_slash_health_choice():
    from modules.slash_commands import SlashCommands
    # check that health choice is registered via decorator introspection
    # SlashCommands.configpanel is app_commands.Command
    cmd = getattr(SlashCommands, "configpanel", None)
    assert cmd is not None
    # choices are on the app_commands.choices decorator; verify via param
    # fallback: check source contains health
    import inspect
    src = inspect.getsource(SlashCommands.configpanel.callback if hasattr(SlashCommands.configpanel, "callback") else SlashCommands.configpanel)
    assert "health" in src.lower() or "health" in inspect.getsource(SlashCommands)
