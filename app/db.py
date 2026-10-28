"""Database connection pool, migrations and idempotent seeding."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = [
    # 001 — core state schema
    """
    CREATE TABLE IF NOT EXISTS riders (
        rider_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        city TEXT NOT NULL,
        joined_on DATE NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trips (
        trip_id TEXT PRIMARY KEY,
        rider_id TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL,
        distance_km NUMERIC,
        surge_multiplier NUMERIC NOT NULL
    );
    CREATE INDEX IF NOT EXISTS trips_rider_day_idx
        ON trips (rider_id, ((started_at AT TIME ZONE 'Asia/Kolkata')::date));
    CREATE TABLE IF NOT EXISTS payout_lines (
        line_id TEXT PRIMARY KEY,
        payout_date DATE NOT NULL,
        rider_id TEXT NOT NULL,
        line_type TEXT NOT NULL,
        trip_id TEXT NOT NULL,
        amount NUMERIC NOT NULL
    );
    CREATE INDEX IF NOT EXISTS payout_lines_trip_idx ON payout_lines (trip_id);
    CREATE INDEX IF NOT EXISTS payout_lines_rider_idx ON payout_lines (rider_id, payout_date);
    CREATE TABLE IF NOT EXISTS inbound_messages (
        message_id TEXT PRIMARY KEY,
        rider_id TEXT NOT NULL,
        text TEXT NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        reply TEXT,
        reply_at TIMESTAMPTZ,
        error TEXT,
        ok BOOLEAN
    );
    CREATE TABLE IF NOT EXISTS trace_steps (
        step_id BIGSERIAL PRIMARY KEY,
        rider_id TEXT NOT NULL,
        at TIMESTAMPTZ NOT NULL DEFAULT now(),
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        input JSONB,
        output JSONB
    );
    CREATE INDEX IF NOT EXISTS trace_steps_rider_idx ON trace_steps (rider_id, step_id);
    CREATE TABLE IF NOT EXISTS ops_items (
        id TEXT PRIMARY KEY,
        rider_id TEXT NOT NULL,
        type TEXT NOT NULL,
        amount INTEGER,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        resolved_at TIMESTAMPTZ,
        resolution_note TEXT
    );
    CREATE INDEX IF NOT EXISTS ops_items_pending_idx ON ops_items (status, created_at);
    CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        rider_id TEXT NOT NULL,
        amount INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        dispute_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL,
        provider_payout_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS payments_rider_idx ON payments (rider_id, created_at);
    """,
    """ALTER TABLE payments ADD COLUMN IF NOT EXISTS reference TEXT;
    ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
    ALTER TABLE payments ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
    ALTER TABLE payments ADD COLUMN IF NOT EXISTS error TEXT;
    ALTER TABLE payments ADD COLUMN IF NOT EXISTS details JSONB;
    CREATE UNIQUE INDEX IF NOT EXISTS payments_dispute_purpose_idx
        ON payments (dispute_id, purpose) WHERE state <> 'failed';
    """,
    """CREATE TABLE IF NOT EXISTS conversations (
        conversation_id TEXT PRIMARY KEY,
        rider_id TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        last_active TIMESTAMPTZ NOT NULL,
        open BOOLEAN NOT NULL DEFAULT true
    );
    CREATE INDEX IF NOT EXISTS conversations_rider_idx ON conversations (rider_id, last_active);

    CREATE TABLE IF NOT EXISTS issues (
        issue_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
        rider_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('surge', 'short_pay_day', 'incentive', 'penalty', 'other')),
        trip_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        ist_day DATE,
        claimed_amount INTEGER,
        missing_slots JSONB NOT NULL DEFAULT '[]'::jsonb,
        ask_count INTEGER NOT NULL DEFAULT 0,
        outcome TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS issues_rider_idx ON issues (rider_id, status);
    """,
    # Retry-after-failure must reuse the SAME idempotency key (provider dedupe is the
    # safety net when an error hides a commit), so the plain UNIQUE becomes partial:
    # an active payment blocks a duplicate key; failed ones may be retried.
    """ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_idempotency_key_key;
    CREATE UNIQUE INDEX IF NOT EXISTS payments_idem_active_idx
        ON payments (idempotency_key) WHERE state <> 'failed';
    """,
    # ist_day pins the payout to the rider IST day of received_at (the ₹200/day cap
    # is a policy day, not the wall-clock day of processing).
    """ALTER TABLE payments ADD COLUMN IF NOT EXISTS ist_day DATE;
    CREATE INDEX IF NOT EXISTS payments_rider_day_idx ON payments (rider_id, ist_day);
    """,
]


def connect(conninfo: str) -> psycopg.Connection:
    """Bound-timeout DB connection: fail explicitly (for retries / health), never hang."""
    if "connect_timeout" not in conninfo:
        sep = "&" if "?" in conninfo else "?"
        conninfo = f"{conninfo}{sep}connect_timeout=5"
    try:
        return psycopg.connect(conninfo, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as exc:
        raise RuntimeError(f"database connect failed: {exc}") from exc


def is_ready(conn: psycopg.Connection, init_state: dict[str, int] | None = None) -> bool:
    """Ready only when required tables can be read and seed data loaded."""
    try:
        conn.execute("SELECT 1 FROM riders LIMIT 1").fetchone()
        conn.execute("SELECT 1 FROM trips LIMIT 1").fetchone()
        conn.execute("SELECT 1 FROM payout_lines LIMIT 1").fetchone()
        conn.execute("SELECT 1 FROM inbound_messages LIMIT 1").fetchone()
    except psycopg.Error:
        return False
    if init_state is not None and not all(init_state.get(k, 0) or True for k in ("riders", "trips", "payout_lines")):
        return False
    row = conn.execute("SELECT count(*) AS n FROM trips").fetchone()
    return not (row and row["n"] == 0)


def run_migrations(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "migration TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    applied = {
        row["migration"]
        for row in conn.execute("SELECT migration FROM schema_migrations").fetchall()
    }
    for idx, sql in enumerate(MIGRATIONS):
        mig = f"001_{idx}"
        if mig in applied:
            continue
        with conn.transaction():
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (migration) VALUES (%s) ON CONFLICT DO NOTHING", (mig,))


def seed(conn: psycopg.Connection, data_dir: Path) -> dict[str, int]:
    """Idempotent load of riders/lines/trips. Returns counts loaded this run."""
    loaded = {"riders": 0, "trips": 0, "payout_lines": 0}

    with open(data_dir / "riders.csv", newline="") as f:
        import csv

        for row in csv.DictReader(f):
            loaded["riders"] += _upsert(
                conn,
                "riders",
                "rider_id",
                {
                    "rider_id": row["rider_id"],
                    "name": row["name"],
                    "city": row["city"],
                    "joined_on": row["joined_on"],
                },
            )

    trips, _dup = _load_trips_records(data_dir / "trips.csv")
    for t in trips:
        loaded["trips"] += _upsert(
            conn,
            "trips",
            "trip_id",
            {
                "trip_id": t.trip_id,
                "rider_id": t.rider_id,
                "started_at": t.started_at,
                "status": t.status,
                "distance_km": t.distance_km,
                "surge_multiplier": t.surge_multiplier,
            },
        )

    for line in _load_lines_records(data_dir / "payout_lines.csv"):
        loaded["payout_lines"] += _upsert(
            conn,
            "payout_lines",
            "line_id",
            {
                "line_id": line.line_id,
                "payout_date": line.payout_date,
                "rider_id": line.rider_id,
                "line_type": line.line_type,
                "trip_id": line.trip_id,
                "amount": line.amount,
            },
        )
    return loaded


def _upsert(conn: psycopg.Connection, table: str, pk: str, values: dict) -> int:
    cols = list(values)
    ph = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT ({pk}) DO {('UPDATE SET ' + updates) if updates else 'NOTHING'}",
        [values[c] for c in cols],
    )
    return cur.rowcount if cur.rowcount else 0


def _load_trips_records(path: Path):
    import csv

    from app.policy import TripRecord
    from app.reconciliation import normalize_rider_id

    seen: set[str] = set()
    trips = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            tid = row["trip_id"]
            if tid in seen:
                continue
            seen.add(tid)
            km = row["distance_km"].strip()
            trips.append(
                TripRecord(
                    trip_id=tid,
                    rider_id=normalize_rider_id(row["rider_id"].strip()),
                    status=row["status"],
                    started_at=row["started_at"].strip(),
                    distance_km=Decimal(km) if km else None,
                    surge_multiplier=Decimal(row["surge_multiplier"].strip() or "1"),
                )
            )
    return trips, None


def _load_lines_records(path: Path):
    import csv

    from app.reconciliation import PayoutLineRecord

    no = set()
    lines: list[PayoutLineRecord] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            line = PayoutLineRecord(
                line_id=row["line_id"],
                payout_date=row["payout_date"],
                rider_id=row["rider_id"].strip(),
                line_type=row["line_type"],
                trip_id=row["trip_id"].strip(),
                amount=Decimal(row["amount"]),
            )
            key = (line.line_id, str(line.amount))
            if key in no:
                continue
            no.add(key)
            lines.append(line)
    return lines


def trace(conn: psycopg.Connection, rider_id: str, type_: str, name: str, input_: dict | None, output) -> None:
    """Persist an audit step; JSONB columns always receive valid JSON text."""
    conn.execute(
        "INSERT INTO trace_steps (rider_id, type, name, input, output) VALUES (%s,%s,%s,%s,%s)",
        (
            rider_id,
            type_,
            name,
            json.dumps(input_, default=str) if input_ is not None else None,
            json.dumps(output, default=str) if output is not None else None,
        ),
    )
