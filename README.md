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

4. Run the loader. This example loads yesterday plus its preceding 28 days for the 13 October 2026 demonstration:

   ```text
   python ingest.py --from-date 2026-09-14 --to-date 2026-10-12
   ```

   Update the explicit dates for later runs. `--server` overrides the default `localhost\SQLEXPRESS`. Database name, driver and API base URL are fixed in the implementation.

5. Supply credentials at the prompts, or set `VETONE_CLIENT_ID` and `VETONE_CLIENT_SECRET` in the process environment. The local fallback secret prompt is visible because hidden terminal input was unreliable during development. No credentials belong in source control or submission logs.

6. Run `verify.sql`; adjust its fixed example dates to match the load. Open the Power BI report, connect its SQL source to your server and `Nordvet`, and select **Refresh**. Power BI uses Import: a pipeline run does not refresh the report's cached data. Automatic scheduling is not implemented.

**Files:** `ingest.py` is the loader; `setup.sql` defines storage; `reporting.sql` defines `dbo.v_clinic_day`; `verify.sql` contains manual checks; `test_loader.py` contains simulated tests. `check_database.py` tests the local connection. `inspect_api.py` reads one sample page for a fixed date. `inspect_sync.py` inspects deletion responses and accepts date arguments. Inspection scripts do not load SQL; sample output can contain source identifiers and should not be submitted as a data dump. The supplied report is `Nordvet(1).pbix`. Import-mode PBIX files contain cached report data; a PBIT template omits that cache.

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

Reporting date comes from `scheduled_at` converted to the clinic's IANA timezone, **not** `created_at`, `updated_at` or ingestion time. Timestamp strings must include an offset; naive timestamps fail. `tzdata` supplies timezone rules on Windows. Source offsets are retained through ISO-string parameters and explicit SQL casts. Late outcomes update their original scheduled day; a reschedule updates the same row's date. No original booking/version history is retained.

Records are assumed to be full current representations with stable IDs, including deleted records. Missing optional `deleted_at`/`cancelled_at` means NULL, not "leave unchanged". A newer restored record can therefore clear a deletion. Every appointment must reference a returned clinic; required-field, timestamp or foreign-key failures stop the load. Payload validation is targeted, not a full schema/data-quality framework. Patient references are deliberately omitted from storage.

The HTTP adapter allows up to three retries with backoff and `Retry-After` support. Status/read retries are restricted to GET, including selected transient statuses (429, 500, 502, 503, 504); connection retries can also apply to token requests. Connect/read timeouts are 10/60 seconds; the read timeout accommodates API cold starts. A GET 401 triggers one token renewal and another attempt. Token POST requests have no configured status/read retries. Read timeouts and server-directed waits do not form an overall run-duration limit.

A transaction-owned SQL application lock rejects another loader immediately. The transaction includes network extraction to protect checkpoint consistency. In-memory extraction and row-by-row writes are intentionally simple for this slice; long transactions, memory use and round trips limit scalability.

## SQL implementation and metric definitions

| Object | Grain / purpose |
|---|---|
| `dbo.clinic` | One current row per clinic ID; includes inactive clinics |
| `dbo.appointment` | One current row per appointment ID; preserves soft-deleted records |
| `dbo.load_run` | One successful run; requested range, counts and timestamps |
| `dbo.sync_state` | One appointment-source watermark |
| `dbo.v_clinic_day` | One clinic per explicitly loaded local date, on/after its opening date |

Primary keys prevent duplicate stored IDs; the clinic foreign key enforces references. `nvarchar` preserves Danish/Swedish characters, `bit` stores activity, `date` stores the reporting date, and `datetimeoffset` retains source offsets. Scheduled timestamps use whole-second precision; other timestamps allow fractional seconds. SQL uses parameterised values and code-controlled identifiers. There is no raw-payload store or additional reporting index strategy.

The view expands successful run ranges into dates, merges overlapping runs, crosses dates with clinics and left-joins appointment aggregates. Thus a covered date with zero appointments remains visible, while a date without full extraction coverage is absent. Incremental records outside covered dates are stored but do not imply complete daily reporting coverage. Date expansion supports 100,000 days per run. Current clinic attributes/activity are applied to historical dates; historical clinic dimensions and opening hours are not modelled.

**All operational metrics exclude `deleted_at IS NOT NULL`, regardless of retained status.**

| Metric | Definition |
|---|---|
| Appointments | All nondeleted appointments, including cancellations and booked records |
| Eligible outcomes | `completed + no_show` |
| Recorded no-show rate | `no_show / eligible`; zero denominator returns NULL |
| Unresolved | `booked` with scheduled time before the SQL Server's current time |
| Other statuses | Nondeleted records outside the four recognised statuses |
| No-show minutes | Scheduled duration for `no_show`; NULL duration contributes zero and is counted separately |

Status meaning and the denominator are explicit business assumptions: cancellations are separate, and past booked records are not inferred to be no-shows. A zero rate is different from an undefined rate or missing coverage. Group totals must divide summed counts, never average clinic/day percentages. Count reconciliation uses completed + no-show + cancelled + booked + other; eligible already includes no-shows.

`last_loaded_at_utc` is the latest successful **full extraction** covering each date, converted to UTC without an offset for reporting. It does not prove outcomes are final or capture every later incremental-only correction to that date. Unresolved status is evaluated when SQL is queried; Power BI Import changes only on refresh. Both SQL Server and loader clocks are assumed correct.

## Report interpretation and deliberate exclusions

Power BI builds on the SQL view. The supplied PBIX has one overview page; capacity prioritisation, baseline comparison and economic scenarios are extensions demonstrated in screenshots and should be saved in the final report if included in the submission. Their design rules are: aggregate rates divide sums; the baseline covers the preceding 28 loaded days, excludes the reporting day, and suppresses the difference when either period has pending outcomes. These are conservative reporting rules, not statistical significance testing. Loaded history is not proof of resolved outcomes. Display freshness, selected dates and missing coverage separately. Published relative date filters use UTC; a production morning report should explicitly define its business timezone.

Scheduled no-show hours do not establish idle time, lost revenue or avoidable salary cost. The economic scenario uses **assumed contribution margin per recovered hour × assumed recovered share × scheduled no-show hours**, less intervention cost. Costs must match selected dates/clinics; break-even share is cost divided by hours times hourly contribution. All scenario amounts use DKK, including Swedish clinics under a common illustrative assumption; no exchange conversion or actual pricing is implemented.

City-name geocoding misplaced clinics. The map is a prototype, not a validated basis for operational decisions; a future clinic-ID/location mapping should use verified coordinates. Prediction, causal explanations, reminder delivery, intervention-effect measurement, actual financial losses and automated business conclusions are outside this slice.

## Validation and what would change at Nordvet

Run `python -m unittest test_loader -v`. The six simulated tests passed during this review with HTTP/ODBC dependency stand-ins; native SQL Server and live API behaviour were not exercised. The tests cover local-date/deletion preservation, incremental query parameters, pagination/duplicate handling, ignored-filter rejection, and simulated commit/rollback. They mock API/database behaviour; they do **not** establish live endpoint completeness, execute SQL Server constraints, or validate DAX/report interactions. Use `verify.sql` for source-category/count checks, and compare SQL totals with the same Power BI date/activity filters. Counts are live, so sample figures are not fixed acceptance criteria.

For Nordvet, reuse existing infrastructure where possible; otherwise propose a daily scheduled Python Azure Function, Azure SQL Database and Power BI Service. Before deployment: replace the local Windows-authentication connection with an appropriate Azure connection/identity, validate database certificates (`TrustServerCertificate=yes` is only the local-demo shortcut), store the API secret securely, configure scheduling/report refresh, and alert on failures/stale coverage. Inspect their Power BI licences and network setup; Microsoft 365 alone does not establish report-sharing entitlement. Assess database, compute and reporting costs against the expected benefit rather than assume a budget.

This implementation has no scheduler, persisted failure log, alerts, history/audit snapshots, deployment automation or production access-control configuration. Those are explicit next steps for an ongoing client service; they are not required to run the local demonstration.
