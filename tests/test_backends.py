import unittest

from email_triage.backends import backend_capabilities


class BackendCapabilitiesTests(unittest.TestCase):
    def test_every_backend_explicitly_disallows_send_and_attachment_content(self) -> None:
        for source in ("graph", "owa", "local", "desktop", "accessibility"):
            capabilities = backend_capabilities(source)
            self.assertFalse(capabilities.send_mail)
            self.assertFalse(capabilities.attachment_content)
            self.assertFalse(capabilities.bulk_body_export)

    def test_desktop_is_single_message_and_read_only(self) -> None:
        capabilities = backend_capabilities("desktop")
        self.assertFalse(capabilities.supports_apply)
        self.assertEqual(capabilities.read_scope, "frontmost_outlook_message_only")

    def test_accessibility_scans_only_visible_unread_rows(self) -> None:
        capabilities = backend_capabilities("accessibility")
        self.assertFalse(capabilities.supports_apply)
        self.assertEqual(capabilities.read_scope, "visible_unread_inbox_rows")
        self.assertTrue(capabilities.metadata_prefilter)


if __name__ == "__main__":
    unittest.main()
