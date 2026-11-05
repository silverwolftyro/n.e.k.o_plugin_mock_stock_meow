# -*- coding: utf-8 -*-
"""P27.2：**压测守卫**（pytest 版，进 CI 闸门）。

为什么要有这个
--------------
``tests/stress_live.py`` 是**手动**跑的大轮压测（默认 30 局、几十分钟级
的产出），它回答的是"今晚直播前这一版到底稳不稳"。但**手动脚本不会拦人** ——
明天的 CI 闸门必须能在有人改坏代码之后**当场红**，而不是等下一次直播。

所以这里把同一套判定搬进 pytest：**缩小版**（5 局 × 10 拍），跑完只需要
几秒，但盯的六件事和 30 局那轮**完全一致**：

1. **死锁** —— 任何一次调用超时；
2. **心跳卡死** —— ``_heartbeat_timeouts`` / ``_tick_timeouts`` 增量；
3. **UI 渲染异常** —— 面板载荷必须能被宿主 JSON 序列化（不许 NaN）、
   体积不许超限、关键字段不许缺（按 ``stage`` 分别要求）；
4. **OOM / 内存泄漏** —— ``tracemalloc`` 跨局增长趋势；
5. **追单（防自嗨）** —— Yui 成交后再追一单必须被**回执周期锁**挡掉，
   而且**一条消息都不许推**；
6. **live 日配额** —— 第 N+1 单必须被配额闸门挡掉。

外加一条**结构守卫**：压测脚本覆盖的档位必须和插件的 ``JOIN_MODES``
**完全一致** —— 少压一个档位，等于那个档位今晚没人守。

口径与红线
----------
* **只碰插件层**：压测脚本走的是插件的真实入口（``join`` / ``advance_tick``
  / ``buy`` / ``sell`` / ``llm_yui_trade``），``engine/`` 与 ``modes/``
  零改动。
* **时间压缩**：手动局把插件内存里的 ``_last_advance_at`` 归零，自动局把
  ``_last_tick_at`` 往前拨一个间隔 —— 压的是**流水线**，不是 1 秒节流
  （节流另有单测守着）。
* **导入隔离**：``stress_live`` 在 **import 时**就把数据目录指到自己的临时
  区（``NEKO_STORAGE_SELECTED_ROOT``）。这个夹具负责在模块结束后**还原**
  环境变量，免得污染同一 pytest 进程里的其它测试。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


#: 缩小版的规模：5 局 × 10 拍。
#:
#: **5 局**是为了把插件的 5 个档位（经典四档 + 无尽档）**各压一遍**；
#: **10 拍**足以走完「推进 → 买入 → 跨天 → 卖出 → 结算」的骨架，又不至于
#: 让 CI 闸门变慢（实测几秒级）。
CI_GAMES = 5
CI_MAX_TICKS = 10

#: 单次调用的超时判定（秒）。比手动那轮（15s）宽一点 —— CI 机器可能更慢。
CI_CALL_TIMEOUT = 25.0


@pytest.fixture(scope="module")
def stress_module() -> Any:
    """导入压测脚本，并把 ``NEKO_STORAGE_SELECTED_ROOT`` 还回去。

    ``stress_live`` 在 **import 时**就会把自己的临时数据目录写进环境变量
    （它必须先于 ``conftest`` 生效）。这个夹具在模块作用域内保住那个目录
    （压测要往那儿建库），模块结束**还原**成原值 ⇒ 同一 pytest 进程里的
    其它测试不受影响。
    """
    saved = os.environ.get("NEKO_STORAGE_SELECTED_ROOT")
    module = importlib.import_module("stress_live")
    try:
        yield module
    finally:
        if saved is None:
            os.environ.pop("NEKO_STORAGE_SELECTED_ROOT", None)
        else:
            os.environ["NEKO_STORAGE_SELECTED_ROOT"] = saved


@pytest.fixture(scope="module")
def small_stress(stress_module: Any) -> Any:
    """跑一轮**缩小版**压测（5 局 × 10 拍），整轮结果给所有用例共享。"""
    return stress_module.run_stress(
        games=CI_GAMES,
        max_ticks=CI_MAX_TICKS,
        timeout=CI_CALL_TIMEOUT,
    )


# ---------------------------------------------------------------- 死锁 / 心跳


def test_the_small_stress_run_covers_every_game(stress_module: Any, small_stress: Any) -> None:
    """确实跑满了配置的局数（守卫本身没被短路）。"""
    totals = small_stress.totals
    assert totals["games"] == CI_GAMES, totals
    assert totals["ticks"] > 0, totals


def test_the_small_stress_run_has_no_deadlock(small_stress: Any) -> None:
    """**死锁**：任何一次调用超时都算死锁。"""
    totals = small_stress.totals
    assert totals["deadlocks"] == 0, totals
    for game in small_stress.games:
        assert game.deadlocks == 0, f"第 {game.index} 局死锁：{game.exceptions}"


def test_the_small_stress_run_has_no_heartbeat_stall(small_stress: Any) -> None:
    """**心跳卡死**：心跳 / tick 流水线的超时增量必须为 0。"""
    totals = small_stress.totals
    assert totals["heartbeat_timeouts"] == 0, totals
    assert totals["tick_timeouts"] == 0, totals


def test_the_small_stress_run_raises_no_unhandled_exception(small_stress: Any) -> None:
    """**未捕获异常**（含 ``MemoryError``）：一个都不许有。"""
    totals = small_stress.totals
    assert totals["exceptions"] == 0, totals
    for game in small_stress.games:
        assert not game.exceptions, f"第 {game.index} 局异常：{game.exceptions}"


# ---------------------------------------------------------------- UI 渲染


def test_the_small_stress_run_keeps_the_panel_payload_renderable(small_stress: Any) -> None:
    """**UI 渲染异常**：面板载荷必须可 JSON 序列化、不超限、关键字段不缺。"""
    totals = small_stress.totals
    assert totals["payload_problems"] == 0, totals
    for game in small_stress.games:
        assert not game.payload_problems, f"第 {game.index} 局载荷异常：{game.payload_problems}"
        assert game.max_payload_bytes <= 512 * 1024, game.max_payload_bytes


def test_the_small_stress_run_does_not_flood_the_chat(small_stress: Any) -> None:
    """**推送风暴**：一局里几百条主动推送 ⇒ 直播画面会被刷爆。"""
    totals = small_stress.totals
    assert totals["push_storms"] == 0, totals
    for game in small_stress.games:
        assert game.pushes <= 400, f"第 {game.index} 局推送 {game.pushes} 条"


# ---------------------------------------------------------------- 内存


def test_the_small_stress_run_does_not_leak_memory(small_stress: Any) -> None:
    """**OOM / 内存泄漏**：跨局净增不许超过 8 MiB。"""
    memory = small_stress.memory_trend
    assert memory["samples"] > 0, memory
    assert memory["leak"] is False, memory


# ---------------------------------------------------------------- 追单 / 配额


def test_the_small_stress_run_blocks_yui_from_chasing(small_stress: Any) -> None:
    """**追单（防自嗨）**：成交后再下一单必须被挡，且**一条消息都不许推**。"""
    totals = small_stress.totals
    assert totals["chase_leaked"] == 0, totals
    assert totals["chase_pushes_after_reject"] == 0, totals
    # 至少真的尝试过（否则这条守卫是"空跑"过了）
    assert totals["chase_attempts"] > 0, totals
    # 至少有一次**确实**被回执周期锁挡住 —— 这才是被测的那道闸门。
    assert totals["chase_blocked"] > 0, totals
    # 每次「成交后再追一单」都有明确归宿：被周期锁挡住，或漏出去了。
    # （第二次若被**别的**闸门 —— 配额 / 模式 —— 挡住，探针记作"跳过"，
    #  不算漏，但也说明那一局没测到周期锁。）
    assert totals["chase_blocked"] + totals["chase_leaked"] <= totals["chase_attempts"], totals


def test_the_live_daily_quota_still_applies(small_stress: Any) -> None:
    """**live 日配额**：配额用光之后的那一单必须被闸门挡掉。"""
    totals = small_stress.totals
    assert totals["quota_probe_ok"] == CI_GAMES, totals
    for game in small_stress.games:
        assert game.quota_probe_ok, f"第 {game.index} 局配额探针没过：{game.quota_probe_note}"


# ---------------------------------------------------------------- 局间重置


def test_every_game_can_be_reset_and_rejoined(small_stress: Any) -> None:
    """**自动重置**：每局结束后都要能开下一局（破产 ⇒ 结算清场）。"""
    totals = small_stress.totals
    assert totals["resets"] + totals["auto_cleared"] == CI_GAMES, totals
    for game in small_stress.games:
        assert game.resets + game.auto_cleared == 1, f"第 {game.index} 局没清场"


# ---------------------------------------------------------------- 结构守卫


def test_the_stress_script_covers_exactly_the_join_modes(stress_module: Any) -> None:
    """压测脚本覆盖的档位必须和插件的 ``JOIN_MODES`` **完全一致**。"""
    from plugins.mock_stock_meow import JOIN_MODES

    assert set(stress_module.MODES) == set(JOIN_MODES), (
        f"压测档位 {sorted(stress_module.MODES)} != 插件档位 {sorted(JOIN_MODES)}"
    )
    assert "infinite" in stress_module.MODES, "无尽档必须被压到"


def test_the_stress_script_keeps_watching_the_six_signals(stress_module: Any) -> None:
    """六件事一个都不能少 —— 少一件 = 那一类事故今晚没人守。"""
    source = Path(stress_module.__file__).read_text(encoding="utf-8")
    for token in (
        "deadlocks",
        "heartbeat_timeouts",
        "tick_timeouts",
        "payload_problems",
        "memory_trend",
        "chase_leaked",
        "quota_probe_ok",
        "MAX_PUSHES_PER_GAME",
        "PAYLOAD_LIMIT_BYTES",
    ):
        assert token in source, f"压测脚本里找不到 {token}"


def test_the_stress_script_never_touches_the_engine_or_mode_layers(stress_module: Any) -> None:
    """压测脚本只驱动插件入口，**不改**引擎层 / 模式层。"""
    source = Path(stress_module.__file__).read_text(encoding="utf-8")
    for banned in (
        "from engine",
        "import engine",
        "from modes",
        "import modes",
        "from ..engine",
        "from ..modes",
    ):
        assert banned not in source, f"压测脚本不许直接碰引擎 / 模式层：{banned}"
