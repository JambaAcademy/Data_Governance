-- ============================================================================
-- Compliance_Reporting_Scripts.sql
-- Data Governance Implementation Resources / 05_Operational_Workflows
-- ============================================================================
-- Purpose : Reusable queries that generate the compliance reporting views
--           referenced by Compliance Audit Checklists, Compliance Status
--           Reports, and the Monthly Governance Report Template.
-- Assumes : The schema created by Data_Catalog_Setup_Scripts.sql
--           (data_governance schema). ANSI SQL with PostgreSQL flavoring;
--           adjust date functions for your target RDBMS as needed.
-- ============================================================================

SET search_path TO data_governance;

-- ----------------------------------------------------------------------------
-- 1. Metadata Completeness Compliance
--    Flags assets missing required metadata per Metadata Management
--    Procedures (Section 4: Metadata Quality Standards).
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_metadata_completeness_compliance AS
SELECT
    da.asset_id,
    da.asset_name,
    bd.domain_name,
    CASE WHEN da.business_steward_id IS NULL THEN 'Missing' ELSE 'Present' END AS business_steward_status,
    CASE WHEN da.classification_id IS NULL THEN 'Missing' ELSE 'Present' END AS classification_status,
    CASE WHEN ds.technical_steward_id IS NULL THEN 'Missing' ELSE 'Present' END AS technical_steward_status,
    CASE WHEN NOT EXISTS (
        SELECT 1 FROM data_lineage dl
        WHERE dl.source_asset_id = da.asset_id OR dl.target_asset_id = da.asset_id
    ) THEN 'Missing' ELSE 'Present' END AS lineage_status,
    CASE
        WHEN da.business_steward_id IS NOT NULL
         AND da.classification_id IS NOT NULL
        THEN 'Compliant'
        ELSE 'Non-Compliant'
    END AS overall_status
FROM data_assets da
LEFT JOIN data_sources ds ON ds.source_id = da.source_id
LEFT JOIN business_domains bd ON bd.domain_id = da.business_domain_id;

-- Summary roll-up for the monthly governance report
CREATE OR REPLACE VIEW vw_metadata_completeness_summary AS
SELECT
    COUNT(*) AS total_assets,
    SUM(CASE WHEN overall_status = 'Compliant' THEN 1 ELSE 0 END) AS compliant_assets,
    ROUND(100.0 * SUM(CASE WHEN overall_status = 'Compliant' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS compliance_pct
FROM vw_metadata_completeness_compliance;

-- ----------------------------------------------------------------------------
-- 2. Data Quality Compliance (vs. SLA threshold)
--    Reports which monitored assets are meeting the quality SLA defined
--    in the Data Quality Standards (default threshold parameterized below).
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_data_quality_compliance AS
SELECT
    da.asset_id,
    da.asset_name,
    bd.domain_name,
    ROUND(AVG(qr.pass_rate), 2) AS avg_pass_rate,
    COUNT(DISTINCT dqr.rule_id) AS rules_monitored,
    CASE WHEN AVG(qr.pass_rate) >= 95 THEN 'Compliant'
         WHEN AVG(qr.pass_rate) >= 85 THEN 'At Risk'
         ELSE 'Non-Compliant' END AS compliance_status,
    MAX(qr.run_timestamp) AS last_evaluated
FROM data_assets da
LEFT JOIN business_domains bd ON bd.domain_id = da.business_domain_id
JOIN data_elements de ON de.asset_id = da.asset_id
JOIN data_quality_rules dqr ON dqr.element_id = de.element_id AND dqr.active = TRUE
JOIN data_quality_results qr ON qr.rule_id = dqr.rule_id
WHERE qr.run_timestamp >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY da.asset_id, da.asset_name, bd.domain_name;

-- ----------------------------------------------------------------------------
-- 3. Sensitive Data Inventory (supports GDPR / CCPA / HIPAA reporting)
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_sensitive_data_inventory AS
SELECT
    da.asset_name,
    bd.domain_name,
    cl.classification_name,
    de.column_name,
    de.is_pii,
    da.retention_policy_ref,
    s.full_name AS business_steward
FROM data_elements de
JOIN data_assets da ON da.asset_id = de.asset_id
LEFT JOIN business_domains bd ON bd.domain_id = da.business_domain_id
LEFT JOIN classification_levels cl ON cl.classification_id = da.classification_id
LEFT JOIN data_stewards s ON s.steward_id = da.business_steward_id
WHERE de.is_pii = TRUE
   OR cl.classification_name IN ('Confidential', 'Restricted');

-- Count of sensitive elements by domain — useful for DPIA / ROPA reporting
CREATE OR REPLACE VIEW vw_sensitive_data_by_domain AS
SELECT
    domain_name,
    COUNT(*) AS sensitive_element_count
FROM vw_sensitive_data_inventory
GROUP BY domain_name
ORDER BY sensitive_element_count DESC;

-- ----------------------------------------------------------------------------
-- 4. Issue and Incident Compliance (SLA tracking)
--    Mirrors severity-based SLAs from Data_Issue_Resolution_Process.md
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_issue_sla_compliance AS
SELECT
    issue_id,
    severity,
    status,
    opened_at,
    resolved_at,
    EXTRACT(EPOCH FROM (COALESCE(resolved_at, CURRENT_TIMESTAMP) - opened_at)) / 86400.0 AS age_days,
    CASE severity
        WHEN 'Critical' THEN 1
        WHEN 'High' THEN 3
        WHEN 'Medium' THEN 10
        ELSE 30
    END AS sla_target_days,
    CASE
        WHEN status IN ('Resolved', 'Closed') AND
             EXTRACT(EPOCH FROM (resolved_at - opened_at)) / 86400.0 <=
             CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 3 WHEN 'Medium' THEN 10 ELSE 30 END
        THEN 'Met'
        WHEN status NOT IN ('Resolved', 'Closed') AND
             EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - opened_at)) / 86400.0 >
             CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 3 WHEN 'Medium' THEN 10 ELSE 30 END
        THEN 'Breached'
        WHEN status IN ('Resolved', 'Closed')
        THEN 'Missed'
        ELSE 'In Progress'
    END AS sla_status
FROM data_issues;

CREATE OR REPLACE VIEW vw_issue_sla_summary AS
SELECT
    severity,
    sla_status,
    COUNT(*) AS issue_count
FROM vw_issue_sla_compliance
GROUP BY severity, sla_status
ORDER BY severity, sla_status;

-- ----------------------------------------------------------------------------
-- 5. Regulatory Checklist Snapshot
--    A consolidated single-row snapshot for the Compliance Status Reports
--    workbook / Monthly Governance Report.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW vw_compliance_snapshot AS
SELECT
    CURRENT_DATE AS report_date,
    (SELECT compliance_pct FROM vw_metadata_completeness_summary) AS metadata_completeness_pct,
    (SELECT ROUND(AVG(avg_pass_rate), 2) FROM vw_data_quality_compliance) AS avg_data_quality_pct,
    (SELECT COUNT(*) FROM vw_sensitive_data_inventory) AS sensitive_element_count,
    (SELECT COUNT(*) FROM data_issues WHERE status IN ('Open', 'In Progress')) AS open_issue_count,
    (SELECT COUNT(*) FROM vw_issue_sla_compliance WHERE sla_status = 'Breached') AS sla_breaches;

-- ----------------------------------------------------------------------------
-- 6. Example: Generate a point-in-time compliance report
-- ----------------------------------------------------------------------------

-- Run this query and export the result for inclusion in the
-- Quarterly Business Review / Board Reporting Template.
-- SELECT * FROM vw_compliance_snapshot;

-- ============================================================================
-- End of script.
-- ============================================================================
