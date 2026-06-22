import unittest

import main


class MainHelpersTestCase(unittest.TestCase):
    def test_parse_size_supports_decimal_units(self) -> None:
        self.assertEqual(main.parse_size("20MB"), 20_000_000)
        self.assertEqual(main.parse_size("20MiB"), 20 * 1024 * 1024)

    def test_parse_size_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            main.parse_size("abc")

        with self.assertRaises(ValueError):
            main.parse_size("5XB")

    def test_extract_file_id_supports_both_domains(self) -> None:
        self.assertEqual(
            main.extract_file_id("https://k2s.cc/file/example123"),
            "example123",
        )
        self.assertEqual(
            main.extract_file_id("https://keep2share.cc/file/example123?foo=bar"),
            "example123",
        )
        self.assertIsNone(main.extract_file_id("https://example.com/file/example123"))

    def test_build_range_covers_all_bytes(self) -> None:
        ranges = main.buildRange(10, 2)

        self.assertEqual(ranges["0"]["range"], "0-5")
        self.assertEqual(ranges["1"]["range"], "6-10")
        self.assertEqual(ranges["0"]["bytes"] + ranges["1"]["bytes"], 11)


if __name__ == "__main__":
    unittest.main()
