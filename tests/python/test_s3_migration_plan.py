import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "aws"))

from s3_migration_plan import (  # noqa: E402
    AwsCliError,
    AwsCliInventory,
    EXIT_BLOCKED,
    EXIT_READY,
    EXIT_REVIEW,
    READ_ONLY_OPERATIONS,
    MigrationRequest,
    S3MigrationPlanner,
    render_json,
    render_markdown,
    write_plan_bundle,
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
        policy = plan["least_privilege_read_policy"]
        actions = {
            action
            for statement in policy["Statement"]
            for action in statement["Action"]
        }
        self.assertIn("s3:ListBucketVersions", actions)
        self.assertIn("s3:GetBucketObjectLockConfiguration", actions)
        self.assertIn("sts:GetCallerIdentity", actions)
        self.assertFalse(
            any(
                action.split(":", 1)[1].startswith(
                    ("Create", "Delete", "Put", "Update")
                )
                for action in actions
            )
        )
        self.assertEqual(
            policy["Statement"][1]["Resource"],
            "arn:aws:s3:::example-source",
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

class FixtureAwsRunner:
    def __init__(self):
        self.calls = []

    def version(self):
        return "aws-cli/2.31.0 Python/3.13"

    def call(self, service, operation, parameters):
        self.calls.append((service, operation, dict(parameters)))
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/ops/session",
            }
        if operation == "get-bucket-location":
            return {"LocationConstraint": None}
        if operation == "get-bucket-versioning":
            return {"Status": "Enabled"}
        if operation == "get-bucket-policy":
            return {
                "Policy": json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "AWS": "arn:aws:iam::999999999999:root"
                                },
                                "Action": ["s3:GetObject"],
                                "Resource": "arn:aws:s3:::example-source/*",
                                "Condition": {"Bool": {"aws:SecureTransport": "true"}},
                            }
                        ],
                    }
                )
            }
        if operation == "get-bucket-acl":
            return {
                "Grants": [
                    {"Grantee": {"Type": "CanonicalUser"}, "Permission": "FULL_CONTROL"},
                ]
            }
        if operation == "get-bucket-logging":
            return {}
        if operation == "get-bucket-notification-configuration":
            return {"EventBridgeConfiguration": {}}
        if operation == "get-bucket-request-payment":
            return {"Payer": "BucketOwner"}
        if operation == "get-bucket-encryption":
            raise AwsCliError(operation, "AccessDenied")
        if operation == "list-object-versions":
            marker = parameters.get("key_marker")
            if marker is None:
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "normal.txt",
                    "NextVersionIdMarker": "v1",
                    "Versions": [
                        {
                            "Key": "normal.txt",
                            "VersionId": "v1",
                            "Size": 10,
                            "IsLatest": True,
                            "StorageClass": "STANDARD",
                            "ChecksumAlgorithm": ["SHA256"],
                        }
                    ],
                    "DeleteMarkers": [],
                }
            if marker == "normal.txt":
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "page-two",
                    "NextVersionIdMarker": "v2",
                    "Versions": [],
                    "DeleteMarkers": [],
                }
            return {
                "IsTruncated": False,
                "Versions": [
                    {
                        "Key": "folder/\nδ.txt",
                        "VersionId": "v3",
                        "Size": 20,
                        "IsLatest": True,
                        "StorageClass": "STANDARD_IA",
                    }
                ],
                "DeleteMarkers": [
                    {
                        "Key": "removed.txt",
                        "VersionId": "d1",
                        "IsLatest": True,
                    }
                ],
            }
        if operation == "list-multipart-uploads":
            if "key_marker" not in parameters:
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "uploading.bin",
                    "NextUploadIdMarker": "upload-1",
                    "Uploads": [{"Key": "uploading.bin", "UploadId": "upload-1"}],
                }
            return {"IsTruncated": False, "Uploads": []}

        missing_codes = {
            "get-bucket-cors": "NoSuchCORSConfiguration",
            "get-bucket-lifecycle-configuration": "NoSuchLifecycleConfiguration",
            "get-object-lock-configuration": "ObjectLockConfigurationNotFoundError",
            "get-bucket-ownership-controls": "OwnershipControlsNotFoundError",
            "get-bucket-policy-status": "NoSuchBucketPolicy",
            "get-public-access-block": "NoSuchPublicAccessBlockConfiguration",
            "get-bucket-replication": "ReplicationConfigurationNotFoundError",
            "get-bucket-tagging": "NoSuchTagSet",
            "get-bucket-website": "NoSuchWebsiteConfiguration",
        }
        raise AwsCliError(operation, missing_codes[operation])


class AwsAdapterTests(unittest.TestCase):
    def test_collects_paginated_redacted_inventory_and_distinguishes_denial(self):
        runner = FixtureAwsRunner()
        inventory = AwsCliInventory(
            runner,
            clock=lambda: FIXED_TIME,
        ).collect(_request())

        self.assertEqual(inventory["observed_source_region"], "us-east-1")
        self.assertEqual(inventory["caller"]["partition"], "aws")
        self.assertEqual(inventory["caller"]["arn_type"], "assumed-role")
        self.assertNotIn("arn", inventory["caller"])
        self.assertEqual(inventory["objects"]["current_count"], 2)
        self.assertEqual(inventory["objects"]["version_count"], 2)
        self.assertEqual(inventory["objects"]["delete_marker_count"], 1)
        self.assertEqual(inventory["objects"]["current_bytes"], 30)
        self.assertEqual(inventory["multipart_uploads"]["count"], 1)
        self.assertEqual(inventory["controls"]["encryption"]["state"], "denied")
        self.assertEqual(inventory["controls"]["cors"]["state"], "absent")
        self.assertEqual(inventory["controls"]["versioning"]["state"], "enabled")
        self.assertEqual(
            inventory["controls"]["policy"]["summary"],
            {
                "action_services": ["s3"],
                "has_conditions": True,
                "has_negative_elements": False,
                "principal_kinds": ["AWS"],
                "statement_count": 1,
            },
        )
        self.assertNotIn(
            "arn:aws:iam::999999999999",
            json.dumps(inventory["controls"]["policy"]),
        )
        self.assertEqual(
            inventory["controls"]["notifications"],
            {
                "state": "present",
                "summary": {
                    "configuration_counts": {
                        "EventBridgeConfiguration": 1,
                    }
                },
            },
        )
        self.assertEqual(
            inventory["objects"]["key_hazard_samples"][0]["key"],
            "folder/\nδ.txt",
        )

        version_calls = [
            parameters
            for service, operation, parameters in runner.calls
            if operation == "list-object-versions"
        ]
        self.assertEqual(len(version_calls), 3)
        self.assertEqual(version_calls[1]["key_marker"], "normal.txt")
        self.assertEqual(version_calls[2]["key_marker"], "page-two")
        self.assertTrue(
            all(
                (service, operation) in READ_ONLY_OPERATIONS
                for service, operation, _ in runner.calls
            )
        )

    def test_unknown_control_fields_block_instead_of_disappearing(self):
        class UnknownFieldRunner(FixtureAwsRunner):
            def call(self, service, operation, parameters):
                if operation == "get-bucket-tagging":
                    self.calls.append((service, operation, dict(parameters)))
                    return {"TagSet": [], "FutureBucketSetting": {"Enabled": True}}
                return super().call(service, operation, parameters)

        inventory = AwsCliInventory(
            UnknownFieldRunner(),
            clock=lambda: FIXED_TIME,
        ).collect(_request())
        plan = S3MigrationPlanner(
            FakeInventory(inventory),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        self.assertEqual(
            inventory["controls"]["tagging"]["summary"]["unclassified_fields"],
            ["FutureBucketSetting"],
        )
        self.assertIn(
            "UNKNOWN_CONTROL_FIELDS",
            {item["code"] for item in plan["decision"]["blockers"]},
        )

    def test_plan_bundle_requires_dot_prefixed_private_directory(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory()),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        with tempfile.TemporaryDirectory() as root:
            private_dir = Path(root) / ".s3-plan"
            json_path, markdown_path = write_plan_bundle(private_dir, plan)
            self.assertEqual(json.loads(json_path.read_text())["schema_version"], 1)
            self.assertIn("READY", markdown_path.read_text())

            with self.assertRaises(ValueError):
                write_plan_bundle(Path(root) / "public-plan", plan)


if __name__ == "__main__":
    unittest.main()
