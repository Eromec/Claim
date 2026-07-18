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
                "Analyze with GPT-5.6 Terra",
                [button.label for button in app.button],
            )


if __name__ == "__main__":
    unittest.main()
