-- Rule-based pre-score used to order the AI analysis queue, plus the local-board
-- bookkeeping that the sitemap sources need.

ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS prescore INTEGER;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS prescore_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;

-- The processor claims work with: pipeline_status = 'new' ORDER BY prescore DESC.
CREATE INDEX IF NOT EXISTS opp_prescore_queue_idx
    ON opportunities (pipeline_status, prescore DESC NULLS LAST, posted_at DESC NULLS LAST);
