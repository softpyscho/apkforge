# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import os
import unittest
from unittest import mock

from src.core import network


class _Resp:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class ChallengeDetectionTests(unittest.TestCase):
    def test_cloudflare_server_header_alone_is_not_a_challenge(self) -> None:
        # A plain 403 behind Cloudflare must not be mistaken for a JS challenge.
        resp = _Resp(403, {"server": "cloudflare"})
        self.assertFalse(network._is_challenge(resp))

    def test_ok_response_is_not_a_challenge(self) -> None:
        self.assertFalse(network._is_challenge(_Resp(200, {"server": "cloudflare"})))

    def test_html_interstitial_is_a_challenge(self) -> None:
        resp = _Resp(503, {"content-type": "text/html"}, "<title>Just a moment...</title>")
        self.assertTrue(network._is_challenge(resp))

    def test_download_body_is_not_read(self) -> None:
        resp = _Resp(403, {"content-type": "text/html"}, "<title>Just a moment...</title>")
        self.assertFalse(network._is_challenge(resp, read_body=False))

    def test_cf_mitigated_header_is_a_challenge(self) -> None:
        self.assertTrue(network._is_challenge(_Resp(403, {"cf-mitigated": "challenge"})))

    def test_managed_challenge_requires_browser_signal(self) -> None:
        self.assertTrue(network._is_managed_challenge(_Resp(403, {"cf-mitigated": "challenge"})))
        self.assertTrue(network._is_managed_challenge(_Resp(503, {"content-type": "text/html"}, "Just a moment...")))
        self.assertFalse(network._is_managed_challenge(_Resp(403, {"content-type": "text/html"}, "Attention Required")))
        self.assertTrue(network._is_managed_challenge(_Resp(403, {"cf-mitigated": "challenge"}), read_body=False))


class RetryAfterTests(unittest.TestCase):
    def test_parses_seconds(self) -> None:
        self.assertEqual(network._retry_after(_Resp(429, {"Retry-After": "7"})), 7.0)

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(network._retry_after(_Resp(429, {"Retry-After": "soon"})))
        self.assertIsNone(network._retry_after(_Resp(429, {})))


class ProxyConfigTests(unittest.TestCase):
    def test_explicit_proxy_wins(self) -> None:
        env = {"APKFORGE_PROXY": "http://127.0.0.1:8080", "HTTPS_PROXY": "http://other:1"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(network._proxy_config(), {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"})

    def test_standard_proxy_vars(self) -> None:
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:3128"}, clear=True):
            self.assertEqual(network._proxy_config(), {"http": "http://proxy:3128", "https": "http://proxy:3128"})

    def test_no_proxy(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(network._proxy_config())


class BrowserHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.net = network.NetworkManager()

    def tearDown(self) -> None:
        self.net.session.close()

    def test_navigation_headers(self) -> None:
        headers = self.net._browser_headers("https://www.apkmirror.com/apk/x/", None, download=False)
        self.assertEqual(headers["Sec-Fetch-Mode"], "navigate")
        self.assertEqual(headers["Sec-Fetch-Site"], "none")
        self.assertIn("text/html", headers["Accept"])

    def test_referer_marks_same_origin(self) -> None:
        headers = self.net._browser_headers("https://www.apkmirror.com/x/", {"Referer": "https://www.apkmirror.com/"}, download=False)
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")

    def test_cross_site_referer(self) -> None:
        headers = self.net._browser_headers("https://cdn.example.com/a.apk", {"Referer": "https://www.apkmirror.com/"}, download=False)
        self.assertEqual(headers["Sec-Fetch-Site"], "cross-site")

    def test_solved_user_agent_is_applied(self) -> None:
        self.net._solved_ua = "Mozilla/5.0 (Solved)"
        headers = self.net._browser_headers("https://www.apkmirror.com/x/", None, download=False)
        self.assertEqual(headers["User-Agent"], "Mozilla/5.0 (Solved)")

    def test_download_headers(self) -> None:
        headers = self.net._browser_headers("https://cdn.example.com/a.apk", {"Accept": "application/octet-stream"}, download=True)
        self.assertEqual(headers["Accept"], "application/octet-stream")
        self.assertNotIn("Sec-Fetch-Mode", headers)


class _JsonResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FlareSolverrTests(unittest.TestCase):
    def setUp(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.net = network.NetworkManager()

    def tearDown(self) -> None:
        self.net.session.close()

    def test_disabled_without_url(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.net._flaresolverr_get("https://www.apkmirror.com/x/"))

    def test_success_applies_cookies_and_returns_html(self) -> None:
        payload = {
            "status": "ok",
            "solution": {
                "response": "<html>solved</html>",
                "userAgent": "Mozilla/5.0 (Test)",
                "cookies": [{"name": "cf_clearance", "value": "abc", "domain": ".apkmirror.com", "path": "/"}],
            },
        }
        with (
            mock.patch.dict(os.environ, {"FLARESOLVERR_URL": "http://localhost:8191"}, clear=True),
            mock.patch.object(network.requests, "post", return_value=_JsonResp(payload)),
        ):
            html = self.net._flaresolverr_get("https://www.apkmirror.com/x/")
        self.assertEqual(html, "<html>solved</html>")
        self.assertEqual(self.net._solved_ua, "Mozilla/5.0 (Test)")
        self.assertTrue(any(c.name == "cf_clearance" and c.value == "abc" for c in self.net.session.cookies.jar))

    def test_return_only_cookies_omits_body(self) -> None:
        payload = {
            "status": "ok",
            "solution": {
                "response": "<html>solved</html>",
                "cookies": [{"name": "cf_clearance", "value": "abc", "domain": ".apkmirror.com", "path": "/"}],
            },
        }
        captured: dict = {}

        def _post(url: str, json: dict, timeout: tuple) -> _JsonResp:
            captured.update(json)
            return _JsonResp(payload)

        with (
            mock.patch.dict(os.environ, {"FLARESOLVERR_URL": "http://localhost:8191"}, clear=True),
            mock.patch.object(network.requests, "post", side_effect=_post),
        ):
            result = self.net._flaresolverr_get("https://www.apkmirror.com/a.apk", return_only_cookies=True)
        self.assertEqual(result, "")
        self.assertTrue(captured.get("returnOnlyCookies"))

    def test_error_status_returns_none(self) -> None:
        payload = {"status": "error", "message": "not solvable"}
        with (
            mock.patch.dict(os.environ, {"FLARESOLVERR_URL": "http://localhost:8191"}, clear=True),
            mock.patch.object(network.requests, "post", return_value=_JsonResp(payload)),
        ):
            self.assertIsNone(self.net._flaresolverr_get("https://www.apkmirror.com/x/"))


if __name__ == "__main__":
    unittest.main()