-- design §1.9: tenant_id in every table + FORCE RLS; append-only per-tenant hash-chained audit log; HITL records.
CREATE TABLE IF NOT EXISTS session_facts (
  tenant_id text NOT NULL, session_id uuid NOT NULL, id bigserial, fact text NOT NULL,
  evidence_chunk_id text NOT NULL, hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id));
CREATE INDEX IF NOT EXISTS session_facts_sess ON session_facts (tenant_id, session_id);

CREATE TABLE IF NOT EXISTS audit_log (
  tenant_id text NOT NULL, seq bigint NOT NULL, ts timestamptz NOT NULL DEFAULT now(), event_type text NOT NULL,
  actor text NOT NULL, payload jsonb NOT NULL, prev_hash text NOT NULL, hash text NOT NULL,
  PRIMARY KEY (tenant_id, seq));
CREATE OR REPLACE FUNCTION audit_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_update ON audit_log;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_immutable();

CREATE TABLE IF NOT EXISTS hitl (
  tenant_id text NOT NULL, approval_id uuid NOT NULL, requester text NOT NULL, tool text NOT NULL, args_hash text NOT NULL,
  args jsonb NOT NULL, status text NOT NULL CHECK (status IN ('PENDING_APPROVAL','APPROVED','REJECTED','EXPIRED','CONSUMED')),
  created_at timestamptz NOT NULL DEFAULT now(), decided_at timestamptz, approver text,
  PRIMARY KEY (tenant_id, approval_id));
CREATE INDEX IF NOT EXISTS hitl_lookup ON hitl (tenant_id, requester, tool, args_hash, status);

CREATE TABLE IF NOT EXISTS lot_state (
  tenant_id text NOT NULL, lot text NOT NULL, status text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, lot));

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['session_facts','audit_log','hitl','lot_state'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_iso ON %I', t);
    EXECUTE format('CREATE POLICY tenant_iso ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', t);
  END LOOP; END $$;
