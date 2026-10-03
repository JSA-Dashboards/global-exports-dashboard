-- JSA Export Dashboard — Snowflake setup (run as ACCOUNTADMIN on JSA-ANALYTICS)
-- Run once to create the schema, tables, service user, and role.

USE ROLE ACCOUNTADMIN;

-- ── Database / Schema ─────────────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS EXPORTS;
CREATE SCHEMA IF NOT EXISTS EXPORTS.PUBLIC;
USE SCHEMA EXPORTS.PUBLIC;

-- ── Tables ────────────────────────────────────────────────────────────────────

-- TDM monthly export data (non-US countries)
CREATE TABLE IF NOT EXISTS TDM_MONTHLY (
    reporter     VARCHAR(10)   NOT NULL,   -- e.g. "BR", "AR", "UA"
    product_code VARCHAR(20)   NOT NULL,   -- e.g. "205719"
    year         INTEGER       NOT NULL,
    month        INTEGER       NOT NULL,
    tmt          FLOAT,                    -- thousand metric tons
    updated_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (reporter, product_code, year, month)
);

-- USDA ESR weekly data (US exports)
CREATE TABLE IF NOT EXISTS ESR_WEEKLY (
    commodity_code     INTEGER     NOT NULL,  -- 401=Corn, 801=Soy, 901=SoyMeal, 107=Wheat
    country_code       VARCHAR(10) NOT NULL,  -- e.g. "US"
    week_ending        DATE        NOT NULL,
    weekly_exports     FLOAT,
    accumulated_exports FLOAT,
    market_year        INTEGER,
    updated_at         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (commodity_code, country_code, week_ending)
);

-- ── Warehouse ─────────────────────────────────────────────────────────────────
-- Use the existing COMPUTE_WH (never silently create a new one).

-- ── Role ─────────────────────────────────────────────────────────────────────
CREATE ROLE IF NOT EXISTS EXPORTER_ROLE;

GRANT USAGE  ON DATABASE  EXPORTS            TO ROLE EXPORTER_ROLE;
GRANT USAGE  ON SCHEMA    EXPORTS.PUBLIC     TO ROLE EXPORTER_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE
             ON ALL TABLES IN SCHEMA EXPORTS.PUBLIC TO ROLE EXPORTER_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE
             ON FUTURE TABLES IN SCHEMA EXPORTS.PUBLIC TO ROLE EXPORTER_ROLE;
GRANT USAGE  ON WAREHOUSE COMPUTE_WH         TO ROLE EXPORTER_ROLE;

-- ── Service user ─────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS EXPORTER_SVC
    DEFAULT_ROLE      = EXPORTER_ROLE
    DEFAULT_WAREHOUSE = COMPUTE_WH
    DEFAULT_NAMESPACE = EXPORTS.PUBLIC
    MUST_CHANGE_PASSWORD = FALSE;

GRANT ROLE EXPORTER_ROLE TO USER EXPORTER_SVC;

-- After running this script, set the RSA public key:
--   ALTER USER EXPORTER_SVC SET RSA_PUBLIC_KEY='<paste key here>';
-- Then verify:
--   DESC USER EXPORTER_SVC;
