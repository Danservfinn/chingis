-- Chingis data model. Plan §3.
--
-- Every decision row is a future training example; every outcome row is its label.
-- Schema changes are migrations, never in-place edits -- the log is the research asset.
--
-- Applied at B1. Present now so the shape is frozen alongside the wire schemas.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Append-only. Written before effects apply, so replays are byte-deterministic.
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    task_id      TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    ts           TEXT    NOT NULL,
    type         TEXT    NOT NULL CHECK (type IN (
                     'task_received','worker_done','worker_failed','worker_refused',
                     'verify_failed','budget_threshold','quota_threshold','timeout',
                     'policy_exception','human_input','decision_invalid')),
    payload_json TEXT    NOT NULL,
    audit_hash   TEXT    NOT NULL,          -- hash chain per task
    UNIQUE (task_id, seq)
);

-- One row per executive wake. The training set.
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES events(id),
    ledger_hash TEXT    NOT NULL,
    verb        TEXT    NOT NULL,
    reason_code TEXT    NOT NULL,
    confidence  REAL    NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    params_json TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    latency_ms  INTEGER,
    cost_usd    REAL
);

CREATE TABLE IF NOT EXISTS contracts (
    id         TEXT    PRIMARY KEY,
    task_id    TEXT    NOT NULL,
    fleet      TEXT    NOT NULL CHECK (fleet IN ('WR','W1','W2','W0')),
    spec_json  TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('pending','running','done','failed','refused','timeout')),
    created_ts TEXT    NOT NULL
);

-- The labels.
CREATE TABLE IF NOT EXISTS outcomes (
    contract_id    TEXT PRIMARY KEY REFERENCES contracts(id),
    v1_pass        INTEGER CHECK (v1_pass IN (0,1)),
    v1_detail_json TEXT,
    v2_verdict     TEXT,
    v3_verdict     TEXT,
    cost_usd       REAL,
    quota_units    REAL,
    wall_s         REAL,
    score          REAL
);

-- One row per act -> log -> consolidate -> redeploy cycle.
CREATE TABLE IF NOT EXISTS cycles (
    id           INTEGER PRIMARY KEY,
    started_ts   TEXT    NOT NULL,
    base_model   TEXT    NOT NULL,
    adapter_path TEXT,
    eval_json    TEXT,
    promoted     INTEGER NOT NULL DEFAULT 0 CHECK (promoted IN (0,1)),
    notes        TEXT
);

-- Quota is the scarce resource, not dollars. Metered exactly like tokens.
CREATE TABLE IF NOT EXISTS quota_ledger (
    fleet        TEXT    NOT NULL CHECK (fleet IN ('WR','W1','W2','W0')),
    window_start TEXT    NOT NULL,
    units_est    REAL    NOT NULL,
    multiplier   REAL    NOT NULL DEFAULT 1.0,
    source       TEXT    NOT NULL CHECK (source IN ('observed','estimated','operator')),
    PRIMARY KEY (fleet, window_start)
);

CREATE INDEX IF NOT EXISTS idx_events_task    ON events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_decisions_verb ON decisions(verb, reason_code);
CREATE INDEX IF NOT EXISTS idx_contracts_task ON contracts(task_id);
