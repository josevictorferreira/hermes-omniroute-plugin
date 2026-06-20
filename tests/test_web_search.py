"""Web-search tests: search() position mapping (position=0 preservation).

Merged from the former ``test_position_zero.py``.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

import omniroute_plugin as mod


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
