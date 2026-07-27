#!/usr/bin/env sh
#
# RETIRED: the previous workflow deleted the source bucket to reclaim its
# globally unique name, then relied on count-only verification. That can lose
# versions, configuration, or the name itself.
#
# Generate a read-only plan with a different, parallel target name instead:
#
#   python3 aws/s3_migration_plan.py plan \
#     --source-bucket SOURCE \
#     --source-region SOURCE_REGION \
#     --target-bucket PARALLEL_TARGET \
#     --target-region TARGET_REGION
#
echo "RETIRED: same-name delete/recreate S3 migration is unsafe." >&2
echo "Use aws/s3_migration_plan.py plan with a parallel target name." >&2
exit 3
