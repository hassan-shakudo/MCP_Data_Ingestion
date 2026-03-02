# Ski Resort Data Ingestion Pipeline

## Overview

This pipeline extracts operational data from a ski resort's Microsoft SQL Server database and loads it into cloud storage (MinIO/S3) in an optimized format for analytics and reporting.

Think of it as an automated process that:
1. **Connects** to your resort's operational database
2. **Extracts** daily data (revenue, payroll, visits, weather, etc.)
3. **Transforms** it into an analytics-ready format
4. **Loads** it into a data lake for reporting and business intelligence

---

## What Data Does It Collect?

The pipeline can extract the following data types (called "procedures" or "procs"):

| Data Type | Description | Business Use |
|-----------|-------------|--------------|
| **Revenue** | Sales and revenue metrics | Track daily sales performance, identify trends |
| **Payroll** | Hourly employee time and wages | Monitor labor costs for contract workers |
| **Payroll Salary** | Salaried employee compensation | Track fixed labor costs |
| **Payroll History** | Historical payroll records | Access off-season payroll data |
| **Budget** | Budget information | Compare actuals vs. planned budgets |
| **Visits** | Guest visit metrics | Understand foot traffic and attendance |
| **Weather** | Snow and weather data | Correlate weather with operations/revenue |

You can choose which data types to extract (see [Configuration](#configuration) below).

---

## How It Works

### High-Level Process

```
┌─────────────────┐
│  SQL Server DB  │  ← Your operational database
│  (MCP Database) │
└────────┬────────┘
         │
         │ 1. Connect & Extract
         │    (Run stored procedures)
         ▼
┌─────────────────┐
│  Data Pipeline  │  ← This script
│  (Python)       │
└────────┬────────┘
         │
         │ 2. Transform & Process
         │    (Clean, aggregate, add metadata)
         ▼
┌─────────────────┐
│  Parquet Files  │  ← Temporary local storage
│  (Local)        │
└────────┬────────┘
         │
         │ 3. Upload & Store
         │    (Copy to cloud)
         ▼
┌─────────────────┐
│  MinIO / S3     │  ← Cloud data lake
│  (Object Store) │
└─────────────────┘
```

### Detailed Step-by-Step

1. **Environment Setup**
   - Validates all required configuration (database credentials, S3 details, resort info, dates)
   - Installs necessary software dependencies (ODBC drivers, Python libraries)

2. **Date Range Calculation**
   - Uses `DATE_START` (required) and `DATE_END` (optional)
   - If `DATE_END` not provided:
     - Uses today's date if current UTC time >= 15:00
     - Uses yesterday's date if current UTC time < 15:00
   - This ensures complete data availability (data cutoff at 3 PM UTC)

3. **Database Connection**
   - Connects to your Microsoft SQL Server database
   - Uses ODBC Driver 18 for SQL Server (industry-standard database connector)

4. **Daily Data Extraction** (loops through each day)
   - For each date in the range:
     - Runs selected stored procedures (SQL queries) for your resort
     - Retrieves data as rows and columns
     - Handles connection issues with automatic retries (3 attempts, 30s delay)

5. **Data Processing**
   - **For Revenue, Budget, Visits, Weather:**
     - Minimal processing - extracts data as-is
   - **For Payroll:**
     - Compares date against `ACTIVE_PAYROLL_DATE`
     - **Active payroll** (date >= ACTIVE_PAYROLL_DATE):
       - Runs `payroll` (hourly) + `payroll_salary` stored procedures
       - Calculates: `(hours × rate) + dollar_amount` per department
       - Combines into aggregated `processed_payroll`
     - **Historical payroll** (date < ACTIVE_PAYROLL_DATE):
       - Uses `payroll_history` stored procedure
       - Extracts pre-aggregated historical totals

6. **Data Formatting**
   - Converts to **Parquet format** (compressed, columnar storage)
   - Adds metadata columns:
     - `_meta_proc`: Which procedure was run
     - `_meta_resort`: Resort name
     - `_meta_date`: Date in YYYY-MM-DD format
     - `_meta_rowcount`: Number of records
     - `fetchedAt`: When data was extracted (UTC timestamp)

7. **File Organization**
   - Structures files by: procedure → resort → date
   - Example: `proc=revenue/resort=purgatory/date_start=2024-11-01/date_end=2024-11-01/part-000.parquet`
   - This structure enables efficient filtering in analytics tools

8. **Cloud Upload**
   - Uploads organized files to MinIO/S3
   - Uses date-by-date uploads for reliability

9. **Cleanup**
   - Removes local files after successful upload
   - Keeps cloud storage as single source of truth

---

## Prerequisites

### Required Access
- Microsoft SQL Server database credentials
- MinIO or S3 bucket with read/write permissions

### Software Requirements
- **Operating System**: Linux (Debian-based)
- **Python**: 3.x
- **Database Driver**: ODBC Driver 18 for SQL Server (installed automatically)

---

## Configuration

The pipeline is configured entirely through **environment variables**. No code changes needed!

### All Environment Variables

```bash
# ============================================
# REQUIRED VARIABLES
# ============================================

# Database Configuration
MCP_DB_USERNAME              # SQL Server username
MCP_DB_PASSWORD              # SQL Server password
MCP_DB_SERVER                # SQL Server hostname/IP
MCP_DB_NAME                  # Database name

# MinIO/S3 Configuration
MINIO_ENDPOINT               # MinIO endpoint URL (e.g., http://minio.minio.svc:9000)
MINIO_BUCKET                 # S3/MinIO bucket name
AWS_ACCESS_KEY_ID            # MinIO access key
AWS_SECRET_ACCESS_KEY        # MinIO secret key

# Resort Configuration
RESORT_NAME                  # Resort name (e.g., "Big Sky")
RESORT_DB_NAME               # Resort-specific database name
RESORT_GROUP_NUM             # Resort group number (must be integer, e.g., 46)

# Date Configuration
DATE_START                   # Start date in YYYY-MM-DD format
ACTIVE_PAYROLL_DATE          # Cutoff date for active vs historical payroll (YYYY-MM-DD)
                             # Dates >= this use active payroll processing
                             # Dates < this use historical payroll data

# ============================================
# OPTIONAL VARIABLES (with defaults)
# ============================================

# Database Configuration
MCP_DB_PORT=1433             # SQL Server port

# MinIO/S3 Configuration
MINIO_PREFIX=mcp_parquet     # S3 prefix/folder for organized storage
AWS_DEFAULT_REGION=us-east-1 # AWS region (for S3 compatibility)

# Date Configuration
DATE_END                     # End date in YYYY-MM-DD format
                             # If not provided:
                             #   - Uses today if UTC time >= 15:00
                             #   - Uses yesterday if UTC time < 15:00

# Processing Configuration
PROCS=revenue,payroll,payroll_salary,payroll_history,budget,visits,weather
                             # Comma-separated list of procedures to run
                             # Valid: revenue, payroll, payroll_salary,
                             #        payroll_history, budget, visits, weather

OUTDIR=/work/out             # Local output directory

# Error Reporting
ERROR_WEBHOOK_URL=https://n8n-v2.mcp.hyperplane.dev/webhook/data-ingestion-errors
                             # Webhook URL for error notifications
```

---

## Usage

### Running the Pipeline

```bash
# 1. Set required environment variables
export MCP_DB_USERNAME="your_username"
export MCP_DB_PASSWORD="your_password"
export MCP_DB_SERVER="db.example.com"
export MCP_DB_NAME="MCP"

export MINIO_ENDPOINT="http://minio.example.com:9000"
export MINIO_BUCKET="data-lake"
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin"

export RESORT_NAME="PURGATORY"
export RESORT_DB_NAME="Purgatory"
export RESORT_GROUP_NUM="46"

export DATE_START="2024-11-01"
export ACTIVE_PAYROLL_DATE="2024-11-01"  # Payroll cutoff date

# 2. (Optional) Customize what to extract
export PROCS="revenue,visits,weather"

# 3. (Optional) Specify end date (defaults to today/yesterday based on time)
export DATE_END="2024-11-30"

# 4. Run the pipeline
./run.sh
```

### Example: Extract Only Revenue for Recent Days

```bash
# Set required variables
export MCP_DB_USERNAME="..." MCP_DB_PASSWORD="..." MCP_DB_SERVER="..."
export MCP_DB_NAME="MCP"
export MINIO_ENDPOINT="..." MINIO_BUCKET="..."
export AWS_ACCESS_KEY_ID="..." AWS_SECRET_ACCESS_KEY="..."
export RESORT_NAME="PURGATORY" RESORT_DB_NAME="Purgatory" RESORT_GROUP_NUM="46"

# Configure for revenue only, starting 7 days ago
export DATE_START="2024-11-08"
export ACTIVE_PAYROLL_DATE="2024-11-01"
export PROCS="revenue"
./run.sh
```

### Example: Extract All Data for Specific Date Range

```bash
# Required config (same as above)...

# Extract all data types for November 2024
export DATE_START="2024-11-01"
export DATE_END="2024-11-30"
export ACTIVE_PAYROLL_DATE="2024-11-01"
./run.sh
```

---

## Output Structure

### File Organization

Data is stored in a hierarchical structure optimized for analytics tools like Dremio, Spark, or Athena:

```
s3://your-bucket/mcp_parquet/
├── proc=revenue/
│   └── resort=purgatory/
│       └── date=2024-11-01/
│           └── data.parquet
├── proc=payroll/
│   └── resort=purgatory/
│       └── date=2024-11-01/
│           └── data.parquet
├── proc=processed_payroll/  ← Combined hourly + salary totals
│   └── resort=purgatory/
│       └── date=2024-11-01/
│           └── data.parquet
...
```

### Parquet Format Benefits

**Parquet** is a columnar storage format that:
- **Compresses data** efficiently (smaller file sizes)
- **Speeds up queries** (only reads needed columns)
- **Plays nice with analytics tools** (Dremio, Spark, Tableau, Power BI, etc.)

### Metadata Columns

Every file includes these additional columns for tracking:

```
_meta_proc          # Which procedure: "revenue", "payroll", etc.
_meta_resort        # Resort name in kebab-case: "lee-canyon", "purgatory"
_meta_date          # Date: "2024-11-01"
_meta_rowcount      # Number of records: "145"
fetchedAt           # Extraction timestamp: "2024-11-01T15:30:00Z"
```

---

## Technical Details

### Architecture

- **Language**: Python 3.x
- **Database**: Microsoft SQL Server (via `pyodbc` + ODBC Driver 18)
- **Data Processing**: `pandas` for DataFrames
- **Storage Format**: Apache Parquet (via `pyarrow`)
- **Cloud Storage**: S3-compatible (MinIO/AWS S3) via `boto3`

### Key Components

| File | Purpose |
|------|---------|
| `run.sh` | Shell script that orchestrates the entire process |
| `main.py` | Python script with core data extraction logic |

### Data Processing Logic

#### Payroll Processing
The pipeline uses `ACTIVE_PAYROLL_DATE` to determine processing method:

- **Active Payroll** (date >= ACTIVE_PAYROLL_DATE):
  - Runs `payroll` (hourly) + `payroll_salary` stored procedures
  - Calculates wages: `(hours × rate) + dollar_amount`
  - Aggregates totals by department code
  - Combines both into `processed_payroll` output

- **Historical Payroll** (date < ACTIVE_PAYROLL_DATE):
  - Runs `payroll_history` stored procedure
  - Uses pre-aggregated historical totals directly
  - Faster processing for older data

#### Date Handling
- Processes data **day by day** for reliability
- `DATE_START` is required, `DATE_END` is optional
- If `DATE_END` not provided:
  - Uses today's date if UTC time >= 15:00
  - Uses yesterday's date if UTC time < 15:00
- This 3 PM cutoff ensures complete daily data availability
- **Stored procedure calls**: Include full datetime range (00:00:00 to 23:59:59 for each day)
- **Metadata and folders**: Use simple date format (YYYY-MM-DD)

#### Error Handling
- **Database Retries**: Automatically retries failed database calls (3 attempts with 30s delay)
- **Upload Retries**: S3/MinIO uploads retry 3 times with 10s delays on failure
- **Upload Verification**: Verifies each file exists in S3 after upload before deleting local copy
- **Safe Failure**: Local files preserved if upload fails, preventing data loss
- **Error Collection**: All errors tracked and reported via webhook notification
- **Validation**: Checks all required environment variables before starting
- **Logging**: Outputs JSON-formatted logs for monitoring

---

## Monitoring & Logs

The pipeline outputs **JSON-formatted logs** for easy parsing:

```json
{"seasons_to_process": 1, "seasons": [{"start": "2024-11-01", "end": "2025-03-31"}]}
{"processing_date": "2024-11-01", "season": 1, "date_count": 1}
{"writing": "/work/out/proc=revenue/resort=purgatory/.../part-000.parquet", "rows": 245}
{"uploading": "mcp_parquet/proc=revenue/resort=purgatory/.../part-000.parquet"}
{"upload_verified": "mcp_parquet/proc=revenue/resort=purgatory/.../part-000.parquet"}
{"status": "completed", "total_dates_processed": 150, "location": "s3://bucket/mcp_parquet/"}
```

### Webhook Error Reporting

The pipeline automatically sends error notifications to an n8n webhook when failures occur.

**Webhook URL**: `https://n8n-v2.mcp.hyperplane.dev/webhook/data-ingestion-errors` (configurable via `ERROR_WEBHOOK_URL`)

**When notifications are sent:**
- ✅ Upload failures (files failed to upload to S3)
- ✅ Payroll processing errors
- ✅ Any critical pipeline errors

**Example webhook payload:**

```json
{
  "pipeline": "data_ingestion",
  "resort": "PURGATORY",
  "timestamp": "2024-11-15T14:30:00Z",
  "total_errors": 2,
  "summary": {
    "status": "completed_with_failures",
    "total_dates_processed": 150,
    "successful_dates": 148,
    "failed_dates": ["2024-11-15", "2024-12-03"],
    "failed_count": 2,
    "location": "s3://bucket/mcp_parquet/"
  },
  "errors": [
    {
      "type": "upload_failure",
      "date": "2024-11-15",
      "resort": "PURGATORY",
      "error_message": "Connection timeout to S3",
      "local_files_preserved": "/work/out",
      "timestamp": "2024-11-15T14:28:33Z"
    },
    {
      "type": "payroll_processing_failure",
      "date": "2024-12-03",
      "resort": "PURGATORY",
      "error_message": "Division by zero in department 201",
      "timestamp": "2024-12-03T14:29:45Z"
    }
  ]
}
```

**Error Types:**
- `upload_failure` - S3/MinIO upload failed (local files preserved for retry)
- `payroll_processing_failure` - Payroll aggregation failed (other procs continued)

**Important Notes:**
- Webhook notification failures do NOT crash the pipeline
- Each error includes timestamp, date, resort, and detailed error message
- Summary provides overview of pipeline status and counts

---

## Troubleshooting

### Common Issues

**"Missing [VARIABLE_NAME]"**
- **Cause**: Required environment variable not set
- **Solution**: Export the missing variable before running `./run.sh`

**"Unknown proc 'xyz'"**
- **Cause**: Invalid procedure name in `PROCS` variable
- **Solution**: Use only valid names: `revenue`, `payroll`, `payroll_salary`, `payroll_history`, `budget`, `visits`, `weather`

**Database Connection Timeout**
- **Cause**: Cannot reach SQL Server
- **Solution**: Verify `MCP_DB_SERVER` and `MCP_DB_PORT`, check network connectivity

**S3 Upload Failed**
- **Cause**: Invalid MinIO/S3 credentials or endpoint
- **Solution**: Verify `MINIO_ENDPOINT`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

**"RESORT_GROUP_NUM must be a valid integer"**
- **Cause**: Group number is not a number
- **Solution**: Set `RESORT_GROUP_NUM` to a numeric value (e.g., `46`, `-1`)

---

## FAQs

**Q: How long does the pipeline take to run?**
A: Depends on date range and data volume. A full season (~150 days) typically takes several hours.

**Q: Can I run multiple resorts at once?**
A: Not in a single run. Run the pipeline separately for each resort (can be parallelized using separate processes).

**Q: What if the pipeline fails midway?**
A: The pipeline preserves local files if S3 upload fails, preventing data loss. Check the error webhook notification for details on which dates failed. You can re-run for specific dates using `DATE_START` and `DATE_END`. Successfully uploaded dates can be safely overwritten if re-run.

**Q: Can I run this for historical data?**
A: Yes! Set `DATE_START` and `DATE_END` to any past date range.

**Q: Do I need to run this daily?**
A: Recommended. Typically scheduled as a daily job (e.g., via cron, Airflow, or Kubernetes CronJob) to keep data fresh.

**Q: Where can I query the data after it's loaded?**
A: Use any tool that supports Parquet/S3: Dremio, AWS Athena, Spark, Presto, or even pandas/Python.

**Q: How do I know if errors occurred during the pipeline?**
A: Errors are sent to the configured webhook (n8n) in real-time. Check your n8n workflow for error notifications with full details including error type, date, and message.

**Q: What happens if the webhook is down?**
A: The pipeline continues and completes normally. Webhook notification failures are logged but don't affect data processing or pipeline status.

**Q: Are my local files safe if S3 upload fails?**
A: Yes! Local files are only deleted AFTER successful upload verification. If upload fails, files are preserved in `/work/out` (or your `OUTDIR`) for manual recovery or retry.

**Q: What is ACTIVE_PAYROLL_DATE and how do I set it?**
A: `ACTIVE_PAYROLL_DATE` is the cutoff date that determines payroll processing method. For dates >= this value, the pipeline uses detailed active payroll stored procedures (hourly + salary). For dates < this value, it uses historical payroll data. Typically set to the start of the current payroll period (e.g., start of current ski season like `2024-11-01`).

**Q: What happens if DATE_END is not provided?**
A: The pipeline automatically determines the end date based on current UTC time. If it's 15:00 UTC or later, it uses today's date. If before 15:00 UTC, it uses yesterday's date. This ensures you're processing complete daily data.
