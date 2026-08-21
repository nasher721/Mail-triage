import types
import unittest
from unittest.mock import patch

from email_triage.accessibility import (
    AccessibilityMailbox,
    OutlookAccessibilityMailbox,
    accessibility_diagnostic,
)
from email_triage.config import ConfigurationError


class Element:
    def __init__(self, role="AXGroup", children=None, **attrs):
        self.attrs = {"AXRole": role, **attrs}
        self.attrs.setdefault("AXChildren", list(children or []))
        self.selected = False

    def getAttribute(self, name):
        return self.attrs.get(name)

    def setAttribute(self, name, value):
        self.attrs[name] = value
        if name == "AXSelected":
            self.selected = value


class App:
    def __init__(self, window):
        self.window = window

    def windows(self):
        return [self.window]


class AccessibilityTests(unittest.TestCase):
    def make_app(self):
        unread = Element("AXRow", AXTitle="Subject", children=[Element("AXImage", AXDescription="Unread")])
        read = Element("AXRow", AXTitle="Do not include", children=[Element("AXImage", AXDescription="Read")])
        table = Element("AXTable", children=[unread, read], AXRows=[unread, read])
        pane = Element("AXGroup", AXTitle="Reading Pane", AXBody="Body text", AXSenderName="Alex", AXSenderAddress="alex@example.org", AXReceivedAt="2026-08-20T10:00:00+00:00")
        window = Element("AXWindow", AXTitle="Inbox", children=[table, pane])
        module = types.SimpleNamespace(getAppRefByBundleId=lambda bundle: App(window))
        return module, unread

    def test_only_explicitly_unread_visible_rows_are_read_without_selection(self):
        module, row = self.make_app()
        mailbox = OutlookAccessibilityMailbox(module, sleep=lambda _: None)
        messages = mailbox.unread_messages(10)
        self.assertEqual(len(messages), 1)
        self.assertFalse(row.selected)
        self.assertEqual(messages[0].body, "Subject\nUnread")
        self.assertEqual(messages[0].sender_address, "")

    def test_limit_and_exclude_are_deterministic(self):
        module, _ = self.make_app()
        mailbox = AccessibilityMailbox(module, sleep=lambda _: None)
        first = mailbox.unread_messages(1)[0]
        self.assertEqual(mailbox.unread_messages(1, {first.id}), [])
        self.assertEqual(first.id, mailbox.unread_messages(1)[0].id)

    def test_outlook_split_group_header_and_webarea_layout(self):
        row = Element(
            "AXRow",
            AXTitle="Row preview",
            children=[Element("AXImage", AXDescription="Unread")],
        )
        table = Element("AXTable", AXVisibleRows=[row], AXRows=[row])
        header = Element(
            "AXGroup",
            children=[
                Element("AXStaticText", AXValue="Quarterly update"),
                Element("AXStaticText", AXValue="Alex <alex@example.org>"),
            ],
        )
        body = Element(
            "AXGroup",
            children=[
                Element(
                    "AXWebArea",
                    children=[Element("AXStaticText", AXValue="Visible body")],
                )
            ],
        )
        content = Element(
            "AXSplitGroup",
            children=[Element("AXGroup", children=[table]), Element(), header, Element(), body],
        )
        inner = Element("AXSplitGroup", children=[content])
        main = Element("AXSplitGroup", children=[inner])
        window = Element("AXWindow", AXTitle="Inbox", children=[main])
        module = types.SimpleNamespace(
            getAppRefByBundleId=lambda _bundle: App(window)
        )

        message = OutlookAccessibilityMailbox(module, sleep=lambda _: None).unread_messages(1)[0]
        self.assertEqual(message.subject, "Row preview")
        self.assertEqual(message.sender_address, "")
        self.assertEqual(message.body, "Row preview\nUnread")
        self.assertFalse(row.selected)

    def test_missing_optional_dependency_is_actionable(self):
        with patch("email_triage.accessibility.importlib") as importlib_module:
            importlib_module.import_module.side_effect = ImportError
            with self.assertRaisesRegex(ConfigurationError, "atomacos"):
                OutlookAccessibilityMailbox().unread_messages(1)

    def test_missing_inbox_fails_closed(self):
        module = types.SimpleNamespace(getAppRefByBundleId=lambda _: App(Element("AXWindow", AXTitle="Calendar")))
        with self.assertRaisesRegex(ConfigurationError, "Inbox"):
            OutlookAccessibilityMailbox(module).unread_messages(1)

    @patch("email_triage.accessibility.platform")
    def test_diagnostic_checks_tree_without_selecting_rows(self, platform_module):
        platform_module.system.return_value = "Darwin"
        module, row = self.make_app()
        result = accessibility_diagnostic(module)
        self.assertTrue(result.available)
        self.assertEqual(result.code, "ready")
        self.assertFalse(row.selected)


if __name__ == "__main__":
    unittest.main()
