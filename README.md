# Nordvet: SQL Server + Power BI

## Run locally (Windows)

Requires the existing Nordvet database, ODBC Driver 18 and Python 3.12.

1. Run setup.sql in SSMS: creates missing tables and migrates existing tables.
2. Run reporting.sql in SSMS: creates or replaces dbo.v_clinic_day.
3. In Anaconda Prompt, from the project directory:

```text
conda activate nordvet
python -m pip install -r requirements.txt
python ingest.py --from-date 2026-10-05 --to-date 2026-10-11
```

Optional --server overrides localhost\SQLEXPRESS. VETONE_CLIENT_ID and
VETONE_CLIENT_SECRET environment variables replace interactive prompts. The
local secret prompt is visible because hidden entry did not work in the user's
terminal. Credentials, virtual environments and data dumps are not included.
Run verify.sql, then Refresh in Power BI Desktop.

## Synchronisation decisions

Every run fully refreshes the supplied local-date range with include_deleted=true,
then fetches updated_since changes without appointment-date filters. On the first
upgraded run it replays changes from five minutes before the original loader's
first successful start (or this run's start for an empty database). Later runs
replay from five minutes before the saved watermark. The watermark is the current
run's START time; data and watermark commit together. Failure rolls back both.
Changes during extraction are replayed on the next run. The overlap handles
boundary duplicates and modest clock/visibility differences, not arbitrarily
late vendor change publication.

Pagination uses opaque cursors and preserves the query parameters. GETs retry
429 and transient 5xx responses and honour Retry-After. A 401 triggers one token
renewal. Duplicate appointment IDs use the newest updated_at; conflicting equal
versions within one paginated extraction fail. A SQL application lock serialises
runs. Row-by-row parameterised upserts reject older stored versions. The SQL
transaction includes extraction, to protect the checkpoint from concurrent runs;
this is acceptable for the laptop slice but should be shortened if loads grow.

Source statuses are preserved. deleted_at is independent of status: deleted
appointments remain in SQL but are excluded from all operational metrics. Missing
deleted_at/cancelled_at fields mean NULL. These are full-record responses, not
partial patches; a later full-record restoration clears deleted_at. This and
updated_at being the source version timestamp are assumptions to verify.
Patient references are omitted. All clinics, including inactive clinics, are
retained. Source offsets are preserved; local dates use clinic IANA timezones.
Python tzdata supports Windows. Single-day testing supports inclusive date bounds.
No appointments on a date does not establish clinic opening hours.

## Reporting

One row per clinic per fully extracted local date, including zero-appointment
rows. Overlapping successful extraction ranges do not duplicate daily counts.
Dates before clinic opening are excluded. The view uses current clinic activity,
not a historical active flag. Date expansion supports up to 100,000 days per run.

No-show rate = no_show / (completed + no_show). Cancelled, deleted and booked
appointments are excluded from that denominator. Past booked appointments are
unresolved; other statuses are surfaced separately. Aggregate rates divide sums,
never average percentages. A zero denominator yields NULL/blank. No-show minutes
are scheduled capacity associated with no-shows, not proven monetary loss.
Missing no-show durations are counted separately. No price fields were observed.
last_loaded_at_utc is the last full date extraction, not outcome finalisation.

In Power BI Import dbo.v_clinic_day; after each successful pipeline run, refresh
Power BI's separate imported copy. Metrics can change due to late source updates.

## Remaining assumptions and exclusions

The updated_since endpoint is assumed to return all appointment dates when that
filter is supplied, with deleted records retained and updated_at advanced on
changes. Verify this against an older appointment update before claiming full
historical change coverage. Cursor snapshot guarantees, deletion retention,
clock skew and arbitrary visibility delays are unverified. A complete historical
backfill is not implemented: only explicitly extracted dates have coverage, even
if incremental changes introduce records on other dates. Periodically re-fetch
the last 30 days and reconcile older dates if vendor change delivery is unreliable.
Tombstones are assumed to contain the full appointment fields observed in the
sample; incomplete tombstones fail the load rather than being silently skipped.
This small implementation holds each extraction in memory. Bulk staging,
alerting, historical clinic dimensions and revenue estimates are omitted.
Failure details are terminal errors; load_run records only successes.

## Nordvet deployment

Proposed: daily scheduled Azure Function, Azure SQL Database, Power BI Service.
Keep the API secret outside code, use managed identity for Azure SQL where
supported, and alert on ingestion/refresh failures. Refresh the report after
successful ingestion. Verify existing Power BI licences and network access.
Daily batch serves the morning requirement; real-time requires a concrete use case.

## Validation

Six simulated tests passed for timezone/deletion preservation, pagination, incremental query parameters, ignored-filter detection and transaction commit/rollback. Run `python -m unittest test_loader -v`. SQL Server and
live API verification run on the candidate's Windows laptop. The observed week
contained 4,623 undeleted appointments and 61 additional soft-deleted records;
8 additional deleted records retained status no_show. The operational report
must exclude those deleted no-shows. Counts may change as the source is live.
