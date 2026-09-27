CREATE TABLE IF NOT EXISTS objects (
 id text PRIMARY KEY, name text NOT NULL, address text DEFAULT '', created_at timestamptz DEFAULT now(),
 status text NOT NULL DEFAULT 'PENDING', manifest jsonb, applicability jsonb NOT NULL DEFAULT '{}', generation integer NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS parameters (
 code text PRIMARY KEY, ordinal integer UNIQUE NOT NULL, name text NOT NULL, section text NOT NULL,
 unit text NOT NULL, source_pd text, source_rd text, source_id text, trigger_text text, priority text,
 config jsonb NOT NULL, matrix_version text NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id text PRIMARY KEY, object_id text REFERENCES objects(id), name text NOT NULL, path text NOT NULL UNIQUE,
 sha256 text NOT NULL, size_bytes bigint NOT NULL, format text NOT NULL, stage text NOT NULL DEFAULT 'UNKNOWN',
 discipline text NOT NULL DEFAULT 'UNKNOWN', document_code text, revision text, approval_status text DEFAULT 'UNKNOWN',
 approval_date text, predecessor_id text, successor_id text, signature_status text DEFAULT 'UNKNOWN',
 metadata jsonb NOT NULL DEFAULT '{}', parse_status text DEFAULT 'PENDING', page_count integer DEFAULT 0,
 parsed_pages integer DEFAULT 0, quality jsonb DEFAULT '{}', created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_object_stage ON documents(object_id,stage,document_code);
CREATE INDEX IF NOT EXISTS documents_hash ON documents(sha256);
CREATE TABLE IF NOT EXISTS revision_choices (
 id bigserial PRIMARY KEY, object_id text REFERENCES objects(id), stage text, document_code text,
 file_id text REFERENCES documents(id), user_id text NOT NULL, reason text NOT NULL, created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pages (
 id bigserial PRIMARY KEY, file_id text REFERENCES documents(id), page integer NOT NULL,
 text text NOT NULL, content jsonb NOT NULL, quality text NOT NULL, method text NOT NULL,
 seconds double precision NOT NULL, parser_version text NOT NULL,
 search_vector tsvector GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED,
 UNIQUE(file_id,page)
);
CREATE INDEX IF NOT EXISTS pages_search ON pages USING gin(search_vector);
CREATE TABLE IF NOT EXISTS facts (
 id text PRIMARY KEY, file_id text REFERENCES documents(id), object_id text REFERENCES objects(id),
 parameter_code text REFERENCES parameters(code), entity text NOT NULL DEFAULT 'OBJECT',
 page integer NOT NULL, value jsonb NOT NULL, evidence jsonb NOT NULL,
 method text NOT NULL, confidence double precision NOT NULL, created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS facts_object_param ON facts(object_id,parameter_code,entity);
CREATE TABLE IF NOT EXISTS checks (
 object_id text REFERENCES objects(id), parameter_code text REFERENCES parameters(code),
 implementation_status text NOT NULL, completeness_status text NOT NULL, finding_status text NOT NULL,
 details jsonb NOT NULL DEFAULT '{}', updated_at timestamptz DEFAULT now(), PRIMARY KEY(object_id,parameter_code)
);
CREATE TABLE IF NOT EXISTS findings (
 id text PRIMARY KEY, object_id text REFERENCES objects(id), parameter_code text,
 rule_code text NOT NULL, entity text NOT NULL, machine_status text NOT NULL,
 expected_value jsonb, actual_value jsonb, delta jsonb, evidence jsonb NOT NULL,
 rationale text NOT NULL, priority text NOT NULL, fingerprint text NOT NULL,
 active boolean NOT NULL DEFAULT true, model_version text NOT NULL, created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS findings_object ON findings(object_id,active);
CREATE TABLE IF NOT EXISTS reviews (
 id bigserial PRIMARY KEY, finding_id text REFERENCES findings(id), status text NOT NULL,
 reason_code text NOT NULL, comment text NOT NULL, user_id text NOT NULL,
 evidence_snapshot jsonb NOT NULL, created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS evidence_overrides (
 id bigserial PRIMARY KEY, finding_id text REFERENCES findings(id), evidence jsonb NOT NULL,
 reason text NOT NULL, user_id text NOT NULL, created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS protocols (
 id bigserial PRIMARY KEY, object_id text REFERENCES objects(id), version integer NOT NULL,
 status text NOT NULL, snapshot jsonb NOT NULL, manifest_hash text NOT NULL, created_at timestamptz DEFAULT now(),
 UNIQUE(object_id,version)
);
CREATE TABLE IF NOT EXISTS jobs (
 id text PRIMARY KEY, object_id text REFERENCES objects(id), kind text NOT NULL, payload jsonb NOT NULL,
 status text DEFAULT 'QUEUED', progress integer DEFAULT 0, total integer DEFAULT 0,
 message text DEFAULT '', result jsonb DEFAULT '{}', attempts integer DEFAULT 0,
 cancel_requested boolean DEFAULT false, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit (
 id bigserial PRIMARY KEY, user_id text NOT NULL, action text NOT NULL, object_id text,
 details jsonb NOT NULL, ip text, created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS model_calls (
 id bigserial PRIMARY KEY, model text NOT NULL, purpose text NOT NULL, request_hash text NOT NULL,
 seconds double precision, valid boolean, result jsonb, error text, created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS datasets (
 id text PRIMARY KEY, metadata jsonb NOT NULL, created_at timestamptz DEFAULT now()
);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS checkpoint jsonb NOT NULL DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS request_key text;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_request ON jobs(request_key) WHERE request_key IS NOT NULL AND status IN ('QUEUED','RUNNING');
ALTER TABLE protocols ADD COLUMN IF NOT EXISTS job_id text;
CREATE UNIQUE INDEX IF NOT EXISTS protocols_job ON protocols(job_id) WHERE job_id IS NOT NULL;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS expert_verified boolean NOT NULL DEFAULT false;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS training_consent boolean NOT NULL DEFAULT false;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS label_source text NOT NULL DEFAULT 'UNVERIFIED_LOCAL_REVIEW';

CREATE TABLE IF NOT EXISTS rin_deliveries (
 id bigserial PRIMARY KEY, protocol_id bigint NOT NULL REFERENCES protocols(id) UNIQUE,
 status text NOT NULL, receipt jsonb, payload_hash text NOT NULL, attempts integer NOT NULL DEFAULT 0,
 last_error text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rin_attempts (
 id bigserial PRIMARY KEY, protocol_id bigint NOT NULL REFERENCES protocols(id),
 status text NOT NULL, details jsonb NOT NULL, user_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS mock_rin_inbox (
 idempotency_key text PRIMARY KEY, payload_hash text NOT NULL, receipt jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS training_runs (
 id text PRIMARY KEY, dataset_id text REFERENCES datasets(id), status text NOT NULL,
 job_id text, details jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fact_validations (
 fact_id text PRIMARY KEY REFERENCES facts(id), accepted boolean NOT NULL,
 validator_version text NOT NULL, reason text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE findings ADD COLUMN IF NOT EXISTS search_scope text;
