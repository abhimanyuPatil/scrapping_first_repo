"""Step 2: upload output/hockey_teams.parquet to Cloud Storage (localgcp).

Creates the bucket if it does not exist, uploads the file, then lists it back
to confirm the upload worked.

Usage:
    python upload_to_gcs.py
"""
from __future__ import annotations

from google.cloud import exceptions

from common import setup_logger
from common.gcp_local import BUCKET_NAME, GCS_OBJECT, PARQUET_PATH, get_storage_client, gcs_uri

log = setup_logger("upload_to_gcs")


def main() -> None:
    if not PARQUET_PATH.exists():
        raise SystemExit(f"{PARQUET_PATH} not found. Run build_parquet.py first.")

    client = get_storage_client()

    try:
        bucket = client.get_bucket(BUCKET_NAME)
    except exceptions.NotFound:
        bucket = client.create_bucket(BUCKET_NAME)
        log.info("Created bucket", extra={"bucket": BUCKET_NAME})

    blob = bucket.blob(GCS_OBJECT)
    blob.upload_from_filename(str(PARQUET_PATH), content_type="application/octet-stream")

    # Read the object's details back from the server to confirm it is there.
    stored = bucket.get_blob(GCS_OBJECT)
    if stored is None or stored.size != PARQUET_PATH.stat().st_size:
        raise SystemExit("Upload verification failed: stored size does not match local file")

    log.info("Uploaded", extra={"uri": gcs_uri(), "bytes": stored.size})


if __name__ == "__main__":
    main()
