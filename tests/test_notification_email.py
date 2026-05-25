import unittest
from unittest.mock import patch

try:
    from src.notification import NotificationService
except ModuleNotFoundError as exc:
    NotificationService = None
    MISSING_DEPENDENCY = exc.name
else:
    MISSING_DEPENDENCY = None


class FakeSMTP:
    sent_messages = []

    def __init__(self, *args, **kwargs):
        pass

    def login(self, *args, **kwargs):
        pass

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        pass


@unittest.skipIf(
    NotificationService is None,
    f"project dependency missing: {MISSING_DEPENDENCY}",
)
class NotificationEmailTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.sent_messages = []

    def test_inline_image_email_keeps_text_fallback_body(self):
        service = NotificationService()
        service._email_config = {
            "sender": "sender@qq.com",
            "sender_name": "tester",
            "password": "secret",
            "receivers": ["receiver@example.com"],
        }
        content = "# 今日报告\n\n- 买入观察\n- 风险提示"

        with patch("src.notification.smtplib.SMTP_SSL", FakeSMTP):
            sent = service._send_email_with_inline_image(
                b"not-a-real-png-but-subtype-is-explicit",
                content=content,
            )

        self.assertTrue(sent)
        self.assertEqual(len(FakeSMTP.sent_messages), 1)
        msg = FakeSMTP.sent_messages[0]

        plain_parts = []
        html_parts = []
        image_parts = []
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(part.get_payload(decode=True).decode("utf-8"))
            elif content_type == "text/html":
                html_parts.append(part.get_payload(decode=True).decode("utf-8"))
            elif content_type == "image/png":
                image_parts.append(part)

        self.assertEqual(plain_parts, [content])
        self.assertEqual(len(html_parts), 1)
        self.assertIn("如果图片未显示", html_parts[0])
        self.assertIn("今日报告", html_parts[0])
        self.assertEqual(len(image_parts), 1)


if __name__ == "__main__":
    unittest.main()
