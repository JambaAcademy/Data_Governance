-- ============================================================================
-- Data Catalog Setup Scripts
-- Data Governance Implementation Resources / 03_Implementation_Resources
-- ============================================================================
-- Purpose : Create the foundational schema for a custom/lightweight data
--           catalog used to track data assets, ownership, classification,
--           and lineage when a dedicated catalog platform is not yet in
--           place (or to seed metadata before migrating to one).
-- Notes   : Written in ANSI SQL with PostgreSQL-flavored extensions noted.
--           Adjust data types/sequences for your target RDBMS as needed.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0. Schema
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS data_governance;
SET search_path TO data_governance;

-- ----------------------------------------------------------------------------
-- 1. Reference tables
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS classification_levels (
    classification_id      SERIAL PRIMARY KEY,
    classification_name    VARCHAR(50)  NOT NULL UNIQUE,   -- e.g. Public, Internal, Confidential, Restricted
    description             VARCHAR(500),
    handling_requirements   TEXT,
    sort_order               SMALLINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS business_domains (
    domain_id        SERIAL PRIMARY KEY,
    domain_name       VARCHAR(100) NOT NULL UNIQUE,         -- e.g. Customer, Finance, Operations, HR
    domain_owner      VARCHAR(150),
    description        VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS data_stewards (
    steward_id      SERIAL PRIMARY KEY,
    full_name        VARCHAR(150) NOT NULL,
    email             VARCHAR(150) NOT NULL UNIQUE,
    steward_type      VARCHAR(20)  NOT NULL CHECK (steward_type IN ('Business', 'Technical')),
    domain_id         INTEGER REFERENCES business_domains(domain_id),
    active            BOOLEAN NOT NULL DEFAULT TRUE
);

-- ----------------------------------------------------------------------------
-- 2. Core catalog tables
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_sources (
    source_id         SERIAL PRIMARY KEY,
    source_name        VARCHAR(150) NOT NULL,
    source_type        VARCHAR(50)  NOT NULL,                 -- Database, File, API, SaaS, Warehouse, Stream
    connection_info     VARCHAR(500),                          -- non-sensitive connection metadata only
    environment          VARCHAR(20)  NOT NULL DEFAULT 'Production' CHECK (environment IN ('Production','Staging','Development','Test')),
    domain_id            INTEGER REFERENCES business_domains(domain_id),
    technical_steward_id INTEGER REFERENCES data_stewards(steward_id),
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_assets (
    asset_id              SERIAL PRIMARY KEY,
    source_id              INTEGER NOT NULL REFERENCES data_sources(source_id) ON DELETE CASCADE,
    asset_name              VARCHAR(200) NOT NULL,             -- table, file, dataset, or feed name
    asset_type               VARCHAR(50)  NOT NULL,             -- Table, View, File, Report, Dashboard, API Endpoint
    schema_name              VARCHAR(150),
    description                VARCHAR(1000),
    business_domain_id        INTEGER REFERENCES business_domains(domain_id),
    business_steward_id       INTEGER REFERENCES data_stewards(steward_id),
    classification_id         INTEGER REFERENCES classification_levels(classification_id),
    contains_pii               BOOLEAN NOT NULL DEFAULT FALSE,
    retention_policy_ref        VARCHAR(150),                   -- link to Data Retention Policy schedule
    row_count_estimate           BIGINT,
    created_at                    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, schema_name, asset_name)
);

CREATE TABLE IF NOT EXISTS data_elements (
    element_id          SERIAL PRIMARY KEY,
    asset_id              INTEGER NOT NULL REFERENCES data_assets(asset_id) ON DELETE CASCADE,
    column_name             VARCHAR(150) NOT NULL,
    data_type                VARCHAR(50),
    business_definition       VARCHAR(1000),
    classification_id          INTEGER REFERENCES classification_levels(classification_id),
    is_pii                       BOOLEAN NOT NULL DEFAULT FALSE,
    is_critical_data_element     BOOLEAN NOT NULL DEFAULT FALSE,    -- flag for CDEs under quality monitoring
    valid_values_or_format        VARCHAR(500),
    UNIQUE (asset_id, column_name)
);

-- ----------------------------------------------------------------------------
-- 3. Lineage tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_lineage (
    lineage_id         SERIAL PRIMARY KEY,
    source_asset_id      INTEGER NOT NULL REFERENCES data_assets(asset_id),
    target_asset_id       INTEGER NOT NULL REFERENCES data_assets(asset_id),
    transformation_summary VARCHAR(1000),                  -- short description of the transform/ETL step
    process_name             VARCHAR(150),                   -- job/pipeline name
    captured_method            VARCHAR(20) NOT NULL DEFAULT 'Manual' CHECK (captured_method IN ('Manual','Automated')),
    created_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_asset_id <> target_asset_id)
);

-- ----------------------------------------------------------------------------
-- 4. Data quality rule linkage (see Data_Quality_Rules_Library.py for engine)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_quality_rules (
    rule_id           SERIAL PRIMARY KEY,
    element_id          INTEGER NOT NULL REFERENCES data_elements(element_id) ON DELETE CASCADE,
    rule_type             VARCHAR(50) NOT NULL,                -- Completeness, Validity, Uniqueness, Consistency, Timeliness, Accuracy
    rule_description       VARCHAR(500) NOT NULL,
    rule_expression          VARCHAR(1000),                      -- SQL/python expression reference
    severity                   VARCHAR(20) NOT NULL DEFAULT 'Medium' CHECK (severity IN ('Low','Medium','High','Critical')),
    active                       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_quality_results (
    result_id        BIGSERIAL PRIMARY KEY,
    rule_id             INTEGER NOT NULL REFERENCES data_quality_rules(rule_id) ON DELETE CASCADE,
    run_timestamp          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    records_evaluated         BIGINT,
    records_failed              BIGINT,
    pass_rate                     NUMERIC(5,2),
    status                          VARCHAR(20) CHECK (status IN ('Pass','Fail','Warning','Error'))
);

-- ----------------------------------------------------------------------------
-- 5. Issue tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_issues (
    issue_id          SERIAL PRIMARY KEY,
    asset_id             INTEGER REFERENCES data_assets(asset_id),
    element_id             INTEGER REFERENCES data_elements(element_id),
    reported_by               VARCHAR(150),
    issue_description           VARCHAR(1000) NOT NULL,
    severity                      VARCHAR(20) NOT NULL DEFAULT 'Medium' CHECK (severity IN ('Low','Medium','High','Critical')),
    status                          VARCHAR(20) NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','In Progress','Resolved','Closed','Deferred')),
    assigned_to_steward_id            INTEGER REFERENCES data_stewards(steward_id),
    opened_at                           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at                           TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 6. Indexes
-- ----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_assets_domain        ON data_assets(business_domain_id);
CREATE INDEX IF NOT EXISTS idx_assets_classification ON data_assets(classification_id);
CREATE INDEX IF NOT EXISTS idx_elements_asset        ON data_elements(asset_id);
CREATE INDEX IF NOT EXISTS idx_elements_pii          ON data_elements(is_pii) WHERE is_pii = TRUE;
CREATE INDEX IF NOT EXISTS idx_lineage_source         ON data_lineage(source_asset_id);
CREATE INDEX IF NOT EXISTS idx_lineage_target         ON data_lineage(target_asset_id);
CREATE INDEX IF NOT EXISTS idx_quality_results_rule   ON data_quality_results(rule_id, run_timestamp);
CREATE INDEX IF NOT EXISTS idx_issues_status          ON data_issues(status);

-- ----------------------------------------------------------------------------
-- 7. Seed reference data
-- ----------------------------------------------------------------------------

INSERT INTO classification_levels (classification_name, description, handling_requirements, sort_order) VALUES
    ('Public',        'Information approved for public release',              'No special handling required',                                   1),
    ('Internal',       'Information for internal use only',                    'Do not share externally without approval',                       2),
    ('Confidential',    'Sensitive business or personal information',          'Access restricted to authorized roles; encrypt in transit/rest', 3),
    ('Restricted',       'Highly sensitive data (e.g. regulated PII, secrets)', 'Strict access controls, logging, and encryption required',       4)
ON CONFLICT (classification_name) DO NOTHING;

INSERT INTO business_domains (domain_name, description) VALUES
    ('Customer',    'Customer and prospect data'),
    ('Financial',    'Financial and accounting data'),
    ('Operations',    'Operational and supply chain data'),
    ('Human Resources', 'Employee and workforce data'),
    ('Product',          'Product and inventory data')
ON CONFLICT (domain_name) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 8. Example utility views
-- ----------------------------------------------------------------------------

-- Critical data elements requiring active quality monitoring
CREATE OR REPLACE VIEW vw_critical_data_elements AS
SELECT
    da.asset_name,
    de.column_name,
    de.business_definition,
    bd.domain_name,
    cl.classification_name,
    de.is_pii
FROM data_elements de
JOIN data_assets da ON da.asset_id = de.asset_id
LEFT JOIN business_domains bd ON bd.domain_id = da.business_domain_id
LEFT JOIN classification_levels cl ON cl.classification_id = de.classification_id
WHERE de.is_critical_data_element = TRUE;

-- Latest data quality score per asset
CREATE OR REPLACE VIEW vw_asset_quality_scorecard AS
SELECT
    da.asset_id,
    da.asset_name,
    ROUND(AVG(qr.pass_rate), 2) AS avg_pass_rate,
    COUNT(DISTINCT dqr.rule_id) AS rules_monitored,
    MAX(qr.run_timestamp) AS last_evaluated
FROM data_assets da
JOIN data_elements de ON de.asset_id = da.asset_id
JOIN data_quality_rules dqr ON dqr.element_id = de.element_id AND dqr.active = TRUE
JOIN data_quality_results qr ON qr.rule_id = dqr.rule_id
GROUP BY da.asset_id, da.asset_name;

-- Open data issues by severity
CREATE OR REPLACE VIEW vw_open_issues_by_severity AS
SELECT
    severity,
    COUNT(*) AS open_issue_count
FROM data_issues
WHERE status IN ('Open', 'In Progress')
GROUP BY severity
ORDER BY CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END;

-- ============================================================================
-- End of script. See Data_Lineage_Mapping_Templates.xlsx for manual lineage
-- intake, and Data_Quality_Rules_Library.py for the rule execution engine
-- that writes into data_quality_results.
-- ============================================================================
