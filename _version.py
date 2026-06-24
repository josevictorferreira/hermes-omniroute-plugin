"""Plugin version resolution.

Reads ``version:`` from ``plugin.yaml`` so the HTTP ``User-Agent`` header and
``plugin.yaml`` stay in sync across releases. Kept separate so config/provider
modules can import ``__version__`` / ``_PLUGIN_VERSION`` without pulling in
provider code.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


def _read_plugin_version() -> str:
    """Read ``version:`` from plugin.yaml so User-Agent stays in sync across releases."""
    try:
        path = os.path.join(os.path.dirname(__file__), "plugin.yaml")
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^version:\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip("\"'")
    except Exception:
        logger.debug("Failed to read version from plugin.yaml", exc_info=True)
        return "0.3.5"


_PLUGIN_VERSION = _read_plugin_version()


__version__ = _PLUGIN_VERSION
