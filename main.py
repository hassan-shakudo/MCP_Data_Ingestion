import os
import re
import json
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import pyodbc
import pandas as pd
import numpy as np
import boto3
import requests

# Shakudo secrets directory - secrets are mounted as files here
SHAKUDO_SECRETS_DIR = Path("/etc/hyperplane/secrets")


def get_secret(env_var: str, default: str = None) -> str:
    """
    Read a secret value with the following priority:
    1. Environment variable (for parameters passed to job)
    2. Shakudo secret file at /etc/hyperplane/secrets/<env_var>
    3. Default value (if provided)

    Shakudo mounts secrets as files where the filename is the secret name
    and content structure varies. We try to read the value matching env_var key.
    """
    # First try environment variable
    if env_var in os.environ:
        return os.environ[env_var]

    # Try reading from Shakudo secrets directory
    if SHAKUDO_SECRETS_DIR.exists():
        # Secrets are mounted as files, try to find one containing our key
        for secret_file in SHAKUDO_SECRETS_DIR.iterdir():
            if secret_file.is_file():
                try:
                    content = secret_file.read_text().strip()
                    # If the file name matches a secret that contains our env var as key
                    # The secret file contains the value directly
                    if secret_file.name == env_var:
                        return content
                    # Also check if secret file content is the value we need
                    # (for secrets where key matches env_var name)
                except Exception:
                    continue

        # Direct file read - secret file named after the env var
        direct_path = SHAKUDO_SECRETS_DIR / env_var
        if direct_path.exists():
            try:
                return direct_path.read_text().strip()
            except Exception:
                pass

    if default is not None:
        return default

    raise KeyError(f"Secret '{env_var}' not found in environment or Shakudo secrets")


STORED_PROCS = {
    "revenue": "exec Shakudo_DMRGetRevenue @database=?, @group_no=?, @date_ini=?, @date_end=?",
    "payroll": "exec Shakudo_DMRGetPayroll @resort=?, @date_ini=?, @date_end=?",
    "payroll_salary": "exec Shakudo_DMRGetPayrollSalary @resort=?, @date_ini=?, @date_end=?",
    "payroll_history": "exec Shakudo_DMRGetPayrollHistory @resort=?, @date_ini=?, @date_end=?",
    "budget": "exec Shakudo_DMRBudget @resort=?, @date_ini=?, @date_end=?",
    "visits": "exec Shakudo_DMRGetVists @resort=?, @date_ini=?, @date_end=?",
    "weather": "exec Shakudo_GetSnow @resort=?, @date_ini=?, @date_end=?",
}


def safe_name(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def cursor_to_df(cur) -> pd.DataFrame:
    cols = [c[0] for c in cur.description] if cur.description else []
    rows = cur.fetchall()
    return pd.DataFrame.from_records(rows, columns=cols)


PUNCHTIME_COLS = {
    "start_punchtime",
    "end_punchtime",
    "start_punch_time",
    "end_punch_time",
}


def normalize_punchtime_cols(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col.lower() in PUNCHTIME_COLS:
            s = df[col]
            if pd.api.types.is_datetime64_any_dtype(s):
                df[col] = s.dt.strftime("%Y-%m-%d %H:%M:%S").astype("string")
            else:
                df[col] = s.where(~pd.isna(s), pd.NA).astype("string")
    return df


def normalize_date_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Date columns to consistent string format (YYYY-MM-DD)"""
    for col in df.columns:
        if col.lower() == "date":
            s = df[col]

            # Handle datetime/timestamp types
            if pd.api.types.is_datetime64_any_dtype(s):
                df[col] = s.dt.strftime("%Y-%m-%d").astype(str)

            # Handle integer dates (e.g., 20260203 format)
            elif pd.api.types.is_integer_dtype(s):
                try:
                    # Convert integer YYYYMMDD to datetime then to string
                    df[col] = (
                        pd.to_datetime(s, format="%Y%m%d", errors="coerce")
                        .dt.strftime("%Y-%m-%d")
                        .astype(str)
                    )
                except:
                    # If conversion fails, convert to string as-is
                    df[col] = s.astype(str)

            # Handle float dates (rare but possible)
            elif pd.api.types.is_float_dtype(s):
                try:
                    # Try converting float to int first, then to date
                    df[col] = (
                        pd.to_datetime(
                            s.astype("Int64"), format="%Y%m%d", errors="coerce"
                        )
                        .dt.strftime("%Y-%m-%d")
                        .astype(str)
                    )
                except:
                    df[col] = s.astype(str)

            # Handle object/string types
            elif pd.api.types.is_object_dtype(s):
                try:
                    # Try to parse as datetime and standardize
                    df[col] = (
                        pd.to_datetime(s, errors="coerce")
                        .dt.strftime("%Y-%m-%d")
                        .astype(str)
                    )
                except:
                    # If parsing fails, convert to string as-is
                    df[col] = s.astype(str)

            # For any other type, just convert to string
            else:
                df[col] = s.astype(str)

    return df


def sanitize_value(val):
    if val is None or pd.isna(val):
        return 0.0
    try:
        float_val = float(val)
        if np.isinf(float_val):
            return 0.0
        return float_val
    except (ValueError, TypeError):
        return 0.0


def get_season_bounds(date: datetime):
    year = date.year
    month = date.month

    if month >= 11:
        season_start = datetime(year, 11, 1)
        season_end = datetime(year + 1, 3, 31)
    else:
        season_start = datetime(year - 1, 11, 1)
        season_end = datetime(year, 3, 31)

    return season_start, season_end


def get_current_season(today: datetime):
    year = today.year
    month = today.month

    if month in [11, 12]:
        season_start = datetime(year, 11, 1)
        season_end = datetime(year + 1, 3, 31)
    elif month in [1, 2, 3]:
        season_start = datetime(year - 1, 11, 1)
        season_end = datetime(year, 3, 31)
    else:
        season_start = datetime(year - 1, 11, 1)
        season_end = datetime(year, 3, 31)

    return season_start, season_end


def is_date_in_season(
    date_str: str, season_start: datetime, season_end: datetime
) -> bool:
    date = datetime.strptime(date_str, "%Y-%m-%d")
    return season_start <= date <= season_end


def calculate_date_range(start_date_str, end_date_str=None):
    """Calculate the date range to process.

    Args:
        start_date_str: Required start date in YYYY-MM-DD format
        end_date_str: Optional end date in YYYY-MM-DD format.
                     If not provided, uses today (if past 15:00 UTC) or yesterday

    Returns:
        Tuple of (start_date, end_date) as date objects
    """
    # Parse start date (required)
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(
            f"Invalid DATE_START format: {start_date_str}. Expected YYYY-MM-DD"
        )

    # Parse or calculate end date
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit(
                f"Invalid DATE_END format: {end_date_str}. Expected YYYY-MM-DD"
            )
    else:
        # Default to today if past 15:00 UTC, else yesterday
        now = datetime.utcnow()
        if now.hour >= 15:
            end_date = now.date()
        else:
            end_date = (now - timedelta(days=1)).date()

    # Validate date range
    if start_date > end_date:
        raise SystemExit(
            f"DATE_START ({start_date}) cannot be after DATE_END ({end_date})"
        )

    return start_date, end_date


def generate_dates(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def retry_with_backoff(func, *args, max_retries=3, delay=30, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                print(
                    json.dumps(
                        {"retry": attempt + 1, "error": str(e), "waiting": delay}
                    )
                )
                time.sleep(delay)
            else:
                raise


def connect() -> pyodbc.Connection:
    username = get_secret("MCP_DB_USERNAME")
    password = get_secret("MCP_DB_PASSWORD")
    server = get_secret("MCP_DB_SERVER")
    port = get_secret("MCP_DB_PORT", "1433")
    dbname = get_secret("MCP_DB_NAME")

    driver = "ODBC Driver 18 for SQL Server"
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={dbname};"
        f"UID={username};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=60;"
    )
    return pyodbc.connect(conn_str)


def run_proc(
    cur, proc_key: str, m: dict, date_start: datetime, date_end: datetime
) -> pd.DataFrame:
    sql = STORED_PROCS[proc_key]
    if proc_key == "revenue":
        params = (m["dbName"], int(m["groupNum"]), date_start, date_end)
    else:
        params = (m["resortName"], date_start, date_end)

    def execute():
        cur.execute(sql, params)
        return cursor_to_df(cur)

    return retry_with_backoff(execute)


def process_hourly_payroll(df: pd.DataFrame) -> dict:
    contract_totals = {}

    for _, row in df.iterrows():
        dept_code = str(row.get("department", row.get("deptcode", ""))).strip()
        if not dept_code:
            continue

        start_punch = row.get("start_punchtime") or row.get("start_punch_time")
        end_punch = row.get("end_punchtime") or row.get("end_punch_time")
        rate = sanitize_value(row.get("rate", 0))
        dollar_amount = sanitize_value(row.get("dollaramount", 0))

        hours_col = row.get("hours")
        if hours_col is not None and sanitize_value(hours_col) > 0:
            hours = sanitize_value(hours_col)
        elif pd.notna(start_punch) and pd.notna(end_punch):
            try:
                if not isinstance(start_punch, pd.Timestamp):
                    start_punch = pd.to_datetime(start_punch)
                if not isinstance(end_punch, pd.Timestamp):
                    end_punch = pd.to_datetime(end_punch)

                time_diff = (end_punch - start_punch).total_seconds() / 3600.0
                hours = max(0.0, time_diff)
            except:
                hours = 0.0
        else:
            hours = 0.0

        wage = (hours * rate) + dollar_amount
        contract_totals[dept_code] = contract_totals.get(dept_code, 0.0) + wage

    return contract_totals


def process_salary_payroll(df: pd.DataFrame) -> dict:
    salary_totals = {}

    for _, row in df.iterrows():
        dept_code = str(row.get("deptcode", row.get("department", ""))).strip()
        if not dept_code:
            continue

        total = sanitize_value(row.get("total", 0))
        salary_totals[dept_code] = salary_totals.get(dept_code, 0.0) + total

    return salary_totals


def process_historical_payroll(df: pd.DataFrame) -> pd.DataFrame:
    records = []

    for _, row in df.iterrows():
        dept_code = str(row.get("department", row.get("deptcode", ""))).strip()
        dept_title = str(row.get("DepartmentTitle", "")).strip()
        total = round(
            sanitize_value(row.get("total", 0)), 1
        )  # Round to 1 decimal place

        if dept_code:
            records.append(
                {"departmentTitle": dept_title, "deptCode": dept_code, "payroll": total}
            )

    return pd.DataFrame(records)


def combine_payroll_totals(
    contract_totals: dict,
    salary_totals: dict,
    contract_df: pd.DataFrame,
    salary_df: pd.DataFrame,
) -> pd.DataFrame:
    all_dept_codes = set(contract_totals.keys()) | set(salary_totals.keys())
    records = []

    dept_titles = {}
    for _, row in contract_df.iterrows():
        dept_code = str(row.get("department", "")).strip()
        dept_title = str(row.get("DepartmentTitle", "")).strip()
        if dept_code and dept_title:
            dept_titles[dept_code] = dept_title

    for _, row in salary_df.iterrows():
        dept_code = str(row.get("deptcode", "")).strip()
        dept_title = str(row.get("DepartmentTitle", "")).strip()
        if dept_code and dept_title:
            dept_titles[dept_code] = dept_title

    for dept_code in all_dept_codes:
        contract_total = contract_totals.get(dept_code, 0.0)
        salary_total = salary_totals.get(dept_code, 0.0)
        final_payroll = round(
            contract_total + salary_total, 1
        )  # Round to 1 decimal place

        records.append(
            {
                "departmentTitle": dept_titles.get(dept_code, ""),
                "deptCode": dept_code,
                "payroll": final_payroll,
            }
        )

    return pd.DataFrame(records)


def write_df(df: pd.DataFrame, out_path: str, metadata: dict, fetched_at: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df = normalize_punchtime_cols(df)
    df = normalize_date_cols(df)

    for k, v in metadata.items():
        df[f"_meta_{k}"] = v

    df["fetchedAt"] = fetched_at

    df.to_parquet(out_path, index=False)


def process_and_save_payroll(
    cur,
    m: dict,
    date_start: datetime,
    date_end: datetime,
    outdir: str,
    fetched_at: str,
    active_payroll_date: str,
    error_collection: dict = None,
    date_str: str = None,
):
    resort = safe_name(m["resortName"])

    # Use date_str for comparisons and file paths (if not provided, extract from datetime)
    if date_str is None:
        date_str = date_start.strftime("%Y-%m-%d")

    # Check if date is >= active payroll date
    is_active = date_str >= active_payroll_date

    try:
        if is_active:
            # Active payroll: use detailed SPs
            contract_df = run_proc(cur, "payroll", m, date_start, date_end)
            salary_df = run_proc(cur, "payroll_salary", m, date_start, date_end)

            contract_totals = process_hourly_payroll(contract_df)
            salary_totals = process_salary_payroll(salary_df)

            processed_df = combine_payroll_totals(
                contract_totals, salary_totals, contract_df, salary_df
            )
        else:
            # Historical payroll: use history SP
            history_df = run_proc(cur, "payroll_history", m, date_start, date_end)
            processed_df = process_historical_payroll(history_df)

        base = os.path.join(
            outdir,
            "proc=processed_payroll",
            f"resort={resort}",
            f"date={date_str}",
        )
        out_path = os.path.join(base, "data.parquet")

        meta = {
            "proc": "processed_payroll",
            "resort": resort,
            "date": date_str,
            "rowcount": str(len(processed_df)),
        }

        print(json.dumps({"writing": out_path, "rows": len(processed_df)}))
        write_df(processed_df, out_path, meta, fetched_at)

    except Exception as e:
        error_msg = {
            "error": "processed_payroll_failed",
            "resort": m["resortName"],
            "date": date_str,
            "message": str(e),
        }
        print(json.dumps(error_msg))

        # Add to error collection if provided
        if error_collection is not None:
            error_collection["errors"].append(
                {
                    "type": "payroll_processing_failure",
                    "date": date_str,
                    "resort": m["resortName"],
                    "error_message": str(e),
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )


def upload_to_s3(local_dir: str, bucket: str, prefix: str, endpoint: str):
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=get_secret("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=get_secret("AWS_SECRET_ACCESS_KEY"),
        region_name=get_secret("AWS_DEFAULT_REGION", "us-east-1"),
    )

    uploaded_files = []
    failed_uploads = []

    for root, _, files in os.walk(local_dir):
        for file in files:
            local_path = os.path.join(root, file)
            relative_path = os.path.relpath(local_path, local_dir)
            s3_key = os.path.join(prefix, relative_path).replace("\\", "/")

            print(json.dumps({"uploading": s3_key}))

            try:
                # Upload with retry logic
                def upload_file():
                    s3_client.upload_file(local_path, bucket, s3_key)

                retry_with_backoff(upload_file, max_retries=3, delay=10)

                # Verify upload succeeded by checking if file exists
                def verify_upload():
                    s3_client.head_object(Bucket=bucket, Key=s3_key)

                retry_with_backoff(verify_upload, max_retries=2, delay=5)

                uploaded_files.append(s3_key)
                print(json.dumps({"upload_verified": s3_key}))

            except Exception as e:
                print(json.dumps({"upload_error": s3_key, "message": str(e)}))
                failed_uploads.append(s3_key)

    # Check if any uploads failed
    if failed_uploads:
        raise RuntimeError(
            f"Failed to upload {len(failed_uploads)} file(s): {failed_uploads}. "
            f"Successfully uploaded {len(uploaded_files)} file(s)."
        )

    print(
        json.dumps(
            {
                "upload_summary": {
                    "total_files": len(uploaded_files),
                    "successful": len(uploaded_files),
                    "failed": len(failed_uploads),
                }
            }
        )
    )


def load_resort_config():
    resort_name = os.environ["RESORT_NAME"]
    resort_db_name = os.environ["RESORT_DB_NAME"]
    resort_group_num_str = os.environ["RESORT_GROUP_NUM"]

    # Convert group number to int
    try:
        group_num = int(resort_group_num_str)
    except ValueError:
        raise SystemExit(
            f"RESORT_GROUP_NUM must be a valid integer, got: {resort_group_num_str}"
        )

    return {"resortName": resort_name, "dbName": resort_db_name, "groupNum": group_num}


def load_procs():
    procs_str = os.environ.get(
        "PROCS", "revenue,payroll,payroll_salary,payroll_history,budget,visits,weather"
    )
    selected = [p.strip() for p in procs_str.split(",") if p.strip()]
    for p in selected:
        if p not in STORED_PROCS:
            raise SystemExit(f"Unknown proc '{p}'. Valid: {list(STORED_PROCS.keys())}")
    return selected


def send_errors_to_webhook(error_report: dict, webhook_url: str):
    """Send error report to n8n webhook via POST request."""
    if not error_report.get("errors"):
        # No errors to report
        return

    try:
        print(json.dumps({"sending_error_report_to_webhook": webhook_url}))

        response = requests.post(
            webhook_url,
            json=error_report,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

        response.raise_for_status()

        print(
            json.dumps(
                {
                    "webhook_notification_sent": True,
                    "webhook_status_code": response.status_code,
                }
            )
        )

    except Exception as webhook_error:
        # Don't fail the pipeline if webhook notification fails
        print(
            json.dumps(
                {
                    "webhook_notification_failed": True,
                    "webhook_error": str(webhook_error),
                    "note": "Pipeline status unchanged, webhook notification only",
                }
            )
        )


def main():
    start_date_str = os.environ["DATE_START"]  # Required
    end_date_str = os.environ.get("DATE_END")  # Optional
    active_payroll_date = os.environ["ACTIVE_PAYROLL_DATE"]  # Required

    # Validate active payroll date format
    try:
        datetime.strptime(active_payroll_date, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(
            f"Invalid ACTIVE_PAYROLL_DATE format: {active_payroll_date}. Expected YYYY-MM-DD"
        )

    outdir = os.environ.get("OUTDIR", "/work/out")
    bucket = os.environ["MINIO_BUCKET"]
    prefix = os.environ.get("MINIO_PREFIX", "mcp_parquet")
    endpoint = os.environ["MINIO_ENDPOINT"]

    resort_config = load_resort_config()
    selected_procs = load_procs()

    # Initialize error collection
    error_collection = {
        "pipeline": "data_ingestion",
        "resort": resort_config["resortName"],
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": [],
    }

    # Webhook URL for error reporting
    webhook_url = os.environ.get(
        "ERROR_WEBHOOK_URL",
        "https://n8n-v2.mcp.hyperplane.dev/webhook/data-ingestion-errors",
    )

    # Calculate date range (DATE_START is required, DATE_END is optional)
    start_date, end_date = calculate_date_range(start_date_str, end_date_str)

    print(
        json.dumps(
            {
                "date_range": {
                    "start": str(start_date),
                    "end": str(end_date),
                    "total_days": (end_date - start_date).days + 1,
                }
            }
        )
    )

    conn = connect()
    total_dates = 0
    failed_dates = []

    try:
        cur = conn.cursor()

        for date in generate_dates(start_date, end_date):
            total_dates += 1
            date_str = date.strftime("%Y-%m-%d")

            # Create datetime objects with specific times for SP calls
            date_ini = datetime(
                date.year, date.month, date.day, 0, 0, 0
            )  # Start of day
            date_end_dt = datetime(
                date.year, date.month, date.day, 23, 59, 59
            )  # End of day

            print(json.dumps({"processing_date": date_str, "date_count": total_dates}))

            fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            resort = safe_name(resort_config["resortName"])

            for proc_key in selected_procs:
                df = run_proc(cur, proc_key, resort_config, date_ini, date_end_dt)

                base = os.path.join(
                    outdir,
                    f"proc={proc_key}",
                    f"resort={resort}",
                    f"date={date_str}",
                )
                out_path = os.path.join(base, "data.parquet")

                meta = {
                    "proc": proc_key,
                    "resort": resort,
                    "date": date_str,
                    "rowcount": str(len(df)),
                }

                print(json.dumps({"writing": out_path, "rows": len(df)}))
                write_df(df, out_path, meta, fetched_at)

            payroll_procs = {"payroll", "payroll_salary", "payroll_history"}
            if payroll_procs & set(selected_procs):
                process_and_save_payroll(
                    cur,
                    resort_config,
                    date_ini,
                    date_end_dt,
                    outdir,
                    fetched_at,
                    active_payroll_date,
                    error_collection,
                    date_str,
                )

            # Upload to S3 with error handling
            print(json.dumps({"uploading_date": date_str}))
            try:
                upload_to_s3(outdir, bucket, prefix, endpoint)

                # Only cleanup local files if upload succeeded
                print(json.dumps({"cleaning_local": outdir}))
                for item in os.listdir(outdir):
                    item_path = os.path.join(outdir, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)

            except Exception as upload_error:
                error_details = {
                    "type": "upload_failure",
                    "date": date_str,
                    "resort": resort_config["resortName"],
                    "error_message": str(upload_error),
                    "local_files_preserved": outdir,
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }

                print(
                    json.dumps(
                        {
                            "upload_failed_for_date": date_str,
                            "error": str(upload_error),
                            "local_files_preserved": outdir,
                        }
                    )
                )

                error_collection["errors"].append(error_details)
                failed_dates.append(date_str)
                # Continue processing other dates despite this failure

    finally:
        conn.close()

    # Report final status
    if failed_dates:
        final_status = {
            "status": "completed_with_failures",
            "total_dates_processed": total_dates,
            "successful_dates": total_dates - len(failed_dates),
            "failed_dates": failed_dates,
            "failed_count": len(failed_dates),
            "location": f"s3://{bucket}/{prefix}/",
        }
        print(json.dumps(final_status))

        # Add summary to error collection
        error_collection["summary"] = final_status
        error_collection["total_errors"] = len(error_collection["errors"])

        # Send error report to webhook
        send_errors_to_webhook(error_collection, webhook_url)

        raise SystemExit(
            f"Pipeline completed with failures. "
            f"{len(failed_dates)} date(s) failed to upload: {failed_dates}"
        )
    else:
        print(
            json.dumps(
                {
                    "status": "completed",
                    "total_dates_processed": total_dates,
                    "location": f"s3://{bucket}/{prefix}/",
                }
            )
        )

        # Send error report if there were any errors (e.g., payroll processing)
        # even if overall pipeline succeeded
        if error_collection["errors"]:
            error_collection["summary"] = {
                "status": "completed_with_non_critical_errors",
                "total_dates_processed": total_dates,
                "location": f"s3://{bucket}/{prefix}/",
            }
            error_collection["total_errors"] = len(error_collection["errors"])
            send_errors_to_webhook(error_collection, webhook_url)


if __name__ == "__main__":
    main()
