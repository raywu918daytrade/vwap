from __future__ import annotations

import unittest
from unittest.mock import patch

from main import render_reader


class RenderReaderMemoryTest(unittest.TestCase):
    def test_reads_linux_current_rss(self):
        with patch("main.render_reader.Path.read_text", return_value="Name:\tpython\nVmRSS:\t440320 kB\n"):
            self.assertEqual(render_reader._current_rss_mb(), 430.0)

    def test_guard_clears_caches_over_limit(self):
        with (
            patch.object(render_reader, "_current_rss_mb", return_value=431.0),
            patch.object(render_reader.startup_data, "clear_market_query_caches") as clear_data,
            patch.object(render_reader, "clear_vwap_bundle_cache") as clear_bundle,
            patch.object(render_reader.gc, "collect") as collect,
        ):
            render_reader._guard_memory()
        clear_data.assert_called_once_with()
        clear_bundle.assert_called_once_with()
        collect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
