"""反向暴富模拟器（N.E.K.O. 插件 · 第一层外壳）。

架构定位
--------
本文件属于**第一层：N.E.K.O. 插件层**，铁律是「只做翻译」：
把宿主事件（入口调用、生命周期、UI 动作）翻译成对第二层（游戏模式层）的调用，
再把第二层的结果翻译成 ``Ok`` / ``Err`` 与 UI 可用的数据结构。
本层**不生成价格、不计算上头指数、不包含任何游戏规则**——那些都在第三层
``engine/`` 里，纯标准库，不认识 N.E.K.O 的存在。

对应分层::

    第一层（本文件）  plugin.toml / __init__.py / config.example.toml
                      i18n/zh-CN.json / ui/panel.tsx / cards.py / storage/*.py
    第二层            modes/classic.py / modes/infinite.py / modes/shared.py
    第三层            engine/*.py（纯标准库，不认识 N.E.K.O 的存在）

教育目的声明
------------
本插件是一个**纯虚构、纯娱乐**的反诈教学游戏：

* 所有标的 ID 形如 ``FAKE-xxx``，**不映射任何真实股票代码或外汇对**；
* 价格、费率、代币均为虚构，**无现金价值**，不接入任何真实行情源；
* 插件**不存在**充值、提现、兑换、入金、出金一类功能，连变量名与占位符都没有；
* 游戏刻意演示「养套杀」（先让你小赢，在你上头的瞬间精准收割）与「每日账单斩杀」
  两种真实世界高频出现的收割手法，目的是让玩家在零成本环境里亲身体验被收割的
  全过程，从而对真实交易中的高收益诱导保持警惕。

请勿把本插件的代码、参数或文案用于任何真实的金融工具、投资建议或营销场景。
本文件内所有涉及收割／斩杀／二道贩子的逻辑均带注释说明其教育目的。

局中隐藏规则（重要）
--------------------
* **heat（上头指数）**局中完全隐藏：不出现在 ``status`` / ``market`` /
  ``buy`` / ``sell`` 的返回体里，只在 ``settle`` 的结算报告中出现。
* **seed（本局种子）**局中完全隐藏：``join`` 也不回显，只在 ``settle`` 时展示。
* **反转日**不可预告：不出现在 ``market`` 返回体里。
* 因此本层**逐字段手写返回字典**，禁止 ``dataclasses.asdict`` 之类整体导出
  ——漏删一个 key 就是泄漏。``tests/test_visibility.py`` 会递归扫描返回体做守卫。
"""

from __future__ import annotations

import functools
import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
    ui,
)

from .cards import (
    ACHIEVEMENTS,
    ACHIEVEMENT_UNLOCK_NOTE,
    CARD_CSS,
    DEFAULT_IDENTITY,
    DEATH_REASON_LABELS,
    EPITAPHS,
    IDENTITIES,
    Card,
    achievement_by_key,
    achievement_by_source,
    achievement_by_title,
    build_achievement_card,
    build_bill_card,
    build_catgirl_daily_card,
    build_harvest_card,
    build_kill_card,
    build_news_flash_card,
    build_pawnbroker_card,
    build_taunt_card,
    build_welfare_card,
    death_reason_label,
    identity_echo,
    identity_intro,
    identity_label,
    pick_catgirl_silence,
    pick_taunt,
    t as i18n_text,
)
from .engine.config import (
    STREAM_CATGIRL,
    STREAM_HARVEST,
    STREAM_MARKET,
    EngineConfig,
)
from .engine.errors import (
    EngineError,
    InsufficientCashError,
    InvalidAmountError,
    StockNotFoundError,
    TPlusOneError,
)
from .engine.harvest import TRIGGER_PEAK, TRIGGER_PHASE, execute_harvest
from .engine.heat import (
    apply_heat_events,
    heat_events_for_trade,
    position_concentration,
    update_heat,
)
from .engine.market import advance_market, apply_jump, create_market
from .engine.models import HeatEventContext, MarketState, Player, Position
from .engine.news import news_kind
from .engine.rng import derive_rng, derive_stream_rng
from .engine.settlement import (
    apply_death,
    build_settlement_report,
    buy_fee,
    check_inaction,
    claim_welfare,
    execute_buy,
    execute_sell,
    sell_fee,
    welfare_entitlement,
)
from .modes import get_mode
from .modes.shared import (
    ModeContext,
    classic_initial_days,
    classic_living_cost_range,
    daily_living_cost,
    execute_scheduled_harvest,
    next_living_cost,
)
from .storage.repo import DbUnavailableError, Repo
from .storage.session import (
    Session,
    build_engine_config,
    load_session,
    load_snapshot,
    save_snapshot,
)

__all__ = ["ReverseRichSimPlugin"]

#: 插件 ID，与 [plugin].id 保持一致。
PLUGIN_ID = "reverse_rich_sim"

#: 插件版本号，与 [plugin].version 保持一致。
PLUGIN_VERSION = "0.8.2"

#: 「无为而治」称号的解锁天数（P5.5 改动 6）。
#:
#: 存活到第 14 天（两个工资周期）且**零有效交易** —— 触发后**不强制结束**，
#: 玩家可以继续"什么都不做"地活着。
#:
#: 「有效交易」用**引擎的豁免口径**算：无限模式开局现金为 0，玩家**必须**先
#: 卖掉基准股票才交得上第一天生活费，那一笔不算他主动交易（与隐藏结局
#: ``check_inaction`` 的口径完全一致）。
INACTION_UNLOCK_DAYS = 14

_TAG_LABELS = {"stable": "稳定", "neutral": "中性", "explosive": "爆炸"}

#: 每天的分析次数（P5）。分析是**有成本**的：想拿到当天的模糊提示，就得花次数。
ANALYSES_PER_DAY = 2

#: 面板载荷里最多带多少只标的的 K 线（细节 2：限流）。
#:
#: 宿主用 ``document.createElement`` 建元素，**画不了 SVG**，K 线只能用 CSS 拼；
#: 而且 60 点 × 全部标的会让 ``@ui.context`` 载荷爆掉（推送上限 512 KiB）。
#: 所以只给"持仓 + 爆炸标签"的 K 线，其余标的只给 price / tag / alive。
PANEL_KLINE_MAX = 20

#: 当天有异动时的**模糊**提示（P5 方案 B）。
#:
#: **只说"今天特殊"，绝不说特殊在哪** —— 不给 bool、不说方向、不提"反转"，
#: 更不能说"今天的利好是假的"之类的话。这句话**只**出现在 ``analyze`` 的
#: 返回体里；面板载荷（``@ui.context``）里没有它，想拿到就得花分析次数。
ANALYSIS_MARKET_NOTE = "今天盘面有异动，新闻方向别全信。"

#: 分析的固定说明（P5）。
#:
#: **注意**：这句里不能出现"反转"两个字 —— 局中任何返回体都不得出现
#: 反转日词族（守卫按词族扫描）。所以这里说"下一次异动"而不是"哪天反转"。
ANALYSIS_NOTE = "分析只给模糊倾向：不给精确数值，也不预告下一次异动。"

#: 新闻中性分类的中文标签（P5：局中只给分类，不给标题正文）。
_NEWS_KIND_LABELS = {
    "bullish": "偏多",
    "bearish": "偏空",
    "vague": "含糊",
    "noise": "噪音",
}


def _news_headline(content: str) -> str:
    """从新闻正文里切出「标题」（第一句）。

    引擎生成的是**一整句**新闻，P5 契约 §6 要求"点开详情才给标题和内容"，
    所以这里按中文标点把第一句切出来当标题，剩下的当正文。
    """
    text = str(content or "")
    for mark in ("。", "！", "？", "；"):
        index = text.find(mark)
        if index > 0:
            return text[: index + 1]
    return text
_TAG_COLORS = {"stable": "green", "neutral": "amber", "explosive": "red"}

#: 引擎配置缓存的存活时间（秒）。
_ENGINE_CONFIG_TTL = 5.0

#: 每多少次心跳打一条"心跳体检"日志（1 秒心跳 → 每分钟一条）。
_HEARTBEAT_LOG_EVERY = 60

#: 猫娘弹幕在内存里最多留多少条。
_DANMAKU_MAX = 20

#: 面板载荷里最多带多少条弹幕（限流：载荷上限 512 KiB）。
_DANMAKU_PAYLOAD_MAX = 8

#: P7.2：每个标的的**走势小折线**带多少个点（降采样，别把载荷撑爆）。
_PANEL_SPARK_POINTS = 18

#: 结算页固定反诈文案（**不可通过配置关闭**）。
ANTI_FRAUD_NOTICE = (
    "你连这个纯虚构的游戏都玩不过。全球最聪明的钱，正在真实市场里等着你。别去。"
)

_MODE_NAMES = {
    "classic_arcade": "街机",
    "classic_beginner": "入门",
    "classic_advanced": "进阶",
    "classic_hell": "地狱",
    "infinite": "无限",
}

#: 引擎异常 → 宿主错误码的映射表（契约 §8.2）。
#:
#: 映射固定写在**插件层**：引擎层不认识 N.E.K.O 的存在，也就不该知道宿主
#: 的错误码长什么样。
ERROR_CODES: dict[type[EngineError], str] = {
    InsufficientCashError: "insufficient_cash",
    InvalidAmountError: "invalid_amount",
    StockNotFoundError: "stock_not_found",
    TPlusOneError: "t_plus_one_locked",
}


def _engine_error_code(error: EngineError) -> str:
    """把引擎异常翻译成宿主错误码。"""
    return ERROR_CODES.get(type(error), "engine_error")


def _fill_text(text: str, params: dict[str, Any]) -> str:
    """填变量：``{name}`` → 值（改动 4 的三屏文案用）。"""
    for key, value in params.items():
        text = text.replace("{" + str(key) + "}", str(value))
    return text


def _err(code: str, detail: str | None = None) -> Err:
    """统一构造业务错误：消息**只从 i18n 的 ``error.<code>`` 查表**。

    错误消息的**唯一**来源是 ``i18n/zh-CN.json`` —— 代码里**只留错误码**，
    中文一个字都不写在这儿（P6 清掉的技术债：P1~P5 的 23 条硬编码中文）。

    ``detail`` 用于追加上下文（数据库异常、引擎原始说明）；没有它就是
    纯查表结果。查不到 key 时退回错误码本身（不会给出空白消息）。
    """
    message = i18n_text(f"error.{code}", code)
    if detail:
        message = f"{message}（{detail}）"
    return Err(SdkError(message, code=code))


def _locked(method):
    """给入口方法套上插件层的全局锁（P3 契约 §1.3）。

    锁覆盖"读 Session → 改 → 写回"的**整个事务区间**，这样心跳线程与
    入口线程不会互相覆盖对方的写。

    注意：定时器 ``market_tick`` **不能**用这个装饰器——它自己会去拿锁，
    嵌套会死锁。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        with self._lock:
            return await method(self, *args, **kwargs)

    return wrapper


class _TickJournal:
    """收集一个 tick 内发生的所有写操作，最后一次性提交。

    「先在内存里把整条流水线的结果算完，最后一步才在一个事务里一次性写回」
    是 P3 契约 §3.4 的硬要求：tick 中途崩掉时，数据库里不会留下半写状态。
    """

    __slots__ = (
        "trades",
        "positions",
        "players",
        "liquidations",
        "pawnbroker",
        "deleted_trades",
        "daily",
        "catgirl_daily",
    )

    def __init__(self) -> None:
        #: 本 tick 产生的成交（按顺序）。
        self.trades: list[Any] = []
        #: ``player_id -> 需要重写的 stock_id 集合``。
        self.positions: dict[str, set[str]] = {}
        #: 本 tick 内状态被改动的玩家（人类玩家 / 猫娘）。
        self.players: dict[str, Player] = {}
        #: 本 tick 的收割结果。
        self.liquidations: list[Any] = []
        #: 本 tick 的二道贩子流水。
        self.pawnbroker: list[dict[str, Any]] = []
        #: 被撤销的成交 id（撤销窗口）。
        self.deleted_trades: list[int] = []
        #: 玩家日终结算结果。
        self.daily: Any = None
        #: 猫娘日终结算结果。
        self.catgirl_daily: Any = None

    def touch_player(self, player: Player) -> None:
        """标记某个玩家的状态在本 tick 被改动过。"""
        self.players[player.id] = player

    def touch_position(self, player: Player, stock_id: str) -> None:
        """标记某个持仓在本 tick 被改动过。"""
        self.positions.setdefault(player.id, set()).add(stock_id)


@neko_plugin
class ReverseRichSimPlugin(NekoPluginBase):
    """反向暴富模拟器插件外壳（P2：持久化 + 宿主入口）。

    本层只做三件事：

    1. 把宿主事件（入口调用、生命周期）翻译成对第三层引擎的调用；
    2. 把引擎结果翻译成 ``Ok`` / ``Err`` 与 UI 数据（**逐字段白名单**）；
    3. 把状态落进 ``self.db``（权威）与 ``self.state``（缓存）。

    游戏规则一律不在这里——它们在 ``engine/`` 与 ``modes/`` 里。
    """

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._repo = Repo(self.db)
        # 全局锁（P3 契约 §1.3）：覆盖"读 Session → 改 → 写回"的整个事务区间。
        # 不可重入——**严禁**出现 ``with self._lock:`` 的嵌套。
        self._lock = threading.Lock()
        #: 本次 tick 是否已经在跑（防止 TRIGGER 手动触发时重入）。
        self._tick_running = False
        #: 上一次推进 tick 的真实时间戳（秒）。**只在内存里**：重启后重新
        #: 记基线，避免"插件重启即跳盘"。
        self._last_tick_at = 0.0
        #: 心跳计数（每 ``_HEARTBEAT_LOG_EVERY`` 次打一条体检日志）。
        self._heartbeat_count = 0
        #: 二道贩子强制卖出的撤销窗口截止时间戳（秒）。
        self._daily_window_until = 0.0
        #: 最近一次收割事件（局中通知用；**不含 heat 数值**）。
        self._pending_event: dict[str, Any] | None = None
        #: 引擎配置缓存（心跳每秒都读配置太浪费）。
        self._engine_config_cache: EngineConfig | None = None
        self._engine_config_cached_at: float = 0.0
        #: 业务配置缓存（``config.example.toml`` 的那些段；P7 的
        #: ``[director]`` / ``[visual]`` 从这里读）。
        self._business_config_cache: dict[str, Any] | None = None
        self._business_config_cached_at: float = 0.0
        #: 导演模式开关的缓存（面板载荷是同步的，要能直接读）。
        self._director_enabled_cache: bool = False
        #: 最近一次玩家 id（P5：局终后 ``market_state.player_id`` 被清空，
        #: 面板要靠它去找归档的结算报告）。
        self._last_player_id: str = ""
        #: 猫娘最近的日终摘要（P5 面板用）。
        self._catgirl_daily: dict[str, Any] | None = None
        #: 猫娘上一次日终时的累计成交笔数（用来算"今天她动没动手"）。
        self._catgirl_trades_seen: int | None = None
        #: ── P7：社区名册 ──────────────────────────────────────────────
        #: 社区名册（群友人设 + 抽象新闻池）。从 ``community_roster.json``
        #: 读取；读不到就退化成空名册（插件照常跑，只是没有彩蛋）。
        self._roster: list[dict[str, Any]] = []
        self._roster_loaded = False
        #: ── P7：猫娘弹幕 ──────────────────────────────────────────────
        #: 猫娘实时嘴臭的弹幕队列（**只在内存里**：弹幕是"此刻"的装饰，
        #: 重启后清空是合理的）。新条目追加在末尾，面板取最新的几条。
        self._danmaku: list[dict[str, Any]] = []
        #: 弹幕序号（面板靠它判断"这条是不是新的"）。
        self._danmaku_seq = 0
        #: ── P7：导演模式（直播特制版）────────────────────────────────
        #: 导演指令队列：面板点一下 → 插件排一条指令 → 下个 tick 执行。
        #: **只存在于特制版**；纯净版下 ``director.enabled`` 恒为 False，
        #: 导演入口一律直接报错。
        self._director_queue: list[dict[str, Any]] = []
        #: ── P7.2：导演台的「赛博弹窗轰炸」──────────────────────────────
        #: 轰炸**截止时间戳**（秒）与**小丑数量**。面板据此弹出无法关闭的
        #: Emoji 海，并在倒计时里封锁操作。纯表现层，不影响任何数值。
        self._director_boom_until: float = 0.0
        self._director_boom_count: int = 50

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件进程启动：幂等地建表建索引。

        数据库被用户或清理工具删掉也能自愈——``CREATE TABLE IF NOT EXISTS``
        的开销是毫秒级，不需要"只建一次"的逻辑。

        **宿主时序（真机踩过的坑）**：宿主可能在插件实例构造**之后**才把
        有效配置下发到插件进程 —— 那时 ``db.enabled`` 还是 False，建表必然
        失败。这种情况下**不能放弃**：读到有效配置后由 ``Repo`` 的懒建表
        自愈机制补建（``Repo.ensure_schema``），下一次读写会自动把表建好。

        **0.7.4 真机复盘（关键）**：不先同步一次有效配置就建表，**必然**
        失败——宿主给插件进程的有效配置里 ``plugin.database.enabled`` 可能
        是 False，而 ``refresh_runtime_config()`` **不带参数**时只会重新
        解析插件进程里那份（可能是空的）缓存，等于什么都没刷。所以这里
        先 ``await self._refresh_runtime_capabilities()``（它会真的去拉
        一份有效配置回来），再建表。
        """
        # 先拉一次有效配置：让 db.enabled / state.backend 与宿主对齐。
        await self._refresh_runtime_capabilities()
        self._log_runtime_capabilities()
        try:
            await self._repo.ensure_schema()
        except (DbUnavailableError, sqlite3.Error) as error:
            # 多半是宿主还没把有效配置下发到插件进程。拉一次最新配置再试。
            await self._refresh_runtime_capabilities()
            try:
                await self._repo.ensure_schema()
            except (DbUnavailableError, sqlite3.Error) as retry_error:
                self.logger.warning(
                    f"数据库暂不可用（{retry_error}；首次失败：{error}）；"
                    "待宿主下发有效配置后会在首次读写时自动建表。"
                )
        return Ok({"status": "ready", "stage": "p2-persistence"})

    def _log_runtime_capabilities(self) -> None:
        """打印宿主给到的运行时能力快照（排查宿主版本差异用）。"""
        try:
            effective = getattr(self.ctx, "_effective_config", None)
            plugin_cfg = effective.get("plugin", {}) if isinstance(effective, dict) else {}
            database_cfg = (
                plugin_cfg.get("database", {}) if isinstance(plugin_cfg, dict) else {}
            )
            self.logger.info(
                "运行时能力快照：db.enabled={} db.name={} state.backend={} "
                "配置.database.enabled={}",
                getattr(self.db, "enabled", None),
                getattr(self.db, "db_name", None),
                getattr(self.state, "backend", None),
                database_cfg.get("enabled") if isinstance(database_cfg, dict) else None,
            )
        except Exception:  # pragma: no cover - 诊断日志，失败不影响启动
            pass

    def _manifest_capabilities(self) -> dict[str, Any]:
        """读插件自己 ``plugin.toml`` 里的能力段（``[plugin.database]`` 等）。

        **为什么要有兜底**：真机实测（Steam 版宿主 0.9.0）——宿主在插件进程
        启动时给到的 ``ctx._effective_config`` 里**可能压根没有**
        ``[plugin.database]`` / ``[plugin_state]`` 段，于是 ``db.enabled``
        停在 False，心跳每秒钟都撞在"数据库不可用"上，tick 永远不推进。

        而 SDK 自己写明了清单的地位：*"plugin.toml manifest metadata is
        authoritative"*（见 SDK ``config.py`` 的 ``_PLUGIN_METADATA_WRITE_WARNING``）。
        所以插件读自己的清单来兜底是**合规**的；宿主显式给过的值仍然优先
        （只在**缺段**时补，不覆盖）。
        """
        try:
            import tomllib
        except Exception:  # pragma: no cover - Python < 3.11
            return {}
        try:
            manifest_path = Path(__file__).resolve().parent / "plugin.toml"
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover - 清单读不到就不兜底
            return {}
        out: dict[str, Any] = {}
        plugin_section = data.get("plugin")
        if isinstance(plugin_section, dict):
            capabilities = {
                key: plugin_section[key]
                for key in ("database", "store")
                if isinstance(plugin_section.get(key), dict)
            }
            if capabilities:
                out["plugin"] = capabilities
        state_section = data.get("plugin_state")
        if isinstance(state_section, dict):
            out["plugin_state"] = state_section
        return out

    def _effective_runtime_config(self, raw: object = None) -> dict[str, Any]:
        """合成一份"带 ``plugin.*`` 能力段"的有效配置。

        优先级：**宿主缓存 > 读到的配置 > 插件自己的清单**。

        * 宿主缓存（``ctx._effective_config``）是宿主给的**权威**有效配置；
        * 读到的配置（``config.dump()``）是插件自己的业务配置，用来补宿主
          没给的业务键；
        * 清单（``plugin.toml``）兜底补齐**两边都缺**的能力段。
        """
        cfg: dict[str, Any] = {}
        if isinstance(raw, dict):
            cfg.update(raw)
        host_cfg = getattr(self.ctx, "_effective_config", None)
        if isinstance(host_cfg, dict):
            for key, value in host_cfg.items():
                if isinstance(cfg.get(key), dict) and isinstance(value, dict):
                    merged = dict(cfg[key])
                    merged.update(value)
                    cfg[key] = merged
                else:
                    cfg[key] = value
        manifest = self._manifest_capabilities()
        plugin_cfg = cfg.get("plugin")
        plugin_cfg = dict(plugin_cfg) if isinstance(plugin_cfg, dict) else {}
        for key, value in manifest.get("plugin", {}).items():
            if key not in plugin_cfg:
                plugin_cfg[key] = value
        if plugin_cfg:
            cfg["plugin"] = plugin_cfg
        if "plugin_state" not in cfg and manifest.get("plugin_state"):
            cfg["plugin_state"] = manifest["plugin_state"]
        return cfg

    def _apply_runtime_config(self, raw: object = None) -> None:
        """把一份有效配置喂给宿主的 ``refresh_runtime_config``。

        **必须传配置进去**：``refresh_runtime_config()`` 不带参数时只会重新
        解析插件进程里的 ``ctx._effective_config`` 缓存，而那份缓存**可能
        缺掉能力段**（见 :meth:`_manifest_capabilities`）——于是"刷新"等于
        什么都没做，``db.enabled`` 永远是 False。

        ``refresh_runtime_config`` 是**幂等**的（只是重新赋值几个开关），
        所以每次读到配置都调一次没有任何副作用。
        """
        refresh = getattr(self, "refresh_runtime_config", None)
        if not callable(refresh):
            return
        try:
            refresh(self._effective_runtime_config(raw))
        except Exception:  # pragma: no cover - 诊断路径，失败不影响主流程
            pass

    async def _refresh_runtime_capabilities(self) -> None:
        """拉一次最新有效配置并刷新运行时能力（宿主补发配置后用得上）。

        **0.7.4 真机复盘**：这里以前只调 ``refresh_runtime_config()``（不带
        参数），等于重新解析插件进程里那份**缺能力段**的
        ``ctx._effective_config`` ——``db.enabled`` 一直停在 False，心跳每秒
        钟都撞在"数据库不可用"上，tick 永远不推进。

        正确姿势：**先 ``await self.config.dump()``**（宿主拿到新配置时会
        顺带刷新实例），**再把合成后的配置喂给** ``refresh_runtime_config``。
        """
        raw: object = None
        try:
            raw = await self.config.dump()
        except Exception:  # pragma: no cover - 配置不可用时退回宿主缓存
            raw = None
        self._apply_runtime_config(raw)

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件进程停止。连接由 SDK 在 session 关闭时释放，这里无需处理。"""
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------
    # 入口：存档
    # ------------------------------------------------------------------

    @plugin_entry(
        id="join",
        name="加入一局",
        description="开始新的一局；不传 seed 则随机开局。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "classic_arcade",
                        "classic_beginner",
                        "classic_advanced",
                        "classic_hell",
                        "infinite",
                    ],
                    "description": "游戏模式",
                },
                "seed": {
                    "type": "integer",
                    "description": "可选：指定本局种子以复现历史对局",
                },
                "identity": {
                    "type": "string",
                    "enum": list(IDENTITIES),
                    "description": (
                        "入场身份（叙事用，不影响任何数值）："
                        "debt=还债的 / prove=证明自己的 / quick=不想打工的"
                    ),
                },
            },
            "required": [],
        },
    )
    @_locked
    async def join(
        self,
        mode: str = "classic_arcade",
        seed: int | None = None,
        identity: str = DEFAULT_IDENTITY,
    ) -> Ok:
        """开一局新对局。

        返回体**不含 seed**——局中种子必须完全隐藏。

        P5.5 改动 2：新增 ``identity``（入场三选一）。
        它是**纯叙事字段** —— 开盘金、市场、初始天数全都不变，
        只在 ``join`` / ``status`` 与**死亡结算页**上出现。
        """
        engine_config = await self._engine_config()
        if mode not in engine_config.difficulties:
            return _err("invalid_mode")
        if identity not in IDENTITIES:
            return _err("invalid_identity")

        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is not None:
            return _err("already_joined")

        # 主种子只需要唯一，不需要可复现——可复现的是引擎层派生出来的随机流。
        actual_seed = (
            int(seed) if seed is not None else random.SystemRandom().randrange(1, 2**31 - 1)
        )
        difficulty = engine_config.difficulties[mode]
        initial_cash = self._initial_cash(mode, actual_seed, engine_config)

        player_id = "p_" + uuid4().hex[:8]
        player = Player(
            id=player_id,
            name="玩家",
            cash=initial_cash,
            mode=mode,
            day=1,
            alive=True,
            initial_cash=initial_cash,
        )
        market = MarketState(seed=actual_seed, tick=0, day=1, phase="honeymoon")
        market.stocks = create_market(
            actual_seed,
            count=difficulty.initial_symbols,
            config=engine_config.market,
            difficulty=difficulty,
        )

        now = time.time()

        # ── 模式层开局（P4）────────────────────────────────────────
        # 经典模式：预计划 1~3 个反转日；无限模式：发放基准股票。
        # 两者都会生成第 1 天的新闻（当天开盘前）。
        mode_impl = get_mode(mode)
        setup_outcome = None
        if mode_impl is not None:
            ctx = ModeContext(
                player=player,
                market=market,
                difficulty=difficulty,
                engine_config=engine_config,
                mode=mode,
                now=now,
            )
            setup_outcome = mode_impl.setup(ctx)

        actions: list[tuple[str, tuple[Any, ...]]] = [
            (
                "INSERT OR REPLACE INTO market_state "
                "(id, tick, day, phase, seed, player_id, fee_pool, total_fees, "
                "reversal_days, updated_at) "
                "VALUES (1, 0, 1, 'honeymoon', ?, ?, 0, 0, ?, ?)",
                (
                    actual_seed,
                    player_id,
                    json.dumps(sorted(market.reversal_days), ensure_ascii=False),
                    now,
                ),
            ),
            (
                "INSERT INTO players "
                "(id, name, cash, mode, day, alive, initial_cash, trade_count, heat, "
                "total_fee_paid, tokens, strikes, loan_debt, pending_action, death_reason, "
                "death_title, is_catgirl, welfare_missed_days, loan_taken_day, "
                "welfare_claimed_day, identity, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, "
                "0, 0, 0, ?, ?, ?)",
                (
                    player_id,
                    player.name,
                    player.cash,
                    mode,
                    initial_cash,
                    identity,
                    now,
                    now,
                ),
            ),
        ]
        # 无限模式常驻猫娘：她是普通的 Player 行，走同一套 heat 逻辑，
        # 代码里**不存在**猫娘豁免分支。她有本金（玩家开局是 0 + 基准股票）。
        if mode == "infinite":
            catgirl_id = "c_" + uuid4().hex[:8]
            catgirl_cash = self._catgirl_initial_cash(engine_config)
            actions.append(
                (
                    "INSERT INTO players "
                    "(id, name, cash, mode, day, alive, initial_cash, trade_count, heat, "
                    "total_fee_paid, tokens, strikes, loan_debt, pending_action, death_reason, "
                    "death_title, is_catgirl, welfare_missed_days, loan_taken_day, "
                    "welfare_claimed_day, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL, 1, "
                    "0, 0, 0, ?, ?)",
                    (
                        catgirl_id,
                        "猫娘",
                        catgirl_cash,
                        mode,
                        catgirl_cash,
                        now,
                        now,
                    ),
                )
            )
        # 开局发放的基准股票（无限模式）要落进持仓表。
        for stock_id, position in player.positions.items():
            actions.append(
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        player_id,
                        stock_id,
                        position.amount,
                        position.avg_cost,
                        position.today_bought,
                        position.today_bought_day,
                        position.opened_day,
                    ),
                )
            )
        # 第 1 天的新闻（当天开盘前生成）。
        for item in setup_outcome.news if setup_outcome is not None else []:
            actions.append(
                (
                    "INSERT OR REPLACE INTO news_log "
                    "(day, news_id, news_type, stock_id, content, accuracy) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        item.day,
                        item.id,
                        item.type,
                        item.stock_id,
                        item.content,
                        item.accuracy,
                    ),
                )
            )
        try:
            await self._repo.transaction(actions)
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        await save_snapshot(self.state, market, player_id)
        # 记住玩家 id：局终后 ``market_state.player_id`` 被清空，面板要靠它
        # 去找归档的结算报告（P4 遗留问题 4）。
        self._last_player_id = player_id
        # P5：第 1 天的新闻卡片（**只推第一条**，与新闻类型无关）。
        if setup_outcome is not None:
            await self._push_first_news_card(list(setup_outcome.news))
        payload: dict[str, Any] = {
            "player_id": player_id,
            "mode": mode,
            "mode_name": _MODE_NAMES.get(mode, mode),
            "initial_cash": round(initial_cash, 2),
            "day": 1,
            "new_game": True,
            "ticks_per_day": difficulty.ticks_per_day,
            # ── P5.5 改动 2：入场身份 + 开场台词 ────────────────────
            # 呼应句**不在这里** —— 它只在死亡结算页出现。
            "identity": identity,
            "identity_label": identity_label(identity),
            "identity_intro": identity_intro(identity),
            "notice": "纯虚构反诈沙盘。所有标的、价格、代币均为虚构，无现金价值。",
        }
        # 无限模式：开局发放的基准股票（不卖就活不过第 1 天）。
        if player.positions:
            baseline = next(iter(player.positions.items()))
            payload["baseline_stock"] = {
                "stock_id": baseline[0],
                "amount": round(baseline[1].amount, 6),
                "cost": round(baseline[1].avg_cost, 6),
            }
            payload["notice"] = (
                "开局送你的唯一本金就是这只票：不卖掉它，第一天就交不起生活费。"
                "现钱唯一，股票市值不算数。"
            )
        return Ok(payload)

    @plugin_entry(
        id="status",
        name="查看状态",
        description="查看自己的资产、持仓与距离斩杀线。",
    )
    @_locked
    async def status(self) -> Ok:
        """查看自己的状态。

        返回体**不含 heat / seed / 反转日**——这些是局中秘密。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        prices = market.prices()

        positions: list[dict[str, Any]] = []
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            price = prices.get(stock_id, 0.0)
            positions.append(
                {
                    "stock_id": stock_id,
                    "name": stock.name if stock is not None else stock_id,
                    "amount": round(position.amount, 6),
                    "avg_cost": round(position.avg_cost, 6),
                    "price": round(price, 6),
                    "market_value": round(position.market_value(price), 6),
                    "unrealized_pnl": round(position.unrealized_pnl(price), 6),
                    "sellable": round(
                        self._sellable_amount(position, player.day, session.difficulty), 6
                    ),
                    "tag": stock.tag if stock is not None else "neutral",
                    "tag_label": _TAG_LABELS.get(
                        stock.tag if stock is not None else "neutral", "未知"
                    ),
                }
            )

        next_cost = self._next_living_cost(session.difficulty, engine_config)
        distance = (player.cash - next_cost) if next_cost > 0 else None
        return Ok(
            {
                "joined": True,
                "mode": player.mode,
                "mode_name": _MODE_NAMES.get(player.mode, player.mode),
                "phase": market.phase,
                "day": player.day,
                "alive": player.alive,
                "cash": round(player.cash, 6),
                "total_asset": round(player.total_asset(prices), 6),
                "distance_to_death": round(distance, 6) if distance is not None else None,
                "concentration": round(position_concentration(player.positions, prices), 6),
                "trade_count": player.trade_count,
                "fee_paid": round(player.total_fee_paid, 6),
                "strikes": player.strikes,
                "positions": positions,
                # ── P3 新增：二道贩子提案 / 收割通知 / 死因 ──
                # 这三项都**不含 heat 数值**（heat 只在 settle 出现）。
                "pending_action": self._public_pending_action(player.pending_action or {}),
                "pending_event": self._pending_event,
                "death_reason": player.death_reason,
                "death_title": player.death_title,
                # ── P5.5 改动 2：入场身份（**只有 ID，不含呼应句**）──
                # 呼应句只在死亡结算页出现，局中一律不给。
                "identity": session.identity,
                # ── P4 新增：低保 / 高利贷 / 当天新闻 ──
                # 连续未领低保的天数（超过上限会被斩）。
                "welfare_missed_days": player.welfare_missed_days,
                # 未偿还的高利贷本金。
                "loan_debt": round(player.loan_debt, 6),
                # 现在去领能拿回多少钱（0 = 本周期已领过或经典模式没有低保）。
                "pending_relief": round(self._pending_relief(session, engine_config), 6),
                # 当天新闻：**只有中性分类**，真假与方向都不在局中暴露。
                "news_today": self._public_news(market),
            }
        )

    def _pending_relief(self, session: Session, engine_config: EngineConfig) -> float:
        """现在去领低保能拿回多少钱。"""
        if session.mode != "infinite":
            return 0.0
        infinite = engine_config.infinite
        cycles = welfare_entitlement(
            session.player,
            day=session.player.day,
            interval_days=infinite.relief_interval_days,
            claim_limit_days=infinite.welfare_claim_limit_days,
        )
        return infinite.weekly_relief * cycles

    @staticmethod
    def _public_news(market: MarketState) -> list[dict[str, Any]]:
        """当天新闻的**白名单**返回体（P5：**只给中性分类**）。

        P4 §11 原本给的是 ``{id, headline, kind}``；**P5 契约 §6** 把标题与正文
        收进「新闻详情」交互 —— 局中默认**看不到标题**，点开才给。

        ``kind`` 是中性分类（bullish / bearish / vague / noise）：那是这条新闻
        **自称**的方向（反转日会把它整体翻转），**不泄露真假与雷新闻**。
        """
        rows: list[dict[str, Any]] = []
        for item in market.news_today:
            kind = news_kind(item)
            rows.append(
                {
                    "id": item.id,
                    "kind": kind,
                    "kind_label": _NEWS_KIND_LABELS.get(kind, "未知"),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # 入口：行情与交易
    # ------------------------------------------------------------------

    @plugin_entry(
        id="market",
        name="查看行情",
        description="查看当前市场行情与标的稳定／爆炸标签。",
    )
    @_locked
    async def market(self) -> Ok:
        """看一次盘。

        **P3 起本入口不再推进 tick**——tick 由 ``@timer_interval`` 的心跳按
        难度配置的间隔自动推进（经典 8 秒 / 无限 30 秒）。P2 的"看盘顺手
        推一格"会让计时承诺失效，所以这里改成**只读**。

        返回体**不含 seed / 反转日 / heat**。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return Ok(self._market_payload(session))

    @ui.action(
        id="buy",
        label="买入",
        icon="plus",
        tone="primary",
        group="trade",
        order=1,
    )
    @plugin_entry(
        id="buy",
        name="买入",
        description="按当前市价买入一只虚构标的。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "amount": {"type": "number", "minimum": 0, "description": "买入数量"},
            },
            "required": ["stock_id", "amount"],
        },
    )
    @_locked
    async def buy(self, stock_id: str, amount: float) -> Ok:
        """买入。手续费计入持仓成本；买入行为会影响上头指数。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _err("invalid_amount")
        if amount <= 0:
            return _err("invalid_amount")

        player = session.player
        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        if not stock.alive:
            return _err("stock_delisted")

        price = stock.price
        notional = price * amount
        fee = buy_fee(notional, engine_config.fees)
        if notional + fee > player.cash + 1e-9:
            return _err("insufficient_cash")

        context = await self._heat_context(session, stock, amount, engine_config)
        try:
            trade = execute_buy(
                player,
                stock,
                amount,
                config=engine_config.fees,
                day=player.day,
                tick=market.tick,
                trade_id=0,
                reason="manual",
            )
        except EngineError as engine_error:
            return _err(_engine_error_code(engine_error), str(engine_error))

        player.heat = apply_heat_events(
            player.heat, heat_events_for_trade(context, engine_config.heat), engine_config.heat
        )
        market.fee_pool += trade.fee
        market.total_fees += trade.fee

        trade_id = await self._persist_trade(session, trade, stock_id)
        # P5：连续补仓 ≥3 次 → 猫娘嘲讽。
        await self._maybe_add_down_taunt(session, stock_id, market)
        # ── P7：猫娘弹幕 ────────────────────────────────────────────
        # 运行时**没有卡片 API** ⇒ 猫娘在局中的嘲讽改走面板的弹幕悬浮窗
        # （外加宿主 showToast 兜底）。文案来自 i18n 的 taunt.buy.* 键族。
        self._push_danmaku(
            pick_taunt(
                "buy",
                seed=player.trade_count,
                params={"stock": stock.name, "amount": amount, "price": price},
            )
        )
        position = player.positions.get(stock_id)
        return Ok(
            {
                "stock_id": stock_id,
                "amount": round(trade.amount, 6),
                "price": round(trade.price, 6),
                "notional": round(trade.notional if hasattr(trade, "notional") else trade.price * trade.amount, 6),
                "fee": round(trade.fee, 6),
                "cash_after": round(player.cash, 6),
                "position": (
                    {
                        "amount": round(position.amount, 6),
                        "avg_cost": round(position.avg_cost, 6),
                        "sellable": round(
                            self._sellable_amount(position, player.day, session.difficulty), 6
                        ),
                    }
                    if position is not None
                    else None
                ),
                "trade_id": trade_id,
            }
        )

    async def _maybe_add_down_taunt(
        self, session: Session, stock_id: str, market
    ) -> None:
        """连续补仓 ≥3 次 → 猫娘嘲讽（P5 的 5 个触发点之一）。

        判定：同一标的**当天**累计买入笔数 ≥3。
        """
        player = session.player
        try:
            rows = await self._repo.fetch_all(
                "SELECT COUNT(*) AS n, AVG(price) AS avg_price FROM trades "
                "WHERE player_id = ? AND stock_id = ? AND side = 'buy' AND day = ?",
                (player.id, stock_id, player.day),
            )
        except (DbUnavailableError, sqlite3.Error):
            return
        if not rows:
            return
        count = int(rows[0]["n"] or 0)
        if count < 3:
            return
        stock = market.stocks.get(stock_id)
        await self._push_taunt(
            "add_down",
            market.seed,
            {
                "n": count,
                "avg": f"{float(rows[0]['avg_price'] or 0.0):.2f}",
                "stock": stock.name if stock is not None else stock_id,
            },
        )

    @ui.action(
        id="sell",
        label="卖出",
        icon="minus",
        tone="default",
        group="trade",
        order=2,
    )
    @plugin_entry(
        id="sell",
        name="卖出",
        description="卖出持仓中的虚构标的。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "amount": {"type": "number", "minimum": 0, "description": "卖出数量"},
            },
            "required": ["stock_id", "amount"],
        },
    )
    @_locked
    async def sell(self, stock_id: str, amount: float) -> Ok:
        """卖出。印花税只在卖出方向收取（现实方式，单边）。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _err("invalid_amount")
        if amount <= 0:
            return _err("invalid_amount")

        player = session.player
        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        if not stock.alive:
            return _err("stock_delisted")

        position = player.positions.get(stock_id)
        if position is None or position.amount <= 0:
            return _err("insufficient_position")
        if amount > position.amount + 1e-9:
            return _err("insufficient_position")

        difficulty = session.difficulty
        if difficulty.t_plus_one and amount > position.sellable_amount(player.day) + 1e-9:
            return _err("t_plus_one_locked")

        try:
            trade = execute_sell(
                player,
                stock,
                amount,
                config=engine_config.fees,
                day=player.day,
                tick=market.tick,
                trade_id=0,
                reason="manual",
                t_plus_one=difficulty.t_plus_one,
            )
        except EngineError as engine_error:
            return _err(_engine_error_code(engine_error), str(engine_error))

        market.fee_pool += trade.fee
        market.total_fees += trade.fee

        trade_id = await self._persist_trade(session, trade, stock_id)
        notional = trade.price * trade.amount
        # 手续费总额来自引擎（与成交同源），这里只把它拆成佣金与印花税两项。
        total_fee = sell_fee(notional, engine_config.fees)
        stamp_tax = notional * engine_config.fees.stamp_tax_rate
        remaining = player.positions.get(stock_id)
        # ── P7：猫娘弹幕（见 buy 里的说明）─────────────────────────
        self._push_danmaku(
            pick_taunt(
                "sell",
                seed=player.trade_count,
                params={"stock": stock.name, "amount": amount, "price": trade.price},
            )
        )
        return Ok(
            {
                "stock_id": stock_id,
                "amount": round(trade.amount, 6),
                "price": round(trade.price, 6),
                "notional": round(notional, 6),
                "fee": round(trade.fee, 6),
                "sell_fee": round(total_fee - stamp_tax, 6),
                "stamp_tax": round(stamp_tax, 6),
                "cash_after": round(player.cash, 6),
                "position": (
                    {
                        "amount": round(remaining.amount, 6),
                        "avg_cost": round(remaining.avg_cost, 6),
                        "sellable": round(
                            self._sellable_amount(remaining, player.day, session.difficulty), 6
                        ),
                    }
                    if remaining is not None
                    else None
                ),
                "trade_id": trade_id,
            }
        )

    # ------------------------------------------------------------------
    # 入口：分析 / 新闻详情（P5 · @ui.action）
    # ------------------------------------------------------------------

    @ui.action(
        id="analyze",
        label="分析",
        icon="search",
        tone="default",
        group="info",
        order=10,
    )
    @plugin_entry(
        id="analyze",
        name="分析",
        description=(
            "花一次分析机会看看某只标的的模糊倾向。"
            "每天次数有限；分析只给倾向，不给精确数值，也不告诉你哪天会反转。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {
                    "type": "string",
                    "description": "标的 ID，形如 FAKE-001；留空则分析持仓最重的那只",
                }
            },
            "required": [],
        },
    )
    @_locked
    async def analyze(self, stock_id: str = "") -> Ok:
        """分析一只标的（P5）。

        **每天 ``ANALYSES_PER_DAY`` 次机会**（用掉的次数落库，重启不丢）。

        分析给的是**模糊倾向**：评级（绿／黄／红）、是不是在追高、你的持仓占比。

        **局中隐藏（P5 方案 B）**：

        * 本入口**不给**"今天是不是反转日"这个 bool；
        * 只在当天确有异动时附一句**不带方向**的提示
          （``ANALYSIS_MARKET_NOTE``）—— 玩家只知道"今天特殊"，
          **不知道特殊在哪**；
        * 这句话**只在这里**出现，``@ui.context`` 载荷里没有它 ——
          想拿到就得花分析次数（这正是方案 B 的"有成本"）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        prices = market.prices()

        used = player.analyses_used if player.analyses_day == player.day else 0
        if used >= ANALYSES_PER_DAY:
            return _err("analysis_limit_reached")

        stock = self._analysis_target(session, stock_id, prices)
        if stock is None:
            return _err("stock_not_found")

        heat_config = engine_config.heat
        recent = [value for _, value in stock.kline[-heat_config.chase_window :]]
        chasing = bool(recent) and stock.price >= max(recent) * heat_config.chase_ratio

        position = player.positions.get(stock.id)
        weight = 0.0
        if position is not None:
            total = player.total_asset(prices)
            value = position.market_value(prices.get(stock.id, 0.0))
            weight = (value / total) if total > 0 else 0.0

        if chasing or stock.tag == "explosive":
            rating = "red"
        elif stock.tag == "stable":
            rating = "green"
        else:
            rating = "yellow"

        # ── P5 方案 B：当天异动的**模糊**提示（不带方向、不给 bool）──
        market_note = ""
        if market.day in market.reversal_days:
            market_note = ANALYSIS_MARKET_NOTE

        # 消耗一次机会（落库：重启不丢次数）。
        player.analyses_day = player.day
        player.analyses_used = used + 1
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET analyses_day = ?, analyses_used = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.analyses_day, player.analyses_used, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7.2：花钱买分析也要挨骂（每一次点击都必有一句嘴臭）──────
        self._push_danmaku(
            pick_taunt(
                "analyze",
                seed=player.analyses_used * 13 + int(player.day),
                params={"stock": stock.name},
            )
            or i18n_text("taunt.analyze.1", "花钱买建议？我这儿的建议不值这个价。")
        )

        return Ok(
            {
                "stock_id": stock.id,
                "name": stock.name,
                "price": round(stock.price, 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "rating": rating,
                "chasing": chasing,
                "position_weight": round(weight, 4),
                # 空串 = 今天没有异动；有值也只有一句不带方向的话。
                "market_note": market_note,
                "analyses_left": max(0, ANALYSES_PER_DAY - player.analyses_used),
                "note": ANALYSIS_NOTE,
            }
        )

    @ui.action(
        id="news_detail",
        label="新闻详情",
        icon="news",
        tone="default",
        group="info",
        order=20,
    )
    @plugin_entry(
        id="news_detail",
        name="新闻详情",
        description=(
            "看某条新闻的标题与正文。"
            "局中只给中性分类，点开详情才给标题与正文 —— 但**真假永远不给**，"
            "完整真相只在结算页出现。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "news_id": {"type": "string", "description": "新闻 ID，形如 news-3-2"}
            },
            "required": ["news_id"],
        },
    )
    @_locked
    async def news_detail(self, news_id: str) -> Ok:
        """新闻详情（P5）。

        局中默认只给中性分类（见 :meth:`_public_news`）；**点开详情**才给标题与
        正文 —— 但**仍然不给真假**（``accuracy``）、不给原始 ``type``、
        不给雷新闻标记。完整真相只在结算页出现。

        两种失败要分清：

        * ``news_not_found`` —— 全库都没有这条新闻；
        * ``news_detail_forbidden`` —— 这条新闻**存在**，但不属于当前这一局
          （``news_log`` 跨局累积，旧局的新闻 id 仍然留在库里）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        market = session.market
        for item in market.news_today:
            if item.id != news_id:
                continue
            kind = news_kind(item)
            # ── P7：名册群友的抽象新闻 ──────────────────────────────
            # P7.2：按**本局的绑定**认领群友（群友按波动率落在真实槽位上）。
            headline, roster_alias, roster_title = self._roster_news_headline(
                item, market
            )
            return Ok(
                {
                    "id": item.id,
                    "day": item.day,
                    "stock_id": item.stock_id,
                    "kind": kind,
                    "kind_label": _NEWS_KIND_LABELS.get(kind, "未知"),
                    "headline": headline,
                    "content": item.content,
                    # ── P7：名册彩蛋（群友信息）──────────────────────
                    "roster_alias": roster_alias,
                    "roster_title": roster_title,
                }
            )

        # 不在今天的新闻里 —— 看看是不是**别的局**留下的。
        try:
            rows = await self._repo.fetch_all(
                "SELECT news_id FROM news_log WHERE news_id = ? LIMIT 1", (news_id,)
            )
        except (DbUnavailableError, sqlite3.Error):
            rows = []
        if rows:
            return _err("news_detail_forbidden")
        return _err("news_not_found")

    # ------------------------------------------------------------------
    # 入口：按需拉 K 线（P6 档 2 第 6 项）
    # ------------------------------------------------------------------
    #
    # 局中载荷的 K 线是**限流**的（只给持仓 + 爆炸标签的前
    # ``PANEL_KLINE_MAX`` 只）—— 玩家想看**任意**标的的 K 线就调这个入口。
    # 单只标的，载荷很小（60 个点 ≈ 1 KB），不构成负担。

    @ui.action(
        id="kline",
        label="看K线",
        icon="chart",
        tone="default",
        group="market",
        order=4,
    )
    @plugin_entry(
        id="kline",
        name="查看K线",
        description="按需拉取某只标的的 K 线（最近 60 个点）。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {
                    "type": "string",
                    "description": "标的 ID（形如 FAKE-xxx）",
                }
            },
            "required": ["stock_id"],
        },
    )
    @_locked
    async def kline(self, stock_id: str) -> Ok:
        """按需拉取某只标的的 K 线（P6 档 2 第 6 项）。

        局中载荷只带前 20 只标的的 K 线（限流，避免载荷爆掉）；
        这个入口让玩家能看**任意**标的 —— 包括没持仓的、没爆炸标签的。

        局中隐藏规则照旧：**不给 heat、不给反转日、不给 seed**。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        return Ok(
            {
                "stock_id": stock.id,
                "name": stock.name,
                "price": round(stock.price, 6),
                "prev_price": round(stock.prev_price, 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                "alive": bool(stock.alive),
                "kline": [
                    [int(tick), round(float(value), 4)] for tick, value in stock.kline
                ],
            }
        )

    # ------------------------------------------------------------------
    # P5.5 改动 4：天台虚惊一场（death_scene）
    # ------------------------------------------------------------------
    #
    # 三屏是**梦**：第一屏在天台，第二屏醒来（趴在出租屋的桌上），第三屏落在
    # "你还有机会"。**不写任何具体的自杀细节**（不写"跳下去""往前走"）——
    # 留白本身就是表达。
    #
    # 只对**无限模式的死亡**触发：经典模式是教学关，不需要这么重。

    @staticmethod
    def _death_scene(
        session: Session,
        *,
        dead: bool,
        death_reason: str | None,
        days: int,
    ) -> dict[str, str] | None:
        """构造三屏文案（经典模式恒为 ``None``）。"""
        if not dead or session.mode != "infinite":
            return None
        params = {
            "identity_quote": identity_intro(session.identity),
            "death_reason_label": death_reason_label(death_reason),
            "days": int(days),
        }
        return {
            "screen1": _fill_text(
                i18n_text(
                    "death_scene.screen1",
                    "你站在天台上。风很大。\n你想起入场那天说的话：“{identity_quote}”",
                ),
                params,
            ),
            "screen2": _fill_text(
                i18n_text(
                    "death_scene.screen2",
                    "……然后你醒了。\n你趴在出租屋的桌子上，手机屏幕还亮着，"
                    "股市 K 线还在跳。\n是猫娘把你摇醒的。"
                    "她说：“你刚才说梦话，说什么‘再借一笔就能回本’。”",
                ),
                params,
            ),
            "screen3": _fill_text(
                i18n_text(
                    "death_scene.screen3",
                    "你死了。死因：{death_reason_label}。存活天数：{days} 天。\n"
                    "但你醒过来了。\n现实里，你还有机会不玩这个游戏。",
                ),
                params,
            ),
        }

    # ------------------------------------------------------------------
    # P5.5 改动 5：称号图鉴（achievements 独立表）
    # ------------------------------------------------------------------

    async def _unlocked_achievement_titles(self, player_id: str) -> set[str]:
        """这个玩家已解锁的称号（**称号名**，不是内部 key）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT title FROM achievements WHERE player_id = ?", (player_id,)
            )
        except (DbUnavailableError, sqlite3.Error):
            return set()
        return {str(row["title"]) for row in rows}

    async def _achievements_payload(
        self, player_id: str, identity: str
    ) -> dict[str, Any]:
        """称号图鉴载荷（改动 5 / 改动 7）。

        ``unlocked`` 给称号名 + 叙事句 + 身份呼应句；``locked`` 只给
        「？？？」+ 一句提示 —— **提示里唯一带「活」字的是「无为而治」**，
        那是玩家发现隐藏结局的线索。
        """
        unlocked_titles = await self._unlocked_achievement_titles(player_id)
        echo = identity_echo(identity)
        unlocked: list[dict[str, Any]] = []
        locked: list[dict[str, Any]] = []
        for item in ACHIEVEMENTS:
            if str(item["title"]) in unlocked_titles:
                unlocked.append(
                    {
                        "title": str(item["title"]),
                        "epitaph": str(item["hint"]),
                        "identity_epitaph": echo,
                    }
                )
            else:
                locked.append(
                    {"title": "？？？", "hint": str(item["hint"])}
                )
        return {
            "unlocked": unlocked,
            "locked": locked,
            "total": len(ACHIEVEMENTS),
            "unlocked_count": len(unlocked),
        }

    async def _unlock_achievement(
        self, player_id: str, key: str, *, tick: int
    ) -> dict[str, Any] | None:
        """解锁一个称号（**幂等**）。已经解锁过时返回 ``None``。"""
        item = achievement_by_key(key)
        if item is None:
            return None
        title = str(item["title"])
        try:
            row = await self._repo.fetch_one(
                "SELECT title FROM achievements WHERE player_id = ? AND title = ?",
                (player_id, title),
            )
            if row is not None:
                return None
            await self._repo.transaction(
                [
                    (
                        "INSERT INTO achievements (player_id, title, unlocked_at) "
                        "VALUES (?, ?, ?)",
                        (player_id, title, int(tick)),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as error:
            self.logger.warning(f"称号解锁写入失败（{title}）：{error}")
            return None
        return item

    async def _maybe_unlock_achievements(self, session: Session) -> None:
        """称号解锁的两个触发点（P5.5 改动 5 / 改动 6）。

        1. **死因成就** —— 玩家死的那一刻就解锁（死因 → 称号）。
        2. **「无为而治」** —— 第 ``INACTION_UNLOCK_DAYS`` 天且零有效交易。

        两处都是**幂等**的（``achievements`` 表主键是 ``(player_id, title)``）。
        """
        player = session.player
        if not player.alive:
            reason = player.death_reason or ""
            if reason:
                item = achievement_by_source(reason)
                if item is not None:
                    unlocked = await self._unlock_achievement(
                        player.id, str(item["key"]), tick=session.market.tick
                    )
                    if unlocked is not None:
                        self.logger.info(
                            f"玩家 {player.id} 解锁称号：{unlocked['title']}"
                            f"（死因 {reason}）"
                        )
            return
        await self._maybe_unlock_inaction(session)

    async def _maybe_unlock_settlement_achievements(self, session: Session, report) -> None:
        """结算时的成就解锁（P5.5 改动 5 · P6 选项 B 修正）。

        * 死因 → 对应称号（与日终那次重复时是**幂等**的，不会插两行）；
        * 结局 ``inaction`` → 「无为而治」；
        * **活着**结算时总资产为 0 → 「一贫如洗」。

        「一贫如洗」的口径是 P6 拍板的**选项 B**：必须是**活着**的。
        选项 A（不论死活）会与 5 个死因成就**必然同时解锁**（死在盘上账上必然是零），
        等于没有独立价值；选项 B 两者互斥，叙事才成立（"你没死，但什么都没了"）。
        """
        player = session.player
        tick = int(getattr(report, "ticks_played", 0) or 0)
        if not player.alive and player.death_reason:
            item = achievement_by_source(player.death_reason)
            if item is not None:
                await self._unlock_achievement(
                    player.id, str(item["key"]), tick=tick
                )
        if getattr(report, "ending", None) == "inaction":
            await self._unlock_achievement(player.id, "inaction", tick=tick)
        if (
            player.alive
            and abs(float(getattr(report, "final_asset", 0.0) or 0.0)) <= 1e-9
        ):
            await self._unlock_achievement(player.id, "broke", tick=tick)

    async def _maybe_unlock_inaction(self, session: Session) -> None:
        """「无为而治」的解锁判定（P5.5 改动 6）。

        存活到第 ``INACTION_UNLOCK_DAYS`` 天且**零有效交易**。
        「有效交易」用**引擎的豁免口径**（与隐藏结局 ``check_inaction`` 一致）——
        字面的 ``trade_count == 0`` 在无限模式下**永远不可达**（开局必须卖掉
        基准股票才能交上第一天生活费）。

        触发后**不强制结束**：玩家可以继续"什么都不做"地活着。
        """
        player = session.player
        if not player.alive or session.mode != "infinite":
            return
        if int(session.inaction_unlocked):
            return
        ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
        days = session.market.tick // ticks_per_day
        if days < INACTION_UNLOCK_DAYS:
            return
        exempt = await self._inaction_exempt_trades(session)
        effective = int(player.trade_count) - max(0, exempt)
        if effective > 0:
            return

        item = await self._unlock_achievement(
            player.id, "inaction", tick=session.market.tick
        )
        if item is None:
            return
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET inaction_unlocked = 1, updated_at = ? "
                        "WHERE id = ?",
                        (time.time(), player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            self.logger.warning(f"无为而治标记写入失败：{db_error}")
            return
        unlocked_titles = await self._unlocked_achievement_titles(player.id)
        note = ACHIEVEMENT_UNLOCK_NOTE.format(
            days=days, title=str(item["title"])
        )
        await self._push_card(
            build_achievement_card(
                title_name=str(item["title"]),
                note=note,
                unlocked_count=len(unlocked_titles),
                total=len(ACHIEVEMENTS),
                identity_echo=identity_echo(session.identity),
            )
        )
        self.logger.info(f"玩家 {player.id} 解锁称号：{item['title']}（第 {days} 天）")

    # ------------------------------------------------------------------
    # 入口：结算本局
    # ------------------------------------------------------------------

    @plugin_entry(
        id="settle",
        name="结算本局",
        description="结束当前这一局并给出结算报告。",
    )
    @_locked
    async def settle(self) -> Ok:
        """结束整局并返回结算报告。

        这是**唯一**允许出现 ``seed`` 与 ``heat_curve`` 的入口——局中它们必须
        完全隐藏，结算页才允许展示。

        经典模式在第 30 个游戏日结束时**已经自动结算**过了，这时再调本入口会
        拿到 ``no_active_game``（结算不重复）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("no_active_game")

        player = session.player
        market = session.market
        payload = await self._build_settlement_payload(session, engine_config)

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = ?, updated_at = ? WHERE id = ?",
                        (int(player.alive), now, player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        self._archive_settlement(payload, session)
        return Ok(payload)

    async def _build_settlement_payload(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """组装结算报告（``settle`` 与经典模式自动结算共用）。

        这是 heat 曲线与种子**唯一**允许出现的地方 —— 也是新闻**真相**唯一
        允许出现的地方（局中只看得到中性分类）。
        """
        player = session.player
        market = session.market
        difficulty = session.difficulty

        exempt = await self._inaction_exempt_trades(session)
        ending = (
            "inaction"
            if player.alive and check_inaction(player, max_trades=0, exempt_trades=exempt)
            else None
        )
        harvest_events = await self._harvest_events(player.id)
        catgirl_result = await self._catgirl_result(session)
        news_log = await self._news_log()

        # 「存活天数」= 走完的游戏日数（tick 数 ÷ 每日 tick 数）。
        # 这与 P1/P2 契约的样例一致（60 tick ÷ 12 = 5 天），也避免了
        # "日终结算后 player.day 已经 +1" 带来的差一天。
        days_survived = market.tick // max(1, int(difficulty.ticks_per_day))
        if session.mode != "infinite":
            days_survived = min(days_survived, engine_config.classic.total_game_days)

        report = build_settlement_report(
            player,
            market=market,
            mode=player.mode,
            days_survived=days_survived,
            ticks_played=market.tick,
            ticks_per_day=difficulty.ticks_per_day,
            achievements=[],
            ending=ending,
            catgirl_result=catgirl_result,
            news_log=news_log,
        )
        infinite = engine_config.infinite
        # ── P5.5 改动 5：结算时的成就解锁（死因 / 结局 / 一贫如洗）────
        await self._maybe_unlock_settlement_achievements(session, report)
        # ── P5.5 改动 2/3：结算页的叙事三件套 ──────────────────────
        # 身份呼应句**只在死亡结算页**出现（活到终局不给）。
        dead = not player.alive and bool(player.death_reason)
        identity_echo_text = identity_echo(session.identity) if dead else ""
        # 猫娘沉默旁白：只在**本局有猫娘**时出现，概率 30%，按 seed 稳定。
        catgirl_silence_text = ""
        if session.catgirl is not None and pick_catgirl_silence(market.seed):
            catgirl_silence_text = i18n_text(
                "settlement.catgirl_silence", "猫娘站在旁边，什么都没说。"
            )
        # 「无为而治」的打工叙事。
        inaction_worker_text = (
            i18n_text("settlement.inaction_worker", "你每周打工、交生活费、不碰股市。刚好活下来。")
            if ending == "inaction"
            else ""
        )
        # ── P5.5 改动 5：死亡称号的叙事句（epitaph）────────────────
        # **插件层**的映射表（引擎的 DEATH_TITLES 一行不动）。
        epitaph_text = ""
        if report.death_reason:
            epitaph_text = EPITAPHS.get(report.death_reason, "")
        elif ending == "inaction":
            epitaph_text = EPITAPHS["inaction"]
        # 细节 A（拍板）：``{epitaph} {身份呼应句}`` 一句话读完。
        display_title = report.death_title
        if epitaph_text:
            display_title = (
                f"{epitaph_text} {identity_echo_text}".strip()
                if identity_echo_text
                else epitaph_text
            )
        # ── P5.5 改动 4：天台虚惊一场（**只对无限模式的死亡**）──────
        death_scene = self._death_scene(
            session,
            dead=dead,
            death_reason=report.death_reason,
            days=report.days_survived,
        )
        # ── P5.5 改动 5：称号图鉴 ────────────────────────────────
        achievements = await self._achievements_payload(player.id, session.identity)
        return {
            "player_id": report.player_id,
            "mode": report.mode,
            "mode_name": _MODE_NAMES.get(report.mode, report.mode),
            "seed": report.seed,
            "survivor": report.survivor,
            "days_survived": report.days_survived,
            "ticks_played": report.ticks_played,
            "trade_count": report.trade_count,
            "initial_cash": round(report.initial_cash, 6),
            "final_cash": round(report.final_cash, 6),
            "final_asset": round(report.final_asset, 6),
            "total_fee_paid": round(report.total_fee_paid, 6),
            "fee_pool_total": round(report.fee_pool_total, 6),
            "heat_curve": [round(value, 4) for value in report.heat_curve],
            "peak_day": report.peak_day,
            "peak_heat": round(report.peak_heat, 4),
            "death_reason": report.death_reason,
            "death_title": display_title,
            "epitaph": epitaph_text,
            "achievements": report.achievements,
            "ending": report.ending,
            # ── P3 新增：收割时间线与猫娘结局 ──
            # 这里是**唯一**能看到收割时刻 heat 数值的地方（局中看不到）。
            "harvest_events": harvest_events,
            "catgirl_result": catgirl_result,
            # ── P4 新增：低保 / 高利贷 / 新闻真相 ──
            "welfare_summary": self._welfare_summary(session, engine_config),
            "loan_summary": self._loan_summary(session, engine_config),
            "news_log": news_log,
            # ── P5.5 新增：叙事三件套（只在结算页）──────────────────
            "identity": session.identity,
            "identity_echo": identity_echo_text,
            "catgirl_silence": catgirl_silence_text,
            "inaction_worker": inaction_worker_text,
            # ── P5.5 改动 4/5/7 ───────────────────────────────────
            "death_scene": death_scene,
            "achievements": achievements,
            "anti_fraud_notice": ANTI_FRAUD_NOTICE,
        }

    async def _inaction_exempt_trades(self, session: Session) -> int:
        """隐藏结局豁免的交易笔数。

        无限模式开局现金为 0，玩家**必须**先卖掉基准股票才交得上第一天生活费
        —— 那笔不是他的主动交易，豁免。除此之外的每一笔买卖都算数。
        """
        if session.mode != "infinite":
            return 0
        try:
            row = await self._repo.fetch_one(
                "SELECT side FROM trades WHERE player_id = ? ORDER BY id ASC LIMIT 1",
                (session.player.id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return 0
        if row is None or row["side"] != "sell":
            return 0
        return 1

    def _welfare_summary(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """工资小结（工资是主动领的，不领就欠着）。"""
        player = session.player
        infinite = engine_config.infinite
        if session.mode != "infinite":
            return {
                "available": False,
                "note": "经典模式没有工资。",
            }
        return {
            "available": True,
            "claimed": player.welfare_claimed_day > 0,
            "claimed_day": player.welfare_claimed_day,
            "missed_days": player.welfare_missed_days,
            "limit_days": infinite.welfare_claim_limit_days,
            "interval_days": infinite.relief_interval_days,
            "weekly_relief": round(infinite.weekly_relief, 6),
            "note": (
                "工资必须主动领。连着七天不去领，第八天日终就没人保你了。"
                if player.welfare_missed_days < infinite.welfare_claim_limit_days
                else "你已经连着七天没领工资了。"
            ),
        }

    def _loan_summary(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """高利贷小结。"""
        player = session.player
        infinite = engine_config.infinite
        if session.mode != "infinite":
            return {"available": False, "note": "经典模式没有高利贷。"}
        return {
            "available": True,
            "borrowed": player.loan_taken_day > 0,
            "taken_day": player.loan_taken_day,
            "outstanding": round(player.loan_debt, 6),
            "amount": round(infinite.loan_amount, 6),
            "repay": round(infinite.loan_repay, 6),
            "note": "借五百还六百。还不上，明天就是你的忌日。",
        }

    # ------------------------------------------------------------------
    # P5.5 改动 1：死因称号的**文案层覆盖**
    # ------------------------------------------------------------------
    #
    # 引擎的 ``DEATH_TITLES`` 里还留着"低保"字样（P5.5 总约束：``engine/``
    # 一行不动），但玩家**永远看不到**那一句 —— 插件层按 ``death_reason``
    # （**死因枚举**，不是字符串比对）改写称号。
    #
    #: 死因 → i18n key（值在 ``i18n/zh-CN.json`` 的 ``death.*`` 命名空间）。
    _DEATH_TITLE_OVERRIDES: dict[str, str] = {
        "welfare_neglect": "death.welfare_neglect",
    }

    @classmethod
    def _override_death_title(cls, player: Player) -> None:
        """把引擎产出的死因称号改写成插件层文案（**原地**修改）。"""
        reason = player.death_reason
        if not reason:
            return
        key = cls._DEATH_TITLE_OVERRIDES.get(reason)
        if key is None:
            return
        player.death_title = i18n_text(
            key, "连续七天没去领工资。制度不养闲人 —— 但这条命是你自己的。"
        )

    async def _news_log(self) -> list[dict[str, Any]]:
        """新闻全记录（**含真假**）—— 只有结算页能拿到。

        局中玩家只看得到中性分类（bullish / bearish / vague / noise）；这里
        才给出真新闻、假新闻与雷新闻的真相。
        """
        try:
            rows = await self._repo.fetch_all(
                "SELECT day, news_id, news_type, stock_id, content, accuracy, direction "
                "FROM news_log ORDER BY day ASC, news_id ASC"
            )
        except (DbUnavailableError, sqlite3.Error):
            return []
        keys = rows[0].keys() if rows else ()
        return [
            {
                "day": int(row["day"]),
                "id": row["news_id"],
                "type": row["news_type"],
                "stock_id": row["stock_id"],
                "headline": row["content"],
                "accuracy": row["accuracy"],
                "direction": row["direction"] if "direction" in keys else None,
            }
            for row in rows
        ]

    def _archive_settlement(self, payload: dict[str, Any], session: Session) -> None:
        """把结算产物归档到用户数据目录（**不是**插件源码目录）。"""
        try:
            folder = self.data_path("settlements")
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (
                f"settlement-{session.player.id}-{session.market.tick}.json"
            )
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            self.logger.warning(f"结算归档写入失败：{error}")

    # ------------------------------------------------------------------
    # 卡片投递（P5）
    # ------------------------------------------------------------------

    async def _push_card(self, card: Card) -> None:
        """投递一张卡片到聊天频道。

        卡片走 ``ctx.create_card(...)``（**公开 SDK API**），**AI 看不到**
        （``ai_behavior="blind"``）。投递失败**不影响本局** —— 游戏状态已经
        落库了，卡片只是"锦上添花"。

        严禁改回 ``plugin.sdk.shared.core.cards.create_card``：``shared`` 是宿主
        **内部实现**，插件直引会导致在真实宿主里启动即崩
        （``No module named 'plugin.sdk.shared.core.cards'``）。

        同理**不要**为具体的卡片异常类写 ``except``：宿主不同版本导出的
        门面符号集并不一致（0.7.1 引的 ``CardSubmissionError`` 在 Steam 版
        运行时就**不存在** → ``cannot import name`` → 启动即崩）。
        一律用 ``except Exception`` 兜底记日志。

        **0.7.4 真机复盘**：Steam 版宿主（0.9.0）的 SDK 门面里**根本没有**
        卡片 API ——``create_card`` / ``ChatCard`` / ``PluginView`` 全部
        不存在（实测：运行时二进制里 ``create_card`` 出现 0 次）。所以这里
        **按能力探测**：有 ``create_card`` 就用卡片；没有就降级成一条
        **纯文本**聊天消息（``push_message`` 在 0.9.0 里是有的）。
        """
        if card is None:
            return
        creator = getattr(self.ctx, "create_card", None)
        if callable(creator):
            try:
                await creator(
                    html=card.html,
                    summary=card.summary,
                    css=CARD_CSS,
                    actions=card.action_spec(),
                )
                return
            except Exception as error:  # pragma: no cover - 宿主侧异常
                self.logger.warning(f"卡片投递失败（{card.kind}）：{error}")
                return

        # 宿主没有卡片 API（Steam 版 0.9.0）：降级成一条纯文本消息。
        push = getattr(self.ctx, "push_message", None)
        if not callable(push):
            self.logger.info(
                f"宿主未提供卡片 API（{card.kind}）：本次跳过卡片投递"
            )
            return
        try:
            result = push(
                visibility=["chat"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": card.summary}],
            )
            if hasattr(result, "__await__"):
                await result
        except Exception as error:  # pragma: no cover - 宿主侧异常
            self.logger.warning(f"卡片降级投递失败（{card.kind}）：{error}")

    async def _push_taunt(
        self, trigger: str, seed: int, params: dict[str, Any] | None = None
    ) -> None:
        """按触发点推一张猫娘嘲讽卡片（文案按 seed **稳定**选取）。"""
        text = pick_taunt(trigger, seed, params)
        if not text:
            return
        await self._push_card(
            build_taunt_card(trigger=trigger, text=text, detail="")
        )

    async def _push_first_news_card(self, news: list[Any]) -> None:
        """推当天的「突发新闻」卡片 —— **只推当天的第一条**。

        **为什么不按新闻类型挑**（P5 文档契约变更第 2 条）：

        如果只给雷新闻推卡片，玩家看到"偏空 + 有卡片"就能推断"这条是雷"，
        P4 §11 的"局中不泄露真假与雷"当场作废。所以推送规则**与类型无关** ——
        每天固定推第一条，卡片出现与否不携带任何类型信息。
        """
        if not news:
            return
        item = news[0]
        kind = news_kind(item)
        await self._push_card(
            build_news_flash_card(
                news_id=item.id,
                kind=kind,
                kind_label=_NEWS_KIND_LABELS.get(kind, "未知"),
                headline=item.content,
                content=item.content,
                day=item.day,
                stock_id=item.stock_id,
            )
        )

    async def _push_cards(
        self,
        session: Session,
        outcome,
        engine_config: EngineConfig,
    ) -> None:
        """把这一 tick 产生的事件变成卡片（P5）。

        推送规则
        --------
        * **突发新闻卡片按"当天的第一条"推送**，**不看新闻类型** ——
          如果只给雷新闻推卡片，玩家立刻能推断"这条是雷"，
          P4 §11 的"局中不泄露真假与雷"就被破坏了（详见 P5 文档契约变更）。
        * **账单 / 二道贩子卡片**只在日终且交不起时推。
        * **猫娘嘲讽**按 5 个触发点推（见 ``cards.TAUNT_TRIGGERS``）。
        """
        market = session.market
        player = session.player
        difficulty = session.difficulty
        prices = market.prices()
        seed = market.seed
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        daily = outcome.daily

        # ── ① 收割卡片（每次收割一张）────────────────────────────
        for result in outcome.liquidations:
            stock = market.stocks.get(result.stock_id)
            position = player.positions.get(result.stock_id)
            amount = position.amount if position is not None else 0.0
            await self._push_card(
                build_harvest_card(
                    tick=market.tick,
                    day=player.day,
                    stock_id=result.stock_id,
                    stock_name=stock.name if stock is not None else result.stock_id,
                    concentration=result.concentration,
                    severity=result.severity,
                    price_before=result.price_before,
                    price_after=result.price_after,
                    amount=amount,
                    trigger=result.trigger,
                )
            )
            if result.concentration >= 0.5:
                loss = max(0.0, result.price_before - result.price_after) * amount
                await self._push_taunt(
                    "harvest",
                    seed,
                    {
                        "stock": stock.name if stock is not None else result.stock_id,
                        "weight": f"{result.concentration:.0%}",
                        "severity": f"{result.severity:.0%}",
                        "loss": f"{loss:.2f}",
                    },
                )

        # ── ② 突发新闻卡片（当天的第一条，**不按类型挑**）──────────
        await self._push_first_news_card(list(outcome.news))

        # ── ③ 重仓退市 → 猫娘嘲讽 ─────────────────────────────────
        total_assets = player.total_asset(prices)
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            if stock is None or stock.alive:
                continue
            if total_assets <= 0:
                continue
            weight = position.market_value(prices.get(stock_id, 0.0)) / total_assets
            if weight < 0.5:
                continue
            await self._push_taunt(
                "heavy_delisted",
                seed,
                {"stock": stock.name, "weight": f"{weight:.0%}"},
            )

        # ── ④ 猫娘自己饿死 → 猫娘嘲讽 ─────────────────────────────
        catgirl = session.catgirl
        if catgirl is not None and not catgirl.alive and daily is not None:
            await self._push_taunt(
                "catgirl_dead",
                seed,
                {"days": market.tick // ticks_per_day},
            )

        if daily is None:
            return

        settlement = daily.settlement
        living_cost = daily_living_cost(player.mode, player.day, seed, engine_config)
        next_cost = next_living_cost(difficulty, engine_config)

        # ── ⑤ 账单卡片 + 二道贩子报价卡片（交不起时）───────────────
        if settlement is not None and next_cost > 0 and player.cash < next_cost:
            await self._push_card(
                build_bill_card(
                    day=player.day,
                    living_cost=living_cost,
                    next_cost=next_cost,
                    cash=player.cash,
                    gap=next_cost - player.cash,
                    missed_bills=int(player.strikes),
                )
            )
            pending = player.pending_action or {}
            legs = pending.get("legs") or []
            if pending.get("kind") == "pawnbroker_sell" and legs:
                await self._push_card(
                    build_pawnbroker_card(
                        day=player.day,
                        gap=next_cost - player.cash,
                        discount=float(pending.get("discount", 0.2)),
                        legs=[
                            {
                                "stock_id": leg.get("stock_id", ""),
                                "stock_name": leg.get("stock_id", ""),
                                "amount": leg.get("amount", 0.0),
                                "notional": float(leg.get("amount", 0.0))
                                * float(leg.get("price", 0.0)),
                            }
                            for leg in legs
                        ],
                        cash_after=player.cash,
                        window_seconds=int(
                            max(0.0, self._daily_window_until - time.time())
                        ),
                    )
                )

        # ── ⑥ 斩杀卡片 + 现钱斩杀嘲讽 ──────────────────────────────
        if settlement is not None and settlement.dead and player.death_reason:
            # 改动 5：死因的**叙事句**（epitaph）当称号用 —— 细节 A：
            # ``{epitaph} {身份呼应句}`` 一句话读完。
            epitaph = EPITAPHS.get(player.death_reason, "")
            await self._push_card(
                build_kill_card(
                    death_reason=player.death_reason or "",
                    death_title=epitaph
                    or player.death_title
                    or player.death_reason
                    or "",
                    days_survived=market.tick // ticks_per_day,
                    total_asset=player.total_asset(prices),
                    harvest_count=await self._harvest_count(player.id),
                    # ── P5.5 改动 2/3：结算时刻的叙事 ──────────────
                    # 身份呼应句 + 猫娘沉默旁白（与结算报告用同一套判定，
                    # 所以面板和聊天卡片说的是同一件事）。
                    identity_echo=identity_echo(session.identity),
                    catgirl_silence=(
                        i18n_text(
                            "settlement.catgirl_silence",
                            "猫娘站在旁边，什么都没说。",
                        )
                        if session.catgirl is not None
                        and pick_catgirl_silence(seed)
                        else ""
                    ),
                )
            )
            if player.death_reason in {"starved", "liquidated"}:
                await self._push_taunt(
                    "cash_kill",
                    seed,
                    {
                        "bill": f"{next_cost:.2f}",
                        "cash": f"{player.cash:.2f}",
                        "gap": f"{max(0.0, next_cost - player.cash):.2f}",
                        "days": market.tick // ticks_per_day,
                        "title": player.death_title or "出局",
                    },
                )

        # ── ⑦ 猫娘播报 + 低保提醒（无限模式日终）──────────────────
        if player.mode == "infinite":
            if catgirl is not None:
                await self._push_card(
                    build_catgirl_daily_card(
                        day=player.day,
                        action=self._catgirl_action_label(),
                        cash=catgirl.cash,
                        positions_value=catgirl.position_value(prices),
                        days_survived=market.tick // ticks_per_day,
                        position_count=len(catgirl.positions),
                    )
                )
            cycle_start = ((player.day - 1) // 7) * 7 + 1
            if (
                player.day == cycle_start
                and player.welfare_claimed_day < cycle_start
            ):
                await self._push_card(
                    build_welfare_card(
                        cycle=(player.day - 1) // 7 + 1,
                        amount=engine_config.infinite.weekly_relief,
                        interval_days=engine_config.infinite.relief_interval_days,
                    )
                )

    async def _harvest_count(self, player_id: str) -> int:
        """这个玩家一共被收割了几次（斩杀卡片用）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT COUNT(*) AS n FROM liquidation_log WHERE player_id = ?",
                (player_id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return 0
        if not rows:
            return 0
        return int(rows[0]["n"])

    def _catgirl_action_label(self) -> str:
        """猫娘"今天做了什么"的一句话（面板与卡片共用）。"""
        daily = self._catgirl_daily or {}
        trades = int(daily.get("trades", 0)) if daily else 0
        previous = self._catgirl_trades_seen
        if previous is None:
            return "—"
        if trades > previous:
            return f"交易了 {trades - previous} 笔"
        return "观望"

    # ------------------------------------------------------------------
    # 心跳与 tick 流水线（P3）
    # ------------------------------------------------------------------

    @timer_interval(id="market_tick", name="市场心跳", seconds=1, auto_start=True)
    async def market_tick(self, **_) -> Ok:
        """1 秒**心跳**。

        **心跳不是 tick。** 真正的 tick 间隔由难度配置决定（经典四档 8 秒、
        无限模式 30 秒），这里只负责判断"到没到期"。

        为什么不是直接把 ``seconds`` 设成 8/30：SDK 的 ``@timer_interval``
        要求 ``seconds`` 是**类定义期的字面量整数**（运行时读不到配置），
        而本局的模式是玩家 ``join`` 时才确定的。详见 P3 契约 §1.1。
        """
        started = time.perf_counter()
        due = 0
        try:
            due = await self._due_ticks()
            if due > 0:
                await self._run_ticks(due)
        except Exception as error:  # noqa: BLE001 - 心跳绝不能把定时器带崩
            self.logger.error(f"市场心跳异常（不影响后续 tick）：{error}")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > 100.0:
            # 只有真的慢了才去读配置，避免每秒都打一次宿主配置
            warn_ms = (await self._engine_config()).market.heartbeat_warn_ms
            if elapsed_ms > warn_ms:
                self.logger.warning(f"市场心跳耗时 {elapsed_ms:.0f}ms，偏慢")
        # 每分钟打一条心跳体检：真机上"tick 不推进"这类问题只能靠它定位
        # （0.7.4 真机复盘）。
        self._heartbeat_count += 1
        if self._heartbeat_count % _HEARTBEAT_LOG_EVERY == 0:
            since = time.time() - self._last_tick_at if self._last_tick_at > 0.0 else 0.0
            self.logger.info(
                f"市场心跳体检：{self._heartbeat_count} 次，本次到期 {due} 个，"
                f"距上次推进 {since:.0f}s"
            )
        return Ok({"status": "heartbeat", "due": due})

    async def _fetch_due_row(self) -> tuple[bool, Any]:
        """读当前局的 ``player_id`` 与模式。

        返回 ``(读到了, 行)``。**"读不到"和"没有这一局"是两回事**：

        * 读不到 → 数据库不可用（宿主还没下发有效配置 / 库被删了），
          要刷新运行时能力再重试，而且**不能**动 ``_last_tick_at`` 基线
          ——否则库恢复后时间差被抹平，市场永远追不上；
        * 读到了但没有行 → 确实没有开局，重置基线（防止"插件重启即跳盘"）。
        """
        try:
            row = await self._repo.fetch_one(
                "SELECT ms.player_id AS player_id, p.mode AS mode "
                "FROM market_state ms LEFT JOIN players p ON p.id = ms.player_id "
                "WHERE ms.id = 1"
            )
        except (DbUnavailableError, sqlite3.Error):
            return False, None
        return True, row

    async def _due_ticks(self) -> int:
        """算出这次心跳该补几个 tick（0 = 还没到期）。"""
        # 先读一次配置：顺带把宿主补发的有效配置同步进运行时能力
        # （``db.enabled`` / ``state.backend``），否则心跳会一直撞在
        # "数据库不可用"上（0.7.4 真机复盘：tick 不推进的根因）。
        engine_config = await self._engine_config()

        readable, row = await self._fetch_due_row()
        if not readable:
            # 库读不到：拉一次最新有效配置再试一次。
            await self._refresh_runtime_capabilities()
            readable, row = await self._fetch_due_row()
            if not readable:
                # 库确实不可用：**保留基线**，等库恢复后按"补 tick"规则追赶。
                return 0

        now = time.time()
        if row is None or not row["player_id"] or not row["mode"]:
            self._last_tick_at = now
            return 0

        difficulty = engine_config.difficulties.get(row["mode"])
        if difficulty is None:
            return 0
        interval = max(1, int(difficulty.tick_interval_seconds))
        if self._last_tick_at <= 0.0:
            # 首次心跳只记基线：不跳盘（插件重启即跳盘是最糟的体验）
            self._last_tick_at = now
            return 0
        due = int((now - self._last_tick_at) // interval)
        if due <= 0:
            return 0
        cap = max(1, int(engine_config.market.max_catchup_ticks))
        if due > cap:
            self.logger.warning(
                f"补 tick：本次心跳到期 {due} 个，只补 {cap} 个（跳过 {due - cap} 个）"
            )
            due = cap
        return due

    async def _run_ticks(self, count: int) -> None:
        """连续跑 ``count`` 个 tick。"""
        for _ in range(max(1, count)):
            await self._run_one_tick()

    async def _run_one_tick(self) -> None:
        """推进一个 tick（持锁；异常不影响定时器）。"""
        with self._lock:
            if self._tick_running:
                return
            self._tick_running = True
            try:
                await self._tick_once()
            except Exception as error:  # noqa: BLE001
                # 异常发生时**不写回**（内存里的半成品直接丢弃）
                self.logger.error(f"tick 异常（不影响后续 tick）：{error}")
            finally:
                self._tick_running = False
                self._last_tick_at = time.time()

    async def _tick_once(self) -> None:
        """tick 流水线（P4：瘦身为**纯调度**）。

        顺序**不能乱**：先价格、再 heat 衰减、再模式层（当天新闻 + 阶段判定与
        收割调度）、再收割执行、再日终结算，最后才写回。

        本方法**不含任何游戏规则** —— 规则全在 ``modes/`` 里；这里只负责把
        模式层算出来的结果**执行**（收割）与**落库**。
        """
        started = time.perf_counter()
        engine_config = await self._engine_config()
        try:
            session = await load_session(
                self._repo, self.state, engine_config=engine_config
            )
        except (DbUnavailableError, sqlite3.Error) as error:
            # 宿主补发有效配置后重试一次。
            await self._refresh_runtime_capabilities()
            try:
                session = await load_session(
                    self._repo, self.state, engine_config=engine_config
                )
            except (DbUnavailableError, sqlite3.Error):
                self.logger.warning(f"tick 跳过：数据库不可用（{error}）")
                return
        if session is None:
            return

        mode = get_mode(session.mode)
        if mode is None:
            return

        market = session.market
        player = session.player
        difficulty = session.difficulty
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        tick = market.tick
        journal = _TickJournal()
        # 记住玩家 id：局终后 ``market_state.player_id`` 被清空，
        # 面板要靠它去找归档的结算报告（P4 遗留问题 4）。
        self._last_player_id = player.id

        # ── 步 2：更新所有存活标的价格（公共底盘）────────────────
        advance_market(
            market,
            seed=market.seed,
            ticks_per_day=ticks_per_day,
            config=engine_config.market,
        )
        market.day = market.tick // ticks_per_day + 1

        # ── P7：导演暴跌（**只在特制版**，纯净版这里什么都不做）────
        # 排在 advance_market 之后、模式层之前 —— 这样暴跌之后的集中度、
        # 收割等后续反应**照常发生**，而不是硬改结果。引擎一行没改：
        # 暴跌用的是引擎既有的纯函数 apply_jump。
        crashed = self._apply_director_crashes(market, engine_config)
        if crashed:
            journal.touch_player(player)

        # ── 步 3：衰减上头指数（交易事件在成交时加，这里只做衰减）──
        prices = market.prices()
        concentration = position_concentration(player.positions, prices)
        player.heat = update_heat(
            player.heat,
            events=[],
            concentration=concentration,
            config=engine_config.heat,
        )
        player.heat_history.append(player.heat)
        journal.touch_player(player)

        # ── 步 4~7：模式层（当天新闻 + 阶段判定与收割调度）────────
        ctx = ModeContext(
            player=player,
            market=market,
            difficulty=difficulty,
            engine_config=engine_config,
            catgirl=session.catgirl,
            mode=session.mode,
            now=time.time(),
        )
        outcome = mode.tick(ctx)

        # ── 步 5 / 6 / 7：收割执行（公共底盘，由模式层调度）───────
        self._execute_harvest(ctx, outcome, journal, engine_config, tick)

        # ── 步 8 / 9：日终结算（模式层）──────────────────────────
        if market.tick % ticks_per_day == 0:
            daily = mode.daily_settlement(ctx)
            outcome.daily = daily
            outcome.trades.extend(daily.trades)
            outcome.pawnbroker.extend(daily.pawnbroker)
            if daily.distress_window_until:
                self._daily_window_until = daily.distress_window_until
            journal.daily = daily.settlement
            journal.catgirl_daily = daily.catgirl_daily
            if daily.catgirl_daily is not None:
                # 面板要显示"猫娘今天动没动手"：靠**累计成交笔数**的增量。
                self._catgirl_daily = dict(daily.catgirl_daily)
                self._catgirl_trades_seen = int(daily.catgirl_daily.get("trades", 0))

        # ── P5.5：死因称号的文案层覆盖（引擎零改动）───────────────
        # 引擎给的称号里还有"低保"字样；这里按 death_reason 改写，
        # 于是落库的、面板上的、卡片里的称号**全部**是"打工工资"的说法。
        self._override_death_title(player)
        if session.catgirl is not None:
            self._override_death_title(session.catgirl)

        # ── 步 10：一次性写回 ────────────────────────────────────
        # 模式层可能同时改动了玩家与猫娘：两个都要写回。
        journal.touch_player(player)
        if session.catgirl is not None:
            journal.touch_player(session.catgirl)

        elapsed = time.perf_counter() - started
        if elapsed > engine_config.market.tick_skip_seconds:
            self.logger.error(
                f"tick 耗时 {elapsed:.1f}s，超过 {engine_config.market.tick_skip_seconds}s "
                "阈值，放弃本次 tick（不写回）"
            )
            return
        if elapsed > engine_config.market.tick_warn_seconds:
            self.logger.warning(f"tick 耗时 {elapsed:.1f}s，偏慢")

        await self._commit_tick(session, journal, engine_config, outcome)
        await save_snapshot(self.state, market, player.id)

        # ── 步 11：通知（P3 用日志，**P5 改卡片**）──────────────────
        for result in outcome.liquidations:
            self._pending_event = {
                "kind": "harvest",
                "tick": market.tick,
                "stock_id": result.stock_id,
                "concentration": round(result.concentration, 4),
                "severity": round(result.severity, 4),
            }
            self.logger.info(
                f"玩家 {player.id} 在第 {market.tick} tick 被收割："
                f"{result.stock_id} 集中度 {result.concentration:.0%} "
                f"触发源 {result.trigger}"
            )

        # P5：把这一 tick 的事件变成聊天频道里的卡片。
        await self._push_cards(session, outcome, engine_config)

        # ── P5.5 改动 5/6：称号解锁 ────────────────────────────────
        # 死因成就（死的那一刻就解锁）+ 「无为而治」（第 14 天零有效交易）。
        await self._maybe_unlock_achievements(session)

        # ── P4：经典模式第 30 个游戏日自动终局并自动结算 ──────────
        await self._auto_finish_if_needed(session, ctx, mode, engine_config)

    def _execute_harvest(
        self,
        ctx: ModeContext,
        outcome,
        journal: _TickJournal,
        engine_config: EngineConfig,
        tick: int,
    ) -> None:
        """执行模式层调度出来的收割（公共底盘）。

        模式层只回答"该不该收割、用什么阈值"；**执行**在这里 —— 因为执行要
        落库（收割流水），而模式层不碰数据库。两个触发源共用一个执行函数，
        每 tick 最多收割一次。
        """
        result = execute_scheduled_harvest(ctx, outcome.phase_decision, tick=tick)
        if result is not None:
            outcome.liquidations.append(result)
            journal.liquidations.append(result)

    async def _auto_finish_if_needed(
        self,
        session: Session,
        ctx: ModeContext,
        mode,
        engine_config: EngineConfig,
    ) -> None:
        """经典模式第 30 个游戏日结束 → **自动终局并自动结算**。

        自动结算之后，玩家再调 ``settle`` 会拿到 ``no_active_game`` ——
        结算不重复。无限模式没有终点，这段对它什么也不做。
        """
        if not mode.is_finished(ctx):
            return
        if session.mode == "infinite":
            return

        payload = await self._build_settlement_payload(session, engine_config)
        payload["auto_finished"] = True
        payload["finish_reason"] = "classic_total_days"

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = ?, updated_at = ? WHERE id = ?",
                        (int(session.player.alive), now, session.player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            self.logger.warning(f"自动结算落库失败：{db_error}")
            return

        self._archive_settlement(payload, session)
        self.logger.info(
            f"经典模式第 {engine_config.classic.total_game_days} 个游戏日结束，"
            f"玩家 {session.player.id} 自动终局并结算"
        )
    async def _commit_tick(
        self,
        session: Session,
        journal: _TickJournal,
        engine_config: EngineConfig,
        outcome,
    ) -> None:
        """把一个 tick 的全部改动写进数据库（一个事务）。

        P4：成交、收割、二道贩子流水、新闻都来自**模式层**的 ``outcome``；
        插件层只负责把它们拼成一个事务写回去 —— 这里不含任何游戏规则。
        """
        market = session.market
        now = time.time()
        actions: list[tuple[str, tuple[Any, ...]]] = []

        for trade_id in journal.deleted_trades:
            actions.append(("DELETE FROM trades WHERE id = ?", (trade_id,)))

        trades = list(journal.trades) + list(outcome.trades)

        # 持仓：凡是本 tick 被动过的（成交涉及的）都要重写一遍。
        touched: dict[str, set[str]] = {}
        for trade in trades:
            touched.setdefault(trade.player_id, set()).add(trade.stock_id)
        for player_id, stock_ids in journal.positions.items():
            touched.setdefault(player_id, set()).update(stock_ids)

        for player_id, stock_ids in touched.items():
            owner = journal.players.get(player_id)
            for stock_id in stock_ids:
                actions.append(
                    (
                        "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                        (player_id, stock_id),
                    )
                )
                position = owner.positions.get(stock_id) if owner else None
                if position is None:
                    continue
                actions.append(
                    (
                        "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                        "today_bought, today_bought_day, opened_day) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            player_id,
                            stock_id,
                            position.amount,
                            position.avg_cost,
                            position.today_bought,
                            position.today_bought_day,
                            position.opened_day,
                        ),
                    )
                )

        for trade in trades:
            actions.append(
                (
                    "INSERT INTO trades (player_id, stock_id, side, amount, price, fee, "
                    "tick, day, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trade.player_id,
                        trade.stock_id,
                        trade.side,
                        trade.amount,
                        trade.price,
                        trade.fee,
                        trade.tick,
                        trade.day,
                        trade.reason,
                        now,
                    ),
                )
            )

        # ── P4：当天新生成的新闻（真假与方向一起落库）───────────
        for item in outcome.news:
            actions.append(
                (
                    "INSERT OR REPLACE INTO news_log "
                    "(day, news_id, news_type, stock_id, content, accuracy, direction) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.day,
                        item.id,
                        item.type,
                        item.stock_id,
                        item.content,
                        item.accuracy,
                        item.direction,
                    ),
                )
            )

        for result in outcome.liquidations or journal.liquidations:
            actions.append(
                (
                    "INSERT INTO liquidation_log (player_id, tick, day, phase, trigger, "
                    "stock_id, severity, player_heat_at_harvest, concentration_at_harvest, "
                    "death_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.player.id,
                        market.tick,
                        market.day,
                        result.phase,
                        result.trigger,
                        result.stock_id,
                        result.severity,
                        result.heat_at_harvest,
                        result.concentration,
                        session.player.death_reason,
                        now,
                    ),
                )
            )

        for entry in list(journal.pawnbroker) + list(outcome.pawnbroker):
            actions.append(
                (
                    "INSERT INTO pawnbroker_log (player_id, tick, day, action, stock_id, "
                    "price, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry["player_id"],
                        entry["tick"],
                        entry["day"],
                        entry["action"],
                        entry["stock_id"],
                        entry["price"],
                        entry["reason"],
                        now,
                    ),
                )
            )

        for player in journal.players.values():
            actions.append(
                (
                    "UPDATE players SET cash = ?, day = ?, alive = ?, trade_count = ?, "
                    "heat = ?, total_fee_paid = ?, strikes = ?, pending_action = ?, "
                    "death_reason = ?, death_title = ?, welfare_missed_days = ?, "
                    "loan_taken_day = ?, welfare_claimed_day = ?, "
                    "analyses_day = ?, analyses_used = ?, updated_at = ? WHERE id = ?",
                    (
                        player.cash,
                        player.day,
                        int(player.alive),
                        player.trade_count,
                        player.heat,
                        player.total_fee_paid,
                        player.strikes,
                        json.dumps(player.pending_action, ensure_ascii=False)
                        if player.pending_action
                        else None,
                        player.death_reason,
                        player.death_title,
                        player.welfare_missed_days,
                        player.loan_taken_day,
                        player.welfare_claimed_day,
                        player.analyses_day,
                        player.analyses_used,
                        now,
                        player.id,
                    ),
                )
            )
            actions.append(
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(player_id, tick) DO UPDATE SET heat = excluded.heat, "
                    "day = excluded.day",
                    (player.id, market.tick, market.day, player.heat),
                )
            )

        actions.append(
            (
                "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                "total_fees = ?, reversal_days = ?, updated_at = ? WHERE id = 1",
                (
                    market.tick,
                    market.day,
                    market.phase,
                    market.fee_pool,
                    market.total_fees,
                    json.dumps(sorted(market.reversal_days), ensure_ascii=False),
                    now,
                ),
            )
        )

        await self._repo.transaction(actions)

    # ------------------------------------------------------------------
    # 入口：日终结算（撤销窗口）
    # ------------------------------------------------------------------

    @plugin_entry(
        id="daily",
        name="日终结算",
        description="领工资、查账单、撤销二道贩子的强制卖出，或借高利贷。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["claim", "reject", "status", "loan"],
                    "description": (
                        "claim=领工资（必须主动领，不领不到账）；"
                        "reject=撤销二道贩子强制卖出；status=只看今日账单；"
                        "loan=借高利贷（明天还）"
                    ),
                }
            },
            "required": [],
        },
    )
    @_locked
    async def daily(self, action: str = "claim") -> Ok:
        """日终结算入口。

        P4 起有四个动作：

        * ``claim`` —— **主动领工资**。不领不到账；允许补领（上限 7 天）。
        * ``reject`` —— 撤销二道贩子的强制卖出（**只在撤销窗口内**可用）。
        * ``status`` —— 只看今日账单（任何时候都能看）。
        * ``loan`` —— 借高利贷（任何时候都能借，每天一次）。

        只有 ``reject`` 受撤销窗口限制 —— 领工资和借钱都不该被窗口卡住。
        """
        if action not in {"claim", "reject", "status", "loan"}:
            return _err("invalid_action")

        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        now = time.time()
        player = session.player

        if action == "reject":
            if now >= self._daily_window_until:
                return _err("not_at_daily_boundary")
            return await self._reject_distress_sell(session, engine_config)
        if action == "claim":
            return await self._claim_welfare(session, engine_config)
        if action == "loan":
            return await self._take_loan(session, engine_config)

        pending = player.pending_action or {}
        return Ok(
            {
                "action": "status",
                "day": player.day,
                "cash": round(player.cash, 6),
                "distance_to_death": self._distance_to_death(session, engine_config),
                "strikes": player.strikes,
                "alive": player.alive,
                "pending_action": self._public_pending_action(pending),
                "window_seconds": round(
                    max(0.0, self._daily_window_until - now), 3
                ),
                "welfare_missed_days": player.welfare_missed_days,
                "loan_debt": round(player.loan_debt, 6),
                "pending_relief": round(
                    self._pending_relief(session, engine_config), 6
                ),
            }
        )

    # ------------------------------------------------------------------
    # 入口：日终的三个薄入口（P5 · @ui.action）
    # ------------------------------------------------------------------
    #
    # 这三个入口是给**面板按钮**用的薄壳：各自配独立的 label / tone / confirm，
    # 但业务逻辑**一行都不重写** —— 全部委托给下面那三个私有方法。
    # LLM 侧的 ``daily(action=...)`` 入口保持原样不动。

    @ui.action(
        id="daily_claim",
        label="领工资",
        icon="hand",
        tone="default",
        group="daily",
        order=1,
    )
    @plugin_entry(
        id="daily_claim",
        name="领工资",
        description="主动领取工资。不领不到账；允许补领，最多补 7 天。",
    )
    @_locked
    async def daily_claim(self) -> Ok:
        """领工资（面板按钮的薄入口）。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return await self._claim_welfare(session, engine_config)

    @ui.action(
        id="daily_loan",
        label="借高利贷",
        icon="coin",
        tone="danger",
        group="daily",
        order=2,
        confirm="借五百还六百，明天不还就直接斩。确定要借吗？",
    )
    @plugin_entry(
        id="daily_loan",
        name="借高利贷",
        description="借一笔高利贷：到手 500，次日还 600，还不上直接斩杀。每天限借一次。",
    )
    @_locked
    async def daily_loan(self) -> Ok:
        """借高利贷（面板按钮的薄入口）。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return await self._take_loan(session, engine_config)

    @ui.action(
        id="daily_reject",
        label="拒绝二道贩子",
        icon="close",
        tone="danger",
        group="daily",
        order=3,
        confirm="拒绝之后这次强制卖出就撤销了，账单你自己扛。确定拒绝吗？",
    )
    @plugin_entry(
        id="daily_reject",
        name="拒绝二道贩子",
        description="拒绝二道贩子的强制卖出（只在撤销窗口内可用）。",
    )
    @_locked
    async def daily_reject(self) -> Ok:
        """拒绝二道贩子的强制卖出（面板按钮的薄入口）。

        **"接受"不需要按钮** —— 不调这个入口就是默认接受，强制卖出照常执行
        （P5 契约 §3.4 方案 A：引擎零改动）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        if time.time() >= self._daily_window_until:
            return _err("not_at_daily_boundary")
        return await self._reject_distress_sell(session, engine_config)

    # ------------------------------------------------------------------
    # 入口：跳过本日（P5.5 改动 6 · 第 8 个 @ui.action）
    # ------------------------------------------------------------------
    #
    # **不是** ``@_locked``：它自己拿锁（``self._lock`` 不可重入，嵌套会死锁），
    # 因为它要在锁内连续跑几十个 tick（``_tick_once`` **不**拿锁）。

    @ui.action(
        id="skip_day",
        label="跳过本日",
        icon="clock",
        tone="default",
        group="daily",
        order=4,
    )
    @plugin_entry(
        id="skip_day",
        name="跳过本日",
        description="无限模式专用：立即推进到下一个日终，不等 tick 走完。",
    )
    async def skip_day(self) -> Ok:
        """跳过本日（无限模式专用）。

        **逐 tick 快进**（不是"跳 tick 计数"）—— 复用现有的 tick 流水线一直
        跑到下一个日终边界：

        * heat 是**每 tick 衰减 0.85** 的，直接跳 tick 会漏掉几十次衰减，
          heat 保持原值 → 可能触发 ``peak_threshold`` 的**虚假收割**；
        * 快进之后的状态与"等满一天"**逐位一致** —— 跳过只是"不等"，不是"不算"。

        约束：

        * **只允许无限模式**（经典模式是教学关，不给跳）；
        * **每天只能跳一次**（``players.skip_day_used`` 记当前游戏日）；
        * 跳过时**照常走日终结算**（扣生活费、三振、猫娘日终决策一个不少）；
        * 跳过时**照常发卡片**（日终卡片、收割卡片都在）。
        """
        with self._lock:
            engine_config = await self._engine_config()
            session, error = await self._session_or_error(engine_config)
            if error is not None:
                return error
            if session is None:
                return _err("not_joined")
            if session.mode != "infinite":
                return _err("skip_day_classic_forbidden")
            player = session.player
            if not player.alive:
                return _err("skip_day_dead")
            if int(session.skip_day_used) == int(player.day):
                return _err("skip_day_already_used")
            if self._tick_running:
                return _err("skip_day_busy")

            ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
            current_tick = int(session.market.tick)
            # 下一个日终边界：``market.tick`` 是 ``ticks_per_day`` 的倍数时，
            # 日终结算已经跑过了，所以下一个边界是**再往后一个**。
            target_tick = (current_tick // ticks_per_day + 1) * ticks_per_day
            steps = max(1, target_tick - current_tick)
            skip_day_value = int(player.day)

            self._tick_running = True
            try:
                for _ in range(steps):
                    await self._tick_once()
            except Exception as error:  # noqa: BLE001 - 跳过失败不该炸掉入口
                self.logger.error(f"跳过本日异常（不影响后续 tick）：{error}")
                return _err("skip_day_failed")
            finally:
                self._tick_running = False
                # 重置补 tick 的基线：跳过之后不该立刻又补一堆 tick。
                self._last_tick_at = time.time()

            # 重新读一遍：``_tick_once`` 用的是它自己重建的 Session。
            fresh, fresh_error = await self._session_or_error(engine_config)
            if fresh_error is not None:
                return fresh_error
            # ── P7：猫娘弹幕（跳过本日也算一次嘴臭）─────────────────
            self._push_danmaku(
                pick_taunt(
                    "skip_day",
                    seed=skip_day_value,
                    params={"day": skip_day_value + 1},
                )
            )
            # 记下"今天已经跳过一次"（记的是**发起跳过时**的游戏日 = 规格说的
            # "当前游戏日"；``_commit_tick`` 不会写这一列，所以这里单独写）。
            #
            # 注意：跳过本身会把日子推进一天，所以**下一次**跳过发生在新的一天，
            # 不会撞上这条限制 —— 这条限制是"同一天不许连按"的安全网。
            try:
                await self._repo.transaction(
                    [
                        (
                            "UPDATE players SET skip_day_used = ?, updated_at = ? "
                            "WHERE id = ?",
                            (skip_day_value, time.time(), player.id),
                        )
                    ]
                )
            except (DbUnavailableError, sqlite3.Error) as db_error:
                return _err("db_unavailable", db_error)

            if fresh is None:
                return Ok(
                    {
                        "action": "skip_day",
                        "skipped_to_day": skip_day_value + 1,
                        "ticks_advanced": steps,
                        "note": "这一局已经结束了。",
                    }
                )
            return Ok(
                {
                    "action": "skip_day",
                    "day": int(fresh.player.day),
                    "ticks_advanced": steps,
                    "cash": round(fresh.player.cash, 6),
                    "alive": bool(fresh.player.alive),
                    "death_reason": fresh.player.death_reason,
                    "welfare_missed_days": int(fresh.player.welfare_missed_days),
                    "note": (
                        "这一天过完了。"
                        if fresh.player.alive
                        else "这一天过完了，你也到了头。"
                    ),
                }
            )

    # ------------------------------------------------------------------
    # 入口：导演台（P7 · 直播特制版）
    # ------------------------------------------------------------------
    #
    # **只有一个入口**（而不是四个）：入口列表就是 AI 的工具列表，越短越好。
    # 用 ``op`` 区分指令。
    #
    # **纯净版（默认）下这个入口恒报错** —— 开源版严格遵守原有数学概率，
    # 插件层不做任何后台干预。
    #
    # 四条指令全部**复用引擎既有的函数**，引擎层（``engine/``）一行不改：
    #   * ``crash``   → ``engine.market.apply_jump``（引擎已有的纯函数）
    #   * ``loan``    → ``InfiniteConfig`` 的既有借贷参数
    #   * ``welfare`` → 引擎既有的 ``welfare_neglect`` 死因线
    #   * ``kill``    → ``engine.settlement.apply_death``

    @ui.action(
        id="director",
        label="导演指令",
        icon="chart",
        tone="danger",
        group="director",
        order=90,
    )
    @plugin_entry(
        id="director",
        name="导演指令",
        description=(
            "**直播特制版专用**的导演台指令。开源纯净版下这个入口恒报错。"
            "op=crash 让某只标的暴跌（要 stock_id）/ loan 强制发高利贷"
            "（仅无限模式）/ welfare 强制断供 / kill 立即斩杀。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["crash", "loan", "welfare", "kill", "boom"],
                    "description": "导演指令类型。",
                },
                "stock_id": {
                    "type": "string",
                    "description": "crash 用的标的 id（FAKE-001 这种槽位 id）。",
                },
            },
            "required": ["op"],
        },
    )
    @_locked
    async def director(self, op: str, stock_id: str = "") -> Ok:
        """导演台指令（**纯净版恒报错**）。

        这些干预**只发生在插件层**：暴跌复用引擎既有的 ``apply_jump``，
        借贷复用引擎配置里的既有参数，斩杀复用引擎既有的 ``apply_death``。
        引擎的公式与数值一个都没改。

        暴跌是**排队制**：面板点一下就排一条，**下一个 tick** 施加 ——
        这样暴跌之后集中度、收割等后续反应**照常发生**，而不是硬改结果。
        """
        engine_config = await self._engine_config()
        # 刷新配置缓存再读开关（特制版要能即时打开/关闭）。
        await self._business_config()
        if not self._director_on():
            return _err("director_disabled")
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        if not session.player.alive:
            return _err("director_dead_player")

        if op == "crash":
            stock = session.market.stocks.get(str(stock_id))
            if stock is None:
                return _err("director_stock_not_found")
            self._director_queue.append({"op": "crash", "stock_id": stock.id})
            return Ok(
                {
                    "action": "director",
                    "op": "crash",
                    "stock_id": stock.id,
                    "stock_name": stock.name,
                    "queued": True,
                    "note": _fill_text(
                        i18n_text(
                            "director.note.crash",
                            "安排 {stock} 在下一个 tick 崩给你看。",
                        ),
                        {"stock": stock.name},
                    ),
                }
            )
        if op == "loan":
            if session.mode != "infinite":
                return _err("director_loan_classic_forbidden")
            return await self._director_force_loan(session, engine_config)
        if op == "welfare":
            return await self._director_force_welfare(session, engine_config)
        if op == "kill":
            return await self._director_force_kill(session, engine_config)
        if op == "boom":
            return await self._director_force_boom()
        return _err("director_bad_op")

    async def _director_force_boom(self) -> Ok:
        """导演「赛博弹窗轰炸」（P7.2）。

        纯表现层：在玩家的屏幕上盖一层**关不掉**的小丑海，并在倒计时里
        封锁操作。**不碰任何数值** —— 引擎的公式、结算、收割全部照常。

        参数走 ``config.example.toml`` 的 ``[director]`` 段：
        ``boom_seconds``（默认 5 秒）与 ``boom_emoji_count``（默认 50 个）。
        """
        settings = await self._director_settings()
        seconds = max(1.0, min(30.0, float(settings.get("boom_seconds", 5) or 5)))
        count = max(1, min(200, int(settings.get("boom_emoji_count", 50) or 50)))
        self._director_boom_until = time.time() + seconds
        self._director_boom_count = count
        return Ok(
            {
                "action": "director",
                "op": "boom",
                "count": count,
                "seconds": int(seconds),
                "note": _fill_text(
                    i18n_text(
                        "director.note.boom",
                        "屏幕已经被 {count} 个小丑占领 {seconds} 秒。",
                    ),
                    {"count": count, "seconds": int(seconds)},
                ),
            }
        )

    async def _director_force_loan(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演强制放贷（**仅无限模式**）。

        经典模式引擎里**没有还款路径**（``daily_settlement`` 的还贷只在无限
        模式），在经典模式硬塞一笔就是白送钱 —— 所以直接拒绝。

        金额与"每天限借一次"之外的规则**照常**：到手 500，次日还 600。
        限借一次会被**绕过**（导演要的就是"再逼一次"），但经济账一分不变。
        """
        infinite = engine_config.infinite
        player = session.player
        player.cash += infinite.loan_amount
        player.loan_debt += infinite.loan_repay
        player.loan_taken_day = player.day
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, loan_debt = ?, loan_taken_day = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.loan_debt,
                            player.loan_taken_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        self._push_danmaku(
            pick_taunt(
                "loan",
                seed=player.trade_count,
                params={"amount": infinite.loan_amount, "repay": infinite.loan_repay},
            )
            or i18n_text(
                "taunt.loan.1", "钱到账了。利息也到账了，只是它不会告诉你。"
            )
        )
        return Ok(
            {
                "action": "director",
                "op": "loan",
                "cash": round(player.cash, 6),
                "loan_debt": round(player.loan_debt, 6),
                "note": i18n_text(
                    "director.note.loan",
                    "魔爪契约已生效。明天记得还，忘了就当你签的是遗嘱。",
                ),
            }
        )

    async def _director_force_welfare(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演强制断供（把连续未领天数拉过引擎的斩杀线）。

        用的是引擎**既有的** ``welfare_neglect`` 死因 —— 引擎只管判死，
        怎么走到那条线是插件层的事。
        """
        player = session.player
        limit = int(getattr(engine_config.infinite, "welfare_claim_limit_days", 7))
        player.welfare_missed_days = limit + 1
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET welfare_missed_days = ?, updated_at = ? "
                        "WHERE id = ?",
                        (player.welfare_missed_days, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        return Ok(
            {
                "action": "director",
                "op": "welfare",
                "welfare_missed_days": player.welfare_missed_days,
                "note": i18n_text(
                    "director.note.welfare",
                    "恭喜，你已经连续七天没去领那份不该领的东西了。",
                ),
            }
        )

    async def _director_force_kill(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演立即斩杀（复用引擎既有的 ``apply_death``）。"""
        player = session.player
        apply_death(player, "harvested")
        self._override_death_title(player)
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = 0, death_reason = ?, death_title = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.death_reason, player.death_title, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        return Ok(
            {
                "action": "director",
                "op": "kill",
                "death_reason": player.death_reason,
                "note": i18n_text("director.note.kill", "收工。"),
            }
        )

    @ui.action(
        id="declare_bankrupt",
        label="宣布破产",
        icon="close",
        tone="danger",
        group="daily",
        order=5,
    )
    @plugin_entry(
        id="declare_bankrupt",
        name="宣布破产",
        description=(
            "交不起下一顿饭时的最后一条路：当场结束这一局（死因：交不起账单），"
            "并给出结算报告。经典模式没有高利贷，弹窗上就只剩这一条路。"
        ),
    )
    @_locked
    async def declare_bankrupt(self) -> Ok:
        """宣布破产（面板「赛博网贷广告」弹窗的唯一出路）。

        走的是**引擎既有的** ``apply_death(player, "starved")`` —— 死因文案
        仍然是引擎那一套，引擎层一行不改。归档路径与 ``settle`` 入口**完全
        复用**（结算报告 + 归档 JSON + 清空 ``market_state.player_id``）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("no_active_game")
        player = session.player
        if not player.alive:
            return _err("director_dead_player")

        apply_death(player, "starved")
        self._override_death_title(player)
        payload = await self._build_settlement_payload(session, engine_config)

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = 0, death_reason = ?, death_title = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.death_reason, player.death_title, now, player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        self._archive_settlement(payload, session)
        self._push_danmaku(
            pick_taunt(
                "bankrupt",
                seed=int(player.trade_count),
                params={"days": int(player.day)},
            )
            or i18n_text("taunt.bankrupt.1", "你按了破产。放心，天台的风是免费送你的。")
        )
        return Ok(payload)

    def _roster_news_headline(
        self, item: Any, market: Any = None
    ) -> tuple[str, str | None, str | None]:
        """名册群友的抽象新闻标题。

        返回 ``(标题, 群友别名, 群友称号)``。

        **只换标题**：正文（``item.content``）是引擎给的**方向信号**，
        换掉它就破坏了 P4 §11 的反泄露铁律 —— 所以这里一个字都不动它。

        给了 ``market`` 就按**本局的绑定**认领群友（P7.2：群友按波动率落到
        真实槽位上）；不给就退回名册文件里的默认槽位。
        """
        roster = self._roster_for_stock(getattr(item, "stock_id", None), market)
        if roster is None:
            return _news_headline(item.content), None, None
        alias = str(roster.get("alias") or "")
        title = str(roster.get("title") or "")
        abstract = self._roster_pick_headline(
            roster, seed=sum(ord(ch) for ch in str(item.id))
        )
        headline = f"{alias}：{abstract}" if abstract else _news_headline(item.content)
        return headline, alias, title

    def _apply_director_crashes(
        self, market: MarketState, engine_config: EngineConfig
    ) -> int:
        """把排队的导演暴跌施加到市场上（**下一个 tick 生效**）。

        复用引擎既有的纯函数 ``apply_jump`` —— **引擎一行不改**，而且暴跌
        之后集中度、收割等后续反应**照常发生**。
        """
        if not self._director_on() or not self._director_queue:
            return 0
        crashes = [item for item in self._director_queue if item.get("op") == "crash"]
        if not crashes:
            return 0
        self._director_queue = [
            item for item in self._director_queue if item.get("op") != "crash"
        ]
        raw = self._business_config_cache or {}
        director = raw.get("director")
        director = director if isinstance(director, dict) else {}
        scale = float(director.get("crash_severity_scale", 1.0) or 1.0)
        applied = 0
        for item in crashes:
            stock = market.stocks.get(str(item.get("stock_id")))
            if stock is None or not stock.alive:
                continue
            severity = min(0.9, max(0.05, (0.15 + 0.25 * scale)))
            stock.prev_price = stock.price
            stock.price = apply_jump(stock.price, severity=severity)
            stock.kline.append((market.tick, stock.price))
            window = int(getattr(engine_config.market, "kline_window", 60) or 60)
            if len(stock.kline) > window:
                del stock.kline[: len(stock.kline) - window]
            applied += 1
        return applied

    async def _claim_welfare(self, session: Session, engine_config: EngineConfig) -> Ok:
        """主动领取工资（P4 · P5.5 改成「打工工资」的叙事）。

        工资**必须主动领**：不调用就一分钱没有。允许补领，但补领上限是
        ``welfare_claim_limit_days`` 天，更早的欠款作废。
        """
        player = session.player
        if session.mode != "infinite":
            return Ok(
                {
                    "action": "claim",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "welfare_missed_days": player.welfare_missed_days,
                    "note": "经典模式没有工资——你的对手是市场，不是福利系统。",
                }
            )

        infinite = engine_config.infinite
        claim = claim_welfare(
            player,
            day=player.day,
            weekly_relief=infinite.weekly_relief,
            interval_days=infinite.relief_interval_days,
            claim_limit_days=infinite.welfare_claim_limit_days,
        )
        if claim.already_claimed:
            return _err("welfare_already_claimed")

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, welfare_missed_days = ?, "
                        "welfare_claimed_day = ?, updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.welfare_missed_days,
                            player.welfare_claimed_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7.2：领工资也要挨骂（每一次点击都必有一句嘴臭）─────────
        self._push_danmaku(
            pick_taunt(
                "claim",
                seed=player.trade_count * 31 + int(player.day),
                params={"days": int(player.day)},
            )
            or i18n_text("taunt.claim.1", "领了？领了也是白领。")
        )

        return Ok(
            {
                "action": "claim",
                "granted": round(claim.granted, 6),
                "days_owed": claim.days_owed,
                "cash_after": round(player.cash, 6),
                "welfare_missed_days": player.welfare_missed_days,
                "note": "工资到手。先活下去，再谈别的。",
            }
        )

    async def _take_loan(self, session: Session, engine_config: EngineConfig) -> Ok:
        """借高利贷（P4）：到手 500，次日还 600，还不上直接斩杀。"""
        player = session.player
        if session.mode != "infinite":
            return Ok(
                {
                    "action": "loan",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "note": "经典模式没有高利贷——你已经输在规则上了。",
                }
            )

        infinite = engine_config.infinite
        if player.loan_taken_day == player.day:
            return _err("loan_already_taken")
        if player.loan_debt > 0:
            return _err("loan_pending")

        player.cash += infinite.loan_amount
        player.loan_debt += infinite.loan_repay
        player.loan_taken_day = player.day

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, loan_debt = ?, loan_taken_day = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.loan_debt,
                            player.loan_taken_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7：猫娘弹幕（借了钱也要挨骂）────────────────────────────
        self._push_danmaku(
            pick_taunt(
                "loan",
                seed=player.trade_count,
                params={"amount": infinite.loan_amount, "repay": infinite.loan_repay},
            )
            or i18n_text(
                "taunt.loan.1", "钱到账了。利息也到账了，只是它不会告诉你。"
            )
        )

        return Ok(
            {
                "action": "loan",
                "granted": round(infinite.loan_amount, 6),
                "repay": round(infinite.loan_repay, 6),
                "cash_after": round(player.cash, 6),
                "loan_debt": round(player.loan_debt, 6),
                "due_day": player.day + 1,
                "note": "借五百还六百。这不是帮助，是他们唯一的利润来源。",
            }
        )

    async def _reject_distress_sell(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """撤销二道贩子的强制卖出。

        P4 语义：**撤销不再额外 +1 三振，也不斩杀**。

        ``strikes`` 数的是"连续交不起的次数"，它在**日终结算**时就记上了 ——
        "你确实交不起"是事实，撤销只是反悔，不是没发生过。三振斩杀也由结算
        负责，撤销窗口里反悔一次不会让你立刻死，但明天你依然交不起。
        """
        player = session.player
        market = session.market
        pending = player.pending_action or {}
        legs = pending.get("legs") or []
        if pending.get("kind") != "pawnbroker_sell" or not legs:
            return _err("no_pending_action")

        for leg in legs:
            notional = float(leg.get("notional", 0.0))
            fee = float(leg.get("fee", 0.0))
            player.cash -= notional - fee
            player.trade_count = max(0, player.trade_count - 1)
            player.total_fee_paid = max(0.0, player.total_fee_paid - fee)
            market.fee_pool = max(0.0, market.fee_pool - fee)
            market.total_fees = max(0.0, market.total_fees - fee)
            prev = leg.get("prev_position")
            stock_id = str(leg.get("stock_id", ""))
            if prev:
                player.positions[stock_id] = Position(stock_id=stock_id, **prev)
            else:
                player.positions.pop(stock_id, None)
            if leg.get("trade_id"):
                await self._repo.transaction(
                    [("DELETE FROM trades WHERE id = ?", (int(leg["trade_id"]),))]
                )
            await self._repo.transaction(
                [
                    (
                        "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                        (player.id, stock_id),
                    )
                ]
            )
            position = player.positions.get(stock_id)
            if position is not None:
                await self._repo.transaction(
                    [
                        (
                            "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                            "today_bought, today_bought_day, opened_day) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                player.id,
                                stock_id,
                                position.amount,
                                position.avg_cost,
                                position.today_bought,
                                position.today_bought_day,
                                position.opened_day,
                            ),
                        )
                    ]
                )
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, trade_count = ?, total_fee_paid = ?, "
                        "strikes = ?, pending_action = ?, updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.trade_count,
                            player.total_fee_paid,
                            player.strikes,
                            None,
                            time.time(),
                            player.id,
                        ),
                    ),
                    (
                        "UPDATE market_state SET fee_pool = ?, total_fees = ?, "
                        "updated_at = ? WHERE id = 1",
                        (market.fee_pool, market.total_fees, time.time()),
                    ),
                ]
            )

        player.pending_action = None
        self._daily_window_until = 0.0
        await self._repo.transaction(
            [
                (
                    "UPDATE players SET cash = ?, trade_count = ?, total_fee_paid = ?, "
                    "strikes = ?, alive = ?, pending_action = ?, death_reason = ?, "
                    "death_title = ?, updated_at = ? WHERE id = ?",
                    (
                        player.cash,
                        player.trade_count,
                        player.total_fee_paid,
                        player.strikes,
                        int(player.alive),
                        None,
                        player.death_reason,
                        player.death_title,
                        time.time(),
                        player.id,
                    ),
                )
            ]
        )
        await self._repo.transaction(
            [
                (
                    "INSERT INTO pawnbroker_log (player_id, tick, day, action, stock_id, "
                    "price, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        player.id,
                        market.tick,
                        player.day,
                        "forced_sell",
                        legs[0].get("stock_id", ""),
                        float(legs[0].get("price", 0.0)),
                        "rejected_by_player",
                        time.time(),
                    ),
                )
            ]
        )
        # ── P7.2：拒绝二道贩子也要挨骂（每一次点击都必有一句嘴臭）────
        self._push_danmaku(
            pick_taunt(
                "reject",
                seed=player.trade_count * 17 + int(player.day),
                params={"stock": str(legs[0].get("stock_id", ""))},
            )
            or i18n_text("taunt.reject.1", "硬气。硬气不能当饭吃。")
        )
        return Ok(
            {
                "action": "reject",
                "accepted": False,
                "cash_after": round(player.cash, 6),
                "strikes": player.strikes,
                "alive": player.alive,
                "death_reason": player.death_reason,
                "killed": False,
                "note": "反悔成功。但三振不会因此回退——明天你依然要交钱。",
            }
        )

    @staticmethod
    def _public_pending_action(pending: dict[str, Any]) -> dict[str, Any] | None:
        """把二道贩子提案变成**可以公开**的形态（不含内部账目细节）。"""
        if not pending:
            return None
        legs = pending.get("legs") or []
        return {
            "kind": pending.get("kind", "pawnbroker_sell"),
            "stock_ids": [leg.get("stock_id") for leg in legs],
            "amount": round(sum(float(leg.get("amount", 0.0)) for leg in legs), 6),
            "price": round(
                sum(float(leg.get("amount", 0.0)) * float(leg.get("price", 0.0))
                    for leg in legs),
                6,
            ),
            "reason": "现钱不足以支付明日生活费",
        }

    def _distance_to_death(
        self, session: Session, engine_config: EngineConfig
    ) -> float | None:
        """距离斩杀线（没有生活费的档位为 None）。"""
        next_cost = self._next_living_cost(session.difficulty, engine_config)
        if next_cost <= 0:
            return None
        return round(session.player.cash - next_cost, 6)

    # ------------------------------------------------------------------
    # Hosted UI 上下文
    # ------------------------------------------------------------------

    @ui.context(id="market")
    @_locked
    async def market_context(self, **_):
        """为 ``ui/panel.tsx`` 提供**面板数据**（P5：完整面板）。

        与 ``market`` 入口**分开**：``market`` 是给 LLM 用的轻量白名单；这里给
        的是面板需要的完整白名单（持仓 / 账单 / 猫娘 / K 线 / 新闻分类）。

        **局中隐藏**：载荷不含 heat / seed / 反转日 / 新闻真假。

        局终之后（经典模式第 30 天自动结算）``market_state.player_id`` 被清空，
        此时载荷带上**归档的结算报告**（P4 遗留问题 4：自动终局后玩家看不到
        结算报告）。结算报告里有 heat 曲线与种子 —— 那是**结算页**才允许
        出现的东西，而此刻局已经结束了。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return {"stage": "p2-persistence", "notice": "数据库不可用。"}
        if session is None:
            settlement = self._latest_settlement()
            if settlement is not None:
                return {
                    "stage": "finished",
                    "day": 0,
                    "tick": 0,
                    "ticks_per_day": 0,
                    "ticks_to_settle": 0,
                    "phase": "slaughter",
                    "mode": "",
                    "mode_name": "",
                    "alive": False,
                    "cash": 0.0,
                    "total_assets": 0.0,
                    "living_cost": 0.0,
                    "next_bill": 0.0,
                    "bill_gap": 0.0,
                    "missed_bills": 0,
                    "distance_to_kill": None,
                    "concentration": 0.0,
                    "positions": [],
                    "stocks": [],
                    "kline_stock_ids": [],
                    "catgirl": None,
                    "news_today": [],
                    "pending_action": {},
                    "pending_event": None,
                    "analyses_left": 0,
                    # ── P7：终局页也要有视觉块（Emoji 海 / 降级档位）──
                    "visual": self._visual_block(None, engine_config),
                    "settlement": settlement,
                    # ── P5.5 改动 5/7：称号图鉴（**只在结算页**）────
                    # 局中载荷**不带** achievements —— 图鉴是"打完才看"的东西。
                    "achievements": settlement.get("achievements")
                    if isinstance(settlement, dict)
                    else None,
                    "notice": "本局已经结算归档。下面是这一局的最终成绩。",
                }
            return {
                "stage": "idle",
                "notice": "当前没有进行中的对局，请先加入一局。",
            }
        return self._panel_payload(session, engine_config)

    # ------------------------------------------------------------------
    # 内部工具（以下方法以 _ 开头，不会被宿主收集为入口）
    # ------------------------------------------------------------------

    async def _business_config(self) -> dict[str, Any]:
        """读宿主下发的**业务配置**（``config.example.toml`` 里那些段）。

        P7 的 ``[director]`` / ``[visual]`` 都在这里读。带 5 秒缓存 ——
        和引擎配置**共用同一次 dump**，不额外打扰宿主。
        """
        now = time.time()
        cached = self._business_config_cache
        if cached is not None and now - self._business_config_cached_at < _ENGINE_CONFIG_TTL:
            return cached
        try:
            raw = await self.config.dump()
        except Exception:  # noqa: BLE001 - 配置不可用时退回空配置
            raw = {}
        self._apply_runtime_config(raw)
        self._business_config_cache = raw if isinstance(raw, dict) else {}
        self._business_config_cached_at = now
        # 导演模式的开关顺手缓存一份（面板载荷是同步的，要能直接读）。
        director = self._business_config_cache.get("director")
        self._director_enabled_cache = bool(
            director.get("enabled", False) if isinstance(director, dict) else False
        )
        return self._business_config_cache

    async def _engine_config(self) -> EngineConfig:
        """读取宿主配置并装配成引擎配置。

        带 5 秒缓存：1 秒心跳每次都去读宿主配置太浪费了，而配置改动
        5 秒内生效对游戏节奏完全无感。

        **顺带同步运行时能力**：读到配置的同时把运行时能力（``db.enabled`` /
        ``state.backend``）与宿主对齐 —— 宿主补发配置后，心跳不用等宿主再推
        一次 ``CONFIG_UPDATE``（0.7.4 真机复盘：心跳不推进的根因）。
        """
        now = time.time()
        cached = self._engine_config_cache
        if cached is not None and now - self._engine_config_cached_at < _ENGINE_CONFIG_TTL:
            return cached
        raw = await self._business_config()
        config = build_engine_config(raw)
        self._engine_config_cache = config
        self._engine_config_cached_at = now
        return config

    # ------------------------------------------------------------------
    # P7：社区名册 / 导演模式 / 猫娘弹幕 / 视觉降级
    #
    # 这一整节都是**表现层**：
    #   * 社区名册 = 群友人设与抽象新闻（纯数据，来自 community_roster.json）；
    #   * 导演模式 = 直播特制版的开关，**默认关**；
    #   * 猫娘弹幕 = 局中的嘲讽，取代发不出去的卡片；
    #   * 视觉降级 = 三档 UI 的档位判定。
    # 这里算出来的任何东西都**不参与游戏规则**，也不改引擎数值。
    # ------------------------------------------------------------------

    def _roster_members(self) -> list[dict[str, Any]]:
        """社区名册（群友人设 + 抽象新闻池）。

        从 ``community_roster.json`` 读；**读不到就退化成空名册** ——
        插件照常跑，只是没有彩蛋。名册是**数据**：加群友只改那个文件。
        """
        if self._roster_loaded:
            return self._roster
        self._roster_loaded = True
        try:
            path = Path(__file__).resolve().parent / "community_roster.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover - 名册缺失不影响主流程
            self._roster = []
            return self._roster
        members = data.get("members") if isinstance(data, dict) else None
        if isinstance(members, list):
            self._roster = [item for item in members if isinstance(item, dict)]
        return self._roster

    def _roster_binding(self, market: Any) -> dict[str, dict[str, Any]]:
        """把名册群友绑到本局的**真实槽位**上（**数值零改动**）。

        引擎按种子给每个槽位算了一个 ``sigma``（波动率），而且**整局不变**
        （``advance_market`` 只读它、不改它）。所以可以按波动率排名来绑：

        * 带 ``stable`` / ``anchor`` 标签的群友（星辰）→ 绑到波动率**最低**的槽位；
        * 带 ``explosive`` / ``chaotic`` 标签的群友（Wehos）→ 绑到波动率**最高**的槽位；
        * 其余群友（老王 / 小明 / 熬夜怪 / 潜水员）→ 按波动率升序依次取剩下的槽位。

        于是「星辰走势平稳如老狗」「Wehos 极高波动」**永远成立**，而引擎的
        sigma、价格、结算公式**一个字节都没动**（P7.2 硬要求：数值零改动，
        500 局基线必须逐位一致）。绑定只影响**显示名**与群友新闻。
        """
        members = self._roster_members()
        if not members:
            return {}
        stocks = getattr(market, "stocks", None)
        if not stocks:
            return {}
        ordered = sorted(
            stocks.values(),
            key=lambda stock: (
                float(getattr(stock, "sigma", 0.0) or 0.0),
                str(getattr(stock, "id", "")),
            ),
        )
        slot_ids = [str(getattr(stock, "id", "")) for stock in ordered]
        binding: dict[str, dict[str, Any]] = {}
        used: set[str] = set()

        def _take(candidates: list[str]) -> str | None:
            for slot in candidates:
                if slot not in used:
                    used.add(slot)
                    return slot
            return None

        # 1) 必须最稳的 → 波动率升序
        for member in members:
            tags = {str(tag) for tag in (member.get("tags") or [])}
            if not tags & {"stable", "anchor"}:
                continue
            slot = _take(slot_ids)
            if slot is None:
                break
            binding[slot] = member
        # 2) 必须最野的 → 波动率降序
        for member in members:
            tags = {str(tag) for tag in (member.get("tags") or [])}
            if not tags & {"explosive", "chaotic"}:
                continue
            slot = _take(list(reversed(slot_ids)))
            if slot is None:
                break
            binding[slot] = member
        # 3) 其余的 → 剩下的槽位按波动率升序
        for member in members:
            if any(bound is member for bound in binding.values()):
                continue
            slot = _take(slot_ids)
            if slot is None:
                break
            binding[slot] = member
        return binding

    def _roster_for_stock(
        self, stock_id: str | None, market: Any = None
    ) -> dict[str, Any] | None:
        """某个标的是不是名册里的群友（是的话返回名册条目）。

        给了 ``market`` 就用**本局的绑定**（群友按波动率落到真实槽位上）；
        不给就退回名册文件里的**默认槽位**（纯数据口径，测试也走这条）。
        """
        if not stock_id:
            return None
        if market is not None:
            bound = self._roster_binding(market).get(str(stock_id))
            if bound is not None:
                return bound
        for member in self._roster_members():
            if member.get("stock_id") == stock_id:
                return member
        return None

    @staticmethod
    def _roster_display_name(member: dict[str, Any]) -> str:
        """群友在**面板上**显示的名字（如「星辰·定海神针股」）。"""
        alias = str(member.get("alias") or "")
        title = str(member.get("title") or "")
        if alias and title:
            return f"{alias}·{title}"
        return alias or title

    def _roster_pick_headline(self, member: dict[str, Any], seed: int) -> str:
        """按 seed **稳定**地给群友挑一句抽象新闻（可复现）。"""
        headlines = member.get("headlines")
        if not isinstance(headlines, list) or not headlines:
            return ""
        ticker = str(member.get("ticker") or member.get("alias") or "")
        index = seed % len(headlines)
        text = str(headlines[index])
        if ticker and ticker not in text:
            text = f"{ticker}{text}"
        return text

    async def _director_settings(self) -> dict[str, Any]:
        """导演模式的开关与参数（**默认全关**）。"""
        raw = await self._business_config()
        section = raw.get("director")
        return section if isinstance(section, dict) else {}

    def _director_on(self) -> bool:
        """导演模式是否开启（同步读缓存）。

        **默认 False** —— 开源纯净版严格遵守原有数学概率，插件层不做任何
        后台干预。开启后只有面板的「导演台」能用，且指令**只改观感与剧情**，
        不改引擎公式。
        """
        return bool(self._director_enabled_cache)

    def _visual_settings(self) -> dict[str, Any]:
        """视觉降级的表现层参数（阈值来自 ``config.example.toml``）。"""
        raw = self._business_config_cache or {}
        section = raw.get("visual")
        return section if isinstance(section, dict) else {}

    def _push_danmaku(self, text: str, kind: str = "cat") -> None:
        """记一条猫娘弹幕（面板的「赛博弹幕悬浮窗」用）。

        运行时没有卡片 API ⇒ 猫娘在局中的嘲讽改走面板弹幕（外加宿主的
        ``showToast`` 兜底）。弹幕**只在内存里**：它是"此刻"的装饰。
        """
        if not text:
            return
        self._danmaku_seq += 1
        self._danmaku.append(
            {
                "id": self._danmaku_seq,
                "kind": kind,
                "text": text,
                "at": time.time(),
            }
        )
        if len(self._danmaku) > _DANMAKU_MAX:
            del self._danmaku[: len(self._danmaku) - _DANMAKU_MAX]

    def _visual_block(
        self, session: Session | None, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """面板载荷的 ``visual`` 块：三档档位 + 弹幕 + 导演台开关 + 名册。

        **纯表现层**：这里判定的东西不参与任何游戏规则。
        """
        settings = self._visual_settings()
        broke_day = int(settings.get("broke_day_threshold", 3) or 3)
        dying_assets = float(settings.get("dying_assets_threshold", 20.0) or 20.0)
        panic_distance = float(settings.get("panic_distance", 40.0) or 40.0)
        emoji_sea_size = max(50, int(settings.get("emoji_sea_size", 60) or 60))

        player = session.player if session is not None else None
        market = session.market if session is not None else None
        day = int(player.day) if player is not None else 0
        cash = float(player.cash) if player is not None else 0.0
        alive = bool(player.alive) if player is not None else True
        mode = session.mode if session is not None else ""
        total_assets = cash
        distance: float | None = None
        if player is not None and market is not None:
            prices = market.prices()
            # 用引擎自己的口径算总资产（不另起一套算法）。
            total_assets = player.total_asset(prices)
            distance = self._distance_to_death(session, engine_config)

        # 病危：现金为负、或者离斩杀只差一步、或者已经死了。
        critical = bool(
            cash < 0
            or (distance is not None and distance <= panic_distance)
            or not alive
        )

        # 三档：濒死 > 断供 > 富二代。
        stage = "rich"
        if mode == "infinite" or critical or total_assets <= dying_assets:
            stage = "dying"
        elif day >= broke_day:
            stage = "broke"

        # 名册里"这一局在场"的群友（P7.2：按**波动率排名**绑定到真实槽位）。
        roster: list[dict[str, Any]] = []
        if market is not None:
            for slot_id, member in self._roster_binding(market).items():
                stock = market.stocks.get(slot_id)
                if stock is None:
                    continue
                roster.append(
                    {
                        "stock_id": stock.id,
                        "alias": str(member.get("alias") or ""),
                        "ticker": str(member.get("ticker") or ""),
                        "title": str(member.get("title") or ""),
                        "persona": str(member.get("persona") or ""),
                        "price": round(float(stock.price), 4),
                        "display_name": self._roster_display_name(member),
                    }
                )

        # 导演台的「赛博弹窗轰炸」：还没到点就是 inactive。
        boom_left = max(0.0, float(self._director_boom_until) - time.time())
        boom_active = bool(self._director_on() and boom_left > 0)

        return {
            "stage": stage,
            "critical": critical,
            "critical_emoji": "💀" if not alive else "🩸",
            "stage_thresholds": {
                "broke_day": broke_day,
                "dying_assets": dying_assets,
                "panic_distance": panic_distance,
            },
            "emoji_sea_size": emoji_sea_size,
            "danmaku": list(self._danmaku[-_DANMAKU_PAYLOAD_MAX:]),
            "director": {
                "enabled": self._director_on(),
                "queued": len(self._director_queue),
            },
            "boom": {
                "active": boom_active,
                "count": int(self._director_boom_count) if boom_active else 0,
                "seconds_left": int(boom_left) + 1 if boom_active else 0,
                "until": round(float(self._director_boom_until), 3),
            },
            "roster": roster,
        }

    async def _session_or_error(self, engine_config: EngineConfig):
        """加载当前会话；数据库不可用时返回错误结果。"""
        try:
            return await load_session(
                self._repo, self.state, engine_config=engine_config
            ), None
        except (DbUnavailableError, sqlite3.Error) as error:
            # 宿主补发有效配置后重试一次（懒建表自愈也在这里生效）。
            await self._refresh_runtime_capabilities()
            try:
                return await load_session(
                    self._repo, self.state, engine_config=engine_config
                ), None
            except (DbUnavailableError, sqlite3.Error):
                return None, _err("db_unavailable", error)

    async def _harvest_events(self, player_id: str) -> list[dict[str, Any]]:
        """收割时间线（**只有结算页能拿到**，含收割时刻的 heat 数值）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT tick, day, phase, trigger, stock_id, severity, "
                "player_heat_at_harvest, concentration_at_harvest, death_reason "
                "FROM liquidation_log WHERE player_id = ? ORDER BY tick ASC",
                (player_id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return []
        return [
            {
                "tick": int(row["tick"]),
                "day": int(row["day"]),
                "phase": row["phase"],
                "trigger": row["trigger"],
                "stock_id": row["stock_id"],
                "severity": round(float(row["severity"]), 6),
                "heat_at_harvest": round(float(row["player_heat_at_harvest"]), 4),
                "concentration": round(float(row["concentration_at_harvest"]), 6),
                "death_reason": row["death_reason"],
            }
            for row in rows
        ]

    async def _catgirl_result(self, session: Session) -> dict[str, Any] | None:
        """猫娘的结局（本局没有猫娘时返回 ``None``）。"""
        catgirl = session.catgirl
        if catgirl is None:
            return None
        try:
            row = await self._repo.fetch_one(
                "SELECT alive, day, cash, trade_count, death_reason FROM players WHERE id = ?",
                (catgirl.id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            row = None
        alive = bool(row["alive"]) if row is not None else catgirl.alive
        return {
            "alive": alive,
            "days_survived": int(row["day"]) if row is not None else catgirl.day,
            "final_cash": round(float(row["cash"]), 6) if row is not None else round(catgirl.cash, 6),
            "trade_count": int(row["trade_count"]) if row is not None else catgirl.trade_count,
            "death_reason": row["death_reason"] if row is not None else catgirl.death_reason,
        }

    def _initial_cash(self, mode: str, seed: int, engine_config: EngineConfig) -> float:
        """玩家的初始**现金**。

        * 经典模式：按**本档**的生活费 × 初始天数（入门/进阶 10×10 = 100，
          地狱 12×7 = 84，街机沿用旧口径 15~20×7）。
        * 无限模式（P4）：**0**。初始资金以"基准股票"的形式发放（市值 ≈ 1050），
          玩家必须先卖掉它才交得上第一天的生活费 —— 这正是"现钱唯一，股票市值
          不算数"的设计。
        """
        if mode == "infinite":
            return 0.0
        difficulty = engine_config.difficulties[mode]
        low, high = classic_living_cost_range(difficulty, engine_config)
        days = classic_initial_days(difficulty, engine_config)
        base = derive_rng(seed, 999).uniform(low, high)
        return round(base * days, 2)

    @staticmethod
    def _catgirl_initial_cash(engine_config: EngineConfig) -> float:
        """猫娘的初始现金（无限模式常驻）。

        猫娘是 NPC 操盘手，她有本金；玩家开局是 0 现金 + 基准股票。这是**开局
        设定**的差异，不是规则豁免：她照样交生活费、照样会被收割、照样用同一
        套 heat 与日终结算。
        """
        infinite = engine_config.infinite
        return infinite.initial_days * infinite.daily_living_cost

    def _next_living_cost(self, difficulty, engine_config: EngineConfig) -> float:
        """次日生活费；没有生活费的档位返回 0。"""
        return next_living_cost(difficulty, engine_config)

    @staticmethod
    def _sellable_amount(position, day: int, difficulty) -> float:
        """当前可卖出的数量。

        契约 §2.2：``sellable`` = **考虑 T+1 后**的可卖数量。没有 T+1 的档位
        （街机 / 入门 / 无限）不受当日买入锁定，全部持仓都可卖。
        """
        if not difficulty.t_plus_one:
            return position.amount
        return position.sellable_amount(day)

    @staticmethod
    def _analysis_target(
        session: Session, stock_id: str, prices: dict[str, float]
    ) -> Any:
        """要分析哪只标的：指定的优先 → 持仓最重的那只 → 第一只还活着的。"""
        market = session.market
        if stock_id:
            return market.stocks.get(stock_id)
        player = session.player
        best = None
        best_value = -1.0
        for position_id, position in player.positions.items():
            value = position.market_value(prices.get(position_id, 0.0))
            if value > best_value:
                best_value = value
                best = market.stocks.get(position_id)
        if best is not None:
            return best
        for stock in market.stocks.values():
            if stock.alive:
                return stock
        return None

    def _market_payload(self, session: Session) -> dict[str, Any]:
        """市场数据的**白名单**返回体（严禁出现 heat / seed / 反转日）。"""
        market = session.market
        stocks: list[dict[str, Any]] = []
        for stock in market.stocks.values():
            change = (
                (stock.price - stock.prev_price) / stock.prev_price
                if stock.prev_price
                else 0.0
            )
            stocks.append(
                {
                    "id": stock.id,
                    "name": stock.name,
                    "price": round(stock.price, 6),
                    "prev_price": round(stock.prev_price, 6),
                    "change": round(change, 6),
                    "tag": stock.tag,
                    "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                    "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                    "alive": stock.alive,
                }
            )
        return {
            "day": market.day,
            "tick": market.tick,
            "phase": market.phase,
            "stocks": stocks,
            # ── P5 方案 B：**不再返回** ``reversal_today`` ──────────────
            # P4 §11 原本在这里给"今天是不是反转日"（只说是不是、不说方向）。
            # P5 复核认为它仍是**强剧透**：玩家知道今天特殊，就能把新闻反过来读，
            # 反诈教学的核心机制被绕过。现在改成"**花一次分析机会**换一句
            # 不带方向的模糊提示"（见 ``analyze`` 入口）。
            # 守卫的 ``reversal_today`` 白名单例外随之失效（§20.2 第 6 条）。
        }

    # ------------------------------------------------------------------
    # Hosted TSX 面板载荷（P5）
    # ------------------------------------------------------------------

    def _panel_payload(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """``@ui.context`` 的**白名单**载荷（P5 面板用）。

        与 ``market`` 入口**分开**：``market`` 是给 LLM 用的，必须轻量；面板要
        的东西多得多（持仓、账单、猫娘、K 线、结算报告）。

        **局中隐藏**：不含 heat / seed / 反转日 / 新闻真假 / 猫娘的 heat。

        **K 线限流（细节 2）**：K 线只给 ``kline_stock_ids`` 列出的那些标的
        （持仓 + 爆炸标签，最多 ``PANEL_KLINE_MAX`` 只），其余标的只给
        price / tag / alive —— 否则载荷会爆（60 点 × 全部标的）。
        """
        market = session.market
        player = session.player
        difficulty = session.difficulty
        prices = market.prices()
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        total_assets = player.total_asset(prices)

        kline_ids = self._panel_kline_ids(session, prices)
        kline_set = set(kline_ids)

        positions: list[dict[str, Any]] = []
        roster_binding = self._roster_binding(market)
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            price = prices.get(stock_id, 0.0)
            market_value = position.market_value(price)
            tag = stock.tag if stock is not None else "neutral"
            member = roster_binding.get(stock_id)
            positions.append(
                {
                    "stock_id": stock_id,
                    "name": stock.name if stock is not None else stock_id,
                    "display_name": (
                        self._roster_display_name(member)
                        if member is not None
                        else (stock.name if stock is not None else stock_id)
                    ),
                    "amount": round(position.amount, 6),
                    "avg_cost": round(position.avg_cost, 6),
                    "price": round(price, 6),
                    "market_value": round(market_value, 6),
                    "unrealized_pnl": round(position.unrealized_pnl(price), 6),
                    "sellable": round(
                        self._sellable_amount(position, player.day, difficulty), 6
                    ),
                    "weight": round(market_value / total_assets, 6)
                    if total_assets > 0
                    else 0.0,
                    "tag": tag,
                    "tag_label": _TAG_LABELS.get(tag, "未知"),
                    "alive": bool(stock.alive) if stock is not None else False,
                }
            )

        stocks: list[dict[str, Any]] = []
        for stock in market.stocks.values():
            change = (
                (stock.price - stock.prev_price) / stock.prev_price
                if stock.prev_price
                else 0.0
            )
            member = roster_binding.get(stock.id)
            entry: dict[str, Any] = {
                "id": stock.id,
                "name": stock.name,
                "display_name": (
                    self._roster_display_name(member)
                    if member is not None
                    else stock.name
                ),
                "roster_alias": str(member.get("alias") or "") if member else "",
                "roster_title": str(member.get("title") or "") if member else "",
                "price": round(stock.price, 6),
                "prev_price": round(stock.prev_price, 6),
                "change": round(change, 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                "alive": stock.alive,
                # P7.2：走势小折线的数据（**降采样**过，别把载荷撑爆）。
                "spark": [
                    [int(tick), round(float(value), 4)]
                    for tick, value in stock.kline[-_PANEL_SPARK_POINTS:]
                ],
            }
            if stock.id in kline_set:
                entry["kline"] = [
                    [int(tick), round(float(value), 4)] for tick, value in stock.kline
                ]
            stocks.append(entry)

        next_cost = self._next_living_cost(difficulty, engine_config)
        living_cost = daily_living_cost(
            player.mode, player.day, market.seed, engine_config
        )

        return {
            "stage": "playing",
            "day": player.day,
            "tick": market.tick,
            "ticks_per_day": ticks_per_day,
            "ticks_to_settle": ticks_per_day - (market.tick % ticks_per_day),
            "phase": market.phase,
            "mode": player.mode,
            "mode_name": _MODE_NAMES.get(player.mode, player.mode),
            "alive": player.alive,
            # ── 财务 ──────────────────────────────────────────────────
            "cash": round(player.cash, 6),
            "total_assets": round(total_assets, 6),
            # P7.2：玩家自己的开局钱（**不是**隐藏信号，join 早就返回了），
            # 面板拿它算「到现在为止赚/亏了多少」——傻子版的第一屏大数字。
            "initial_cash": round(float(player.initial_cash), 6),
            "living_cost": round(living_cost, 6),
            "next_bill": round(next_cost, 6),
            "bill_gap": round(max(0.0, next_cost - player.cash), 6),
            "missed_bills": int(player.strikes),
            "distance_to_kill": self._distance_to_death(session, engine_config),
            "concentration": round(position_concentration(player.positions, prices), 6),
            "positions": positions,
            "stocks": stocks,
            "kline_stock_ids": kline_ids,
            # ── 猫娘公开状态 ───────────────────────────────────────────
            "catgirl": self._panel_catgirl(session, prices, ticks_per_day),
            # ── 当天新闻：局中只有中性分类（标题正文要点开才给）──────
            "news_today": self._public_news(market),
            # ── 二道贩子提案（公开部分） ───────────────────────────────
            "pending_action": self._public_pending_action(player.pending_action or {}),
            "pending_event": self._pending_event,
            "analyses_left": self._analyses_left(player),
            # ── P7：视觉降级档位 / 猫娘弹幕 / 导演台开关 / 社区名册 ────
            # **纯表现层**：不含任何隐藏信息（heat / seed / 反转日 /
            # 新闻真假一概没有）。
            "visual": self._visual_block(session, engine_config),
            "settlement": None,
            "notice": "",
        }

    @staticmethod
    def _panel_kline_ids(session: Session, prices: dict[str, float]) -> list[str]:
        """哪些标的带 K 线（细节 2：限流，别让载荷爆掉）。

        顺序：持仓（按市值从大到小）→ 爆炸标签的标的（按波动率从高到低）。
        总数上限 ``PANEL_KLINE_MAX``。
        """
        market = session.market
        player = session.player
        picked: list[str] = []
        seen: set[str] = set()

        holdings = sorted(
            player.positions.items(),
            key=lambda item: item[1].market_value(prices.get(item[0], 0.0)),
            reverse=True,
        )
        for stock_id, _position in holdings:
            if stock_id not in seen:
                seen.add(stock_id)
                picked.append(stock_id)
            if len(picked) >= PANEL_KLINE_MAX:
                return picked

        explosive = [
            stock
            for stock in market.stocks.values()
            if stock.alive and stock.tag == "explosive" and stock.id not in seen
        ]
        explosive.sort(key=lambda stock: stock.sigma, reverse=True)
        for stock in explosive:
            if len(picked) >= PANEL_KLINE_MAX:
                break
            seen.add(stock.id)
            picked.append(stock.id)
        return picked

    def _panel_catgirl(
        self, session: Session, prices: dict[str, float], ticks_per_day: int
    ) -> dict[str, Any] | None:
        """猫娘的**公开状态**（面板用）。

        只有持仓 / 现金 / 存活天数 / 今天动没动手 —— **不给她的 heat，
        也不给她买卖了哪只**（P5 契约 §2.4）。
        """
        catgirl = session.catgirl
        if catgirl is None:
            return None
        daily = self._catgirl_daily or {}
        trades = int(daily.get("trades", 0)) if daily else 0
        previous = self._catgirl_trades_seen
        if previous is None:
            action = "—"
        elif trades > previous:
            action = f"交易了 {trades - previous} 笔"
        else:
            action = "观望"
        return {
            "alive": bool(catgirl.alive),
            "cash": round(catgirl.cash, 6),
            "positions_value": round(catgirl.position_value(prices), 6),
            "position_count": len(catgirl.positions),
            "days_survived": session.market.tick // max(1, ticks_per_day),
            "today_action": action,
        }

    @staticmethod
    def _analyses_left(player: Player) -> int:
        """今天还剩几次分析机会。"""
        used = player.analyses_used if player.analyses_day == player.day else 0
        return max(0, ANALYSES_PER_DAY - used)

    def _latest_settlement(self) -> dict[str, Any] | None:
        """最近一次归档的结算报告（面板用；P4 遗留问题 4）。

        经典模式第 30 天**自动结算**之后 ``market_state.player_id`` 被清空，
        ``settle`` 入口也只会回 ``no_active_game`` —— 玩家就再也看不到结算报告
        了。这里把归档 JSON 读回来接到面板上。
        """
        player_id = self._last_player_id
        if not player_id:
            return None
        try:
            folder = self.data_path("settlements")
            candidates = [
                path
                for path in folder.glob(f"settlement-{player_id}-*.json")
                if path.is_file()
            ]
            if not candidates:
                return None
            newest = max(candidates, key=lambda path: path.stat().st_mtime)
            payload = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # 结算报告含 heat 曲线与种子 —— 那是结算页才允许出现的东西。
        # 面板只在**局终之后**展示它，所以这里整体透传是安全的。
        return payload

    async def _heat_context(
        self,
        session: Session,
        stock,
        amount: float,
        engine_config: EngineConfig,
    ) -> HeatEventContext:
        """构造一次买入对 heat 的影响（哪些行为让你上头）。"""
        heat_config = engine_config.heat
        player = session.player

        position = player.positions.get(stock.id)
        is_loss = position is not None and stock.price < position.avg_cost

        recent = [value for _, value in stock.kline[-heat_config.chase_window :]]
        is_chase = bool(recent) and stock.price >= max(recent) * heat_config.chase_ratio

        rows = await self._repo.fetch_all(
            "SELECT amount FROM trades WHERE player_id = ? AND side = 'buy' "
            "ORDER BY id DESC LIMIT ?",
            (player.id, heat_config.high_freq_window),
        )
        amounts = [float(row["amount"]) for row in rows]
        average = sum(amounts) / len(amounts) if amounts else 0.0
        high_frequency = bool(average) and amount > average * heat_config.high_freq_ratio

        next_cost = self._next_living_cost(session.difficulty, engine_config)
        notional = stock.price * amount
        fee = buy_fee(notional, engine_config.fees)
        cash_after = player.cash - notional - fee
        living_cost_averaging = bool(next_cost) and cash_after < next_cost

        # ── P4：新闻 heat 事件 ───────────────────────────────────
        # 「信了假新闻追涨」：最近 N 个 tick 内该股票有**看涨**的假新闻，玩家
        # 在这个时候买入 = 信了。
        # 「雷新闻后补仓」：最近 N 个 tick 内该股票有**雷新闻**，而玩家正在
        # 浮亏中买入 = 补仓。
        after_fake_news, after_thunder_news = self._news_heat_context(
            session, stock, engine_config
        )

        return HeatEventContext(
            is_loss_position=is_loss,
            is_leverage=session.difficulty.leverage > 1.0,
            is_chase_high=is_chase,
            high_frequency=high_frequency,
            after_fake_news=after_fake_news,
            after_thunder_news=after_thunder_news,
            living_cost_averaging=living_cost_averaging,
        )

    def _news_heat_context(
        self, session: Session, stock, engine_config: EngineConfig
    ) -> tuple[bool, bool]:
        """判定这次买入是否"信了假新闻"或"雷新闻后补仓"。

        时间窗：新闻在当天第一个 tick 生成，只有之后 ``news_window_ticks``
        个 tick 内买入才算"信了"（经典模式 1/4 天、无限模式 1/16 天）。
        """
        market = session.market
        window = max(0, int(engine_config.news.news_window_ticks))
        if window <= 0:
            return False, False

        ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
        today = market.day
        news_tick = (today - 1) * ticks_per_day
        if market.tick - news_tick >= window:
            # 已经超出时间窗：今天的新闻不再影响 heat
            return False, False

        after_fake_news = False
        after_thunder_news = False
        for item in market.news_today:
            if item.day != today or item.stock_id != stock.id:
                continue
            if item.type == "fake" and item.direction == "bullish":
                after_fake_news = True
            if item.type == "thunder":
                after_thunder_news = True
        return after_fake_news, after_thunder_news

    async def _persist_tick(self, session: Session) -> None:
        """落盘一个 tick 的结果（heat 曲线 + 市场状态 + 玩家 heat）。"""
        player = session.player
        market = session.market
        now = time.time()
        await self._repo.transaction(
            [
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(player_id, tick) DO UPDATE SET heat = excluded.heat, "
                    "day = excluded.day",
                    (player.id, market.tick, market.day, player.heat),
                ),
                (
                    "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                    "total_fees = ?, updated_at = ? WHERE id = 1",
                    (
                        market.tick,
                        market.day,
                        market.phase,
                        market.fee_pool,
                        market.total_fees,
                        now,
                    ),
                ),
                (
                    "UPDATE players SET heat = ?, day = ?, updated_at = ? WHERE id = ?",
                    (player.heat, player.day, now, player.id),
                ),
            ]
        )
        await save_snapshot(self.state, market, player.id)

    async def _persist_trade(self, session: Session, trade, stock_id: str) -> int:
        """落盘一笔成交（玩家 + 持仓 + 成交流水 + 市场状态），返回成交 id。"""
        player = session.player
        market = session.market
        now = time.time()

        actions: list[tuple[str, tuple[Any, ...]]] = [
            (
                "UPDATE players SET cash = ?, day = ?, alive = ?, trade_count = ?, heat = ?, "
                "total_fee_paid = ?, updated_at = ? WHERE id = ?",
                (
                    player.cash,
                    player.day,
                    int(player.alive),
                    player.trade_count,
                    player.heat,
                    player.total_fee_paid,
                    now,
                    player.id,
                ),
            ),
            (
                "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                (player.id, stock_id),
            ),
        ]
        position = player.positions.get(stock_id)
        if position is not None:
            actions.append(
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        player.id,
                        stock_id,
                        position.amount,
                        position.avg_cost,
                        position.today_bought,
                        position.today_bought_day,
                        position.opened_day,
                    ),
                )
            )
        actions.append(
            (
                "INSERT INTO trades (player_id, stock_id, side, amount, price, fee, tick, day, "
                "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    player.id,
                    stock_id,
                    trade.side,
                    trade.amount,
                    trade.price,
                    trade.fee,
                    trade.tick,
                    trade.day,
                    trade.reason,
                    now,
                ),
            )
        )
        trade_index = len(actions) - 1
        actions.append(
            (
                "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                "total_fees = ?, updated_at = ? WHERE id = 1",
                (
                    market.tick,
                    market.day,
                    market.phase,
                    market.fee_pool,
                    market.total_fees,
                    now,
                ),
            )
        )
        cursors = await self._repo.transaction(actions)
        await save_snapshot(self.state, market, player.id)
        return int(cursors[trade_index].lastrowid or 0)

    def _not_ready(self) -> Err:
        """引擎尚未接入时的统一错误出口（P2 起不再触发）。"""
        return _err("engine_not_ready")
