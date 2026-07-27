import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class LegacyRetirementTests(unittest.TestCase):
    def test_same_name_script_has_no_mutating_commands(self):
        legacy_script = (
            REPO_ROOT / "aws" / "s3-region-migrate.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("RETIRED", legacy_script)
        for command in (
            "aws s3 rm",
            "create-bucket",
            "delete-bucket",
            "delete-objects",
            "put-bucket",
            "put-object",
            "s3 sync",
        ):
            self.assertNotIn(command, legacy_script)


if __name__ == "__main__":
    unittest.main()
