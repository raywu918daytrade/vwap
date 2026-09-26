from __future__ import annotations

import unittest

from api import _compact_indicator_map


class CompactIndicatorMapTest(unittest.TestCase):
    def test_keeps_only_list_fields(self):
        source = {
            "2330": {
                "events": [
                    {
                        "kind": "bull",
                        "time": "10:05",
                        "until": "10:06",
                        "legs": [{"price1": 1, "price2": 2}],
                        "hist1": -0.2,
                    }
                ]
            }
        }

        self.assertEqual(
            _compact_indicator_map(source),
            {"2330": {"events": [{"kind": "bull", "time": "10:05", "until": "10:06"}]}},
        )

    def test_drops_empty_or_invalid_records(self):
        self.assertEqual(_compact_indicator_map({"2330": {}, "2317": None}), {})


if __name__ == "__main__":
    unittest.main()
