"""Step 3: create a BigQuery table from the Parquet file in Cloud Storage.

Two ways to load:
  --mode insert   (default) download the Parquet file from Cloud Storage, build
                  the table schema from the file, and add the rows with SQL
                  INSERT statements. Use this with localgcp: its BigQuery
                  emulator (LocalBQ) implements only SQL queries, not load
                  jobs or streaming inserts.
  --mode uri      real BigQuery reads gs://bucket/file itself in one load job.
                  This is the normal way on real GCP.

Usage:
    python load_to_bigquery.py
    python load_to_bigquery.py --mode uri      # against real GCP
"""
from __future__ import annotations

import argparse
import io
from typing import List

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery

from common import setup_logger
from common.gcp_local import (
    BQ_DATASET,
    BUCKET_NAME,
    GCS_OBJECT,
    PROJECT_ID,
    gcs_uri,
    get_bigquery_client,
    get_storage_client,
    sql_table,
    table_id,
)

log = setup_logger("load_to_bigquery")

BATCH_SIZE = 2000


def bq_type(arrow_type: pa.DataType) -> str:
    """Map a Parquet/Arrow column type to a BigQuery column type."""
    if pa.types.is_integer(arrow_type):
        return "INTEGER"  # legacy name; the emulator does not know "INT64"
    if pa.types.is_floating(arrow_type):
        return "FLOAT"
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"
    if pa.types.is_timestamp(arrow_type):
        return "TIMESTAMP"
    if pa.types.is_date(arrow_type):
        return "DATE"
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "STRING"
    raise ValueError(f"No BigQuery type mapped for Arrow type {arrow_type}")


def schema_from_arrow(schema: pa.Schema) -> List[bigquery.SchemaField]:
    return [
        bigquery.SchemaField(f.name, bq_type(f.type), mode="NULLABLE") for f in schema
    ]


def sql_literal(value) -> str:
    """Turn one Python value into a SQL literal, escaping strings.

    Values come from our own Parquet file, but we still escape quotes and
    backslashes so a team name like "O'Brien" cannot break the statement.
    """
    if value is None or (isinstance(value, float) and value != value):  # None or NaN
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if hasattr(value, "isoformat"):  # datetime / date
        kind = "TIMESTAMP" if hasattr(value, "hour") else "DATE"
        return f"{kind} '{value.isoformat()}'"
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def load_by_uri(client: bigquery.Client) -> None:
    # No schema given: BigQuery reads column names and types from the Parquet file.
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_uri(gcs_uri(), table_id(), job_config=job_config)
    job.result()  # wait until the load job finishes; raises if it failed


def load_by_insert(client: bigquery.Client) -> None:
    data = get_storage_client().bucket(BUCKET_NAME).blob(GCS_OBJECT).download_as_bytes()
    parquet_file = pq.ParquetFile(io.BytesIO(data))

    # Drop and recreate so re-running never duplicates rows.
    client.delete_table(table_id(), not_found_ok=True)
    client.create_table(
        bigquery.Table(table_id(), schema=schema_from_arrow(parquet_file.schema_arrow))
    )

    columns = ", ".join(f"`{f.name}`" for f in parquet_file.schema_arrow)
    for batch in parquet_file.iter_batches(batch_size=BATCH_SIZE):
        values = ",\n".join(
            "(" + ", ".join(sql_literal(v) for v in row.values()) + ")"
            for row in batch.to_pylist()
        )
        client.query(f"INSERT INTO {sql_table()} ({columns}) VALUES {values}").result()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["insert", "uri"], default="insert")
    args = parser.parse_args()

    client = get_bigquery_client()

    # A dataset is the "folder" that holds tables.
    client.create_dataset(bigquery.Dataset(f"{PROJECT_ID}.{BQ_DATASET}"), exists_ok=True)

    if args.mode == "uri":
        load_by_uri(client)
    else:
        load_by_insert(client)

    # Verify with a real query instead of trusting table metadata.
    count = list(client.query(f"SELECT COUNT(*) AS n FROM {sql_table()}").result())[0]["n"]
    log.info("Loaded table", extra={"table": table_id(), "rows": count, "mode": args.mode})
    for field in client.get_table(table_id()).schema:
        print(f"  {field.name:<12} {field.field_type:<10} {field.mode}")


if __name__ == "__main__":
    main()
