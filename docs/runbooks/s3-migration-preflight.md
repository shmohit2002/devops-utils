# S3 migration preflight runbook

## Purpose and safety boundary

`aws/s3_migration_plan.py plan` answers whether the observed bucket can move
through a managed/native path and what must be reviewed first. It calls only
allowlisted `get-*` and `list-*` AWS operations. It never creates a target,
copies an object, changes configuration, deletes a source, or authorizes
cutover.

Use a different target name. AWS documents that a deleted global bucket name
can remain unavailable for 48–72 hours and may be claimed by another account.

## Before running

1. Use AWS CLI v2 and a short-lived role in the source account.
2. Grant only the bucket-read actions emitted in
   `plan.json` under `least_privilege_read_policy`. AWS’s current
   [operation/permission map](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html)
   is authoritative.
3. Choose a parallel target bucket name and a different Region.
4. Run from a protected working directory. The tool writes only to a
   dot-prefixed output directory, which may contain account and exact sampled
   key evidence.

```bash
python3 aws/s3_migration_plan.py plan \
  --source-bucket SOURCE \
  --source-region SOURCE_REGION \
  --target-bucket PARALLEL_TARGET \
  --target-region TARGET_REGION \
  --output-dir .s3-migration-plan
```

The role needs no object read, KMS decrypt, target-bucket, write, or delete
permission for this phase.

## Read the decision

- `ready` / exit `0`: observed current objects are a DataSync candidate and no
  preflight warning was found. This is permission to design a transfer, not to
  cut over.
- `review` / exit `2`: version history, active multipart work, KMS dependency,
  or key portability requires an operator decision. Versioned buckets route to
  S3 Replication plus Batch Replication because DataSync does not preserve
  historical versions.
- `blocked` / exit `3`: same-name reuse, source-Region mismatch, Object Lock,
  MFA Delete, or incomplete control access requires manual architecture.
- exit `1`: AWS CLI, authentication, response, pagination, or private-output
  setup failed; no decision is valid.

Treat `AccessDenied` as missing evidence, never as “not configured.” Resolve
the read permission and rerun. Keep the JSON with the review record; the
Markdown intentionally omits account IDs and object keys.

The JSON includes an explicit source bucket ARN, actual request counts by AWS
operation, ranked engine candidates, and a 24-hour expiry. Refresh sooner when
the bucket is active. New top-level control fields that the installed planner
does not understand block approval rather than disappearing from the summary.
Bucket policy identities/resources are not copied into the report; the summary
retains statement count, principal forms, action services, conditions, and
negative-policy elements.

## Required follow-through

1. Review every configured control listed in the evidence. Object-transfer
   engines do not reproduce all policy, lifecycle, CORS, website, logging,
   ownership, notification, replication, encryption, or tagging semantics.
2. Select and design the engine. For DataSync, use checksum verification and a
   [task report](https://docs.aws.amazon.com/datasync/latest/userguide/task-reports.html).
   DataSync does not preserve historical versions, ACLs, Last-Modified
   timestamps, or every system metadata field. For historical versions,
   evaluate CRR plus Batch Replication.
3. Run an initial transfer while the source remains authoritative, then a
   quiesced catch-up. Re-inventory if writes or configuration changed.
4. Verify exact keys, sizes, versions, checksums, controls, and application
   reads. Count equality alone is not integrity evidence.
5. Update application, IAM, DNS, and event references to the parallel target
   through a separately reviewed change with rollback.
6. Retain the source until the agreed observation and recovery window ends.

## Known limits

- Inventory is point-in-time and can be stale before it finishes on a busy
  bucket.
- Retryable throttling/service failures use at most three attempts with bounded
  exponential backoff; exhausted reads fail or block rather than loop forever.
- The planner summarizes metadata returned by version listings; it does not
  download objects or compute content hashes.
- Directory buckets and newly introduced/unknown controls are not silently
  accepted; failed or unclassified reads block the plan.
- The recommendation is a routing decision, not a cost quote, migration
  implementation, recovery proof, or compliance approval.
