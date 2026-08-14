import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "tools" / "build_single_file.py"
BUNDLE = ROOT / "email_triage_standalone.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_single_file", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SingleFileBundleTests(unittest.TestCase):
    def test_bundle_matches_the_package(self) -> None:
        builder = load_builder()
        self.assertEqual(
            BUNDLE.read_text(encoding="utf-8"),
            builder.render(),
            "email_triage_standalone.py is stale; run python tools/build_single_file.py",
        )

    def test_no_send_or_delete_path_exists(self) -> None:
        from email_triage.graph import READ_SCOPES, READ_WRITE_SCOPES

        for scopes in (READ_SCOPES, READ_WRITE_SCOPES):
            self.assertNotIn("Mail.Send", scopes)
        sources = [BUNDLE.read_text(encoding="utf-8")] + [
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src" / "email_triage").glob("*.py")
        ]
        for source in sources:
            for forbidden in ("sendMail", "/reply", "/forward", 'method="DELETE"'):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
