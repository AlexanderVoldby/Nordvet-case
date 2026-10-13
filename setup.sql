USE Nordvet;
GO

IF OBJECT_ID('dbo.clinic', 'U') IS NULL
CREATE TABLE dbo.clinic (
    clinic_id nvarchar(50) NOT NULL PRIMARY KEY,
    name nvarchar(200) NOT NULL,
    city nvarchar(100) NULL,
    country char(2) NOT NULL,
    timezone nvarchar(100) NOT NULL,
    active bit NOT NULL,
    opened_on date NULL
);
IF OBJECT_ID('dbo.appointment', 'U') IS NULL
CREATE TABLE dbo.appointment (
    appointment_id nvarchar(100) NOT NULL PRIMARY KEY,
    clinic_id nvarchar(50) NOT NULL REFERENCES dbo.clinic(clinic_id),
    vet_id nvarchar(100) NULL,
    service nvarchar(100) NULL,
    scheduled_at datetimeoffset(0) NOT NULL,
    appointment_local_date date NOT NULL,
    duration_minutes int NULL,
    status nvarchar(50) NOT NULL,
    created_at datetimeoffset(7) NULL,
    updated_at datetimeoffset(7) NOT NULL
);
IF OBJECT_ID('dbo.load_run', 'U') IS NULL
CREATE TABLE dbo.load_run (
    run_id bigint IDENTITY PRIMARY KEY,
    started_at datetimeoffset(7) NOT NULL,
    completed_at datetimeoffset(7) NOT NULL,
    from_date date NOT NULL,
    to_date date NOT NULL,
    clinic_count int NOT NULL,
    appointment_count int NOT NULL
);
GO

-- Upgrade existing first-slice tables. Run before the new ingest.py.
IF COL_LENGTH('dbo.appointment', 'deleted_at') IS NULL
    ALTER TABLE dbo.appointment ADD deleted_at datetimeoffset(7) NULL;
IF COL_LENGTH('dbo.appointment', 'cancelled_at') IS NULL
    ALTER TABLE dbo.appointment ADD cancelled_at datetimeoffset(7) NULL;
IF COL_LENGTH('dbo.load_run', 'change_count') IS NULL
    ALTER TABLE dbo.load_run ADD change_count int NULL;
IF OBJECT_ID('dbo.sync_state', 'U') IS NULL
CREATE TABLE dbo.sync_state (
    source_name nvarchar(100) NOT NULL PRIMARY KEY,
    watermark datetimeoffset(7) NOT NULL
);
GO
