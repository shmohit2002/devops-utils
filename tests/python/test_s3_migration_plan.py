import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "aws"))

from s3_migration_plan import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_READY,
    EXIT_REVIEW,
    READ_ONLY_OPERATIONS,
    MigrationRequest,
    S3MigrationPlanner,
    render_json,
    render_markdown,
)


FIXED_TIME = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _control(state="absent", **summary):
    return {"state": state, "summary": summary}


def _inventory(*, versioned=False, denied_control=None, object_lock=False):
    controls = {
        name: _control()
        for name in (
            "acl",
            "cors",
            "encryption",
            "lifecycle",
            "logging",
            "notifications",
            "object_lock",
            "ownership",
            "policy_status",
            "public_access_block",
            "replication",
            "request_payer",
            "tagging",
            "versioning",
            "website",
        )
    }
    controls["versioning"] = _control(
        "present",
        status="Enabled" if versioned else None,
        mfa_delete=None,
    )
    if object_lock:
        controls["object_lock"] = _control(
            "present",
            enabled=True,
        )
    if denied_control:
        controls[denied_control] = _control("denied")

    return {
        "watermarks": {
            "started_at": "2026-07-27T12:00:00+00:00",
            "finished_at": "2026-07-27T12:00:00+00:00",
        },
        "caller": {
            "account": "123456789012",
            "partition": "aws",
            "arn_type": "assumed-role",
        },
        "aws_cli_version": "aws-cli/2.31.0",
        "observed_source_region": "us-east-1",
        "objects": {
            "current_count": 2,
            "current_bytes": 30,
            "version_count": 2 if not versioned else 4,
            "all_version_bytes": 30 if not versioned else 60,
            "delete_marker_count": 0 if not versioned else 1,
            "storage_classes": {"STANDARD": 2},
            "checksum_algorithm_counts": {"SHA256": 1},
            "key_hazard_samples": [],
        },
        "multipart_uploads": {"count": 0},
        "controls": controls,
    }


class FakeInventory:
    def __init__(self, inventory):
        self.inventory = inventory

    def collect(self, request):
        return self.inventory


def _request(*, target_bucket="example-target"):
    return MigrationRequest(
        source_bucket="example-source",
        source_region="us-east-1",
        target_bucket=target_bucket,
        target_region="us-west-2",
    )


class PlannerDecisionTests(unittest.TestCase):
    def test_simple_unversioned_bucket_is_a_datasync_candidate(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory()),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        self.assertEqual(plan["decision"]["status"], "ready")
        self.assertEqual(plan["decision"]["exit_code"], EXIT_READY)
        self.assertEqual(
            plan["decision"]["recommendation"]["engine"],
            "aws-datasync",
        )
        self.assertEqual(plan["evidence"]["objects"]["current_count"], 2)
        self.assertIn(
            "A plan never authorizes transfer or cutover.",
            plan["notice"],
        )

    def test_version_history_routes_to_replication_and_manual_review(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory(versioned=True)),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        self.assertEqual(plan["decision"]["status"], "review")
        self.assertEqual(plan["decision"]["exit_code"], EXIT_REVIEW)
        self.assertEqual(
            plan["decision"]["recommendation"]["engine"],
            "s3-replication-and-batch",
        )
        self.assertIn(
            "VERSION_HISTORY",
            {warning["code"] for warning in plan["decision"]["warnings"]},
        )

    def test_same_name_delete_and_recreate_route_has_no_bypass(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory()),
            clock=lambda: FIXED_TIME,
        ).plan(_request(target_bucket="example-source"))

        self.assertEqual(plan["decision"]["status"], "blocked")
        self.assertEqual(plan["decision"]["exit_code"], EXIT_BLOCKED)
        self.assertIn(
            "SAME_NAME_REUSE",
            {blocker["code"] for blocker in plan["decision"]["blockers"]},
        )

    def test_denied_control_and_object_lock_are_blockers(self):
        plan = S3MigrationPlanner(
            FakeInventory(
                _inventory(
                    denied_control="notifications",
                    object_lock=True,
                )
            ),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        blocker_codes = {
            blocker["code"] for blocker in plan["decision"]["blockers"]
        }
        self.assertIn("INCOMPLETE_INVENTORY", blocker_codes)
        self.assertIn("OBJECT_LOCK", blocker_codes)
        self.assertEqual(plan["decision"]["exit_code"], EXIT_BLOCKED)

    def test_json_and_markdown_are_deterministic_and_redacted(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory()),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        first_json = render_json(plan)
        second_json = render_json(plan)
        first_markdown = render_markdown(plan)
        second_markdown = render_markdown(plan)

        self.assertEqual(first_json, second_json)
        self.assertEqual(first_markdown, second_markdown)
        json.loads(first_json)
        self.assertNotIn("123456789012", first_markdown)
        self.assertIn("READY", first_markdown)

    def test_cloud_operation_allowlist_is_read_only(self):
        mutating_prefixes = (
            "create-",
            "delete-",
            "put-",
            "restore-",
            "replicate-",
            "update-",
        )
        for _, operation in READ_ONLY_OPERATIONS:
            self.assertFalse(operation.startswith(mutating_prefixes), operation)


if __name__ == "__main__":
    unittest.main()
