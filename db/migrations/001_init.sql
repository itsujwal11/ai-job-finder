-- AI Job Discovery - initial schema (PostgreSQL 15+)
-- Applied automatically by the engine on startup (see engine/app/db.py).

-- ---------------------------------------------------------------------------
-- Runs: one row per scheduled/manual pipeline execution
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
    id              BIGSERIAL PRIMARY KEY,
    trigger         TEXT NOT NULL DEFAULT 'manual',          -- schedule | manual | cli
    status          TEXT NOT NULL DEFAULT 'running',         -- running | completed | failed
    stage           TEXT NOT NULL DEFAULT 'fetching',        -- fetching | processing | notifying | done
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    profile_hash    TEXT,
    config_hash     TEXT,
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    n8n_execution_id TEXT
);
CREATE INDEX runs_started_idx ON runs (started_at DESC);

-- ---------------------------------------------------------------------------
-- Fetch tasks: the per-run work queue (feeds, ATS boards, searches, pages)
-- ---------------------------------------------------------------------------
CREATE TABLE fetch_tasks (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                           -- feed | ats_board | search | ai_discovery | page | hn
    source          TEXT NOT NULL,                           -- remotive | greenhouse | brave | ...
    label           TEXT NOT NULL,
    url             TEXT,
    params          JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority        INT NOT NULL DEFAULT 50,                 -- lower runs first
    status          TEXT NOT NULL DEFAULT 'pending',         -- pending | running | ok | failed | blocked | skipped
    http_status     INT,
    items_found     INT NOT NULL DEFAULT 0,
    items_new       INT NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX fetch_tasks_queue_idx ON fetch_tasks (run_id, status, priority, id);
-- A page/board is fetched at most once per run
CREATE UNIQUE INDEX fetch_tasks_run_url_uniq ON fetch_tasks (run_id, kind, url) WHERE url IS NOT NULL;

-- ---------------------------------------------------------------------------
-- ATS boards discovered anywhere on the web (self-expanding discovery)
-- ---------------------------------------------------------------------------
CREATE TABLE ats_boards (
    id                   BIGSERIAL PRIMARY KEY,
    provider             TEXT NOT NULL,                      -- greenhouse | lever | ashby | smartrecruiters | recruitee | workable
    slug                 TEXT NOT NULL,
    company_name         TEXT,
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    discovered_via       TEXT,                               -- seed | search:brave | ai_discovery | feed:remotive ...
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fetched_at      TIMESTAMPTZ,
    last_status          TEXT,
    consecutive_failures INT NOT NULL DEFAULT 0,
    relevant_jobs_seen   INT NOT NULL DEFAULT 0,
    UNIQUE (provider, slug)
);

-- ---------------------------------------------------------------------------
-- URLs found by search engines / AI discovery, waiting to be fetched
-- ---------------------------------------------------------------------------
CREATE TABLE discovered_urls (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT NOT NULL,
    url_hash        TEXT NOT NULL UNIQUE,
    discovered_via  TEXT NOT NULL,
    title_hint      TEXT,
    snippet         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',          -- pending | fetched | blocked | skipped | failed | lead_only
    status_reason   TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched_at      TIMESTAMPTZ,
    attempts        INT NOT NULL DEFAULT 0
);
CREATE INDEX discovered_urls_status_idx ON discovered_urls (status, first_seen_at);

-- ---------------------------------------------------------------------------
-- Opportunities: every discovered posting, never deleted (the discovery log)
-- ---------------------------------------------------------------------------
CREATE TABLE opportunities (
    id                      BIGSERIAL PRIMARY KEY,
    first_run_id            BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    last_run_id             BIGINT REFERENCES runs(id) ON DELETE SET NULL,

    -- identity & duplicate detection
    source                  TEXT NOT NULL,
    source_external_id      TEXT,
    source_url              TEXT NOT NULL,
    canonical_url           TEXT NOT NULL,
    url_hash                TEXT NOT NULL UNIQUE,
    fingerprint             TEXT NOT NULL,                   -- sha1(company_norm | title_norm)
    duplicate_of            BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,

    -- normalized posting
    title                   TEXT NOT NULL,
    title_norm              TEXT NOT NULL,
    company_name            TEXT,
    company_norm            TEXT NOT NULL DEFAULT '',
    company_website         TEXT,
    location_text           TEXT,
    location_restrictions   TEXT[] NOT NULL DEFAULT '{}',
    remote_type             TEXT NOT NULL DEFAULT 'unknown', -- remote | hybrid | onsite | unknown
    employment_type         TEXT NOT NULL DEFAULT 'unspecified',
    salary_min              NUMERIC,
    salary_max              NUMERIC,
    salary_currency         TEXT,
    salary_period           TEXT,                            -- hour | day | week | month | year
    salary_npr_monthly_min  NUMERIC,
    salary_npr_monthly_max  NUMERIC,
    description             TEXT NOT NULL DEFAULT '',
    description_quality     TEXT NOT NULL DEFAULT 'full',    -- full | partial | snippet
    apply_url               TEXT,
    apply_email             TEXT,
    apply_method            TEXT NOT NULL DEFAULT 'unclear', -- email | ats_form | external_form | login_required | unclear
    posted_at               TIMESTAMPTZ,
    expires_at              TIMESTAMPTZ,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw                     JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- pipeline state
    pipeline_status         TEXT NOT NULL DEFAULT 'new',
        -- new | processing | filtered_out | duplicate | awaiting_ai | analyzed | analysis_failed | lead_only
    filter_reasons          JSONB NOT NULL DEFAULT '[]'::jsonb,
    rule_signals            JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysis_attempts       INT NOT NULL DEFAULT 0,
    last_processed_run_id   BIGINT REFERENCES runs(id) ON DELETE SET NULL, -- prevents re-claiming in the same run

    -- eligibility & legitimacy
    nepal_eligibility       TEXT,                            -- eligible | likely_eligible | unclear | not_eligible
    nepal_evidence          TEXT,
    legitimacy_score        INT,
    legitimacy_verdict      TEXT,                            -- legitimate | uncertain | suspicious
    legitimacy_flags        JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- AI match
    analysis                JSONB,
    analysis_model          TEXT,
    analyzed_at             TIMESTAMPTZ,
    match_score             INT,
    score_breakdown         JSONB,
    recommendation          TEXT,                            -- auto_apply_candidate | approval_required | manual_apply | ignore | blocked
    recommendation_reasons  JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- human workflow
    review_status           TEXT NOT NULL DEFAULT 'none',
        -- none | pending_approval | approved | rejected | ready_to_apply | auto_apply_disabled | applied | dismissed
    materials_status        TEXT NOT NULL DEFAULT 'none',    -- none | generated | needs_review | failed
    notified_at             TIMESTAMPTZ,
    user_notes              TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX opp_fingerprint_idx   ON opportunities (fingerprint);
CREATE INDEX opp_company_norm_idx  ON opportunities (company_norm);
CREATE INDEX opp_pipeline_idx      ON opportunities (pipeline_status, first_seen_at);
CREATE INDEX opp_recommend_idx     ON opportunities (recommendation, match_score DESC);
CREATE INDEX opp_review_idx        ON opportunities (review_status);
CREATE INDEX opp_first_seen_idx    ON opportunities (first_seen_at DESC);
CREATE UNIQUE INDEX opp_source_ext_uniq ON opportunities (source, source_external_id) WHERE source_external_id IS NOT NULL;

-- Every time a posting is seen (same or different source) - full discovery trail
CREATE TABLE opportunity_sightings (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    seen_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sightings_opp_idx ON opportunity_sightings (opportunity_id);
CREATE UNIQUE INDEX sightings_run_uniq ON opportunity_sightings (opportunity_id, run_id, source);

-- ---------------------------------------------------------------------------
-- Tailored materials (CV + cover letter), versioned
-- ---------------------------------------------------------------------------
CREATE TABLE application_materials (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    version         INT NOT NULL DEFAULT 1,
    cv_markdown     TEXT NOT NULL,
    cover_letter    TEXT NOT NULL,
    verification    JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified        BOOLEAN NOT NULL DEFAULT FALSE,
    model           TEXT,
    output_dir      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (opportunity_id, version)
);

-- ---------------------------------------------------------------------------
-- Applications: the application log + duplicate-application protection
-- ---------------------------------------------------------------------------
CREATE TABLE applications (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE RESTRICT,
    company_norm    TEXT NOT NULL,
    title_norm      TEXT NOT NULL,
    method          TEXT NOT NULL,                           -- manual | email | ...
    submitted_by    TEXT NOT NULL,                           -- user | system
    status          TEXT NOT NULL DEFAULT 'applied',         -- applied | interviewing | offer | rejected | withdrawn | no_response
    materials_id    BIGINT REFERENCES application_materials(id) ON DELETE SET NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Never two live applications for the same company + role
CREATE UNIQUE INDEX applications_company_title_uniq
    ON applications (company_norm, title_norm) WHERE status <> 'withdrawn' AND company_norm <> '';

-- ---------------------------------------------------------------------------
-- Append-only audit log (discovery, decisions, notifications, errors)
-- ---------------------------------------------------------------------------
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    level           TEXT NOT NULL DEFAULT 'info',            -- debug | info | warn | error
    type            TEXT NOT NULL,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    opportunity_id  BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    message         TEXT NOT NULL,
    data            JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX events_time_idx  ON events (occurred_at DESC);
CREATE INDEX events_level_idx ON events (level, occurred_at DESC);
CREATE INDEX events_opp_idx   ON events (opportunity_id, occurred_at);

-- ---------------------------------------------------------------------------
-- AI usage & cost tracking (daily budget guard)
-- ---------------------------------------------------------------------------
CREATE TABLE ai_usage (
    id                   BIGSERIAL PRIMARY KEY,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id               BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    opportunity_id       BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    purpose              TEXT NOT NULL,                      -- analyze | extract | discovery | materials | verify
    model                TEXT NOT NULL,
    input_tokens         INT NOT NULL DEFAULT 0,
    output_tokens        INT NOT NULL DEFAULT 0,
    cache_read_tokens    INT NOT NULL DEFAULT 0,
    cache_write_tokens   INT NOT NULL DEFAULT 0,
    web_search_requests  INT NOT NULL DEFAULT 0,
    cost_usd             NUMERIC(10, 5) NOT NULL DEFAULT 0,
    request_id           TEXT,
    ok                   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX ai_usage_time_idx ON ai_usage (created_at);

-- ---------------------------------------------------------------------------
-- Notifications sent
-- ---------------------------------------------------------------------------
CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    channel         TEXT NOT NULL,                           -- telegram | email
    kind            TEXT NOT NULL,                           -- digest | error | test
    status          TEXT NOT NULL,                           -- sent | failed | skipped
    subject         TEXT,
    body            TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
