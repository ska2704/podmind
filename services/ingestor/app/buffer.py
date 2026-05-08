"""5-minute SQLite rolling buffer.

Two tables, both with a ts index. The TTL sweep is run from main.py on a
slow tick — we delete by `ts < now - window_s`. SQLite is in WAL mode
so the sweep doesn't block read endpoints.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
from podmind_contracts import HubbleFlow, MetricRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id        INTEGER PRIMARY KEY,
    ts        REAL    NOT NULL,
    name      TEXT    NOT NULL,
    value     REAL    NOT NULL,
    pod       TEXT,
    namespace TEXT,
    container TEXT,
    labels    TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS metrics_ts     ON metrics(ts);
CREATE INDEX IF NOT EXISTS metrics_pod_ts ON metrics(pod, ts);

CREATE TABLE IF NOT EXISTS flows (
    id            INTEGER PRIMARY KEY,
    ts            REAL    NOT NULL,
    verdict       TEXT    NOT NULL,
    src_pod       TEXT,
    src_namespace TEXT,
    dst_pod       TEXT,
    dst_namespace TEXT,
    l4_protocol   TEXT,
    src_port      INTEGER,
    dst_port      INTEGER,
    bytes         INTEGER
);
CREATE INDEX IF NOT EXISTS flows_ts     ON flows(ts);
CREATE INDEX IF NOT EXISTS flows_src_ts ON flows(src_pod, ts);
"""


def _to_unix(ts: datetime) -> float:
    return ts.timestamp()


def _from_unix(u: float) -> datetime:
    return datetime.fromtimestamp(u, tz=UTC)


class Buffer:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def is_open(self) -> bool:
        return self._db is not None

    async def insert_metrics(self, records: list[MetricRecord]) -> None:
        if not records:
            return
        rows = [
            (
                _to_unix(r.ts),
                r.name,
                r.value,
                r.pod,
                r.namespace,
                r.container,
                json.dumps(r.labels, sort_keys=True),
            )
            for r in records
        ]
        assert self._db is not None
        await self._db.executemany(
            "INSERT INTO metrics(ts, name, value, pod, namespace, container, labels)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()

    async def insert_flows(self, flows: list[HubbleFlow]) -> None:
        if not flows:
            return
        rows = [
            (
                _to_unix(f.ts),
                f.verdict,
                f.src_pod,
                f.src_namespace,
                f.dst_pod,
                f.dst_namespace,
                f.l4_protocol,
                f.src_port,
                f.dst_port,
                f.bytes,
            )
            for f in flows
        ]
        assert self._db is not None
        await self._db.executemany(
            "INSERT INTO flows(ts, verdict, src_pod, src_namespace, dst_pod, dst_namespace,"
            " l4_protocol, src_port, dst_port, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()

    async def query_metrics(
        self,
        since: datetime,
        *,
        pod: str | None = None,
        name: str | None = None,
        namespace: str | None = None,
    ) -> list[MetricRecord]:
        clauses = ["ts >= ?"]
        args: list = [_to_unix(since)]
        if pod is not None:
            clauses.append("pod = ?")
            args.append(pod)
        if name is not None:
            clauses.append("name = ?")
            args.append(name)
        if namespace is not None:
            clauses.append("namespace = ?")
            args.append(namespace)
        sql = (
            "SELECT ts, name, value, pod, namespace, container, labels FROM metrics"
            f" WHERE {' AND '.join(clauses)} ORDER BY ts"
        )
        assert self._db is not None
        cur = await self._db.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return [
            MetricRecord(
                ts=_from_unix(r[0]),
                name=r[1],
                value=r[2],
                pod=r[3],
                namespace=r[4],
                container=r[5],
                labels=json.loads(r[6]),
            )
            for r in rows
        ]

    async def query_flows(
        self,
        since: datetime,
        *,
        src: str | None = None,
        dst: str | None = None,
    ) -> list[HubbleFlow]:
        clauses = ["ts >= ?"]
        args: list = [_to_unix(since)]
        if src is not None:
            clauses.append("src_pod = ?")
            args.append(src)
        if dst is not None:
            clauses.append("dst_pod = ?")
            args.append(dst)
        sql = (
            "SELECT ts, verdict, src_pod, src_namespace, dst_pod, dst_namespace,"
            " l4_protocol, src_port, dst_port, bytes FROM flows"
            f" WHERE {' AND '.join(clauses)} ORDER BY ts"
        )
        assert self._db is not None
        cur = await self._db.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return [
            HubbleFlow(
                ts=_from_unix(r[0]),
                verdict=r[1],
                src_pod=r[2],
                src_namespace=r[3],
                dst_pod=r[4],
                dst_namespace=r[5],
                l4_protocol=r[6],
                src_port=r[7],
                dst_port=r[8],
                bytes=r[9],
            )
            for r in rows
        ]

    async def sweep(self, now: datetime, window_s: int) -> int:
        """Drop rows older than `now - window_s`. Returns rows deleted."""
        assert self._db is not None
        cutoff = _to_unix(now - timedelta(seconds=window_s))
        cur = await self._db.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        m_deleted = cur.rowcount
        await cur.close()
        cur = await self._db.execute("DELETE FROM flows WHERE ts < ?", (cutoff,))
        f_deleted = cur.rowcount
        await cur.close()
        await self._db.commit()
        return (m_deleted or 0) + (f_deleted or 0)
