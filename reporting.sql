USE Nordvet;
GO

CREATE OR ALTER VIEW dbo.v_clinic_day
AS
WITH digits AS (
    SELECT n FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS d(n)
), numbers AS (
    SELECT a.n + 10*b.n + 100*c.n + 1000*d.n + 10000*e.n AS n
    FROM digits a CROSS JOIN digits b CROSS JOIN digits c
    CROSS JOIN digits d CROSS JOIN digits e
), loaded_dates AS (
    -- Expand successful extraction ranges, including dates with no appointments.
    -- Group overlapping runs so each reporting date appears exactly once.
    SELECT DATEADD(day, n.n, r.from_date) AS appointment_date,
           MAX(r.completed_at) AS last_loaded_at
    FROM dbo.load_run r
    INNER JOIN numbers n ON n.n <= DATEDIFF(day, r.from_date, r.to_date)
    GROUP BY DATEADD(day, n.n, r.from_date)
), counts AS (
    SELECT clinic_id,
           appointment_local_date AS appointment_date,
           COUNT(*) AS appointment_count,
           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
           SUM(CASE WHEN status = 'no_show' THEN 1 ELSE 0 END) AS no_show_count,
           SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
           SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END) AS booked_count,
           SUM(CASE WHEN status = 'booked' AND scheduled_at < SYSDATETIMEOFFSET()
                    THEN 1 ELSE 0 END) AS unresolved_count,
           SUM(CASE WHEN status NOT IN ('completed', 'no_show', 'cancelled', 'booked')
                    THEN 1 ELSE 0 END) AS other_status_count,
           SUM(CASE WHEN status = 'no_show' THEN COALESCE(duration_minutes, 0)
                    ELSE 0 END) AS no_show_minutes,
           SUM(CASE WHEN status = 'no_show' AND duration_minutes IS NULL
                    THEN 1 ELSE 0 END) AS no_show_missing_duration_count
    FROM dbo.appointment
    WHERE deleted_at IS NULL
    GROUP BY clinic_id, appointment_local_date
)
SELECT d.appointment_date,
       c.clinic_id,
       c.name AS clinic_name,
       c.country,
       c.city,
       c.active AS clinic_active,
       COALESCE(a.appointment_count, 0) AS appointment_count,
       COALESCE(a.completed_count, 0) AS completed_count,
       COALESCE(a.no_show_count, 0) AS no_show_count,
       COALESCE(a.cancelled_count, 0) AS cancelled_count,
       COALESCE(a.booked_count, 0) AS booked_count,
       COALESCE(a.unresolved_count, 0) AS unresolved_count,
       COALESCE(a.other_status_count, 0) AS other_status_count,
       COALESCE(a.completed_count, 0) + COALESCE(a.no_show_count, 0) AS eligible_count,
       COALESCE(a.no_show_minutes, 0) AS no_show_minutes,
       COALESCE(a.no_show_missing_duration_count, 0) AS no_show_missing_duration_count,
       CAST(1.0 * COALESCE(a.no_show_count, 0)
            / NULLIF(COALESCE(a.completed_count, 0) + COALESCE(a.no_show_count, 0), 0)
            AS decimal(10, 6)) AS no_show_rate,
       CAST(SWITCHOFFSET(d.last_loaded_at, '+00:00') AS datetime2(7)) AS last_loaded_at_utc
FROM loaded_dates d
CROSS JOIN dbo.clinic c
LEFT JOIN counts a ON a.clinic_id = c.clinic_id
                  AND a.appointment_date = d.appointment_date
WHERE c.opened_on IS NULL OR c.opened_on <= d.appointment_date;
GO

-- Totals should reconcile to the appointment table for this loaded range.
SELECT SUM(appointment_count) AS appointments,
       SUM(completed_count) AS completed,
       SUM(no_show_count) AS no_shows,
       SUM(cancelled_count) AS cancelled,
       SUM(unresolved_count) AS unresolved,
       CAST(1.0 * SUM(no_show_count) / NULLIF(SUM(eligible_count), 0)
            AS decimal(10,6)) AS overall_no_show_rate
FROM dbo.v_clinic_day
WHERE appointment_date BETWEEN '2026-10-05' AND '2026-10-11';

-- Every retained clinic should appear on Sunday, even without appointments.
SELECT clinic_name, clinic_active, appointment_count, no_show_count, no_show_rate
FROM dbo.v_clinic_day
WHERE appointment_date = '2026-10-11'
ORDER BY clinic_name;

-- Preliminary clinic comparison; inspect counts alongside rates.
SELECT clinic_name, clinic_active,
       SUM(eligible_count) AS eligible_appointments,
       SUM(no_show_count) AS no_shows,
       SUM(no_show_minutes) AS no_show_minutes,
       SUM(unresolved_count) AS unresolved,
       CAST(1.0 * SUM(no_show_count) / NULLIF(SUM(eligible_count),0)
            AS decimal(10,6)) AS no_show_rate
FROM dbo.v_clinic_day
WHERE appointment_date BETWEEN '2026-10-05' AND '2026-10-11'
GROUP BY clinic_name, clinic_active
ORDER BY no_show_rate DESC, no_shows DESC;

-- Deleted records retain their source status. Always exclude deleted_at IS NOT NULL
-- from operational metrics, including the numerator AND denominator.
-- Freshness is the last full extraction of this date, not finalisation of outcomes.
-- clinic_active is current activity, not historical activity on each date.
-- No-show minutes are scheduled capacity associated with no-shows, not financial loss.
-- Date expansion supports full extraction ranges up to 100,000 days.
