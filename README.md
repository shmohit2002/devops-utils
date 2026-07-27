# DevOps utilities

Small, reviewable tools for AWS, Azure, Kubernetes, testing, and web
operations. Each tool owns its prerequisites and failure modes; cloud
utilities should default to validation or read-only planning.

## S3 migration preflight

The supported S3 workflow inventories one general-purpose source bucket and
produces a migration decision before any transfer or cutover:

```bash
python3 aws/s3_migration_plan.py plan \
  --source-bucket example-source \
  --source-region us-east-1 \
  --target-bucket example-parallel-target \
  --target-region us-west-2
```

Requirements: Python 3.11+, AWS CLI v2, and read access to the source bucket.
Output is written to the Git-ignored `.s3-migration-plan/` directory because
the JSON evidence can contain an account ID and sampled object keys.

The command:

- explicitly pages through versions, delete markers, and multipart uploads;
- retries throttled reads at most three times and reports request counts;
- classifies denied, absent, and configured bucket controls separately;
- redacts policy identities while preserving its structural risk signals;
- ranks DataSync, S3 Replication + Batch Replication, and verification options;
- refuses same-name delete/recreate and Object Lock ambiguity;
- emits deterministic JSON, concise Markdown, and the exact read-only IAM
  policy used by the inventory.

Plans expire after 24 hours and should be refreshed sooner when objects or
configuration are changing.

Exit codes are `0` ready, `1` tool/auth failure, `2` operator review required,
and `3` blocked. A plan never authorizes transfer or cutover. See
[the S3 preflight runbook](docs/runbooks/s3-migration-preflight.md).

The former `aws/s3-region-migrate.sh` destructive workflow is retired and
always exits without calling AWS.

## Verification

Run the standard-library test suite without cloud credentials:

```bash
python3 -m unittest discover -s tests/python -v
python3 -m py_compile aws/s3_migration_plan.py
```

## Other utilities

| Area | Path | Status |
|---|---|---|
| Azure group export | `azure/export-az-groups.ps1` | Legacy AzureAD implementation; migrate to Microsoft Graph before use |
| Kubernetes helpers | `kubernetes/` | Review context and inputs before use |
| Load generation | `testing/stress_testing.py` | Development utility; not an SLO/load certification tool |
| Nginx error page | `web/error-page/` | Static container asset |
