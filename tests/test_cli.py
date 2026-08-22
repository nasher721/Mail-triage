import unittest
from pathlib import Path
from unittest.mock import patch

from email_triage.cli import _diagnostic_report, build_parser, main
from email_triage.config import ConfigurationError


class DiagnosticCliTests(unittest.TestCase):
    def test_graph_diagnostic_does_not_authenticate(self) -> None:
        args = build_parser().parse_args(["--source", "graph", "--diagnose"])
        with patch.dict("os.environ", {}, clear=True):
            report = _diagnostic_report(args)
        self.assertEqual(report["readiness"]["code"], "credentials_missing")
        self.assertFalse(report["capabilities"]["send_mail"])

    def test_owa_diagnostic_checks_only_debug_endpoint(self) -> None:
        args = build_parser().parse_args(["--owa", "--diagnose"])
        with patch("email_triage.cli.edge_debug_available", return_value=True):
            report = _diagnostic_report(args)
        self.assertEqual(report["readiness"]["code"], "ready")
        self.assertIn("no session token", report["readiness"]["detail"])

    def test_diagnostic_defaults_to_owa_without_graph_registration(self) -> None:
        args = build_parser().parse_args(["--diagnose"])
        with patch.dict("os.environ", {}, clear=True), patch(
            "email_triage.cli.edge_debug_available", return_value=False
        ):
            report = _diagnostic_report(args)
        self.assertEqual(report["capabilities"]["source"], "owa")
        self.assertEqual(report["readiness"]["code"], "cdp_unreachable")


class ApplyIdsCliTests(unittest.TestCase):
    def test_parser_accepts_apply_ids_file(self) -> None:
        args = build_parser().parse_args(
            ["--source", "owa", "--apply", "--apply-ids-file", "/tmp/ids.json"]
        )
        self.assertEqual(args.apply_ids_file, "/tmp/ids.json")
        self.assertTrue(args.apply)

    def test_watch_and_ids_file_are_rejected(self) -> None:
        with patch("sys.argv", ["email-triage", "--owa", "--apply", "--apply-ids-file", "x", "--watch", "30"]):
            self.assertEqual(main(), 2)

    def test_apply_ids_skips_classifier_and_unread_scan(self) -> None:
        args = build_parser().parse_args(
            ["--source", "graph", "--apply", "--apply-ids-file", "/tmp/ids.json", "--non-interactive"]
        )
        with (
            patch("email_triage.cli.Settings.from_env") as settings_from_env,
            patch("email_triage.cli.load_apply_ids", return_value=("message-1",)) as load_ids,
            patch("email_triage.cli.apply_selected", return_value=(1, 0)) as apply_sel,
            patch("email_triage.cli.build_classifier") as build_classifier,
            patch("email_triage.cli._build_graph_mailbox") as build_mailbox,
            patch("email_triage.cli.GraphActuator") as actuator_cls,
        ):
            settings = settings_from_env.return_value
            settings.mailbox_source = "graph"
            settings.apply_changes = True
            settings.mark_read = False
            settings.output_dir = Path("/tmp")
            from email_triage.cli import run
            code = run(args)
        self.assertEqual(code, 0)
        load_ids.assert_called_once()
        apply_sel.assert_called_once()
        build_classifier.assert_not_called()
        build_mailbox.assert_called_once()
        actuator_cls.assert_called_once()

    def test_apply_ids_rejects_local_source(self) -> None:
        args = build_parser().parse_args(
            ["--source", "local", "--input", "samples/inbox.jsonl", "--apply", "--apply-ids-file", "/tmp/ids.json"]
        )
        with patch.dict("os.environ", {"TRIAGE_PROVIDER": "ollama"}, clear=False):
            with self.assertRaises(ConfigurationError):
                from email_triage.cli import run
                run(args)


if __name__ == "__main__":
    unittest.main()
