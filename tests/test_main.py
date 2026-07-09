import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

import main
import utils


class ParseSizeTests(unittest.TestCase):
    def test_parse_size_accepts_common_units(self):
        self.assertEqual(main.parse_size("20mb"), 20 * 2**20)
        self.assertEqual(main.parse_size("1.5 GB"), int(1.5 * 2**30))
        self.assertEqual(main.parse_size("2mib"), 2 * 2**20)

    def test_parse_size_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            main.parse_size("abc")

        with self.assertRaises(ValueError):
            main.parse_size("10xb")


class UrlSupportTests(unittest.TestCase):
    def test_is_supported_url_accepts_both_domains(self):
        self.assertTrue(main.is_supported_url("https://k2s.cc/file/abc123"))
        self.assertTrue(main.is_supported_url("https://keep2share.cc/file/abc123"))
        self.assertFalse(main.is_supported_url("https://example.com/file/abc123"))

    def test_extract_file_id_returns_match(self):
        self.assertEqual(main.extract_file_id("https://k2s.cc/file/abc123"), "abc123")

        with self.assertRaises(ValueError):
            main.extract_file_id("https://example.com/file/abc123")


class VideoCheckTests(unittest.TestCase):
    def test_check_vid_uses_safe_subprocess_invocation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "test file.mp4"
            video_path.write_bytes(b"data")

            completed = mock.Mock(stderr=b"")
            with mock.patch("main.subprocess.run", return_value=completed) as run_mock:
                self.assertTrue(main.check_vid(video_path))

            run_mock.assert_called_once()
            args = run_mock.call_args.args[0]
            self.assertEqual(args[:3], ["ffmpeg", "-v", "warning"])
            self.assertEqual(args[4], str(video_path))
            self.assertEqual(args[-2], "null")
            self.assertEqual(args[-1], os.devnull)


class ProxyFallbackTests(unittest.TestCase):
    @mock.patch("utils.requests.get", side_effect=utils.requests.RequestException("offline"))
    def test_proxy_fetch_falls_back_to_direct_connection(self, _requests_get):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                self.assertEqual(utils.get_working_proxies(refresh=True), [None])
            finally:
                os.chdir(original_cwd)
