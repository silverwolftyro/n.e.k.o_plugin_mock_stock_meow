"""SQLite 持久化层。

对应 P2 契约 §1 的 7 张表 + P3 契约 §4.3 新增的第 8 张表。设计要点：

* 所有 SQL 都用 ``?`` 占位符绑定参数，**禁止**字符串拼接。
* 外键由 SDK 开启（``PluginDatabase._init_db`` 会执行
  ``PRAGMA foreign_keys = ON``），这里不重复设置。
* 行工厂是 ``sqlite3.Row``，可以按列名访问。
* 连接是 ``check_same_thread=False``，跨线程安全；每次 :meth:`Repo.reader`
  拿到的 session 都会在退出时关闭。
* ``self.db`` 的 ``session()`` **每次新建连接**，所以插件层不需要自己管连接池；
  需要防的是"读-改-写"的丢失更新，那由插件层的全局锁负责（P3 契约 §1.3）。

教育目的声明
------------
这里存的是**纯虚构**对局的状态。所有标的都是 ``FAKE-xxx``，价格与手续费
都是游戏数值，无现金价值，也不涉及任何真实支付渠道。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

__all__ = ["DbUnavailableError", "Repo", "SCHEMA_STATEMENTS"]


class DbUnavailableError(RuntimeError):
    """数据库不可用（未启用、无法打开会话或执行失败）。"""


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS players (
        id           TEXT    PRIMARY KEY,
        name         TEXT    NOT NULL,
        cash         REAL    NOT NULL DEFAULT 0,
        mode         TEXT    NOT NULL,
        day          INTEGER NOT NULL DEFAULT 1,
        alive        INTEGER NOT NULL DEFAULT 1,
        initial_cash REAL    NOT NULL DEFAULT 0,
        trade_count  INTEGER NOT NULL DEFAULT 0,
        heat         REAL    NOT NULL DEFAULT 0,
        total_fee_paid REAL  NOT NULL DEFAULT 0,
        tokens       INTEGER NOT NULL DEFAULT 0,
        strikes      INTEGER NOT NULL DEFAULT 0,
        loan_debt    REAL    NOT NULL DEFAULT 0,
        death_reason TEXT,
        death_title  TEXT,
        created_at   REAL    NOT NULL,
        updated_at   REAL    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        player_id        TEXT    NOT NULL,
        stock_id         TEXT    NOT NULL,
        amount           REAL    NOT NULL,
        avg_cost         REAL    NOT NULL,
        today_bought     REAL    NOT NULL DEFAULT 0,
        today_bought_day INTEGER NOT NULL DEFAULT 0,
        opened_day       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (player_id, stock_id),
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id  TEXT    NOT NULL,
        stock_id   TEXT    NOT NULL,
        side       TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
        amount     REAL    NOT NULL,
        price      REAL    NOT NULL,
        fee        REAL    NOT NULL,
        tick       INTEGER NOT NULL,
        day        INTEGER NOT NULL,
        reason     TEXT    NOT NULL DEFAULT 'manual',
        created_at REAL    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_player_tick ON trades(player_id, tick)",
    """
    CREATE TABLE IF NOT EXISTS heat_history (
        player_id TEXT    NOT NULL,
        tick      INTEGER NOT NULL,
        day       INTEGER NOT NULL,
        heat      REAL    NOT NULL,
        PRIMARY KEY (player_id, tick),
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_state (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        tick       INTEGER NOT NULL DEFAULT 0,
        day        INTEGER NOT NULL DEFAULT 1,
        phase      TEXT    NOT NULL DEFAULT 'honeymoon',
        seed       INTEGER NOT NULL,
        player_id  TEXT    NOT NULL DEFAULT '',
        fee_pool   REAL    NOT NULL DEFAULT 0,
        total_fees REAL    NOT NULL DEFAULT 0,
        updated_at REAL    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        day       INTEGER NOT NULL,
        news_id   TEXT    NOT NULL,
        news_type TEXT    NOT NULL CHECK (news_type IN ('true','fake','vague','thunder','noise')),
        stock_id  TEXT,
        content   TEXT    NOT NULL,
        accuracy  REAL,
        UNIQUE (day, news_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_day ON news_log(day)",
    """
    CREATE TABLE IF NOT EXISTS pawnbroker_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id  TEXT    NOT NULL,
        tick       INTEGER NOT NULL,
        day        INTEGER NOT NULL,
        action     TEXT    NOT NULL CHECK (action IN ('forced_sell', 'forced_buy')),
        stock_id   TEXT    NOT NULL,
        price      REAL    NOT NULL,
        reason     TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pawnbroker_player_tick ON pawnbroker_log(player_id, tick)",
    # ── P5.5 改动 5：称号解锁表（**独立表**，P5.5 拍板细节 B）──────────
    # 主键 (player_id, title) → 同一称号只解锁一次（天然幂等）。
    # ``unlocked_at`` 存 **tick 序号**（P5.5 拍板：tick 序号，不是时间戳）。
    """
    CREATE TABLE IF NOT EXISTS achievements (
        player_id   TEXT    NOT NULL,
        title       TEXT    NOT NULL,
        unlocked_at INTEGER NOT NULL,
        PRIMARY KEY (player_id, title)
    )
    """,
    # ── 第 8 张表：收割流水（P3 契约 §4.3）─────────────────────────────
    # 只记**收割**（强制平仓）。二道贩子的主动/被动卖出记在 pawnbroker_log，
    # 两张表不混用。
    """
    CREATE TABLE IF NOT EXISTS liquidation_log (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id              TEXT    NOT NULL,
        tick                   INTEGER NOT NULL,
        day                    INTEGER NOT NULL,
        phase                  TEXT    NOT NULL,
        trigger                TEXT    NOT NULL CHECK (trigger IN ('phase', 'peak')),
        stock_id               TEXT    NOT NULL,
        severity               REAL    NOT NULL,
        player_heat_at_harvest REAL    NOT NULL,
        concentration_at_harvest REAL  NOT NULL,
        death_reason           TEXT,
        created_at             REAL    NOT NULL,
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_liquidation_player_tick ON liquidation_log(player_id, tick)",
)

#: ``players`` 表在 P3 新增的列（幂等补齐，见 :meth:`Repo.ensure_schema`）。
#:
#: P2 建表时还没有这几列，而 P2 的存档可能已经存在，所以**不能**重建表，
#: 只能用 ``ALTER TABLE ... ADD COLUMN`` 补。
_PLAYER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pending_action", "TEXT"),
    ("death_reason", "TEXT"),
    ("death_title", "TEXT"),
    ("is_catgirl", "INTEGER NOT NULL DEFAULT 0"),
    # ── P4：低保与高利贷的三列 ──────────────────────────────────────
    # welfare_missed_days：连续未领低保的天数（超过上限 → welfare_neglect）
    ("welfare_missed_days", "INTEGER NOT NULL DEFAULT 0"),
    # loan_taken_day：最近一次借款发生在第几天（每天限借一次）
    ("loan_taken_day", "INTEGER NOT NULL DEFAULT 0"),
    # welfare_claimed_day：最近一次领取低保发生在第几天（周期内领取幂等）
    ("welfare_claimed_day", "INTEGER NOT NULL DEFAULT 0"),
    # ── P5：分析次数的两列 ────────────────────────────────────────────
    # analyses_day：最近一次分析发生在第几天（每天重置分析机会）
    ("analyses_day", "INTEGER NOT NULL DEFAULT 0"),
    # analyses_used：今天已经用掉的分析次数
    ("analyses_used", "INTEGER NOT NULL DEFAULT 0"),
    # ── P5.5：入场身份（一列，纯叙事，不参与任何判定）──────────────────
    # identity：入场时选的身份（debt / prove / quick）
    ("identity", "TEXT NOT NULL DEFAULT 'quick'"),
    # ── P5.5 改动 6：跳过本日 + 无为而治解锁标记 ────────────────────
    # skip_day_used：最近一次「跳过本日」发生在第几天（每天限跳一次）
    ("skip_day_used", "INTEGER NOT NULL DEFAULT 0"),
    # inaction_unlocked：是否已解锁「无为而治」称号
    ("inaction_unlocked", "INTEGER NOT NULL DEFAULT 0"),
)

#: ``market_state`` 表在 P4 新增的列（幂等补齐）。
#:
#: ``reversal_days`` 存反转日集合的 JSON 文本（``"[3, 11, 24]"``）。
_MARKET_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reversal_days", "TEXT"),
)

#: ``news_log`` 表在 P4 新增的列（幂等补齐）。
#:
#: ``direction`` 存新闻的多空方向（``bullish`` / ``bearish`` / ``NULL``）。
#: 有了它，插件重启后重建出来的当天新闻才与落库的那一批**完全一致** ——
#: 否则重新生成的新闻可能因为阶段不同而对不上。
_NEWS_LOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("direction", "TEXT"),
)


class Repo:
    """对 ``self.db`` 的最小封装：读、写、事务。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def enabled(self) -> bool:
        """``[plugin.database].enabled`` 是否打开。"""
        return bool(getattr(self._db, "enabled", False))

    async def _open_session(self) -> Any:
        if not self.enabled:
            raise DbUnavailableError("plugin database is disabled")
        result = await self._db.session()
        if result.is_err():
            raise DbUnavailableError(str(result.err()))
        return result.unwrap()

    @asynccontextmanager
    async def reader(self) -> AsyncIterator[Any]:
        """只读会话；退出时自动关闭。"""
        session = await self._open_session()
        try:
            yield session
        finally:
            await session.close()

    async def ensure_schema(self) -> None:
        """幂等地建表建索引。每次启动都执行一遍，数据库被删也能自愈。"""
        async with self.reader() as session:
            for statement in SCHEMA_STATEMENTS:
                await session.execute(statement)
            await self._ensure_columns(session, "players", _PLAYER_COLUMNS)
            await self._ensure_columns(session, "market_state", _MARKET_STATE_COLUMNS)
            await self._ensure_columns(session, "news_log", _NEWS_LOG_COLUMNS)
            await session.commit()

    @staticmethod
    async def _ensure_columns(
        session: Any, table: str, columns: tuple[tuple[str, str], ...]
    ) -> None:
        """幂等地给指定表补齐新增的列。

        先查 ``PRAGMA table_info``，缺了才 ``ALTER TABLE ADD COLUMN``。
        这样 P2/P3 留下的老存档不需要迁移，也不会丢数据。
        """
        cursor = await session.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cursor.fetchall()}
        for name, ddl in columns:
            if name in existing:
                continue
            await session.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    async def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[Any]:
        """查询多行。"""
        async with self.reader() as session:
            cursor = await session.execute(sql, params)
            return list(cursor.fetchall())

    async def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> Any | None:
        """查询单行；没有则返回 ``None``。"""
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def transaction(
        self,
        actions: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[Any]:
        """在一个事务里执行多条语句。

        返回每个语句产生的 cursor（最后一个 INSERT 的 ``lastrowid`` 即自增主键）。
        任何一步失败都会整体回滚。
        """
        session = await self._open_session()
        cursors: list[Any] = []
        try:
            for sql, params in actions:
                cursors.append(await session.execute(sql, params))
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
        return cursors
