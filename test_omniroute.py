"""Tests for omniroute plugin size validation."""
import sys
import types
from pathlib import Path

# Mock Hermes-specific imports before loading the plugin
agent_img = types.ModuleType("agent.image_gen_provider")
agent_img.DEFAULT_ASPECT_RATIO = "1:1"
agent_img.ImageGenProvider = type("ImageGenProvider", (), {})
agent_img.error_response = lambda *a, **k: None
agent_img.resolve_aspect_ratio = lambda *a, **k: "1:1"
agent_img.save_b64_image = lambda *a, **k: None
agent_img.save_url_image = lambda *a, **k: None
agent_img.success_response = lambda *a, **k: None
sys.modules["agent.image_gen_provider"] = agent_img

agent_ws = types.ModuleType("agent.web_search_provider")
agent_ws.WebSearchProvider = type("WebSearchProvider", (), {})
sys.modules["agent.web_search_provider"] = agent_ws

agent_mod = types.ModuleType("agent")
agent_mod.__path__ = []
sys.modules["agent"] = agent_mod

hermes_cfg = types.ModuleType("hermes_cli.config")
hermes_cfg.load_config = lambda: {}
sys.modules["hermes_cli.config"] = hermes_cfg
hermes_mod = types.ModuleType("hermes_cli")
hermes_mod.__path__ = []
sys.modules["hermes_cli"] = hermes_mod

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent))

from __init__ import _is_valid_size, _pick_size


def test_is_valid_size_rejects_extra_separators_x():
    assert _is_valid_size("1024x1024xjunk") is False


def test_is_valid_size_rejects_extra_separators_colon():
    assert _is_valid_size("16:9:1") is False


def test_is_valid_size_rejects_extra_separators_colon2():
    assert _is_valid_size("1024:1024:extra") is False


def test_is_valid_size_accepts_valid_x():
    assert _is_valid_size("1024x1024") is True


def test_is_valid_size_accepts_valid_colon():
    assert _is_valid_size("16:9") is True


def test_pick_size_skips_malformed_tokens():
    assert _pick_size(["1024x1024xjunk", "16:9:1", "1024x1024"], "square") == "1024x1024"
