"""Test that OmnirouteWebSearchProvider.search() preserves position=0.

This test exercises the real search() method with requests.post mocked,
so it will fail if the implementation reverts to `r.get("position") or idx`.
"""
import json
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

# --- Stub Hermes internals so __init__.py can be imported ---

igp = types.ModuleType("agent.image_gen_provider")
igp.DEFAULT_ASPECT_RATIO = "square"


class _ImageGenProvider:
    pass


igp.ImageGenProvider = _ImageGenProvider
igp.resolve_aspect_ratio = lambda a: a if a in ("landscape", "square", "portrait") else "square"
igp.success_response = lambda **k: {"success": True, **k}
igp.error_response = lambda **k: {"success": False, **k}
igp.save_b64_image = lambda b64, prefix="x", extension="png": f"/tmp/{prefix}.{extension}"
igp.save_url_image = lambda u, prefix="x": f"/tmp/{prefix}.png"

wsp = types.ModuleType("agent.web_search_provider")


class _WebSearchProvider:
    pass


wsp.WebSearchProvider = _WebSearchProvider

ttsp = types.ModuleType("agent.tts_provider")


class _TTSProvider:
    pass


ttsp.TTSProvider = _TTSProvider

agent_mod = types.ModuleType("agent")
sys.modules["agent"] = agent_mod
sys.modules["agent.image_gen_provider"] = igp
sys.modules["agent.web_search_provider"] = wsp
sys.modules["agent.tts_provider"] = ttsp

import importlib.util
import os

os.environ.setdefault("OMNIROUTE_TOKEN", "test-token")

# Load the plugin module
spec = importlib.util.spec_from_file_location("omniroute_plugin", "__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestSearchPositionMapping(unittest.TestCase):
    """Test that search() maps positions correctly, especially position=0."""

    def _make_provider(self):
        return mod.OmnirouteWebSearchProvider()

    def _mock_response(self, results, status_code=200):
        """Build a mock requests.post that returns the given results list."""
        mock_resp = MagicMock()
        mock_resp.ok = (200 <= status_code < 300)
        mock_resp.status_code = status_code
        mock_resp.text = json.dumps({"results": results})
        mock_resp.json.return_value = {"results": results}
        return mock_resp

    @patch("requests.post")
    def test_position_zero_preserved(self, mock_post):
        """position=0 in API result must appear as 0 in output, not fall back to idx."""
        results = [
            {"title": "Zeroth result", "url": "https://example.com/0", "snippet": "desc0", "position": 0},
        ]
        mock_post.return_value = self._mock_response(results)

        provider = self._make_provider()
        out = provider.search("test query")

        self.assertTrue(out["success"])
        positions = [r["position"] for r in out["data"]["web"]]
        self.assertEqual(positions, [0], f"Expected [0] but got {positions}")

    @patch("requests.post")
    def test_position_missing_falls_to_idx(self, mock_post):
        """Missing position key should fall back to 1-based index."""
        results = [
            {"title": "No position", "url": "https://example.com/1", "snippet": "desc1"},
        ]
        mock_post.return_value = self._mock_response(results)

        provider = self._make_provider()
        out = provider.search("test query")

        self.assertTrue(out["success"])
        positions = [r["position"] for r in out["data"]["web"]]
        self.assertEqual(positions, [1], f"Expected [1] but got {positions}")

    @patch("requests.post")
    def test_position_none_falls_to_idx(self, mock_post):
        """position=None should fall back to 1-based index."""
        results = [
            {"title": "None position", "url": "https://example.com/1", "snippet": "desc1", "position": None},
        ]
        mock_post.return_value = self._mock_response(results)

        provider = self._make_provider()
        out = provider.search("test query")

        self.assertTrue(out["success"])
        positions = [r["position"] for r in out["data"]["web"]]
        self.assertEqual(positions, [1], f"Expected [1] but got {positions}")

    @patch("requests.post")
    def test_position_positive_preserved(self, mock_post):
        """position=7 should stay 7, not be replaced by idx."""
        results = [
            {"title": "Seventh result", "url": "https://example.com/7", "snippet": "desc7", "position": 7},
        ]
        mock_post.return_value = self._mock_response(results)

        provider = self._make_provider()
        out = provider.search("test query")

        self.assertTrue(out["success"])
        positions = [r["position"] for r in out["data"]["web"]]
        self.assertEqual(positions, [7], f"Expected [7] but got {positions}")

    @patch("requests.post")
    def test_mixed_positions(self, mock_post):
        """Mixed positions: 0, missing, None, positive → [0, 2, 3, 7]."""
        results = [
            {"title": "Zero", "url": "https://a.com/0", "snippet": "d0", "position": 0},
            {"title": "Missing", "url": "https://a.com/1", "snippet": "d1"},
            {"title": "None", "url": "https://a.com/2", "snippet": "d2", "position": None},
            {"title": "Seven", "url": "https://a.com/3", "snippet": "d3", "position": 7},
        ]
        mock_post.return_value = self._mock_response(results)

        provider = self._make_provider()
        out = provider.search("test query")

        self.assertTrue(out["success"])
        positions = [r["position"] for r in out["data"]["web"]]
        self.assertEqual(positions, [0, 2, 3, 7], f"Expected [0, 2, 3, 7] but got {positions}")


if __name__ == "__main__":
    unittest.main()