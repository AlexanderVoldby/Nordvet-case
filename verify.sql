USE Nordvet;
SELECT country, active, COUNT(*) AS clinic_count
FROM dbo.clinic GROUP BY country, active;

SELECT status,
       CASE WHEN deleted_at IS NULL THEN 'active record' ELSE 'deleted record' END AS record_state,
       COUNT(*) AS appointment_count
FROM dbo.appointment
WHERE appointment_local_date BETWEEN '2026-10-05' AND '2026-10-11'
GROUP BY status, CASE WHEN deleted_at IS NULL THEN 'active record' ELSE 'deleted record' END;

SELECT SUM(appointment_count) AS appointments, SUM(no_show_count) AS no_shows,
       SUM(eligible_count) AS eligible,
       1.0*SUM(no_show_count)/NULLIF(SUM(eligible_count),0) AS no_show_rate
FROM dbo.v_clinic_day
WHERE appointment_date BETWEEN '2026-10-05' AND '2026-10-11';

SELECT TOP (5) * FROM dbo.load_run ORDER BY run_id DESC;
SELECT source_name, CONVERT(varchar(40),watermark,127) AS watermark FROM dbo.sync_state;
