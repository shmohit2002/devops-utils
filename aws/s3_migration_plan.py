#!/usr/bin/env python3
"""Read-only S3 migration inventory, decision, and evidence plan.

The module's external interface is ``S3MigrationPlanner.plan(request)``.
Cloud inventory is injected at the internal seam so tests and the AWS CLI
adapter exercise the same decision path.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


SCHEMA_VERSION = 1
EXIT_READY = 0
EXIT_TOOL_ERROR = 1
EXIT_REVIEW = 2
EXIT_BLOCKED = 3

# The production adapter rejects every AWS operation not present here.
READ_ONLY_OPERATIONS = frozenset(
    {
        ("sts", "get-caller-identity"),
        ("s3api", "get-bucket-location"),
        ("s3api", "get-bucket-acl"),
        ("s3api", "get-bucket-cors"),
        ("s3api", "get-bucket-encryption"),
        ("s3api", "get-bucket-lifecycle-configuration"),
        ("s3api", "get-bucket-logging"),
        ("s3api", "get-bucket-notification-configuration"),
        ("s3api", "get-object-lock-configuration"),
        ("s3api", "get-bucket-ownership-controls"),
        ("s3api", "get-bucket-policy-status"),
        ("s3api", "get-public-access-block"),
        ("s3api", "get-bucket-replication"),
        ("s3api", "get-bucket-request-payment"),
        ("s3api", "get-bucket-tagging"),
        ("s3api", "get-bucket-versioning"),
        ("s3api", "get-bucket-website"),
        ("s3api", "list-object-versions"),
        ("s3api", "list-multipart-uploads"),
    }
)


@dataclass(frozen=True)
class MigrationRequest:
    source_bucket: str
    source_region: str
    target_bucket: str
    target_region: str


class InventoryReader(Protocol):
    def collect(self, request: MigrationRequest) -> dict[str, Any]:
        """Return a redacted, structured, point-in-time inventory."""


class AwsCliError(RuntimeError):
    """Sanitized AWS CLI failure; provider stderr is deliberately not retained."""

    def __init__(self, operation: str, code: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(f"{operation} failed ({code})")


class InventoryCollectionError(RuntimeError):
    """Inventory is structurally incomplete and cannot produce a safe plan."""


class AwsRunner(Protocol):
    def version(self) -> str:
        """Return the AWS CLI version without credentials or environment data."""

    def call(
        self,
        service: str,
        operation: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]:
        """Run one allowlisted JSON-returning operation."""


class SubprocessAwsRunner:
    """Argument-array AWS CLI runner with bounded execution and safe failures."""

    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self._timeout_seconds = timeout_seconds

    def version(self) -> str:
        try:
            result = subprocess.run(
                ["aws", "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as error:
            raise AwsCliError("--version", "AwsCliNotFound") from error
        if result.returncode:
            raise AwsCliError("--version", "AwsCliUnavailable")
        return (result.stdout or result.stderr).strip()

    def call(
        self,
        service: str,
        operation: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]:
        command = ["aws", service, operation]
        for name in sorted(parameters):
            command.extend([f"--{name.replace('_', '-')}", parameters[name]])
        command.extend(["--output", "json", "--no-cli-pager", "--no-paginate"])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as error:
            raise AwsCliError(operation, "AwsCliNotFound") from error

        if result.returncode:
            match = re.search(r"\(([^()\s]+)\)", result.stderr)
            code = match.group(1) if match else "AwsCliFailure"
            raise AwsCliError(operation, code)
        try:
            parsed = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise AwsCliError(operation, "InvalidJsonResponse") from error
        if not isinstance(parsed, dict):
            raise AwsCliError(operation, "UnexpectedJsonResponse")
        return parsed


_CONTROL_OPERATIONS = {
    "acl": "get-bucket-acl",
    "cors": "get-bucket-cors",
    "encryption": "get-bucket-encryption",
    "lifecycle": "get-bucket-lifecycle-configuration",
    "logging": "get-bucket-logging",
    "notifications": "get-bucket-notification-configuration",
    "object_lock": "get-object-lock-configuration",
    "ownership": "get-bucket-ownership-controls",
    "policy_status": "get-bucket-policy-status",
    "public_access_block": "get-public-access-block",
    "replication": "get-bucket-replication",
    "request_payer": "get-bucket-request-payment",
    "tagging": "get-bucket-tagging",
    "versioning": "get-bucket-versioning",
    "website": "get-bucket-website",
}

_ABSENT_ERROR_CODES = frozenset(
    {
        "NoSuchBucketPolicy",
        "NoSuchCORSConfiguration",
        "NoSuchLifecycleConfiguration",
        "NoSuchPublicAccessBlockConfiguration",
        "NoSuchTagSet",
        "NoSuchWebsiteConfiguration",
        "ObjectLockConfigurationNotFoundError",
        "OwnershipControlsNotFoundError",
        "ReplicationConfigurationNotFoundError",
        "ServerSideEncryptionConfigurationNotFoundError",
    }
)
_DENIED_ERROR_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "AllAccessDisabled",
        "UnauthorizedOperation",
    }
)


class AwsCliInventory:
    """AWS CLI adapter that emits a redacted inventory and can only perform reads."""

    def __init__(
        self,
        runner: AwsRunner,
        *,
        clock: Callable[[], datetime] | None = None,
        key_sample_limit: int = 20,
    ) -> None:
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._key_sample_limit = key_sample_limit

    def _call(
        self,
        service: str,
        operation: str,
        **parameters: str,
    ) -> dict[str, Any]:
        if (service, operation) not in READ_ONLY_OPERATIONS:
            raise InventoryCollectionError(
                f"Refusing non-read AWS operation: {service} {operation}"
            )
        return self._runner.call(service, operation, parameters)

    def collect(self, request: MigrationRequest) -> dict[str, Any]:
        started_at = self._clock().astimezone(timezone.utc).isoformat()
        identity = self._call("sts", "get-caller-identity")
        location = self._call(
            "s3api",
            "get-bucket-location",
            bucket=request.source_bucket,
            region=request.source_region,
        )
        observed_region = _normalize_region(location.get("LocationConstraint"))

        controls = {
            name: self._read_control(
                name,
                operation,
                request,
            )
            for name, operation in _CONTROL_OPERATIONS.items()
        }
        objects = self._read_objects(request)
        multipart_uploads = self._read_multipart_uploads(request)
        finished_at = self._clock().astimezone(timezone.utc).isoformat()

        return {
            "watermarks": {
                "started_at": started_at,
                "finished_at": finished_at,
            },
            "caller": _summarize_identity(identity),
            "aws_cli_version": self._runner.version(),
            "observed_source_region": observed_region,
            "objects": objects,
            "multipart_uploads": multipart_uploads,
            "controls": controls,
        }

    def _read_control(
        self,
        name: str,
        operation: str,
        request: MigrationRequest,
    ) -> dict[str, Any]:
        try:
            value = self._call(
                "s3api",
                operation,
                bucket=request.source_bucket,
                region=request.source_region,
            )
        except AwsCliError as error:
            if error.code in _ABSENT_ERROR_CODES:
                return {"state": "absent", "summary": {}}
            if error.code in _DENIED_ERROR_CODES:
                return {"state": "denied", "summary": {}}
            return {"state": "error", "summary": {"error_code": error.code}}

        configured, summary = _summarize_control(name, value)
        return {
            "state": "present" if configured else "absent",
            "summary": summary,
        }

    def _read_objects(self, request: MigrationRequest) -> dict[str, Any]:
        versions: list[dict[str, Any]] = []
        delete_markers: list[dict[str, Any]] = []
        parameters = {
            "bucket": request.source_bucket,
            "region": request.source_region,
        }
        seen_tokens: set[tuple[str, str]] = set()

        while True:
            page = self._call(
                "s3api",
                "list-object-versions",
                **parameters,
            )
            versions.extend(page.get("Versions") or [])
            delete_markers.extend(page.get("DeleteMarkers") or [])
            if not page.get("IsTruncated"):
                break

            key_marker = page.get("NextKeyMarker")
            version_marker = page.get("NextVersionIdMarker") or ""
            if not key_marker:
                raise InventoryCollectionError(
                    "Truncated version page omitted NextKeyMarker"
                )
            token = (str(key_marker), str(version_marker))
            if token in seen_tokens:
                raise InventoryCollectionError(
                    "Version pagination repeated a continuation token"
                )
            seen_tokens.add(token)
            parameters["key_marker"] = token[0]
            if token[1]:
                parameters["version_id_marker"] = token[1]
            else:
                parameters.pop("version_id_marker", None)

        current_versions = [
            item for item in versions if bool(item.get("IsLatest"))
        ]
        storage_classes = Counter(
            str(item.get("StorageClass") or "STANDARD")
            for item in versions
        )
        checksum_algorithms = Counter(
            str(algorithm)
            for item in versions
            for algorithm in (item.get("ChecksumAlgorithm") or [])
        )
        key_sizes: dict[str, int] = {}
        for item in versions:
            key_sizes[str(item.get("Key") or "")] = int(item.get("Size") or 0)
        for marker in delete_markers:
            key_sizes.setdefault(str(marker.get("Key") or ""), 0)
        hazards = [
            {"key": key, "hazards": _key_hazards(key, size)}
            for key, size in sorted(key_sizes.items())
            if _key_hazards(key, size)
        ][: self._key_sample_limit]

        return {
            "current_count": len(current_versions),
            "current_bytes": sum(
                int(item.get("Size") or 0) for item in current_versions
            ),
            "version_count": len(versions),
            "all_version_bytes": sum(
                int(item.get("Size") or 0) for item in versions
            ),
            "delete_marker_count": len(delete_markers),
            "storage_classes": dict(sorted(storage_classes.items())),
            "checksum_algorithm_counts": dict(
                sorted(checksum_algorithms.items())
            ),
            "key_hazard_samples": hazards,
        }

    def _read_multipart_uploads(
        self,
        request: MigrationRequest,
    ) -> dict[str, int]:
        count = 0
        parameters = {
            "bucket": request.source_bucket,
            "region": request.source_region,
        }
        seen_tokens: set[tuple[str, str]] = set()

        while True:
            page = self._call(
                "s3api",
                "list-multipart-uploads",
                **parameters,
            )
            count += len(page.get("Uploads") or [])
            if not page.get("IsTruncated"):
                break

            key_marker = page.get("NextKeyMarker")
            upload_marker = page.get("NextUploadIdMarker")
            if not key_marker or not upload_marker:
                raise InventoryCollectionError(
                    "Truncated multipart page omitted continuation markers"
                )
            token = (str(key_marker), str(upload_marker))
            if token in seen_tokens:
                raise InventoryCollectionError(
                    "Multipart pagination repeated a continuation token"
                )
            seen_tokens.add(token)
            parameters["key_marker"] = token[0]
            parameters["upload_id_marker"] = token[1]

        return {"count": count}


def _normalize_region(value: Any) -> str:
    if value in {None, ""}:
        return "us-east-1"
    if value == "EU":
        return "eu-west-1"
    return str(value)


def _summarize_identity(identity: dict[str, Any]) -> dict[str, str]:
    arn_parts = str(identity.get("Arn") or "").split(":", 5)
    resource = arn_parts[5] if len(arn_parts) == 6 else "unknown"
    return {
        "account": str(identity.get("Account") or "unknown"),
        "partition": arn_parts[1] if len(arn_parts) == 6 else "unknown",
        "arn_type": resource.split("/", 1)[0] or "unknown",
    }


def _summarize_control(
    name: str,
    value: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if name == "versioning":
        return True, {
            "status": value.get("Status"),
            "mfa_delete": value.get("MFADelete"),
        }
    if name == "acl":
        grants = value.get("Grants") or []
        return True, {
            "grant_count": len(grants),
            "grantee_types": sorted(
                {
                    str(grant.get("Grantee", {}).get("Type") or "unknown")
                    for grant in grants
                }
            ),
            "permissions": sorted(
                {
                    str(grant.get("Permission") or "unknown")
                    for grant in grants
                }
            ),
        }
    if name == "cors":
        rules = value.get("CORSRules") or []
        return bool(rules), {"rule_count": len(rules)}
    if name == "encryption":
        rules = value.get("ServerSideEncryptionConfiguration", {}).get(
            "Rules", []
        )
        defaults = [
            rule.get("ApplyServerSideEncryptionByDefault", {})
            for rule in rules
        ]
        return bool(rules), {
            "algorithms": sorted(
                {
                    str(default.get("SSEAlgorithm"))
                    for default in defaults
                    if default.get("SSEAlgorithm")
                }
            ),
            "kms_key_configured": any(
                bool(default.get("KMSMasterKeyID")) for default in defaults
            ),
            "bucket_key_enabled": any(
                bool(rule.get("BucketKeyEnabled")) for rule in rules
            ),
        }
    if name == "lifecycle":
        rules = value.get("Rules") or []
        return bool(rules), {
            "rule_count": len(rules),
            "statuses": sorted(
                {str(rule.get("Status") or "unknown") for rule in rules}
            ),
        }
    if name == "logging":
        enabled = bool(value.get("LoggingEnabled"))
        return enabled, {"enabled": enabled}
    if name == "notifications":
        counts = {
            key: len(value.get(key) or [])
            for key in (
                "LambdaFunctionConfigurations",
                "QueueConfigurations",
                "TopicConfigurations",
            )
            if value.get(key)
        }
        if "EventBridgeConfiguration" in value:
            counts["EventBridgeConfiguration"] = 1
        return bool(counts), {"configuration_counts": counts}
    if name == "object_lock":
        enabled = value.get("ObjectLockEnabled") == "Enabled"
        default_rule = value.get("Rule", {}).get(
            "DefaultRetention", {}
        )
        return enabled, {
            "enabled": enabled,
            "default_retention": {
                key.lower(): default_rule[key]
                for key in ("Mode", "Days", "Years")
                if key in default_rule
            },
        }
    if name == "ownership":
        rules = value.get("OwnershipControls", {}).get("Rules", [])
        return bool(rules), {
            "object_ownership": sorted(
                {
                    str(rule.get("ObjectOwnership"))
                    for rule in rules
                    if rule.get("ObjectOwnership")
                }
            )
        }
    if name == "policy_status":
        status = value.get("PolicyStatus") or {}
        return bool(status), {"is_public": bool(status.get("IsPublic"))}
    if name == "public_access_block":
        configuration = value.get("PublicAccessBlockConfiguration") or {}
        return bool(configuration), {
            key: bool(configuration.get(key))
            for key in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        }
    if name == "replication":
        rules = value.get("ReplicationConfiguration", {}).get("Rules", [])
        return bool(rules), {
            "rule_count": len(rules),
            "statuses": sorted(
                {str(rule.get("Status") or "unknown") for rule in rules}
            ),
        }
    if name == "request_payer":
        payer = value.get("Payer")
        configured = payer == "Requester"
        return configured, {"payer": payer} if configured else {}
    if name == "tagging":
        tags = value.get("TagSet") or []
        return bool(tags), {"tag_count": len(tags)}
    if name == "website":
        configured = bool(value)
        return configured, {
            "redirect_all": bool(value.get("RedirectAllRequestsTo")),
            "routing_rule_count": len(value.get("RoutingRules") or []),
        }
    raise InventoryCollectionError(f"Unknown control summarizer: {name}")


def _key_hazards(key: str, size: int) -> list[str]:
    hazards = []
    if any(ord(character) < 32 or ord(character) == 127 for character in key):
        hazards.append("control-character")
    if any(ord(character) > 127 for character in key):
        hazards.append("unicode")
    if any(
        segment.startswith(".") or segment.endswith(".")
        for segment in key.split("/")
        if segment
    ):
        hazards.append("dot-segment")
    if "__" in key:
        hazards.append("repeated-double-underscore")
    if len(key.encode("utf-8")) > 900:
        hazards.append("long-key")
    if key.endswith("/") and size == 0:
        hazards.append("zero-byte-slash-marker")
    return hazards


def _finding(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


class S3MigrationPlanner:
    """Turn one source inventory into a conservative migration decision."""

    def __init__(
        self,
        inventory_reader: InventoryReader,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._inventory_reader = inventory_reader
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(self, request: MigrationRequest) -> dict[str, Any]:
        evidence = self._inventory_reader.collect(request)
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        actions: list[str] = [
            "Create or validate the parallel target without changing the source.",
            "Run a catch-up transfer, then verify content with checksums or an "
            "engine task report.",
            "Update application, IAM, DNS, and event references before a "
            "separately approved cutover.",
        ]

        if request.source_bucket == request.target_bucket:
            blockers.append(
                _finding(
                    "SAME_NAME_REUSE",
                    "Deleting and recreating the same global bucket name is "
                    "refused; release can take 48–72 hours and another account "
                    "can claim the name. Use a parallel target name.",
                )
            )
        if request.source_region == request.target_region:
            blockers.append(
                _finding(
                    "SAME_REGION",
                    "Source and target Regions are identical; this is not a "
                    "cross-Region migration plan.",
                )
            )

        observed_region = evidence.get("observed_source_region")
        if observed_region and observed_region != request.source_region:
            blockers.append(
                _finding(
                    "SOURCE_REGION_MISMATCH",
                    f"AWS reports source Region {observed_region}, not the "
                    f"requested {request.source_region}.",
                )
            )

        controls = evidence.get("controls", {})
        incomplete = sorted(
            name
            for name, value in controls.items()
            if value.get("state") in {"denied", "error", "unknown"}
        )
        if incomplete:
            blockers.append(
                _finding(
                    "INCOMPLETE_INVENTORY",
                    "Required controls could not be classified: "
                    + ", ".join(incomplete)
                    + ". AccessDenied is never treated as not configured.",
                )
            )

        object_lock = controls.get("object_lock", {})
        if (
            object_lock.get("state") == "present"
            and object_lock.get("summary", {}).get("enabled")
        ):
            blockers.append(
                _finding(
                    "OBJECT_LOCK",
                    "Object Lock retention semantics require a reviewed "
                    "architecture and cannot be delegated to a generic copy.",
                )
            )

        versioning = controls.get("versioning", {}).get("summary", {})
        if versioning.get("mfa_delete") == "Enabled":
            blockers.append(
                _finding(
                    "MFA_DELETE",
                    "MFA Delete is enabled and requires a manual security and "
                    "recovery design.",
                )
            )

        objects = evidence.get("objects", {})
        has_history = (
            versioning.get("status") in {"Enabled", "Suspended"}
            or objects.get("version_count", 0) > objects.get("current_count", 0)
            or objects.get("delete_marker_count", 0) > 0
        )
        if has_history:
            warnings.append(
                _finding(
                    "VERSION_HISTORY",
                    "Historical versions or delete markers exist. DataSync does "
                    "not preserve that history; evaluate S3 replication plus "
                    "Batch Replication.",
                )
            )

        multipart_count = evidence.get("multipart_uploads", {}).get("count", 0)
        if multipart_count:
            warnings.append(
                _finding(
                    "ACTIVE_MULTIPART_UPLOADS",
                    f"{multipart_count} multipart upload(s) were active at the "
                    "inventory watermark; quiesce or reconcile writes.",
                )
            )

        hazard_samples = objects.get("key_hazard_samples", [])
        if hazard_samples:
            warnings.append(
                _finding(
                    "KEY_PORTABILITY",
                    f"{len(hazard_samples)} sampled key(s) have portability "
                    "hazards. Preserve exact API keys; do not map them through "
                    "local filenames.",
                )
            )

        manual_controls = sorted(
            name
            for name, value in controls.items()
            if name not in {"object_lock", "versioning"}
            and value.get("state") == "present"
        )
        if manual_controls:
            actions.append(
                "Recreate and independently verify bucket controls: "
                + ", ".join(manual_controls)
                + "."
            )

        encryption = controls.get("encryption", {}).get("summary", {})
        if encryption.get("kms_key_configured"):
            warnings.append(
                _finding(
                    "KMS_DEPENDENCY",
                    "KMS encryption is configured. Validate target-Region keys, "
                    "grants, key policy, and transfer-engine permissions.",
                )
            )

        if blockers:
            status = "blocked"
            exit_code = EXIT_BLOCKED
            engine = "manual-architecture"
            reason = "One or more preservation or inventory blockers exist."
        elif has_history:
            status = "review"
            exit_code = EXIT_REVIEW
            engine = "s3-replication-and-batch"
            reason = (
                "Version-aware native replication is the leading candidate; "
                "bucket controls and cutover still require review."
            )
        else:
            status = "review" if warnings else "ready"
            exit_code = EXIT_REVIEW if warnings else EXIT_READY
            engine = "aws-datasync"
            reason = (
                "The observed current-object shape is compatible with a "
                "managed, incremental transfer candidate."
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self._clock().astimezone(timezone.utc).isoformat(),
            "request": asdict(request),
            "decision": {
                "status": status,
                "exit_code": exit_code,
                "recommendation": {
                    "engine": engine,
                    "reason": reason,
                },
                "blockers": blockers,
                "warnings": warnings,
                "required_actions": actions,
            },
            "evidence": evidence,
            "notice": (
                "A plan never authorizes transfer or cutover. Inventory is "
                "point-in-time evidence and must be refreshed before approval."
            ),
        }


def render_json(plan: dict[str, Any]) -> str:
    """Render stable, reviewable JSON."""

    return json.dumps(
        plan,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_markdown(plan: dict[str, Any]) -> str:
    """Render a concise operator summary without account IDs or object keys."""

    decision = plan["decision"]
    request = plan["request"]
    objects = plan["evidence"].get("objects", {})
    lines = [
        "# S3 migration preflight",
        "",
        f"Decision: **{decision['status'].upper()}** "
        f"(exit {decision['exit_code']})",
        "",
        f"- Source: `{request['source_bucket']}` / `{request['source_region']}`",
        f"- Parallel target: `{request['target_bucket']}` / "
        f"`{request['target_region']}`",
        f"- Recommended engine: "
        f"`{decision['recommendation']['engine']}`",
        f"- Current objects: {objects.get('current_count', 0)} "
        f"({objects.get('current_bytes', 0)} bytes)",
        f"- Versions / delete markers: {objects.get('version_count', 0)} / "
        f"{objects.get('delete_marker_count', 0)}",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(
        f"- **{item['code']}** — {item['message']}"
        for item in decision["blockers"]
    )
    if not decision["blockers"]:
        lines.append("- None observed.")

    lines.extend(["", "## Warnings", ""])
    lines.extend(
        f"- **{item['code']}** — {item['message']}"
        for item in decision["warnings"]
    )
    if not decision["warnings"]:
        lines.append("- None observed.")

    lines.extend(["", "## Required actions", ""])
    lines.extend(
        f"{index}. {action}"
        for index, action in enumerate(decision["required_actions"], start=1)
    )
    lines.extend(["", f"> {plan['notice']}", ""])
    return "\n".join(lines)


def write_plan_bundle(
    output_dir: Path,
    plan: dict[str, Any],
) -> tuple[Path, Path]:
    """Write potentially sensitive evidence only under a dot-prefixed folder."""

    resolved_dir = output_dir.resolve()
    if not resolved_dir.name.startswith("."):
        raise ValueError(
            "Plan output may contain account and object-key evidence; choose a "
            "dot-prefixed directory such as .s3-migration-plan"
        )
    resolved_dir.mkdir(parents=True, exist_ok=True)
    json_path = resolved_dir / "plan.json"
    markdown_path = resolved_dir / "plan.md"
    json_path.write_text(render_json(plan), encoding="utf-8", newline="\n")
    markdown_path.write_text(
        render_markdown(plan),
        encoding="utf-8",
        newline="\n",
    )
    return json_path, markdown_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a read-only S3 cross-Region migration decision and "
            "evidence bundle."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser(
        "plan",
        help="inventory the source and recommend a safe migration path",
    )
    plan.add_argument("--source-bucket", required=True)
    plan.add_argument("--source-region", required=True)
    plan.add_argument("--target-bucket", required=True)
    plan.add_argument("--target-region", required=True)
    plan.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".s3-migration-plan"),
        help=(
            "private dot-prefixed output directory "
            "(default: .s3-migration-plan)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "plan":
        return EXIT_TOOL_ERROR

    request = MigrationRequest(
        source_bucket=args.source_bucket,
        source_region=args.source_region,
        target_bucket=args.target_bucket,
        target_region=args.target_region,
    )
    try:
        inventory = AwsCliInventory(SubprocessAwsRunner())
        result = S3MigrationPlanner(inventory).plan(request)
        json_path, markdown_path = write_plan_bundle(args.output_dir, result)
    except (AwsCliError, InventoryCollectionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR

    decision = result["decision"]
    print(
        f"S3 migration preflight: {decision['status']} "
        f"(exit {decision['exit_code']})"
    )
    print(f"JSON evidence: {json_path}")
    print(f"Review plan: {markdown_path}")
    return int(decision["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
