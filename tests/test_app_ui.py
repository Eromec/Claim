from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class AppUiTests(unittest.TestCase):
    def test_model_picker_updates_the_analysis_action(self) -> None:
        with patch.dict(os.environ, {"CLAIMTRACE_DEFAULT_MODEL": ""}):
            app = AppTest.from_file("app.py").run(timeout=20)

            self.assertFalse(app.exception)
            self.assertEqual(
                app.selectbox[0].options,
                ["GPT-5.6 Sol", "GPT-5.6 Terra", "GPT-5.6 Luna"],
            )
            self.assertEqual(app.selectbox[0].value, "gpt-5.6-sol")

            app.selectbox[0].select("gpt-5.6-terra").run(timeout=20)

            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox[0].value, "gpt-5.6-terra")
            self.assertIn(
                "Review with GPT-5.6 Terra",
                [button.label for button in app.button],
            )

    def test_sample_report_labels_nearby_context_as_orientation_only(self) -> None:
        app = AppTest.from_file("app.py").run(timeout=20)
        button_labels = [button.label for button in app.button]
        sample_button = app.button[button_labels.index("Try the 60-second evidence demo")]

        sample_button.click().run(timeout=20)

        captions = [caption.value for caption in app.caption]
        self.assertFalse(app.exception)
        self.assertIn("Immediately before · Results · p004-b001", captions)
        self.assertIn("Immediately after · Results · p004-b003", captions)
        self.assertIn(
            "Nearby text is shown for reading context. It is not counted as linked "
            "evidence unless it appears separately in the evidence list.",
            captions,
        )


if __name__ == "__main__":
    unittest.main()
