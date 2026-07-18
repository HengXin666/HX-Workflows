from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import send_tg_report


class MailConfigurationTests(unittest.TestCase):
    def test_missing_required_configuration_disables_delivery(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(send_tg_report.load_mail_config())

    def test_aliases_and_recipient_separators_are_supported(self) -> None:
        env = {
            "MAIL_HOST": "smtp.example.test",
            "MAIL_USERNAME": "sender@example.test",
            "MAIL_PASSWORD": "secret",
            "EMAIL_TO": "a@example.test; b@example.test\nc@example.test",
            "MAIL_PORT": "587",
        }
        with patch.dict(os.environ, env, clear=True):
            config = send_tg_report.load_mail_config()
        assert config is not None
        self.assertEqual(config.mail_to, ["a@example.test", "b@example.test", "c@example.test"])
        self.assertFalse(config.use_ssl)
        self.assertTrue(config.starttls)


class ReportRenderingTests(unittest.TestCase):
    def test_untrusted_report_content_is_escaped_and_text_emotes_are_removed(self) -> None:
        report = {
            "tasks": [
                {
                    "id": '<script>alert("task")</script>',
                    "workflow": {"nodes": [{"id": "node", "label": "签到 :)"}]},
                    "accounts": [
                        {
                            "account_index": 1,
                            "status": "success",
                            "matched": True,
                            "messages": ['<img src=x onerror="alert(1)"> OK'],
                            "node_logs": {"node": ["done"]},
                        }
                    ],
                }
            ]
        }
        rendered = send_tg_report.build_html_report(report)
        self.assertNotIn("<script>alert", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x", rendered)
        self.assertNotIn(":)", rendered)


class SmtpAdapterTests(unittest.TestCase):
    def test_starttls_login_and_delivery_use_configured_values(self) -> None:
        env = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "secret",
            "SMTP_TO": "receiver@example.test",
            "SMTP_USE_SSL": "false",
            "SMTP_STARTTLS": "true",
        }
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch.dict(os.environ, env, clear=True), patch.object(send_tg_report.smtplib, "SMTP", return_value=smtp) as smtp_cls:
            send_tg_report.send_html_mail("subject", "<strong>body</strong>")
        smtp_cls.assert_called_once_with("smtp.example.test", 587, timeout=30)
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with("sender@example.test", "secret")
        smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
