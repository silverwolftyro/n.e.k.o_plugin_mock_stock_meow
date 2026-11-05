#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P27.2：**模拟直播**自动化压测脚本（默认 30 局）。

为什么还要这个
--------------
单元测试只能证明「某个函数对」。直播事故恰恰长在**一整局的连续点击流**上：
死锁、心跳卡死、面板载荷爆掉、内存一路涨 —— 这些**不是**单点问题，
单测一个都测不出来。

这个脚本模拟**真实用户的点击流**，连续玩 N 局（覆盖经典四档 + 无尽档），
每局自动点【推进一拍】和【一键梭哈】，全程盯六件事：

1. **死锁** —— 任何一次调用超过 ``--call-timeout`` 秒 ⇒ 判定为疑似死锁；
2. **心跳卡死** —— ``_heartbeat_timeouts`` / ``_tick_timeouts`` 的增量；
3. **UI 渲染异常** —— 面板载荷必须能被宿主 JSON 序列化（**不许 NaN**，
   NaN 会让宿主的 JSON 直接炸 ⇒ 面板白屏）、体积不许超过 512 KiB、
   关键字段不许缺；
4. **内存（OOM）** —— ``tracemalloc`` 逐局峰值 + 跨局增长趋势 + ``MemoryError``；
5. **追单（防自嗨）** —— Yui 成交一单之后**立刻**再下一单，必须被
   **回执周期锁**挡掉，而且**一条消息都不许推**（推了就会再触发一个
   AI 回合 ⇒ 她在那个回合里又追一单 ⇒ 无限循环）；
6. **live 日配额** —— Yui 一天最多 ``YUI_TRADES_PER_DAY`` 单，第 N+1 单
   必须被配额闸门挡掉。

中途触发破产 ⇒ **自动重置**（结算并清场）继续压。

跑法::

    python tests/stress_live.py --games 30
    python tests/stress_live.py --games 30 --ticks 60 --out D:\\tmp\\stress

产出（默认落在仓库的 ``docs/``）::

    P27.2-压测报告.md
    P27.2-压测报告.json

关于"模拟的点击流"
------------------
这是**照着面板真实行为**写的，不是拍脑袋：

* **推进有手动 / 自动两种**（插件默认是**自动**：市场自己按间隔走，
  此时点【推进一拍】会被温和地拒掉 —— "现在是自动推进，不用手动推"）。
  所以脚本**按局轮转**两种节奏：手动局切到手动再点【推进一拍】；
  自动局让心跳自己走（把插件内存里的 ``_last_tick_at`` 基线往前拨，
  等价于"时间到了"）。
* **【一键梭哈】投的是「现金 − 生活费缓冲」** —— 面板上买完会让
  现金掉到生活费红线以下时会给警告；一个想活下去的玩家会留出当天的
  生活费。留缓冲才能撑到**跨天**，跨天才有 T+1 解锁、日终结算、
  收割这些真正会出事的路径。

关于"时间压缩"
--------------
【推进一拍】自带 1 秒节流（真人不可能一秒点 30 次）。压测要的是**流水线**
在连续点击下会不会卡，不是节流本身 —— 节流另有单测守着。所以这里把
``_last_advance_at`` 归零（**插件层内存变量**，不改任何引擎 / 模式代码）
来压缩时间。想连节流一起压就加 ``--real-time``。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Coroutine

# ── 隔离数据目录（**必须在导入 conftest 之前**设好）────────────────────
_TMP_ROOT = tempfile.mkdtemp(prefix="mock_stock_meow_stress_")
os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(Path(_TMP_ROOT) / "storage")

_TESTS_DIR = Path(__file__).resolve().parent


def _load_conftest() -> tuple[Any, Any]:
    """把 ``tests/`` 加进 ``sys.path``，再取 ``conftest`` 里的两个夹具。

    ⚠ 必须**函数内**导入 —— 模块级 import 会被 CI 的 ruff（口径含
    ``--ignore-noqa``、``E402`` 与 ``I001``）当场拦下。而数据目录的隔离环境变量
    必须在 ``conftest`` 被导入**之前**设好，所以这里用惰性导入而不是模块级
    import：环境变量在模块顶层已经设完，导入时机不受影响。
    """
    if str(_TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(_TESTS_DIR))
    import conftest

    return conftest.FakeCtx, conftest.make_plugin


#: 覆盖的档位：经典四档 + 无尽档（与插件的 ``JOIN_MODES`` 一致）。
MODES = (
    "classic_beginner",
    "classic_arcade",
    "classic_advanced",
    "classic_hell",
    "infinite",
)

#: 面板载荷里**必须**存在的键（缺一个 = 面板那一块直接崩）。
#:
#: 局中载荷最全；结算页 / 空闲页各有自己的形状（面板那边一律 ``|| {}`` 兜底），
#: 所以按 ``stage`` 分别要求 —— 否则"打完之后少一个 tutorial"会被误报成渲染异常。
REQUIRED_PAYLOAD_KEYS = (
    "stage",
    "alive",
    "cash",
    "total_assets",
    "positions",
    "stocks",
    "kline_stock_ids",
    "catgirl",
    "news_today",
    "yui",
    "meow",
    "pace",
    "onboarding",
    "tutorial",
    "visual",
)

#: 非局中（结算页 / 空闲页）的必需键 —— 各自只渲染自己那一块。
REQUIRED_PAYLOAD_KEYS_BY_STAGE = {
    "finished": (
        "stage",
        "settlement",
        "yui",
        "meow",
        "pace",
        "onboarding",
        "visual",
        "community_events",
    ),
    "idle": (
        "stage",
        "pace",
        "onboarding",
        "meow",
        "yui",
        "player_name",
        "community_events",
    ),
}

#: 宿主 UI 上下文载荷的上限（512 KiB）—— 超过就传不过去 ⇒ 面板空白。
PAYLOAD_LIMIT_BYTES = 512 * 1024

#: 一局里允许出现的**推送条数**上限（推送风暴 ⇒ 她会一直刷屏）。
MAX_PUSHES_PER_GAME = 400

#: 每 3 局里有 1 局走**自动推进**（其余走手动 +【推进一拍】）。
AUTO_PACE_EVERY = 3


# ---------------------------------------------------------------- 宿主桩


class _PushResult:
    """``push_message`` 的返回桩（插件只看"能不能 await"，不看内容）。"""

    ok = True
    message_id = "stress-push"


class PushRecorder:
    """记录插件往宿主推的每一条消息。

    为什么必须有它
    --------------
    追单防护的判定口径是「**被拒的追单一条消息都不许推**」——
    推了就会再触发一个 AI 回合，她在那个回合里又会试一次 ⇒ 无限循环。
    没有记录器，这个断言**根本无法验证**（宿主侧到底收没收到消息是黑盒）。

    同时它把 ``FakeCtx`` 缺的 ``push_message`` 补上 ⇒ 主动搭话 / 卡片
    降级投递这两条路径在压测里**真的跑起来了**（而不是一路 warning）。

    为什么只留**最近 200 条**
    ----------------------
    30 局能推出八千多条消息 —— 全留着的话，压测器自己就吃掉 1 MiB 以上内存，
    内存趋势里混进的全是**压测器的**开销，插件的 OOM 结论就废了。
    所以：计数用累计值（``total``），正文只留最近 200 条（够断言用）。
    """

    #: 正文窗口（``deque`` 的 maxlen）—— 只留最近的这么多条。
    WINDOW = 200

    def __init__(self) -> None:
        self.calls: deque[dict[str, Any]] = deque(maxlen=self.WINDOW)
        self.total = 0

    def __call__(self, **kwargs: Any) -> _PushResult:
        self.calls.append(dict(kwargs))
        self.total += 1
        return _PushResult()

    # -- 便捷断言辅助 ---------------------------------------------------

    def count(self) -> int:
        """**累计**推送条数（不是窗口里的条数）。"""
        return self.total

    def tail_text(self, count: int = 3) -> list[str]:
        """最后 ``count`` 条消息的文本（用于报告里显示"她说了什么"）。"""
        out: list[str] = []
        for call in self.calls[-count:]:
            parts = call.get("parts") or []
            texts = [
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict)
            ]
            out.append(" / ".join(texts))
        return out


# ---------------------------------------------------------------- 数据结构


@dataclass
class GameReport:
    """一局压测的结果。"""

    index: int
    mode: str
    seed: int
    pace: str = "manual"
    ticks: int = 0
    buys: int = 0
    sell_ok: int = 0
    sell_rejected: int = 0
    buy_rejected: int = 0
    heartbeats: int = 0
    heartbeat_timeouts: int = 0
    tick_timeouts: int = 0
    deadlocks: int = 0
    exceptions: list[str] = field(default_factory=list)
    payload_problems: list[str] = field(default_factory=list)
    max_payload_bytes: int = 0
    max_call_seconds: float = 0.0
    peak_memory_bytes: int = 0
    memory_delta_bytes: int = 0
    memory_after_bytes: int = 0
    bankrupt: bool = False
    settled: bool = False
    resets: int = 0
    auto_cleared: int = 0
    chase_attempts: int = 0
    chase_blocked: int = 0
    chase_leaked: int = 0
    chase_skipped: int = 0
    chase_pushes_after_reject: int = 0
    quota_probe_ok: bool = False
    quota_probe_note: str = ""
    pushes: int = 0
    final_cash: float = 0.0
    final_day: int = 0
    final_tick: int = 0

    @property
    def clean(self) -> bool:
        """这一局有没有异常。"""
        return not (
            self.deadlocks
            or self.exceptions
            or self.payload_problems
            or self.heartbeat_timeouts
            or self.tick_timeouts
            or self.chase_leaked
            or self.chase_pushes_after_reject
            or self.pushes > MAX_PUSHES_PER_GAME
        )


@dataclass
class StressReport:
    """整轮压测的结果。"""

    games: list[GameReport] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    wall_seconds: float = 0.0
    call_timeout_seconds: float = 15.0
    max_ticks: int = 40
    notes: list[str] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "games": len(self.games),
            "ticks": sum(g.ticks for g in self.games),
            "buys": sum(g.buys for g in self.games),
            "sells": sum(g.sell_ok for g in self.games),
            "sell_rejected": sum(g.sell_rejected for g in self.games),
            "heartbeats": sum(g.heartbeats for g in self.games),
            "deadlocks": sum(g.deadlocks for g in self.games),
            "heartbeat_timeouts": sum(g.heartbeat_timeouts for g in self.games),
            "tick_timeouts": sum(g.tick_timeouts for g in self.games),
            "exceptions": sum(len(g.exceptions) for g in self.games),
            "payload_problems": sum(len(g.payload_problems) for g in self.games),
            "bankrupt": sum(1 for g in self.games if g.bankrupt),
            "settled": sum(1 for g in self.games if g.settled),
            "resets": sum(g.resets for g in self.games),
            "auto_cleared": sum(g.auto_cleared for g in self.games),
            "chase_attempts": sum(g.chase_attempts for g in self.games),
            "chase_blocked": sum(g.chase_blocked for g in self.games),
            "chase_leaked": sum(g.chase_leaked for g in self.games),
            "chase_skipped": sum(g.chase_skipped for g in self.games),
            "chase_pushes_after_reject": sum(g.chase_pushes_after_reject for g in self.games),
            "quota_probe_ok": sum(1 for g in self.games if g.quota_probe_ok),
            "push_storms": sum(1 for g in self.games if g.pushes > MAX_PUSHES_PER_GAME),
            "pushes": sum(g.pushes for g in self.games),
        }

    @property
    def memory_trend(self) -> dict[str, Any]:
        """跨局内存趋势（OOM 的判据）。"""
        series = [g.memory_after_bytes for g in self.games if g.memory_after_bytes > 0]
        if not series:
            return {"samples": 0, "first": 0, "last": 0, "growth": 0, "leak": False}
        first = series[0]
        last = series[-1]
        growth = last - first
        # 判定口径：跨 30 局净增超过 8 MiB 视为**泄漏嫌疑**。
        # 阈值给得宽 —— 缓存 / 归档 / 结算报告本来就是逐局增长的。
        leak = growth > 8 * 1024 * 1024
        return {
            "samples": len(series),
            "first": first,
            "last": last,
            "growth": growth,
            "leak": leak,
        }


# ---------------------------------------------------------------- 工具


def _call(
    coro: Coroutine[Any, Any, Any],
    timeout: float,
) -> tuple[str, Any, float]:
    """跑一个协程并计时。

    返回 ``(状态, 结果, 秒数)``；状态 ∈ ``ok`` / ``timeout`` / ``exception``。
    超时 ⇒ 协程被取消（插件内部的 ``finally`` 会把锁放掉）。
    """
    started = time.perf_counter()

    async def _wrapped() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        result = asyncio.run(_wrapped())
    except asyncio.TimeoutError:
        return "timeout", None, time.perf_counter() - started
    except MemoryError as error:  # OOM：单列一类，压测报告要一眼看见
        return "oom", error, time.perf_counter() - started
    except Exception as error:  # noqa: BLE001 - 压测要把异常**记下来**，不许炸
        return "exception", error, time.perf_counter() - started
    return "ok", result, time.perf_counter() - started


def _unwrap(result: Any) -> dict[str, Any]:
    """把插件的 ``Ok`` / 裸 dict 都取成 dict（其它一律空 dict）。"""
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    unwrap = getattr(result, "unwrap", None)
    if callable(unwrap):
        try:
            payload = unwrap()
        except Exception:  # noqa: BLE001 - 错误对象就当没有载荷
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _is_ok(result: Any) -> bool:
    """``Ok`` 就是 ``Ok``（``Err`` 一律 ``False``）。"""
    checker = getattr(result, "is_ok", None)
    return bool(checker()) if callable(checker) else False


def _fee_rate(engine_config: Any) -> float:
    """买入费率（**对象属性**，不是 dict —— ``EngineConfig`` 不是 dict）。"""
    fees = getattr(engine_config, "fees", None)
    if fees is None and isinstance(engine_config, dict):
        fees = engine_config.get("fees")
    rate = getattr(fees, "buy_rate", None)
    if rate is None and isinstance(fees, dict):
        rate = fees.get("buy_rate")
    try:
        return float(rate or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tick_interval_seconds(plugin: Any, engine_config: Any, mode: str) -> float:
    """这一局的心跳间隔（秒）—— 自动推进节奏用。"""
    difficulties = getattr(engine_config, "difficulties", None)
    difficulty = None
    if isinstance(difficulties, dict):
        difficulty = difficulties.get(mode)
    base = getattr(difficulty, "tick_interval_seconds", None)
    if base is None:
        base = 8
    try:
        speed = plugin._resolve_speed()
    except Exception:  # noqa: BLE001 - 拿不到就用默认档
        speed = "normal"
    try:
        return float(plugin._tick_interval(float(base), speed))
    except Exception:  # noqa: BLE001 - 同上
        return float(base)


def _check_payload(payload: dict[str, Any]) -> tuple[list[str], int]:
    """检查面板载荷（**UI 渲染异常**的三条口径）。"""
    problems: list[str] = []
    if not payload:
        return ["面板载荷是空的"], 0
    try:
        blob = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        # NaN / Infinity 在标准 JSON 里不存在 ⇒ 宿主的序列化会炸 ⇒ 白屏。
        return [f"载荷无法被宿主 JSON 序列化（{error}）"], 0
    size = len(blob.encode("utf-8"))
    if size > PAYLOAD_LIMIT_BYTES:
        problems.append(f"载荷 {size} 字节，超过宿主 {PAYLOAD_LIMIT_BYTES} 字节上限")
    stage = str(payload.get("stage") or "")
    required = REQUIRED_PAYLOAD_KEYS_BY_STAGE.get(stage, REQUIRED_PAYLOAD_KEYS)
    missing = [key for key in required if key not in payload]
    if missing:
        problems.append(f"载荷缺字段：{', '.join(missing)}")
    return problems, size


def _all_in(payload: dict[str, Any], fee_rate: float) -> tuple[str, float]:
    """【一键梭哈】：把「现金 − 生活费缓冲」全压进第一只票。

    为什么留缓冲
    ------------
    面板在「买完现金掉到生活费红线以下」时会警告 —— 一个想活下去的玩家
    会留出当天的生活费。留了缓冲才撑得到**跨天**（T+1 解锁 / 日终结算 /
    收割都在跨天那一下）。
    """
    stocks = payload.get("stocks") or []
    if not stocks:
        return "", 0.0
    stock = stocks[0]
    stock_id = str(stock.get("id") or "")
    price = float(stock.get("price") or 0.0)
    cash = float(payload.get("cash") or 0.0)
    bill = float(payload.get("next_bill") or 0.0)
    if not stock_id or price <= 0.0:
        return stock_id, 0.0
    budget = cash - bill
    if budget <= 0.0:
        return stock_id, 0.0
    # 买入总开销 = 名义额 × (1 + 买入费率) ⇒ 名义额 = 预算 / (1 + 费率)。
    return stock_id, (budget / (1.0 + fee_rate)) / price


def _liquidate(payload: dict[str, Any]) -> list[tuple[str, float]]:
    """【割肉卖】：把能卖的持仓全卖掉。"""
    out: list[tuple[str, float]] = []
    for position in payload.get("positions") or []:
        if not isinstance(position, dict):
            continue
        stock_id = str(position.get("stock_id") or "")
        amount = float(position.get("sellable") or 0.0)
        if stock_id and amount > 0.0:
            out.append((stock_id, amount))
    return out


def _first_stock_id(payload: dict[str, Any]) -> str:
    stocks = payload.get("stocks") or []
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        stock_id = str(stock.get("id") or "")
        if stock_id and bool(stock.get("alive", True)):
            return stock_id
    return ""


# ---------------------------------------------------------------- 压测主体


def _new_plugin() -> tuple[Any, PushRecorder]:
    """建一个**全新**的插件实例（数据目录隔离在临时区）。"""
    fake_ctx_cls, make_plugin = _load_conftest()
    ctx = fake_ctx_cls(config_path=Path(_TMP_ROOT) / "plugin.toml")
    recorder = PushRecorder()
    # 宿主 SDK 的 ``push_message``（插件主动搭话 / 卡片降级投递都要它）。
    ctx.push_message = recorder  # type: ignore[attr-defined]
    plugin = make_plugin(ctx)
    asyncio.run(plugin.on_startup())
    return plugin, recorder


def _reset_game(plugin: Any) -> str:
    """**自动重置**：把当前这局结算掉并清场，好让下一局能开。

    返回三种状态：

    * ``"auto_cleared"`` —— 插件**自己**已经清场了（这一局自己走完了，
      例如经典档第 30 天自动终局并结算）⇒ 什么都不用做；
    * ``"manual"`` —— 本脚本显式清场成功。优先 ``declare_bankrupt``（面板那枚
      「紧急逃生」按钮走的同一条路 —— 它对**已经退场**的玩家也照常生效，
      P27.2 修的就是这条）；万一那条路被挡，再回退 ``settle``。
      两条路都会顺带把结算 / 归档 / 弹幕那条流水线也压到；
    * ``"failed"`` —— 清不动（下一局会 join 失败，报告里能一眼看见）。
    """
    payload = _unwrap(asyncio.run(plugin.market_context()))
    stage = str(payload.get("stage") or "")
    if stage in ("finished", "idle"):
        return "auto_cleared"
    result = asyncio.run(plugin.declare_bankrupt())
    if _is_ok(result):
        return "manual"
    # 兜底：万一 ``declare_bankrupt`` 被别的闸门挡了，用 ``settle`` 收场。
    result = asyncio.run(plugin.settle())
    return "manual" if _is_ok(result) else "failed"


def _play_one(
    plugin: Any,
    recorder: PushRecorder,
    *,
    index: int,
    mode: str,
    seed: int,
    max_ticks: int,
    timeout: float,
    real_time: bool,
) -> GameReport:
    """玩一局（自动点击流 + 全程监控）。"""
    report = GameReport(index=index, mode=mode, seed=seed)
    # 推送计数是**累计**的 ⇒ 这一局的条数要自己扣掉上一局的。
    pushes_before = recorder.count()
    engine_config = asyncio.run(plugin._engine_config())
    fee_rate = _fee_rate(engine_config)
    interval = _tick_interval_seconds(plugin, engine_config, mode)

    # ── 推进节奏：手动（点【推进一拍】）/ 自动（心跳自己走）────────────
    # 插件默认是**自动** —— 此时点【推进一拍】会被温和地拒掉。
    auto_pace = (index % AUTO_PACE_EVERY) == (AUTO_PACE_EVERY - 1)
    report.pace = "auto" if auto_pace else "manual"
    asyncio.run(plugin.set_auto_tick(enabled=auto_pace))

    tracemalloc.reset_peak()
    memory_before = tracemalloc.get_traced_memory()[0]

    joined = asyncio.run(plugin.join(mode=mode, seed=seed))
    if not _is_ok(joined):
        report.exceptions.append(f"join 失败：{joined}")
        return report
    # 直播托管：Yui 自己就能动手（用户 P27.2 的场景）。
    asyncio.run(plugin.yui_mode(mode="live"))

    payload = _unwrap(asyncio.run(plugin.market_context()))
    problems, size = _check_payload(payload)
    report.payload_problems.extend(problems)
    report.max_payload_bytes = max(report.max_payload_bytes, size)

    # ── 追单探针 + 配额探针（**开局就跑**，趁现金还足）──────────────
    _probe_chase(plugin, recorder, report, timeout)
    _probe_quota(plugin, recorder, report, timeout)

    for tick in range(max_ticks):
        report.ticks += 1

        # ① 宿主每秒一次的心跳（模拟定时器回调）。
        if auto_pace and not real_time:
            # 时间压缩：把插件内存里的 tick 基线往前拨一个间隔
            # （**插件层内存变量**，不改引擎 / 模式）⇒ 心跳这一次就到期。
            plugin._last_tick_at = time.time() - interval - 0.05
        status, result, seconds = _call(plugin.market_tick(), timeout)
        report.heartbeats += 1
        report.max_call_seconds = max(report.max_call_seconds, seconds)
        if status == "timeout":
            report.deadlocks += 1
            report.exceptions.append(f"心跳调用超时（>{timeout:.0f}s）")
            break
        if status == "oom":
            report.exceptions.append(f"心跳触发 OOM：{result!r}")
            break
        if status == "exception":
            report.exceptions.append(f"心跳抛异常：{result!r}")

        # ② 玩家点【推进一拍】（自动档位下市场自己走，这里不重复点）。
        if not auto_pace:
            if not real_time:
                # 绕过 1 秒节流（时间压缩；节流本身另有单测）。
                plugin._last_advance_at = 0.0
            status, result, seconds = _call(plugin.advance_tick(), timeout)
            report.max_call_seconds = max(report.max_call_seconds, seconds)
            if status == "timeout":
                report.deadlocks += 1
                report.exceptions.append(f"推进一拍超时（>{timeout:.0f}s）")
                break
            if status == "oom":
                report.exceptions.append(f"推进一拍触发 OOM：{result!r}")
                break
            if status == "exception":
                report.exceptions.append(f"推进一拍抛异常：{result!r}")
                break

        # ③ 玩家点【一键梭哈】。
        payload = _unwrap(asyncio.run(plugin.market_context()))
        problems, size = _check_payload(payload)
        report.payload_problems.extend(problems)
        report.max_payload_bytes = max(report.max_payload_bytes, size)
        stock_id, amount = _all_in(payload, fee_rate)
        if stock_id and amount > 0.0:
            status, result, seconds = _call(plugin.buy(stock_id, amount), timeout)
            report.max_call_seconds = max(report.max_call_seconds, seconds)
            if status == "timeout":
                report.deadlocks += 1
                report.exceptions.append(f"买入超时（>{timeout:.0f}s）")
                break
            if status == "oom":
                report.exceptions.append(f"买入触发 OOM：{result!r}")
                break
            if status == "exception":
                report.exceptions.append(f"买入抛异常：{result!r}")
            elif _is_ok(result):
                report.buys += 1
            else:
                report.buy_rejected += 1

        # ④ 每 3 拍点一次【割肉卖】（把卖出的流水线也压到）。
        if tick % 3 == 2:
            for sell_id, sell_amount in _liquidate(payload):
                status, result, seconds = _call(
                    plugin.sell(sell_id, sell_amount), timeout
                )
                report.max_call_seconds = max(report.max_call_seconds, seconds)
                if status == "timeout":
                    report.deadlocks += 1
                    report.exceptions.append(f"卖出超时（>{timeout:.0f}s）")
                    break
                if status == "oom":
                    report.exceptions.append(f"卖出触发 OOM：{result!r}")
                    break
                if status == "exception":
                    report.exceptions.append(f"卖出抛异常：{result!r}")
                elif _is_ok(result):
                    report.sell_ok += 1
                else:
                    # T+1 / 持仓不足 —— 预期内的温和拒绝，不算异常。
                    report.sell_rejected += 1

        # ⑤ 面板每秒轮询一次（UI 渲染载荷）。
        payload = _unwrap(asyncio.run(plugin.market_context()))
        problems, size = _check_payload(payload)
        report.payload_problems.extend(problems)
        report.max_payload_bytes = max(report.max_payload_bytes, size)

        # ⑥ 破产 / 终局？⇒ 这一局到此为止（外层会自动重置并开下一局）。
        if not payload.get("alive", True):
            report.bankrupt = True
            break
        if str(payload.get("stage") or "") in ("finished", "idle"):
            report.settled = True
            break

    # 心跳 / tick 超时的累计增量。
    report.heartbeat_timeouts = int(getattr(plugin, "_heartbeat_timeouts", 0) or 0)
    report.tick_timeouts = int(getattr(plugin, "_tick_timeouts", 0) or 0)
    report.pushes = recorder.count() - pushes_before
    report.final_cash = float(payload.get("cash") or 0.0)
    report.final_day = int(payload.get("day") or 0)
    report.final_tick = int(payload.get("tick") or 0)

    current, peak = tracemalloc.get_traced_memory()
    report.peak_memory_bytes = int(peak)
    report.memory_delta_bytes = int(current - memory_before)
    report.memory_after_bytes = int(current)
    return report


def _probe_chase(
    plugin: Any,
    recorder: PushRecorder,
    report: GameReport,
    timeout: float,
) -> None:
    """**追单探针**：Yui 成交一单 → 立刻再下一单 ⇒ 必须被拒。

    这一段模拟用户 P27.2 的原话：「让 Yui 完成一笔交易，看她收到回执后，
    是否会因为『这一轮不要再下单』的约束而停止」。

    判据有两条，**都要**满足才算拦住：

    1. 第二单返回 ``receipt_cycle``（= 被**回执周期锁**挡的）；
    2. 第二单**一条消息都不许推**（推了就会再触发一个 AI 回合 ⇒
       她在那个回合里又会追一单 ⇒ 无限循环）。

    配额 / 模式闸门挡下来的（不是周期锁）算「本局跳过」，不算漏 ——
    那不是追单。
    """
    payload = _unwrap(asyncio.run(plugin.market_context()))
    stock_id = _first_stock_id(payload)
    if not stock_id:
        report.chase_skipped += 1
        return

    status, first, _seconds = _call(
        plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1), timeout
    )
    if status != "ok" or not isinstance(first, dict):
        report.exceptions.append(f"追单探针的第一次交易异常：{status} {first!r}")
        return
    if first.get("is_error") is not False:
        # 这一局没成交成（钱不够 / 闸门挡住）⇒ 这一局不测追单。
        report.chase_skipped += 1
        return

    report.chase_attempts += 1
    mark_after_first = recorder.count()
    status, second, _seconds = _call(
        plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1), timeout
    )
    if status != "ok" or not isinstance(second, dict):
        report.exceptions.append(f"追单探针的第二次交易异常：{status} {second!r}")
        return

    pushed_after_reject = recorder.count() - mark_after_first
    report.chase_pushes_after_reject = pushed_after_reject
    output = second.get("output") if isinstance(second.get("output"), dict) else {}
    if output.get("receipt_cycle") is True:
        report.chase_blocked += 1
        if pushed_after_reject:
            report.exceptions.append(
                f"追单被周期锁挡住了，但仍然推了 {pushed_after_reject} 条消息"
                "（再推就会再触发一个 AI 回合 ⇒ 无限循环）"
            )
        return
    if second.get("is_error") is True:
        # 被别的闸门（配额 / 模式）挡住了 ⇒ 不是追单。
        report.chase_skipped += 1
        return
    # **追单漏出去了** —— 这是必须立刻修的事故。
    report.chase_leaked += 1
    report.exceptions.append(
        f"追单漏出去了：Yui 成交后同一回合又下了一单（{output!r}）"
    )


def _probe_quota(
    plugin: Any,
    recorder: PushRecorder,
    report: GameReport,
    timeout: float,
) -> None:
    """**live 日配额探针**：把今天的配额用光，第 N+1 单必须被挡掉。

    用户 P27.2 的原话：「确保 live 模式下，Yui 的日配额限制依然生效」。
    真实上限是 ``YUI_TRADES_PER_DAY``（**3 单/天**，不是 60 ——
    60 是心跳日志间隔与工具重注册间隔，别混淆）。

    配额是"局内每一天"的，所以这一探针必须**在同一天里**跑完 ——
    探针在开局（day=1）执行，4 单正好把 3 单配额用光。
    """
    payload = _unwrap(asyncio.run(plugin.market_context()))
    stock_id = _first_stock_id(payload)
    if not stock_id:
        report.quota_probe_note = "没有可交易的标的"
        return

    quota_total = 0
    yui_block = payload.get("yui")
    if isinstance(yui_block, dict):
        quota_total = int(yui_block.get("quota_total") or 0)
    if quota_total <= 0:
        report.quota_probe_note = "面板没有暴露配额总数"
        return

    # 追单探针已经成交了 1 单（配额 3→2）⇒ 这里再打 ``quota_total`` 单。
    for _ in range(quota_total + 1):
        # 每一次都是"新的一回合"⇒ 先解开回执周期锁（否则会被周期锁拦，
        # 那就测不到配额闸门了）。
        plugin._yui_trade_cycle_unlock()
        status, result, _seconds = _call(
            plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.05), timeout
        )
        if status != "ok" or not isinstance(result, dict):
            report.exceptions.append(f"配额探针的调用异常：{status} {result!r}")
            return
        if result.get("is_error") is not False:
            output = result.get("output")
            message = ""
            if isinstance(output, dict):
                message = str(output.get("message") or "")
            if isinstance(output, dict) and output.get("receipt_cycle") is True:
                report.quota_probe_note = "配额探针被回执周期锁挡住了（顺序不对）"
                return
            # 被配额闸门挡下来了 ⇒ 配额生效。
            report.quota_probe_ok = True
            report.quota_probe_note = message[:60]
            return
    report.quota_probe_note = "配额探针跑完仍能成交 —— 配额没生效"
    report.exceptions.append(f"配额探针失败：{report.quota_probe_note}")


def run_stress(
    *,
    games: int = 30,
    max_ticks: int = 40,
    timeout: float = 15.0,
    real_time: bool = False,
) -> StressReport:
    """跑完整轮压测。"""
    report = StressReport(
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        call_timeout_seconds=timeout,
        max_ticks=max_ticks,
    )
    started = time.perf_counter()
    # ⚠ 必须在 ``tracemalloc.start()`` **之前**把 ``conftest``（连带整个插件包）
    # 导入掉 —— 否则那一次性的一万多 KiB 导入开销会被算进**第 1 局**的内存峰值，
    # 报告里的「单局最高」就跟之前几轮完全不可比了（那是导入成本，不是局内增长）。
    _load_conftest()
    tracemalloc.start()
    plugin, recorder = _new_plugin()
    for index in range(games):
        mode = MODES[index % len(MODES)]
        seed = 7300 + index
        game = _play_one(
            plugin,
            recorder,
            index=index + 1,
            mode=mode,
            seed=seed,
            max_ticks=max_ticks,
            timeout=timeout,
            real_time=real_time,
        )
        # ── 局末**自动重置**（用户 P27.2 明确要求）──────────────────
        reset_kind = _reset_game(plugin)
        if reset_kind == "manual":
            game.resets = 1
        elif reset_kind == "auto_cleared":
            game.auto_cleared = 1
        else:
            game.exceptions.append("局末重置失败：下一局开不了")
        report.games.append(game)
        # 疑似死锁之后，这个实例可能已经卡住 ⇒ 换一个干净的。
        if game.deadlocks:
            plugin, recorder = _new_plugin()
    report.wall_seconds = time.perf_counter() - started
    report.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
    tracemalloc.stop()
    return report


# ---------------------------------------------------------------- 报告


def render_markdown(report: StressReport) -> str:
    """把结果渲染成 Markdown 报告。"""
    totals = report.totals
    memory = report.memory_trend
    lines: list[str] = []
    lines.append("# P27.2 模拟直播压测报告")
    lines.append("")
    lines.append(f"- 开始：{report.started_at}")
    lines.append(f"- 结束：{report.finished_at}")
    lines.append(
        f"- 用时：{report.wall_seconds:.1f}s（每局上限 {report.max_ticks} 拍，"
        f"单次调用超时判定 {report.call_timeout_seconds:.0f}s）"
    )
    lines.append(
        f"- 覆盖档位：{'、'.join(MODES)}（经典四档 + 无尽档，轮转）"
    )
    lines.append(
        f"- 推进节奏：手动（点【推进一拍】）与自动（心跳自己走）轮转，"
        f"每 {AUTO_PACE_EVERY} 局有 1 局走自动"
    )
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    verdict = "**通过**" if (
        totals["deadlocks"] == 0
        and totals["exceptions"] == 0
        and totals["payload_problems"] == 0
        and totals["heartbeat_timeouts"] == 0
        and totals["tick_timeouts"] == 0
        and totals["chase_leaked"] == 0
        and totals["chase_pushes_after_reject"] == 0
        and totals["push_storms"] == 0
        and not memory["leak"]
    ) else "**有异常，见下表**"
    lines.append(f"- 总判定：{verdict}")
    lines.append(
        f"- 死锁：{totals['deadlocks']} 次 ｜ 心跳卡死："
        f"{totals['heartbeat_timeouts']} 次 ｜ tick 流水线卡死：{totals['tick_timeouts']} 次"
    )
    lines.append(
        f"- 未捕获异常：{totals['exceptions']} 处 ｜ UI 渲染异常："
        f"{totals['payload_problems']} 处"
    )
    lines.append(
        f"- 追单探针：尝试 {totals['chase_attempts']} 次，拦下 "
        f"{totals['chase_blocked']} 次，跳过 {totals['chase_skipped']} 次，"
        f"**漏出 {totals['chase_leaked']} 次**，"
        f"被拒后仍多推的消息 {totals['chase_pushes_after_reject']} 条"
    )
    lines.append(
        f"- live 日配额探针：{totals['quota_probe_ok']}/{totals['games']} 局验到"
        "「配额用光后第 N+1 单被挡」"
    )
    lines.append(
        f"- 局间清场：手动重置 {totals['resets']} 次 ｜ 插件自动清场（玩家阵亡即结算）"
        f"{totals['auto_cleared']} 次 ｜ 破产局：{totals['bankrupt']} ｜ "
        f"正常终局：{totals['settled']}"
    )
    lines.append(
        f"- 累计：{totals['ticks']} 拍 ｜ {totals['buys']} 次梭哈 ｜ "
        f"{totals['sells']} 次割肉（{totals['sell_rejected']} 次被 T+1 等温和拒绝）｜ "
        f"{totals['heartbeats']} 次心跳 ｜ {totals['pushes']} 条推送"
        f"（推送风暴局：{totals['push_storms']}）"
    )
    peaks = [g.peak_memory_bytes for g in report.games] or [0]
    lines.append(
        f"- 内存：单局最高 {max(peaks) / 1024:.0f} KiB ｜ "
        f"首局 {memory['first'] / 1024:.0f} KiB → 末局 {memory['last'] / 1024:.0f} KiB "
        f"（净增 {(memory['growth'] or 0) / 1024:.0f} KiB，"
        f"{'**有泄漏嫌疑**' if memory['leak'] else '无泄漏'}）"
    )
    lines.append("")
    lines.append("## 逐局明细")
    lines.append("")
    lines.append(
        "| # | 档位 | 节奏 | 拍数 | 天/拍 | 梭哈 | 卖出 | 推送 | 心跳卡死 | tick 卡死 | "
        "死锁 | UI 异常 | 载荷 KiB | 内存 KiB | 追单拦下 | 配额 | 清场 | 结局 |"
    )
    lines.append(
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"
    )
    for game in report.games:
        end = "破产" if game.bankrupt else ("终局" if game.settled else "到顶")
        quota = "✓" if game.quota_probe_ok else ("—" if not game.quota_probe_note else "✗")
        cleared = "手动" if game.resets else ("自动" if game.auto_cleared else "—")
        lines.append(
            f"| {game.index} | {game.mode} | {game.pace} | {game.ticks} | "
            f"{game.final_day}/{game.final_tick} | {game.buys} | "
            f"{game.sell_ok} | {game.pushes} | {game.heartbeat_timeouts} | "
            f"{game.tick_timeouts} | "
            f"{game.deadlocks} | {len(game.payload_problems)} | "
            f"{game.max_payload_bytes / 1024:.1f} | {game.peak_memory_bytes / 1024:.0f} | "
            f"{game.chase_blocked}/{game.chase_attempts} | {quota} | {cleared} | {end} |"
        )
    problems = [
        (game, problem)
        for game in report.games
        for problem in (game.exceptions + game.payload_problems)
    ]
    lines.append("")
    lines.append("## 异常明细")
    lines.append("")
    if problems:
        for game, problem in problems:
            lines.append(f"- 第 {game.index} 局（{game.mode}）：{problem}")
    else:
        lines.append("- 无。")
    lines.append("")
    return "\n".join(lines)


def report_to_dict(report: StressReport) -> dict[str, Any]:
    """报告的机器可读形态（JSON）。"""
    return {
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "wall_seconds": round(report.wall_seconds, 3),
        "call_timeout_seconds": report.call_timeout_seconds,
        "max_ticks": report.max_ticks,
        "totals": report.totals,
        "memory_trend": report.memory_trend,
        "games": [
            {
                "index": g.index,
                "mode": g.mode,
                "seed": g.seed,
                "pace": g.pace,
                "ticks": g.ticks,
                "buys": g.buys,
                "sell_ok": g.sell_ok,
                "sell_rejected": g.sell_rejected,
                "buy_rejected": g.buy_rejected,
                "heartbeats": g.heartbeats,
                "heartbeat_timeouts": g.heartbeat_timeouts,
                "tick_timeouts": g.tick_timeouts,
                "deadlocks": g.deadlocks,
                "exceptions": g.exceptions,
                "payload_problems": g.payload_problems,
                "max_payload_bytes": g.max_payload_bytes,
                "max_call_seconds": round(g.max_call_seconds, 3),
                "peak_memory_bytes": g.peak_memory_bytes,
                "memory_delta_bytes": g.memory_delta_bytes,
                "memory_after_bytes": g.memory_after_bytes,
                "bankrupt": g.bankrupt,
                "settled": g.settled,
                "resets": g.resets,
                "auto_cleared": g.auto_cleared,
                "chase_attempts": g.chase_attempts,
                "chase_blocked": g.chase_blocked,
                "chase_leaked": g.chase_leaked,
                "chase_skipped": g.chase_skipped,
                "chase_pushes_after_reject": g.chase_pushes_after_reject,
                "quota_probe_ok": g.quota_probe_ok,
                "quota_probe_note": g.quota_probe_note,
                "pushes": g.pushes,
                "final_cash": round(g.final_cash, 2),
                "final_day": g.final_day,
                "final_tick": g.final_tick,
            }
            for g in report.games
        ],
    }


def _default_out_dir() -> Path:
    """默认输出目录：仓库根的 ``docs/``。

    ``tests/`` 的上溯：``tests`` → 插件根 → ``plugins`` → ``plugin`` →
    仓库根。少算一层就会写到 ``plugin/docs/`` 去。
    """
    repo_root = _TESTS_DIR.parents[3]
    return repo_root / "docs"


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="P27.2 模拟直播压测")
    parser.add_argument("--games", type=int, default=30, help="压多少局（默认 30）")
    parser.add_argument("--ticks", type=int, default=40, help="每局最多点几拍（默认 40）")
    parser.add_argument(
        "--call-timeout", type=float, default=15.0, help="单次调用超时（秒）"
    )
    parser.add_argument(
        "--real-time",
        action="store_true",
        help="不绕过【推进一拍】的 1 秒节流（很慢）",
    )
    parser.add_argument("--out", default="", help="报告输出目录（默认 <仓库>/docs）")
    args = parser.parse_args(argv)

    report = run_stress(
        games=max(1, int(args.games)),
        max_ticks=max(1, int(args.ticks)),
        timeout=float(args.call_timeout),
        real_time=bool(args.real_time),
    )

    out_dir = Path(args.out) if args.out else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "P27.2-压测报告.md"
    json_path = out_dir / "P27.2-压测报告.json"
    md_path.write_bytes(render_markdown(report).encode("utf-8"))
    json_path.write_bytes(
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2).encode("utf-8")
    )

    totals = report.totals
    print(render_markdown(report))
    print(f"[OK] 报告已写出：{md_path}")
    print(f"[OK] 报告已写出：{json_path}")
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)

    if (
        totals["deadlocks"]
        or totals["exceptions"]
        or totals["payload_problems"]
        or totals["heartbeat_timeouts"]
        or totals["tick_timeouts"]
        or totals["chase_leaked"]
        or totals["chase_pushes_after_reject"]
        or report.memory_trend["leak"]
    ):
        print("[FAIL] 压测发现异常（详见上面的明细）")
        return 1
    print("[OK] 压测通过：无死锁 / 无心跳卡死 / 无 UI 渲染异常 / 无追单漏出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
