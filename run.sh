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

echo "Installing OS dependencies (msodbcsql18 + unixODBC)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  curl ca-certificates gnupg apt-transport-https \
  unixodbc unixodbc-dev

. /etc/os-release
DEBIAN_VER="${VERSION_ID}"
echo "Debian VERSION_ID=${DEBIAN_VER}"

curl -fsSL "https://packages.microsoft.com/config/debian/${DEBIAN_VER}/packages-microsoft-prod.deb" \
  -o /tmp/packages-microsoft-prod.deb
dpkg -i /tmp/packages-microsoft-prod.deb
rm -f /tmp/packages-microsoft-prod.deb

apt-get update -y
ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18

echo "Installing Python dependencies..."
python -m pip install --no-cache-dir -U pip
python -m pip install --no-cache-dir pyodbc pandas pyarrow boto3 numpy requests

echo "*********** Starting data pipeline ***********"
python "$(dirname "$0")/main.py"

echo "Pipeline completed successfully!"
