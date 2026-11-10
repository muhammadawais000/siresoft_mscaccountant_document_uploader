"""Make the audit log append-only at the database level.

A trigger rejects UPDATE and DELETE on the audit table regardless of which client
connects. TRUNCATE (used by test teardown and deliberate maintenance) is not affected.
Reversible: the reverse operation drops the trigger and function.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION document_intake_auditlog_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'document_intake_auditlog is append-only (% blocked)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER document_intake_auditlog_no_change
    BEFORE UPDATE OR DELETE ON document_intake_auditlog
    FOR EACH ROW EXECUTE FUNCTION document_intake_auditlog_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS document_intake_auditlog_no_change ON document_intake_auditlog;
DROP FUNCTION IF EXISTS document_intake_auditlog_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("document_intake", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
