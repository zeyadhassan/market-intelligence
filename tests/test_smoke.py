from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

from fi_intel.cli import main


class ScaffoldSmokeTest(unittest.TestCase):
    def test_status_command_reports_ready(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["status"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "name": "fi-intel",
                "stage": "scaffold",
                "status": "ready",
                "version": "0.1.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
