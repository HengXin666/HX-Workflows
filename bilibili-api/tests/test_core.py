from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili_api._core import BiliSigner, WBISigner
from bilibili_api.cover import _sanitize_filename as sanitize_cover_filename
from bilibili_api.video import _sanitize_filename as sanitize_video_filename


class SigningTests(unittest.TestCase):
    # Agent Notes: HX-Workflows 接入 Agent Notes v2
    # 
    def test_tv_signature_is_stable_and_does_not_mutate_input(self) -> None:
        """Agent Notes
        .agents/notes/implemented/process/2026-10-08-repository-agent-notes-v2-adoption.md
        """
        source = {"bvid": "BV-test", "quality": 80}
        signer = BiliSigner(app_key="app", app_sec="secret")
        with patch("bilibili_api._core.time.time", return_value=1_700_000_000):
            signed = signer.sign(source)
        query = urlencode(sorted({**source, "appkey": "app", "ts": 1_700_000_000}.items()), quote_via=quote)
        self.assertEqual(signed["sign"], hashlib.md5(f"{query}secret".encode()).hexdigest())
        self.assertEqual(source, {"bvid": "BV-test", "quality": 80})

    def test_wbi_signature_sanitizes_reserved_characters(self) -> None:
        signer = WBISigner()
        signer.set_keys("abcdefghijklmnopqrstuvwxyz012345", "ZYXWVUTSRQPONMLKJIHGFEDCBA987654")
        signed = signer.sign_params({"keyword": "a!b(c)*d'e"}, ts=123)
        cleaned = {"keyword": "abcde", "wts": 123}
        query = urlencode(sorted(cleaned.items()), quote_via=quote)
        mixin = signer.get_mixin_key(signer.img_key + signer.sub_key)
        self.assertEqual(signed["w_rid"], hashlib.md5(f"{query}{mixin}".encode()).hexdigest())
        self.assertEqual(signed["wts"], 123)


class FilenameSafetyTests(unittest.TestCase):
    def test_download_filenames_cannot_inject_path_separators(self) -> None:
        unsafe = '../bad:<name>|?*\\file'
        for sanitizer in (sanitize_cover_filename, sanitize_video_filename):
            safe = sanitizer(unsafe)
            self.assertNotIn("/", safe)
            self.assertNotIn("\\", safe)
            self.assertLessEqual(len(safe), 200)


if __name__ == "__main__":
    unittest.main()
