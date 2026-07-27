# Project guide

DevOps utility collection for AWS, Azure, Kubernetes, testing, and web operations. Keep each tool self-contained in its platform folder, document inputs and prerequisites, avoid embedded credentials, and default cloud work to read-only planning. The supported S3 path is `aws/s3_migration_plan.py`; same-name delete/recreate is retired. Verify Python tools with `python3 -m unittest discover -s tests/python -v` plus `py_compile`.
