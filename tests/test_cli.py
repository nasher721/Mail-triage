import unittest
from unittest.mock import patch

from email_triage.cli import _diagnostic_report, build_parser


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


if __name__ == "__main__":
    unittest.main()
