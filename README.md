# Nordvet: a first no-show reporting slice

A local demonstration of **VetOne API → Python → SQL Server → Power BI**. It provides clinic-level results for a selected appointment date, including no-shows, scheduled time associated with them, and outstanding outcomes. Results can change as clinics complete or correct their records.

The priority is Mette's morning view of yesterday. Daily batch processing is sufficient for that decision; a real-time requirement would need a specific operational use case. SQL Server fits Nordvet's SQL/Power BI skills and offers a route to Azure SQL. Python handles the vendor API and timezone conversion; SQL defines the reporting rules; Power BI presents the results. This is a first slice, not a deployed production platform.

## How to run locally

**Prerequisites:** SQL Server Express or another compatible SQL Server instance, SSMS, Microsoft ODBC Driver 18 for SQL Server, and Power BI Desktop. The Windows account needs permissions to create the objects and read/write the database.

1. Create the database in SSMS if it does not exist:

   ```sql
   IF DB_ID(N'Nordvet') IS NULL
       CREATE DATABASE Nordvet;
   GO
   ```

2. Run `setup.sql`, then `reporting.sql` in SSMS. Both target `Nordvet`. Setup creates missing tables/additional columns without deleting existing data; the reporting script replaces the view and runs example queries.

3. In Anaconda Prompt, from the project directory:

   ```text
   conda create -n nordvet python=3.12 pip -y
   conda activate nordvet
   pip install -r requirements.txt
   ```

   Anaconda is just one way to run python code, but any local instance with the required packages will work.

4. Run the loader. This example loads yesterday plus its preceding 28 days

   ```text
   python ingest.py --from-date 2026-09-15 --to-date 2026-10-13
   ```

   Update the explicit dates for later runs. `--server` overrides the default `localhost\SQLEXPRESS`. Database name, driver and API base URL are fixed in the implementation.

5. Supply credentials at the prompts, or set `VETONE_CLIENT_ID` and `VETONE_CLIENT_SECRET` to the correct values.

6. Run `verify.sql`. Adjust its fixed example dates to match the load. Open the Power BI report, connect its SQL source to your server and `Nordvet`, and select **Refresh**. Power BI uses Import: a pipeline run does not refresh the report's cached data. Automatic scheduling is not implemented.

**Files:** `ingest.py` is the loader, `setup.sql` defines storage, `reporting.sql` defines `dbo.v_clinic_day`, `verify.sql` contains manual checks. The supplied report is `Nordvet.pbix`. 

## Python implementation: choices and assumptions

### Extraction and checkpoints

Each run fetches all clinics, re-fetches **every appointment page in the explicit date range**, then fetches changes using `updated_since` without appointment-date filters. Both appointment queries use `include_deleted=true`. These are read operations; the loader does not modify VetOne.

- `from`/`to` are treated as inclusive scheduled local-date bounds (verified). Records outside the requested local dates fail the bounded load.
- The saved watermark is the successful run's **start time**, in UTC. The next run starts five minutes earlier. Data, successful-run metadata and the watermark commit together; a failure rolls back all three. This replays changes occurring during extraction, subject to vendor delivery semantics.
- Without a checkpoint, replay starts five minutes before the earliest existing successful run, or this run's start for a fresh database. Older historical coverage comes from the explicit date-range extraction, not this bootstrap replay.
- The five-minute overlap is a pragmatic allowance for boundaries and modest clock/visibility differences, not a guarantee against arbitrary publication delays. Machine and vendor clocks are assumed sufficiently aligned.
- `updated_since` is assumed to cover all scheduled dates when supplied. The API's implicit seven-day window, deletion retention and cursor snapshot guarantees are **not fully verified**. Rejecting a returned timestamp older than the filter detects some violations, but cannot detect omitted records.

Pagination preserves filters and treats cursors as opaque. Repeated/invalid nonempty cursors fail the run. Records are deduplicated by ID: the newest `updated_at` wins; different records with equal timestamps within one extraction fail. SQL updates accept equal timestamps but reject older versions; conflicting equal versions across separate extractions are not detected.

Re-fetching is an **upsert refresh**, not replacement of the date range. Absence is not interpreted as deletion. Hard-deleted records, expired tombstones or a missed rescheduling update can leave stale rows. Periodic reconciliation would be needed if the vendor's change feed is unreliable; it is not scheduled here.

### Time, records and failures

Appointments are reported by their **scheduled date in the clinic’s timezone**, rather than their creation, update or extraction date. Source timestamps must include timezone offsets. Late updates and rescheduling modify the existing record; previous versions are not retained.

API responses are assumed to contain complete current records with stable IDs. Missing optional deletion or cancellation timestamps are treated as NULL. Every appointment must reference a known clinic. Invalid required data stops the load, and patient references are omitted from storage.

Transient API failures are retried, and expired tokens are renewed once. Database writes, run metadata and the checkpoint commit together or roll back on failure. Concurrent loader runs are prevented. In-memory processing and individual row writes prioritise simplicity over large-scale performance.

## SQL implementation and metric definitions

SQL stores current clinic and appointment records, successful extraction runs and an incremental checkpoint. Primary and foreign keys enforce unique IDs and valid clinic references.

The reporting view contains **one row per clinic per successfully extracted local date**, including dates with zero appointments. Dates without extraction coverage remain absent. Current clinic attributes are used for historical reporting.

**Deleted appointments are excluded from all operational metrics.**

| Metric | Definition |
|---|---|
| Appointments | All nondeleted appointments, including cancellations and booked records |
| Eligible outcomes | Completed appointments plus no-shows |
| Recorded no-show rate | No-shows divided by eligible outcomes; blank when the denominator is zero |
| Unresolved appointments | Past appointments still marked `booked` |
| No-show minutes | Scheduled duration of recorded no-shows; missing durations are flagged separately |

Cancellations are reported separately, and unresolved appointments are not assumed to be no-shows. Unrecognised statuses are also surfaced separately. Overall rates divide summed counts rather than average percentages.

The freshness timestamp represents the latest full extraction covering each date, not finalised outcomes or every subsequent incremental correction. Power BI displays updated SQL results only after refresh.

## Report interpretation and deliberate exclusions

Power BI builds on the SQL view. It consists of four pages with each their own intended use. The first page shows an immediate overview of activities from the previous day, that allows an overview of how many appointments were completed, canceled or had no-shows (the map here is incomplete due to misplacement of dots from the city names). The second gives an overview of the 10 clinics with the most lost time due to no-shows which is assumed to correlate with  lost revenue. The third page compares no-show rates to a 28-day baseline giving an indication of irregular behaviour and the fourth page presents an economic scenario.. The economic scenario uses **assumed contribution margin per recovered hour × assumed recovered share × scheduled no-show hours**, less intervention cost. Costs must match selected dates/clinics; break-even share is cost divided by hours times hourly contribution, which gives a rough idea of how many hours must be covered for an intervention to be valuable. All scenario amounts use DKK, including Swedish clinics under a common illustrative assumption; no exchange conversion or actual pricing is implemented.


## Validation and what would change at Nordvet

Use `verify.sql` for source-category/count checks, and compare SQL totals with the same Power BI date/activity filters. Counts are live, so sample figures are not fixed acceptance criteria.

For Nordvet use a daily scheduled Python Azure Function, Azure SQL Database and Power BI Service. Before deployment we could replace the local Windows-authentication connection with an appropriate Azure connection/identity, validate database certificates (`TrustServerCertificate=yes` is only the local-demo shortcut), store the API secret securely, configure scheduling/report refresh, and alert on failures/stale coverage. database, compute and reporting costs should be assesed against the expected benefit or follow a budget.

This implementation has no scheduler, persisted failure log, alerts, history/audit snapshots, deployment automation or production access-control configuration. Those are explicit next steps for an ongoing client service.
