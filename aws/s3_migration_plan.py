#!/usr/bin/env python3
"""Read-only S3 migration inventory, decision, and evidence plan.

The module's external interface is ``S3MigrationPlanner.plan(request)``.
Cloud inventory is injected at the internal seam so tests and the AWS CLI
adapter exercise the same decision path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
