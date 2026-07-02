#!/usr/bin/env bash
# S3 bucket cross-region migration (same bucket name, region A -> region B), staged via local disk.
# Bucket names are globally unique in S3 -- old bucket must be emptied+deleted before
# the new one (same name) can be created in the target region. Downtime between
# delete-source and upload-complete is unavoidable.
#
# Single sequential run: does inventory -> download -> verify-download -> delete-source
# -> create-target -> upload -> verify-upload, in order, halting after each phase to
# print the relevant details and wait for typed confirmation before moving on.
#
# Usage:
#   ./s3-region-migrate.sh <bucket-name> <source-region> <target-region> <local-dir>
#
set -euo pipefail

BUCKET="${1:?bucket name required}"
SRC_REGION="${2:?source region required}"
DST_REGION="${3:?target region required}"
LOCAL_DIR="${4:?local dir required}"

MANIFEST_DIR="${LOCAL_DIR}/.migration-manifest"
TAGS_DIR="${MANIFEST_DIR}/tags"

mkdir -p "$MANIFEST_DIR" "$TAGS_DIR"

pause_for_review() {
  local msg="$1"
  echo
  echo ">>> $msg"
  read -r -p ">>> Review details above. Type YES to continue, anything else to abort: " ans
  if [[ "$ans" != "YES" ]]; then
    echo "Aborted. Re-run the script to resume -- already-completed phases are safe to repeat"
    echo "(sync/verify are idempotent; delete-source/create-target are not, so don't re-run past them)."
    exit 1
  fi
}

phase_inventory() {
  echo "=========================================="
  echo "PHASE 1/7: inventory (source region: $SRC_REGION)"
  echo "=========================================="
  echo "== Object count/size =="
  aws s3 ls "s3://${BUCKET}" --recursive --region "$SRC_REGION" --summarize | tail -n 3

  echo "== Versioning status =="
  aws s3api get-bucket-versioning --bucket "$BUCKET" --region "$SRC_REGION" | tee "${MANIFEST_DIR}/versioning.json"

  echo "== Bucket encryption (best effort) =="
  aws s3api get-bucket-encryption --bucket "$BUCKET" --region "$SRC_REGION" \
    > "${MANIFEST_DIR}/encryption.json" 2>/dev/null || echo "  (none / not accessible)"

  echo "== Bucket policy (best effort) =="
  aws s3api get-bucket-policy --bucket "$BUCKET" --region "$SRC_REGION" \
    > "${MANIFEST_DIR}/policy.json" 2>/dev/null || echo "  (none / not accessible)"

  echo
  echo "Manifest saved under ${MANIFEST_DIR}."
  pause_for_review "Confirm object count/size look right and versioning.json matches what you expect."
}

phase_download() {
  echo "=========================================="
  echo "PHASE 2/7: download s3://${BUCKET} (${SRC_REGION}) -> ${LOCAL_DIR}"
  echo "=========================================="
  aws s3 sync "s3://${BUCKET}" "$LOCAL_DIR" --region "$SRC_REGION" --exact-timestamps \
    --exclude ".migration-manifest/*"

  echo "Capturing per-object tags ..."
  aws s3api list-objects-v2 --bucket "$BUCKET" --region "$SRC_REGION" \
    --query 'Contents[].Key' --output text | tr '\t' '\n' | while read -r key; do
    [[ -z "$key" ]] && continue
    safe_name=$(echo "$key" | sed 's#/#__#g')
    aws s3api get-object-tagging --bucket "$BUCKET" --key "$key" --region "$SRC_REGION" \
      > "${TAGS_DIR}/${safe_name}.json" 2>/dev/null || true
  done

  echo "Capturing empty-folder markers (zero-byte keys ending in '/') -- s3 sync doesn't handle these ..."
  : > "${MANIFEST_DIR}/folder-markers.txt"
  aws s3api list-objects-v2 --bucket "$BUCKET" --region "$SRC_REGION" \
    --query "Contents[?Size==\`0\` && ends_with(Key, '/')].Key" --output text | tr '\t' '\n' \
    >> "${MANIFEST_DIR}/folder-markers.txt"
  marker_count=$(grep -c . "${MANIFEST_DIR}/folder-markers.txt" || true)
  echo "Found ${marker_count} empty-folder marker(s), saved to ${MANIFEST_DIR}/folder-markers.txt"

  echo "Download + tag capture done."
  pause_for_review "Confirm files landed under ${LOCAL_DIR} as expected."
}

phase_verify_download() {
  echo "=========================================="
  echo "PHASE 3/7: verify-download"
  echo "=========================================="
  s3_count=$(aws s3api list-objects-v2 --bucket "$BUCKET" --region "$SRC_REGION" \
    --query 'length(Contents[])' --output text 2>/dev/null || echo 0)
  marker_count=$(grep -c . "${MANIFEST_DIR}/folder-markers.txt" 2>/dev/null || echo 0)
  local_count=$(find "$LOCAL_DIR" -type f -not -path "${MANIFEST_DIR}/*" | wc -l)
  echo "S3 object count:         $s3_count"
  echo "  of which folder markers: $marker_count (not downloaded as files, tracked separately)"
  echo "Local file count:        $local_count"
  if [[ "$((s3_count - marker_count))" != "$local_count" ]]; then
    echo "MISMATCH -- do not proceed. Aborting."
    exit 1
  fi
  echo "Counts match (accounting for folder markers)."
  pause_for_review "Download verified. Next phase PERMANENTLY DELETES bucket '${BUCKET}' in ${SRC_REGION}."
}

phase_delete_source() {
  echo "=========================================="
  echo "PHASE 4/7: delete-source (${SRC_REGION}) -- DESTRUCTIVE"
  echo "=========================================="

  versioned=$(grep -o '"Status": *"Enabled"' "${MANIFEST_DIR}/versioning.json" 2>/dev/null || true)
  if [[ -n "$versioned" ]]; then
    echo "Bucket is versioned -- deleting all versions and delete markers ..."
    aws s3api list-object-versions --bucket "$BUCKET" --region "$SRC_REGION" \
      --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json \
      > "${MANIFEST_DIR}/_versions.json"
    if [[ $(jq '.Objects | length' "${MANIFEST_DIR}/_versions.json") -gt 0 ]]; then
      aws s3api delete-objects --bucket "$BUCKET" --region "$SRC_REGION" \
        --delete "file://${MANIFEST_DIR}/_versions.json"
    fi
    aws s3api list-object-versions --bucket "$BUCKET" --region "$SRC_REGION" \
      --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json \
      > "${MANIFEST_DIR}/_markers.json"
    if [[ $(jq '.Objects | length' "${MANIFEST_DIR}/_markers.json") -gt 0 ]]; then
      aws s3api delete-objects --bucket "$BUCKET" --region "$SRC_REGION" \
        --delete "file://${MANIFEST_DIR}/_markers.json"
    fi
  else
    echo "Emptying bucket ..."
    aws s3 rm "s3://${BUCKET}" --recursive --region "$SRC_REGION"
  fi

  echo "Deleting bucket ..."
  aws s3api delete-bucket --bucket "$BUCKET" --region "$SRC_REGION"
  echo "Source bucket deleted. Name should free up shortly (occasionally a short propagation delay)."
  pause_for_review "Source bucket gone. Confirm name is free before creating target (retry this pause if AWS still shows it taken)."
}

phase_create_target() {
  echo "=========================================="
  echo "PHASE 5/7: create-target (${DST_REGION})"
  echo "=========================================="
  if [[ "$DST_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$DST_REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$DST_REGION" \
      --create-bucket-configuration LocationConstraint="$DST_REGION"
  fi

  if grep -q '"Status": *"Enabled"' "${MANIFEST_DIR}/versioning.json" 2>/dev/null; then
    echo "Re-enabling versioning ..."
    aws s3api put-bucket-versioning --bucket "$BUCKET" --region "$DST_REGION" \
      --versioning-configuration Status=Enabled
  fi

  if [[ -s "${MANIFEST_DIR}/encryption.json" ]]; then
    echo "Re-applying encryption config ..."
    echo "{\"Rules\": $(jq '.Rules' "${MANIFEST_DIR}/encryption.json")}" \
      > "${MANIFEST_DIR}/_enc_apply.json"
    aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$DST_REGION" \
      --server-side-encryption-configuration "file://${MANIFEST_DIR}/_enc_apply.json" || \
      echo "  (skip -- inspect ${MANIFEST_DIR}/encryption.json and apply manually if needed)"
  fi

  if [[ -s "${MANIFEST_DIR}/policy.json" ]]; then
    echo "NOTE: bucket policy captured at ${MANIFEST_DIR}/policy.json -- review before reapplying"
    echo "  (may reference source-region ARNs/conditions; not auto-applied)"
  fi

  echo "Target bucket ready."
  pause_for_review "Confirm new bucket exists in ${DST_REGION} with expected versioning/encryption before uploading."
}

phase_upload() {
  echo "=========================================="
  echo "PHASE 6/7: upload ${LOCAL_DIR} -> s3://${BUCKET} (${DST_REGION})"
  echo "=========================================="
  aws s3 sync "$LOCAL_DIR" "s3://${BUCKET}" --region "$DST_REGION" \
    --exclude ".migration-manifest/*"

  echo "Recreating empty-folder markers ..."
  if [[ -s "${MANIFEST_DIR}/folder-markers.txt" ]]; then
    while read -r folder_key; do
      [[ -z "$folder_key" ]] && continue
      aws s3api put-object --bucket "$BUCKET" --key "$folder_key" --region "$DST_REGION" \
        > /dev/null || echo "  failed to recreate folder marker: $folder_key"
    done < "${MANIFEST_DIR}/folder-markers.txt"
  fi

  echo "Reapplying tags ..."
  find "$TAGS_DIR" -name '*.json' | while read -r tagfile; do
    key=$(basename "$tagfile" .json | sed 's#__#/#g')
    tagset=$(jq -c '{TagSet: .TagSet}' "$tagfile" 2>/dev/null || echo "")
    [[ -z "$tagset" || "$tagset" == "{\"TagSet\":null}" ]] && continue
    aws s3api put-object-tagging --bucket "$BUCKET" --key "$key" --region "$DST_REGION" \
      --tagging "$tagset" || echo "  tag apply failed for $key"
  done
  echo "Upload + tag reapply done."
  pause_for_review "Confirm objects visible in new bucket before final verify."
}

phase_verify_upload() {
  echo "=========================================="
  echo "PHASE 7/7: verify-upload"
  echo "=========================================="
  s3_count=$(aws s3api list-objects-v2 --bucket "$BUCKET" --region "$DST_REGION" \
    --query 'length(Contents[])' --output text 2>/dev/null || echo 0)
  marker_count=$(grep -c . "${MANIFEST_DIR}/folder-markers.txt" 2>/dev/null || echo 0)
  local_count=$(find "$LOCAL_DIR" -type f -not -path "${MANIFEST_DIR}/*" | wc -l)
  echo "S3 object count (target): $s3_count"
  echo "  of which folder markers: $marker_count"
  echo "Local file count:         $local_count"
  if [[ "$((s3_count - marker_count))" != "$local_count" ]]; then
    echo "MISMATCH -- investigate before pointing app at new bucket."
    exit 1
  fi
  echo "Counts match (accounting for folder markers). Migration complete. Update app config region -> ${DST_REGION}."
}

bucket_exists_in_region() {
  aws s3api head-bucket --bucket "$BUCKET" --region "$1" >/dev/null 2>&1
}

if bucket_exists_in_region "$SRC_REGION"; then
  phase_inventory
  phase_download
  phase_verify_download
  phase_delete_source
else
  echo "=========================================="
  echo "Source bucket '${BUCKET}' not found in ${SRC_REGION} -- assuming already deleted"
  echo "(resuming a prior run past delete-source). Skipping phases 1-4."
  echo "=========================================="
  local_count=$(find "$LOCAL_DIR" -type f -not -path "${MANIFEST_DIR}/*" 2>/dev/null | wc -l)
  echo "Local files already present in ${LOCAL_DIR}: ${local_count}"
  if [[ "$local_count" -eq 0 ]]; then
    echo "No local data found either -- nothing to resume from, and source is gone. Aborting."
    exit 1
  fi
  echo "Local data found -- proceeding directly to create-target."
fi

if bucket_exists_in_region "$DST_REGION"; then
  echo "=========================================="
  echo "Target bucket '${BUCKET}' already exists in ${DST_REGION} -- skipping create-target."
  echo "=========================================="
  pause_for_review "Confirm target bucket's versioning/encryption already match before uploading into it."
else
  phase_create_target
fi

phase_upload
phase_verify_upload
