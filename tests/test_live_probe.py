from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from email_triage.cli import main
from email_triage.config import ConfigurationError
from email_triage.desktop import OutlookDesktopMailbox
from email_triage.graph import GraphError, GraphMailbox
from email_triage.live_probe import LiveProbeResult, run_live_probe
from email_triage.owa import CapturedAuth, OwaMailbox, capture_edge_auth


def _successful_urlopen() -> MagicMock:
    opened = MagicMock()
    opened.return_value.__enter__.return_value = MagicMock()
    return opened


class BackendProbeTests(unittest.TestCase):
    def test_graph_probe_discards_response_without_parsing(self) -> None:
        mailbox = GraphMailbox("tenant", "client", Path("unused"), interactive=False)
        opened = _successful_urlopen()
        with patch.object(
            mailbox, "access_token", return_value="secret-token"
        ) as token, patch("email_triage.graph.urlopen", opened):
            self.assertIsNone(mailbox.probe_access())

        token.assert_called_once_with(persist=False)
        request = opened.call_args.args[0]
        self.assertEqual(
            parse_qs(urlparse(request.full_url).query),
            {"$top": ["1"], "$select": ["id"]},
        )
        self.assertNotIn("body", request.full_url.lower())
        opened.return_value.__enter__.return_value.read.assert_not_called()

    def test_graph_probe_refreshes_in_memory_without_writing_cache(self) -> None:
        mailbox = GraphMailbox("tenant", "client", Path("unused"), interactive=False)
        cached = {
            "access_token": "expired-token",
            "refresh_token": "refresh-token",
            "expires_at": 0,
            "scopes": mailbox.scopes,
        }
        opened = _successful_urlopen()
        with patch.object(mailbox, "_load_cache", return_value=cached), patch.object(
            mailbox,
            "_post_form",
            return_value={"access_token": "fresh-token", "expires_in": 3600},
        ), patch.object(mailbox, "_save_cache") as save, patch(
            "email_triage.graph.urlopen", opened
        ):
            mailbox.probe_access()
        save.assert_not_called()

    def test_graph_probe_without_cached_auth_is_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mailbox = GraphMailbox(
                "tenant",
                "client",
                Path(directory) / "missing.json",
                interactive=False,
            )
            output = io.StringIO()
            with patch("email_triage.graph.urlopen") as opened, redirect_stdout(output):
                with self.assertRaises(GraphError) as caught:
                    mailbox.probe_access()
        self.assertEqual(caught.exception.code, "authentication_required")
        self.assertEqual(output.getvalue(), "")
        opened.assert_not_called()

    def test_owa_probe_discards_response_and_clears_captured_auth(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("secret-token")
        opened = _successful_urlopen()
        with patch("email_triage.owa.urlopen", opened):
            self.assertIsNone(mailbox.probe_access())

        request = opened.call_args.args[0]
        self.assertEqual(
            parse_qs(urlparse(request.full_url).query),
            {"$top": ["1"], "$select": ["id"]},
        )
        self.assertNotIn("body", request.full_url.lower())
        opened.return_value.__enter__.return_value.read.assert_not_called()
        self.assertIsNone(mailbox._auth)

    def test_owa_probe_retries_401_once_and_clears_auth(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("expired-token")
        denied = HTTPError("https://graph.microsoft.com", 401, "denied", None, None)
        success = MagicMock()
        with patch(
            "email_triage.owa.urlopen", side_effect=[denied, success]
        ) as opened, patch(
            "email_triage.owa.capture_edge_auth",
            return_value=CapturedAuth("fresh-token"),
        ) as capture:
            mailbox.probe_access()
        self.assertEqual(opened.call_count, 2)
        capture.assert_called_once_with("http://127.0.0.1:9222")
        self.assertIsNone(mailbox._auth)

    def test_owa_probe_rejects_remote_debug_endpoint_before_contact(self) -> None:
        with patch("email_triage.owa.capture_edge_auth") as capture, patch(
            "email_triage.owa.urlopen"
        ) as opened:
            with self.assertRaisesRegex(ConfigurationError, "loopback endpoint"):
                OwaMailbox("http://192.0.2.10:9222", read_write=False)
            capture.assert_not_called()
            opened.assert_not_called()

    def test_owa_auth_capture_detaches_without_closing_edge(self) -> None:
        browser = MagicMock()
        original_close = browser.close
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        playwright.stop.side_effect = lambda: browser.close()
        controller = MagicMock()
        controller.start.return_value = playwright

        playwright_package = ModuleType("playwright")
        playwright_package.__path__ = []
        sync_api = ModuleType("playwright.sync_api")
        sync_api.sync_playwright = MagicMock(return_value=controller)
        auth = CapturedAuth("synthetic-token")
        with patch.dict(
            sys.modules,
            {"playwright": playwright_package, "playwright.sync_api": sync_api},
        ), patch(
            "email_triage.owa.edge_debug_available", return_value=True
        ), patch(
            "email_triage.owa._outlook_page", return_value=object()
        ), patch(
            "email_triage.owa._auth_from_page", return_value=auth
        ):
            self.assertEqual(
                capture_edge_auth("http://127.0.0.1:9222"),
                auth,
            )

        playwright.stop.assert_called_once_with()
        original_close.assert_not_called()

    @patch("email_triage.desktop.shutil")
    @patch("email_triage.desktop.platform")
    def test_desktop_probe_checks_window_without_visible_text(
        self, platform_module: MagicMock, shutil_module: MagicMock
    ) -> None:
        platform_module.system.return_value = "Darwin"
        shutil_module.which.return_value = "/usr/bin/osascript"
        results = iter(
            [
                subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="ready\n", stderr=""),
            ]
        )
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object):
            calls.append(command)
            return next(results)

        mailbox = OutlookDesktopMailbox(runner)
        self.assertIsNone(mailbox.probe_access())
        self.assertEqual(len(calls), 2)
        probe_script = calls[1][-1].lower()
        self.assertNotIn("static text", probe_script)
        self.assertNotIn("visibletext", probe_script)


class LiveProbeBoundaryTests(unittest.TestCase):
    def assert_privacy_contract(self, result: LiveProbeResult) -> None:
        self.assertFalse(result.retained_mail_data)
        self.assertFalse(result.model_contacted)
        self.assertFalse(result.mailbox_mutated)

    def test_graph_missing_registration_fails_before_backend_creation(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "email_triage.live_probe.GraphMailbox"
        ) as mailbox:
            result = run_live_probe("graph")
        self.assertEqual(result.code, "credentials_missing")
        mailbox.assert_not_called()
        self.assert_privacy_contract(result)

    def test_graph_errors_are_redacted(self) -> None:
        secret = "secret-token secret-subject secret-message-id"
        error = GraphError(secret, code="remote_secret_code", detail=secret)
        with patch.dict(
            os.environ,
            {"MS_TENANT_ID": "tenant", "MS_CLIENT_ID": "client"},
            clear=True,
        ), patch("email_triage.live_probe.GraphMailbox") as mailbox:
            mailbox.return_value.probe_access.side_effect = error
            result = run_live_probe("graph")
        rendered = json.dumps(result.to_dict())
        self.assertEqual(result.code, "probe_failed")
        self.assertNotIn("secret", rendered)
        self.assert_privacy_contract(result)

    def test_owa_rejects_remote_cdp_before_network_check(self) -> None:
        with patch.dict(
            os.environ, {"EDGE_CDP_URL": "http://192.0.2.10:9222"}, clear=True
        ), patch("email_triage.live_probe.edge_debug_available") as available:
            result = run_live_probe("owa")
        self.assertEqual(result.code, "unsafe_cdp_url")
        available.assert_not_called()
        self.assert_privacy_contract(result)

    def test_owa_unavailable_is_actionable(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "email_triage.live_probe.edge_debug_available", return_value=False
        ), patch("email_triage.live_probe.OwaMailbox") as mailbox:
            result = run_live_probe("owa")
        self.assertEqual(result.code, "cdp_unreachable")
        self.assertIn("open_outlook_in_edge.sh", result.detail)
        mailbox.assert_not_called()
        self.assert_privacy_contract(result)

    def test_success_results_preserve_the_privacy_contract(self) -> None:
        with patch("email_triage.live_probe.edge_debug_available", return_value=True), patch(
            "email_triage.live_probe.OwaMailbox"
        ) as owa:
            owa_result = run_live_probe("owa")
        with patch("email_triage.live_probe.OutlookDesktopMailbox") as desktop:
            desktop_result = run_live_probe("desktop")
        owa.return_value.probe_access.assert_called_once_with()
        desktop.return_value.probe_access.assert_called_once_with()
        for result in (owa_result, desktop_result):
            self.assertTrue(result.available)
            self.assertEqual(result.code, "ready")
            self.assert_privacy_contract(result)

    def test_local_source_is_not_a_live_probe(self) -> None:
        result = run_live_probe("local")
        self.assertFalse(result.available)
        self.assertEqual(result.code, "unsupported_source")
        self.assert_privacy_contract(result)

    def test_cli_prints_only_probe_json_and_skips_model_setup(self) -> None:
        result = LiveProbeResult("graph", True, "ready", "Fixed safe detail.")
        output = io.StringIO()
        with patch("email_triage.cli.run_live_probe", return_value=result), patch(
            "email_triage.cli.build_classifier"
        ) as classifier, patch("email_triage.cli.build_agent") as agent, redirect_stdout(output):
            status = main(["--source", "graph", "--live-probe"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), result.to_dict())
        classifier.assert_not_called()
        agent.assert_not_called()

    def test_cli_rejects_implicit_local_and_operational_flags(self) -> None:
        cases = [
            ["--live-probe"],
            ["--source", "local", "--live-probe"],
            ["--source", "graph", "--live-probe", "--apply"],
            ["--source", "graph", "--live-probe", "--mark-read"],
            ["--source", "graph", "--live-probe", "--login"],
            ["--source", "graph", "--live-probe", "--watch"],
        ]
        for argv in cases:
            with self.subTest(argv=argv), patch(
                "email_triage.cli.run_live_probe"
            ) as probe, redirect_stderr(io.StringIO()):
                status = main(argv)
            self.assertEqual(status, 2)
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
