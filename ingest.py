"""Bounded refresh plus incremental updates, including soft deletions."""
import argparse
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pyodbc
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://vetone-api.kinddesert-e2c4fbdc.westeurope.azurecontainerapps.io"


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Source timestamp has no timezone offset")
    return parsed


class VetOne:
    def __init__(self, client_id, secret):
        self.client_id, self.secret = client_id, secret
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"], respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def authenticate(self):
        response = self.session.post(
            f"{BASE_URL}/auth/token",
            json={"client_id": self.client_id, "client_secret": self.secret},
            timeout=(10, 60))
        response.raise_for_status()
        self.session.headers["Authorization"] = (
            "Bearer " + response.json()["access_token"])

    def get(self, path, params=None):
        response = self.session.get(BASE_URL + path, params=params, timeout=(10, 60))
        if response.status_code == 401:
            self.authenticate()
            response = self.session.get(BASE_URL + path, params=params, timeout=(10, 60))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError(f"Unexpected response structure at {path}")
        return payload

    def appointments(self, start=None, end=None, updated_since=None):
        params = {"limit": 100, "include_deleted": "true"}
        if start is not None and end is not None:
            params.update({"from": start.isoformat(), "to": end.isoformat()})
        if updated_since is not None:
            params["updated_since"] = updated_since.isoformat()
        records, cursors = {}, set()
        pages, duplicates = 0, 0
        while True:
            payload = self.get("/v1/appointments", params)
            pages += 1
            for record in payload["data"]:
                if updated_since is not None and timestamp(record["updated_at"]) < updated_since:
                    raise ValueError("API returned updated_at before the requested updated_since")
                identifier = record["id"]
                if identifier in records:
                    duplicates += 1
                    previous = records[identifier]
                    if timestamp(record["updated_at"]) < timestamp(previous["updated_at"]):
                        continue
                    if (timestamp(record["updated_at"]) == timestamp(previous["updated_at"])
                            and record != previous):
                        raise ValueError(f"Conflicting versions of appointment {identifier}")
                records[identifier] = record
            print(f"Fetched page {pages}: {len(records)} unique appointments")
            cursor = payload.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("Invalid or repeated pagination cursor")
            cursors.add(cursor)
            params = {**params, "cursor": cursor}
        return list(records.values()), pages, duplicates


def appointment_row(record, clinics, start=None, end=None):
    clinic = clinics[record["clinic_id"]]
    scheduled = timestamp(record["scheduled_at"])
    local_date = scheduled.astimezone(ZoneInfo(clinic["timezone"])).date()
    # Check the date-filter assumption before writing anything.
    if start is not None and end is not None and not start <= local_date <= end:
        raise ValueError("API returned an appointment outside the requested local dates")
    updated = timestamp(record["updated_at"])
    created = timestamp(record["created_at"]) if record.get("created_at") else None
    deleted = timestamp(record["deleted_at"]) if record.get("deleted_at") else None
    cancelled = timestamp(record["cancelled_at"]) if record.get("cancelled_at") else None
    # Bind ISO strings and explicitly CAST in SQL: preserve offsets through ODBC.
    return (record["id"], record["clinic_id"], record.get("vet_id"),
            record.get("service"), scheduled.isoformat(), local_date.isoformat(),
            record.get("duration_minutes"), record["status"],
            created.isoformat() if created else None,
            deleted.isoformat() if deleted else None,
            cancelled.isoformat() if cancelled else None, updated.isoformat())


def upsert(cursor, table, columns, values, timestamp_columns=(), date_columns=(), newer_only=False):
    # Table/column names are constants supplied by this program, never API input.
    expressions = ["CAST(? AS datetimeoffset(7))" if c in timestamp_columns
                   else "CAST(? AS date)" if c in date_columns else "?" for c in columns]
    assignments = ", ".join(f"{c} = {e}" for c, e in zip(columns[1:], expressions[1:]))
    predicate = f"{columns[0]} = ?"
    update_values = (*values[1:], values[0])
    if newer_only:
        predicate += " AND updated_at <= CAST(? AS datetimeoffset(7))"
        update_values += (values[-1],)
    cursor.execute(f"UPDATE dbo.{table} SET {assignments} WHERE {predicate}", update_values)
    # An older source version must not cause a second INSERT.
    cursor.execute(f"SELECT 1 FROM dbo.{table} WHERE {columns[0]} = ?", values[0])
    if cursor.fetchone() is None:
        cursor.execute(f"INSERT INTO dbo.{table} ({', '.join(columns)}) "
                       f"VALUES ({', '.join(expressions)})", values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--to-date", required=True, type=date.fromisoformat)
    parser.add_argument("--server", default=r"localhost\SQLEXPRESS")
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error("from-date must be on or before to-date")

    client_id = os.environ.get("VETONE_CLIENT_ID") or input("Client ID: ").strip()
    secret = os.environ.get("VETONE_CLIENT_SECRET") or input("Client secret (visible): ").strip()
    api = VetOne(client_id, secret)
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={args.server};DATABASE=Nordvet;Trusted_Connection=yes;"
        "Encrypt=yes;TrustServerCertificate=yes;")
    connection = pyodbc.connect(connection_string, timeout=10, autocommit=False)
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; SET NOCOUNT ON; IF @@TRANCOUNT = 0 BEGIN TRANSACTION;")
        # Serialise the whole run, including reading the checkpoint and extraction.
        cursor.execute("""DECLARE @result int;
            EXEC @result = sys.sp_getapplock @Resource='NordvetLoader',
              @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=0;
            IF @result < 0 THROW 50001, 'Another loader is running', 1;""")
        started_at = datetime.now(timezone.utc)
        cursor.execute("""SELECT CONVERT(varchar(40), watermark, 127)
                          FROM dbo.sync_state WHERE source_name = 'appointments';""")
        checkpoint = cursor.fetchone()
        if checkpoint:
            since = timestamp(checkpoint[0]) - timedelta(minutes=5)
        else:
            # Migration from the original loader: replay changes since its first run.
            cursor.execute("SELECT CONVERT(varchar(40), MIN(started_at), 127) FROM dbo.load_run;")
            first_run = cursor.fetchone()[0]
            since = (timestamp(first_run) if first_run else started_at) - timedelta(minutes=5)
        print(f"Incremental replay starts at {since.isoformat()}")

        api.authenticate()
        clinic_records = api.get("/v1/clinics")["data"]
        clinics = {c["id"]: c for c in clinic_records}
        if len(clinics) != len(clinic_records):
            raise ValueError("Duplicate clinic IDs")
        print("Refreshing selected appointment dates, including deleted records...")
        appointments, pages, duplicates = api.appointments(args.from_date, args.to_date)
        rows = [appointment_row(a, clinics, args.from_date, args.to_date) for a in appointments]
        print("Fetching updates across appointment dates...")
        changes, change_pages, change_duplicates = api.appointments(updated_since=since)
        changed_rows = [appointment_row(a, clinics) for a in changes]
        # Persist the bounded snapshot, then changes. Version comparison prevents regression.
        for c in clinic_records:
            upsert(cursor, "clinic",
                   ["clinic_id", "name", "city", "country", "timezone", "active", "opened_on"],
                   (c["id"], c["name"], c.get("city"), c["country"], c["timezone"],
                    c["active"], c.get("opened_on")), date_columns=["opened_on"])
        columns = ["appointment_id", "clinic_id", "vet_id", "service", "scheduled_at",
                   "appointment_local_date", "duration_minutes", "status", "created_at",
                   "deleted_at", "cancelled_at", "updated_at"]
        for row in [*rows, *changed_rows]:
            upsert(cursor, "appointment", columns, row,
                   timestamp_columns=["scheduled_at", "created_at", "deleted_at", "cancelled_at", "updated_at"],
                   date_columns=["appointment_local_date"], newer_only=True)
        cursor.execute("""INSERT INTO dbo.load_run
            (started_at, completed_at, from_date, to_date, clinic_count, appointment_count, change_count)
            VALUES (CAST(? AS datetimeoffset(7)), SYSDATETIMEOFFSET(),
                    CAST(? AS date), CAST(? AS date), ?, ?, ?)""",
            started_at.isoformat(), args.from_date.isoformat(), args.to_date.isoformat(),
            len(clinics), len(rows), len(changed_rows))
        # Move the checkpoint to RUN START, not run completion. Changes during the
        # extraction are replayed next time. Checkpoint commits with the data.
        cursor.execute("""UPDATE dbo.sync_state SET watermark = CAST(? AS datetimeoffset(7))
                          WHERE source_name = 'appointments';""", started_at.isoformat())
        if not checkpoint:
            cursor.execute("""INSERT INTO dbo.sync_state (source_name, watermark)
                              VALUES ('appointments', CAST(? AS datetimeoffset(7)));""",
                           started_at.isoformat())
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        api.session.close()
        connection.close()
    print(f"Committed {len(clinics)} clinics and {len(rows)} appointments "
          f"from {pages} pages ({duplicates} duplicate records encountered).")
    print(f"Applied {len(changed_rows)} incremental records from {change_pages} pages "
          f"({change_duplicates} duplicate records encountered).")
    print("Deleted records are retained in SQL and excluded by the reporting view.")


if __name__ == "__main__":
    main()
