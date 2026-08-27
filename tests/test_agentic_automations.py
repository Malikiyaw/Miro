"""Tests for the agentic-automation brain (1000x upgrade).

These tests only import the pure-Python ``agent.automation_knowledge`` module,
which has NO Discord dependency, so they run in any environment.
"""

import unittest

from agent.automation_knowledge import (
    parse_natural_language_schedule,
    render_catalog,
    TRIGGER_CATALOG,
    ACTION_SUMMARY,
    EXAMPLE_REQUESTS,
    _WEEKDAYS,
)


class TestAutomationKnowledge(unittest.TestCase):
    def test_catalog_contains_new_capabilities(self):
        catalog = render_catalog()
        self.assertIsInstance(catalog, str)
        self.assertIn("scheduled_task", catalog)
        self.assertIn("event_trigger", catalog)
        self.assertIn("member_joined", catalog)
        self.assertIn("message_contains", catalog)
        self.assertIn("reaction_added", catalog)

    def test_trigger_catalog_has_event_and_action_entries(self):
        self.assertIn("event_trigger", TRIGGER_CATALOG)
        self.assertIn("actions", TRIGGER_CATALOG["scheduled_task"])
        self.assertTrue(len(ACTION_SUMMARY) > 0)
        self.assertTrue(len(EXAMPLE_REQUESTS) > 0)

    def test_parse_daily_at(self):
        out = parse_natural_language_schedule("daily at 9am")
        self.assertIsInstance(out, dict)
        self.assertTrue("daily_at" in out or "cron" in out, msg=out)

    def test_parse_every_minutes(self):
        out = parse_natural_language_schedule("every 15 minutes")
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("every_minutes"), 15)

    def test_parse_every_hours(self):
        out = parse_natural_language_schedule("every 2 hours")
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("every_hours"), 2)

    def test_parse_reminder_relative(self):
        out = parse_natural_language_schedule("in 2 hours")
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("_reminder_seconds"), 7200)
        out2 = parse_natural_language_schedule("in 30 minutes")
        self.assertEqual(out2.get("_reminder_seconds"), 1800)

    def test_weekdays_known(self):
        for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            self.assertIn(d, _WEEKDAYS)


if __name__ == "__main__":
    unittest.main()
