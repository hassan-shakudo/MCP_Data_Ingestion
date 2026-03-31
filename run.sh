#!/usr/bin/env bash
set -euo pipefail

echo "Starting data ingestion pipeline..."

# Check required database env vars
: "${MCP_DB_USERNAME?Missing MCP_DB_USERNAME}"
: "${MCP_DB_PASSWORD?Missing MCP_DB_PASSWORD}"
: "${MCP_DB_SERVER?Missing MCP_DB_SERVER}"
: "${MCP_DB_PORT:=1433}"
: "${MCP_DB_NAME?Missing MCP_DB_NAME}"

# Check required MinIO/S3 env vars
: "${MINIO_ENDPOINT?Missing MINIO_ENDPOINT (e.g. http://minio.minio.svc:9000)}"
: "${MINIO_BUCKET?Missing MINIO_BUCKET}"
: "${MINIO_PREFIX:=mcp_parquet}"
: "${AWS_ACCESS_KEY_ID?Missing AWS_ACCESS_KEY_ID (MinIO key)}"
: "${AWS_SECRET_ACCESS_KEY?Missing AWS_SECRET_ACCESS_KEY (MinIO secret)}"
: "${AWS_DEFAULT_REGION:=us-east-1}"

# Required resort configuration
: "${RESORT_NAME?Missing RESORT_NAME}"
: "${RESORT_DB_NAME?Missing RESORT_DB_NAME}"
: "${RESORT_GROUP_NUM?Missing RESORT_GROUP_NUM}"

# Required date configuration
: "${DATE_START?Missing DATE_START (format: YYYY-MM-DD)}"
: "${ACTIVE_PAYROLL_DATE?Missing ACTIVE_PAYROLL_DATE (format: YYYY-MM-DD)}"

# Optional configuration
: "${PROCS:=revenue,payroll,payroll_salary,payroll_history,budget,visits,weather}"
: "${OUTDIR:=/work/out}"

export AWS_EC2_METADATA_DISABLED=true
mkdir -p "${OUTDIR}"

if ! command -v odbcinst >/dev/null 2>&1 || ! odbcinst -q -d | grep -q "ODBC Driver 18"; then
  echo "ERROR: ODBC Driver 18 for SQL Server is not available in the runtime image."
  echo "Please use the prebuilt Data Ingestion runtime image/environment config."
  exit 1
fi

echo "Runtime image dependencies detected; skipping apt/pip installation."
echo "*********** Starting data pipeline ***********"
python "$(dirname "$0")/main.py"

echo "Pipeline completed successfully!"
