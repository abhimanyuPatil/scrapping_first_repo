"""Settings and client factories for talking to localgcp (local GCP emulators).

localgcp serves Cloud Storage on :4443 and BigQuery (LocalBQ) on :9060.
Every value can be overridden with an environment variable, so the same
scripts can later point at real GCP by changing config instead of code.

Start the emulators first:
    localgcp up --services=bigquery      # BigQuery needs Docker
"""
from __future__ import annotations

import os
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery, storage

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "local-project")
BUCKET_NAME = os.getenv("GCS_BUCKET", "comet-learning")
BQ_DATASET = os.getenv("BQ_DATASET", "hockey")
BQ_TABLE = os.getenv("BQ_TABLE", "hockey_teams")

# Where the Parquet file lives inside the bucket.
GCS_OBJECT = os.getenv("GCS_OBJECT", "hockey/hockey_teams.parquet")

STORAGE_EMULATOR_HOST = os.getenv("STORAGE_EMULATOR_HOST", "localhost:4443")
BIGQUERY_EMULATOR_HOST = os.getenv("BIGQUERY_EMULATOR_HOST", "http://localhost:9060")

SOURCE_CSV = Path("output/hockey_teams.csv")
PARQUET_PATH = Path("output/hockey_teams.parquet")


def gcs_uri() -> str:
    """The gs:// address of the Parquet file."""
    return f"gs://{BUCKET_NAME}/{GCS_OBJECT}"


def table_id() -> str:
    """Fully qualified BigQuery table name: project.dataset.table."""
    return f"{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"


def sql_table() -> str:
    """Table name for use inside SQL text: dataset.table (project comes from the client).

    Written without backticks on purpose. localgcp's BigQuery emulator reads a
    backticked `project.dataset.table` as one single name and cannot find it.
    """
    return f"{BQ_DATASET}.{BQ_TABLE}"


def get_storage_client() -> storage.Client:
    """Cloud Storage client pointed at the local emulator.

    The google-cloud-storage library reads STORAGE_EMULATOR_HOST itself and
    switches to anonymous credentials, so we only need to make sure it is set
    before the client is created.
    """
    os.environ.setdefault("STORAGE_EMULATOR_HOST", STORAGE_EMULATOR_HOST)
    if not os.environ["STORAGE_EMULATOR_HOST"].startswith("http"):
        os.environ["STORAGE_EMULATOR_HOST"] = "http://" + os.environ["STORAGE_EMULATOR_HOST"]
    return storage.Client(project=PROJECT_ID)


def get_bigquery_client() -> bigquery.Client:
    """BigQuery client pointed at the local emulator, with no real credentials."""
    return bigquery.Client(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=BIGQUERY_EMULATOR_HOST),
    )
