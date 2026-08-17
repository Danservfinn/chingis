-- W3 could not be stored. The `contracts.fleet` CHECK shipped as (WR, W1, W2, W0) in
-- 001_init.sql, and the W3 (dsh) lane was added months later without touching it, so
-- every W3 contract failed the constraint at INSERT. Because `outcomes` carries a
-- foreign key onto `contracts`, that also made a W3 result unrecordable -- which is one
-- of the reasons the M0-vs-Chingis founding comparison had never run end to end.
--
-- The new set keeps W1 and W2 even though those lanes were removed on 2026-08-17. The
-- CHECK exists for data integrity, not for policy: historical rows must stay valid, and
-- which lanes may RUN is decided by cli.build_adapters, not by the database.
PRAGMA foreign_keys=OFF;

CREATE TABLE contracts_new (
    id         TEXT    PRIMARY KEY,
    task_id    TEXT    NOT NULL,
    fleet      TEXT    NOT NULL CHECK (fleet IN ('WR','W1','W2','W3','W0')),
    spec_json  TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('pending','running','done','failed','refused','timeout')),
    created_ts TEXT    NOT NULL
);

INSERT INTO contracts_new SELECT id, task_id, fleet, spec_json, status, created_ts FROM contracts;
DROP TABLE contracts;
ALTER TABLE contracts_new RENAME TO contracts;

PRAGMA foreign_keys=ON;
