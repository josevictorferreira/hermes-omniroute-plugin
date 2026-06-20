"""register(ctx) entry point — wires all three Omniroute providers."""
from unittest.mock import MagicMock

import omniroute_plugin as plugin
from omniroute_plugin import (
    OmnirouteImageGenProvider,
    OmnirouteTTSProvider,
    OmnirouteWebSearchProvider,
)


class TestRegister:
    def test_register_wires_all_three(self):
        ctx = MagicMock()
        plugin.register(ctx)
        ctx.register_image_gen_provider.assert_called_once()
        ctx.register_web_search_provider.assert_called_once()
        ctx.register_tts_provider.assert_called_once()

    def test_registered_instances_are_correct_types(self):
        ctx = MagicMock()
        plugin.register(ctx)
        assert isinstance(ctx.register_image_gen_provider.call_args[0][0], OmnirouteImageGenProvider)
        assert isinstance(ctx.register_web_search_provider.call_args[0][0], OmnirouteWebSearchProvider)
        assert isinstance(ctx.register_tts_provider.call_args[0][0], OmnirouteTTSProvider)
