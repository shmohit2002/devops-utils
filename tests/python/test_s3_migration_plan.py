import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
    SubprocessAwsRunner,
    main,
    render_json,
    render_markdown,
    write_plan_bundle,
)


FIXED_TIME = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _control(state="absent", **summary):
    return {"state": state, "summary": summary}


def _inventory(
    *,
    versioned=False,
    denied_control=None,
    object_lock=False,
    multipart_uploads=0,
):
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
        "multipart_uploads": {"count": multipart_uploads},
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
        self.assertEqual(
            plan["evidence"]["source_bucket_arn"],
            "arn:aws:s3:::example-source",
        )
        self.assertEqual(
            plan["expires_at"],
            "2026-07-28T12:00:00+00:00",
        )
        ranked = plan["decision"]["ranked_recommendations"]
        self.assertEqual(
            [item["rank"] for item in ranked],
            list(range(1, len(ranked) + 1)),
        )
        self.assertEqual(ranked[0], {
            **plan["decision"]["recommendation"],
            "rank": 1,
        })
        self.assertEqual(ranked[2]["engine"], "rclone-check")
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
        self.assertEqual(
            plan["decision"]["ranked_recommendations"][0]["engine"],
            "s3-replication-and-batch",
        )
        self.assertIn(
            "VERSION_HISTORY",
            {warning["code"] for warning in plan["decision"]["warnings"]},
        )

    def test_active_writes_route_to_replication_even_without_version_history(self):
        plan = S3MigrationPlanner(
            FakeInventory(_inventory(multipart_uploads=2)),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        self.assertEqual(plan["decision"]["status"], "review")
        self.assertEqual(
            plan["decision"]["recommendation"]["engine"],
            "s3-replication-and-batch",
        )
        self.assertIn(
            "ACTIVE_MULTIPART_UPLOADS",
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

    def test_mfa_delete_requires_manual_architecture(self):
        inventory = _inventory()
        inventory["controls"]["versioning"]["summary"]["mfa_delete"] = "Enabled"

        plan = S3MigrationPlanner(
            FakeInventory(inventory),
            clock=lambda: FIXED_TIME,
        ).plan(_request())

        self.assertIn(
            "MFA_DELETE",
            {item["code"] for item in plan["decision"]["blockers"]},
        )

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
        self.throttled = False

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
                if not self.throttled:
                    self.throttled = True
                    raise AwsCliError(operation, "SlowDown")
                keys = [
                    ".leading",
                    "trailing.",
                    "double__name",
                    "folder/",
                    "tab\tname",
                    "x" * 901,
                    *[f"bulk/{index:04d}" for index in range(994)],
                ]
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "bulk/0993",
                    "NextVersionIdMarker": "v1",
                    "Versions": [
                        {
                            "Key": key,
                            "VersionId": f"v{index}",
                            "Size": 0 if key == "folder/" else 1,
                            "IsLatest": True,
                            "StorageClass": "STANDARD",
                            "ChecksumAlgorithm": ["SHA256"],
                        }
                        for index, key in enumerate(keys)
                    ],
                    "DeleteMarkers": [],
                }
            if marker == "bulk/0993":
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "marker-page",
                    "NextVersionIdMarker": "v2",
                    "Versions": [],
                    "DeleteMarkers": [],
                }
            if marker == "marker-page":
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "page-two",
                    "NextVersionIdMarker": "d999",
                    "Versions": [],
                    "DeleteMarkers": [
                        {
                            "Key": f"removed/{index:04d}.txt",
                            "VersionId": f"d{index}",
                            "IsLatest": True,
                        }
                        for index in range(1000)
                    ],
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
        retry_delays = []
        inventory = AwsCliInventory(
            runner,
            clock=lambda: FIXED_TIME,
            sleep=retry_delays.append,
        ).collect(_request())

        self.assertEqual(inventory["observed_source_region"], "us-east-1")
        self.assertEqual(inventory["caller"]["partition"], "aws")
        self.assertEqual(inventory["caller"]["arn_type"], "assumed-role")
        self.assertNotIn("arn", inventory["caller"])
        self.assertEqual(inventory["objects"]["current_count"], 1001)
        self.assertEqual(inventory["objects"]["version_count"], 1001)
        self.assertEqual(inventory["objects"]["delete_marker_count"], 1001)
        self.assertEqual(inventory["objects"]["current_bytes"], 1019)
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
        hazards_by_key = {
            item["key"]: set(item["hazards"])
            for item in inventory["objects"]["key_hazard_samples"]
        }
        self.assertEqual(
            hazards_by_key["folder/\nδ.txt"],
            {"control-character", "unicode"},
        )
        self.assertIn("dot-segment", hazards_by_key[".leading"])
        self.assertIn("dot-segment", hazards_by_key["trailing."])
        self.assertIn("repeated-double-underscore", hazards_by_key["double__name"])
        self.assertIn("zero-byte-slash-marker", hazards_by_key["folder/"])
        self.assertIn("control-character", hazards_by_key["tab\tname"])
        self.assertIn("long-key", hazards_by_key["x" * 901])

        version_calls = [
            parameters
            for service, operation, parameters in runner.calls
            if operation == "list-object-versions"
        ]
        self.assertEqual(len(version_calls), 5)
        self.assertNotIn("key_marker", version_calls[0])
        self.assertNotIn("key_marker", version_calls[1])
        self.assertEqual(version_calls[2]["key_marker"], "bulk/0993")
        self.assertEqual(version_calls[3]["key_marker"], "marker-page")
        self.assertEqual(version_calls[4]["key_marker"], "page-two")
        self.assertEqual(retry_delays, [0.25])
        self.assertEqual(
            inventory["request_summary"]["operation_counts"][
                "s3api:list-object-versions"
            ],
            5,
        )
        self.assertEqual(
            inventory["request_summary"]["aws_api_calls"],
            len(runner.calls),
        )
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
            sleep=lambda _: None,
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

    def test_versioning_disabled_suspended_and_denied_are_distinct(self):
        class VersionStateRunner(FixtureAwsRunner):
            def __init__(self, response):
                super().__init__()
                self.response = response

            def call(self, service, operation, parameters):
                if operation == "get-bucket-versioning":
                    self.calls.append((service, operation, dict(parameters)))
                    if isinstance(self.response, Exception):
                        raise self.response
                    return self.response
                return super().call(service, operation, parameters)

        cases = (
            ({}, "disabled"),
            ({"Status": "Suspended"}, "suspended"),
            (AwsCliError("get-bucket-versioning", "AccessDenied"), "denied"),
        )
        for response, expected_state in cases:
            with self.subTest(expected_state=expected_state):
                inventory = AwsCliInventory(
                    VersionStateRunner(response),
                    clock=lambda: FIXED_TIME,
                    sleep=lambda _: None,
                ).collect(_request())
                self.assertEqual(
                    inventory["controls"]["versioning"]["state"],
                    expected_state,
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


class CliBoundaryTests(unittest.TestCase):
    def test_subprocess_runner_preserves_each_argument_without_a_shell(self):
        response = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"LocationConstraint": null}',
            stderr="",
        )
        with mock.patch(
            "s3_migration_plan.subprocess.run",
            return_value=response,
        ) as run:
            result = SubprocessAwsRunner().call(
                "s3api",
                "get-bucket-location",
                {
                    "bucket": "literal value\twith space",
                    "region": "us-east-1",
                },
            )

        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(
            command[command.index("--bucket") + 1],
            "literal value\twith space",
        )
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertIsNone(result["LocationConstraint"])

    def test_provider_stderr_is_not_retained_in_failure(self):
        response = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=(
                "SECRET-VALUE An error occurred (AccessDenied) when calling "
                "GetBucketLocation"
            ),
        )
        with mock.patch(
            "s3_migration_plan.subprocess.run",
            return_value=response,
        ):
            with self.assertRaises(AwsCliError) as caught:
                SubprocessAwsRunner().call(
                    "s3api",
                    "get-bucket-location",
                    {"bucket": "example", "region": "us-east-1"},
                )

        self.assertEqual(caught.exception.code, "AccessDenied")
        self.assertNotIn("SECRET-VALUE", str(caught.exception))

    def test_retry_budget_is_bounded_when_throttling_never_recovers(self):
        class AlwaysSlowRunner:
            def __init__(self):
                self.calls = 0

            def version(self):
                return "aws-cli/test"

            def call(self, service, operation, parameters):
                self.calls += 1
                raise AwsCliError(operation, "SlowDown")

        runner = AlwaysSlowRunner()
        delays = []
        with self.assertRaises(AwsCliError):
            AwsCliInventory(
                runner,
                clock=lambda: FIXED_TIME,
                max_attempts=3,
                sleep=delays.append,
            ).collect(_request())

        self.assertEqual(runner.calls, 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_main_returns_decision_codes_and_writes_private_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            ready_dir = Path(root) / ".ready"
            blocked_dir = Path(root) / ".blocked"
            with mock.patch(
                "s3_migration_plan.AwsCliInventory.collect",
                return_value=_inventory(),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    ready_code = main([
                        "plan",
                        "--source-bucket",
                        "example-source",
                        "--source-region",
                        "us-east-1",
                        "--target-bucket",
                        "example-target",
                        "--target-region",
                        "us-west-2",
                        "--output-dir",
                        str(ready_dir),
                    ])
                    blocked_code = main([
                        "plan",
                        "--source-bucket",
                        "example-source",
                        "--source-region",
                        "us-east-1",
                        "--target-bucket",
                        "example-source",
                        "--target-region",
                        "us-west-2",
                        "--output-dir",
                        str(blocked_dir),
                    ])

            self.assertEqual(ready_code, EXIT_READY)
            self.assertEqual(blocked_code, EXIT_BLOCKED)
            self.assertTrue((ready_dir / "plan.json").is_file())
            self.assertTrue((blocked_dir / "plan.md").is_file())

    def test_main_returns_tool_error_without_echoing_provider_stderr(self):
        private_output = Path(".unused-private-output")
        with mock.patch(
            "s3_migration_plan.AwsCliInventory.collect",
            side_effect=AwsCliError("get-bucket-location", "AccessDenied"),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([
                    "plan",
                    "--source-bucket",
                    "example-source",
                    "--source-region",
                    "us-east-1",
                    "--target-bucket",
                    "example-target",
                    "--target-region",
                    "us-west-2",
                    "--output-dir",
                    str(private_output),
                ])

        self.assertEqual(code, 1)
        self.assertEqual(
            stderr.getvalue().strip(),
            "error: get-bucket-location failed (AccessDenied)",
        )
        self.assertFalse(private_output.exists())


if __name__ == "__main__":
    unittest.main()
