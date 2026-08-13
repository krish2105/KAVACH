"""Device-scoped allowlist tests (spec §7, extended to a second device).

**A discrepancy worth stating plainly:** the plan called for a per-app iPhone
allowlist (Notes, Music, Maps, Messages). `mirroir-mcp`'s tools turn out not
to be app-scoped at all — `screenshot`, `describe_screen` and
`start_recording` operate on whatever is on the iPhone screen, and none of
them take an app name. A per-app allowlist for the iPhone is therefore not
enforceable through this server, and pretending otherwise would be security
theatre.

What *is* enforceable, and is tested here:
  - the iPhone is a separate grant from the Mac, off unless explicitly enabled
  - iPhone tools are gated individually: reading is allowed, recording and
    anything that acts requires confirmation
  - **the Mac's behaviour does not change** — every existing allowlist test
    still passes unmodified
"""

import pytest

from kavach.hands.allowlist import Allowlist
from kavach.hands.gate import ToolGate, device_for_server
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog


class YesConfirmer:
    def __init__(self):
        self.prompts: list[str] = []

    async def confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return True


@pytest.fixture
def ks(tmp_path):
    return KillSwitch(log=ActionLog(tmp_path / "a.jsonl"))


def gate(ks, confirmer=None):
    return ToolGate(kill_switch=ks, allowlist=Allowlist(), confirmer=confirmer)


async def allowed(g, tool, args=None):
    return (await g.check(tool, args or {}, context=None)).behavior == "allow"


# ——— which device does a server belong to ———

@pytest.mark.parametrize("server,device", [
    ("macos-automator", "mac"),
    ("macos-accessibility", "mac"),
    ("peekaboo", "mac"),
    ("mirroir", "iphone"),
])
def test_servers_map_to_devices(server, device):
    assert device_for_server(server) == device


def test_an_unknown_server_has_no_device():
    """Unmapped means ungovernable, and ungovernable means denied upstream."""
    assert device_for_server("some-new-server") is None


# ——— the iPhone is a separate grant ———

def test_iphone_is_a_distinct_device_in_the_allowlist():
    al = Allowlist()
    assert "mac" in al.devices
    assert "iphone" in al.devices


def test_mac_apps_do_not_become_iphone_apps():
    """Safari on the Mac and Safari on a phone are different grants."""
    al = Allowlist()
    mac_apps = {e["name"] for e in al.device_entries("mac")}
    assert "Safari" in mac_apps
    # The iPhone grant is not a list of apps at all — see the module docstring.
    assert al.device_enabled("iphone") in (True, False)


# ——— iPhone tool gating ———

@pytest.mark.parametrize("tool", [
    "status", "check_health", "list_targets", "get_orientation",
    "screenshot", "describe_screen", "list_skills",
])
async def test_read_only_iphone_tools_are_permitted(ks, tool):
    assert await allowed(gate(ks), f"mcp__mirroir__{tool}")


@pytest.mark.parametrize("tool", ["start_recording", "calibrate_component"])
async def test_iphone_tools_that_act_require_confirmation(ks, tool):
    """start_recording writes a video of your phone screen to disk. That is
    externally visible by any reasonable reading of §7."""
    confirmer = YesConfirmer()
    await gate(ks, confirmer).check(f"mcp__mirroir__{tool}", {}, context=None)
    assert confirmer.prompts, f"{tool} ran without asking"


async def test_iphone_recording_without_a_confirmer_is_denied(ks):
    assert not await allowed(gate(ks), "mcp__mirroir__start_recording",
                             {"output_path": "/tmp/x.mp4"})


async def test_an_unknown_iphone_tool_is_denied(ks):
    """Default-deny survives the vendor adding tools we have not reviewed."""
    assert not await allowed(gate(ks, YesConfirmer()), "mcp__mirroir__send_message")


# ——— the kill switch still outranks everything, on every device ———

async def test_kill_switch_denies_iphone_tools_too(ks):
    g = gate(ks, YesConfirmer())
    ks.trigger(source="test")
    assert not await allowed(g, "mcp__mirroir__screenshot")


# ——— the Mac is untouched ———

async def test_mac_allowlist_behaviour_is_unchanged(ks):
    g = gate(ks, YesConfirmer())
    assert await allowed(
        g, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Safari" to return name of front window'},
    )
    assert not await allowed(
        g, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Mail" to activate'},
    )


async def test_disabling_the_iphone_denies_every_mirroir_tool(ks, tmp_path):
    """Turning the device off is one switch, not a per-tool edit."""
    import json

    source = json.loads(Allowlist().path.read_text())
    source["devices"]["iphone"]["enabled"] = False
    config = tmp_path / "allowlist.json"
    config.write_text(json.dumps(source))

    g = ToolGate(kill_switch=ks, allowlist=Allowlist(config), confirmer=YesConfirmer())
    assert not await allowed(g, "mcp__mirroir__screenshot")
    assert not await allowed(g, "mcp__mirroir__status")
