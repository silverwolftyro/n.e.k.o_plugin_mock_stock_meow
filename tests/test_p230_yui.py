# -*- coding: utf-8 -*-
"""P23.0 的守卫：**Yui 操控链路** + **大 K 线布局** + **濒危发病** + **耻辱柱**。

这一轮第一次**把账户的钥匙交给 LLM**，所以守卫的重点不是"能不能下单"，
而是"**什么时候不许下单**"。

三条硬契约
----------

1. **引擎层 / 模式层零改动** —— Yui 只能调用插件层**既有**的 ``buy()`` /
   ``sell()``；玩家的单与 Yui 的单走**同一条流水线**（同样扣费、同样算持仓）。
2. **不许死锁** —— ``self._lock`` 是**不可重入**的 ``threading.Lock``，
   所以 ``@llm_tool`` 的处理方法**绝不能**套 ``@_locked``
   （真套上去 ⇒ 与它内部调用的 ``buy()`` 嵌套 ⇒ 死锁）。
3. **三道防线**（缺一不可）：模式闸门 / 授权窗口 / 日配额。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_INIT = _PLUGIN_ROOT / "__init__.py"


# ---------------------------------------------------------------- 工具


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _strip_comments(source: str) -> str:
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _panel_code() -> str:
    source = _strip_comments(_PANEL.read_text(encoding="utf-8"))
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        _, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _PANEL.read_text(encoding="utf-8")
    marker = "const RRM_CSS = `"
    assert marker in source
    _, _, rest = source.partition(marker)
    body, _, _ = rest.partition("`")
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _method_body(text: str, name: str) -> str:
    """取某个方法的源码正文（到下一个同缩进的方法定义为止）。"""
    marker = f"def {name}("
    index = text.find(marker)
    assert index != -1, f"找不到方法 {name}"
    rest = text[index:]
    stops = [pos for pos in (rest.find("\n    def ", 1), rest.find("\n    async def ", 1)) if pos != -1]
    stop = min(stops) if stops else -1
    return rest if stop == -1 else rest[:stop]


def _trade_reasons(plugin: Any) -> list[str]:
    """库里所有成交的 ``reason``（玩家路径 vs Yui 路径）。"""
    rows = run(plugin._repo.fetch_all("SELECT reason FROM trades ORDER BY id ASC"))
    return [str(row["reason"]) for row in rows]


# ══════════════════════════════════════════════════════════════ ① 三道防线


def test_the_default_mode_is_assist(plugin) -> None:
    """默认模式必须是**辅助式**（安全默认值）。

    装上插件的第一秒，Yui **绝不能**自己动玩家的钱。
    """
    assert plugin._yui_current_mode() == "assist"
    payload = run(plugin.market_context())
    assert payload["yui"]["mode"] == "assist"
    assert payload["yui"]["granted"] is False


def test_spectate_mode_refuses_to_trade(plugin) -> None:
    """**纯看戏**：交易工具**硬拒**（提示词管不住模型，工具层才管得住）。"""
    run(plugin.join(mode="classic_arcade", seed=7301))
    run(plugin.yui_mode(mode="spectate"))
    stocks = run(plugin.market_context())["stocks"]
    result = run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=1.0))
    assert result["is_error"] is True
    assert result["output"]["ok"] is False
    assert result["error"]
    # 一笔都不许成交。
    assert _trade_reasons(plugin) == []


def test_assist_mode_needs_an_explicit_grant(plugin) -> None:
    """**辅助式**：玩家**明示**授权之前，交易工具不许放行。"""
    run(plugin.join(mode="classic_arcade", seed=7302))
    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])

    denied = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1))
    assert denied["is_error"] is True
    assert _trade_reasons(plugin) == []

    granted = run(plugin.llm_yui_grant())
    assert granted["is_error"] is False
    assert granted["output"]["ok"] is True

    allowed = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1))
    assert allowed["is_error"] is False, allowed
    assert allowed["output"]["ok"] is True
    assert _trade_reasons(plugin) == ["yui"]


def test_live_mode_trades_without_any_grant(plugin) -> None:
    """**直播托管**：Yui 一站式负责买卖，**不需要**玩家再明示一次。"""
    run(plugin.join(mode="classic_arcade", seed=7303))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    result = run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=0.1))
    assert result["is_error"] is False, result
    assert result["output"]["ok"] is True
    assert _trade_reasons(plugin) == ["yui"]


def test_yui_cannot_escalate_her_own_mode_to_live(plugin) -> None:
    """**Yui 不许给自己升权**。

    直播托管只能由**玩家**（面板按钮）开 —— 否则「模型把「帮忙看看」
    理解成「帮我全仓了」这一步就没有任何硬防线了。
    """
    run(plugin.join(mode="classic_arcade", seed=7304))
    result = run(plugin.llm_yui_mode(mode="live"))
    assert result["is_error"] is True
    assert plugin._yui_current_mode() == "assist"
    # 收紧方向是允许的（玩家说「你别管了」）。
    tightened = run(plugin.llm_yui_mode(mode="spectate"))
    assert tightened["is_error"] is False
    assert plugin._yui_current_mode() == "spectate"


def test_switching_mode_revokes_the_grant(plugin) -> None:
    """换模式 ⇒ **授权立刻作废**（旧模式下的授权不许带到新模式）。"""
    run(plugin.join(mode="classic_arcade", seed=7305))
    run(plugin.llm_yui_grant())
    assert plugin._yui_is_granted() is True
    run(plugin.yui_mode(mode="assist"))
    assert plugin._yui_is_granted() is False


def test_the_daily_quota_blocks_extra_trades(plugin) -> None:
    """**日配额**：今天超过上限 ⇒ 工具**拒绝**（而不是排队等）。

    P27.2：Yui 成交之后会进入**回执周期**（这一回合只许汇报，不许再下单），
    所以每一单之前都要先模拟「玩家又开口了」—— 也就是新的一回合。
    不解开这道锁的话，这里测到的就不是配额，而是回执周期锁了。
    """
    run(plugin.join(mode="classic_arcade", seed=7306))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])
    for index in range(3):
        # 新的一回合（玩家又开口了）⇒ 回执周期锁解开。
        plugin._yui_trade_cycle_unlock()
        result = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.05))
        assert result["is_error"] is False, (index, result)
    plugin._yui_trade_cycle_unlock()
    blocked = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.05))
    assert blocked["is_error"] is True
    assert len(_trade_reasons(plugin)) == 3


# ══════════════════════════════════════════════════════════════ ② 走既有接口


def test_player_trades_keep_the_manual_reason(plugin) -> None:
    """玩家自己点的按钮 ⇒ ``reason`` 仍然是 ``"manual"``（**逐字不变**）。

    这是 P23.0 最重要的一条回归：默认参数保证玩家路径与 P21.5 的
    500 局基线**逐字节全等**。
    """
    run(plugin.join(mode="classic_arcade", seed=7307))
    stocks = run(plugin.market_context())["stocks"]
    run(plugin.buy(str(stocks[0]["id"]), 1.0))
    assert _trade_reasons(plugin) == ["manual"]


def test_yui_trades_go_through_the_same_pipeline(plugin) -> None:
    """Yui 的单与玩家的单**走同一条流水线**（同样扣费、同样算持仓）。"""
    run(plugin.join(mode="classic_arcade", seed=7308))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])
    price = float(stocks[0]["price"])

    before = run(plugin.market_context())
    run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.2))
    after = run(plugin.market_context())

    # 现金真的少了（扣了费），持仓真的多了。
    assert float(after["cash"]) < float(before["cash"])
    positions = {p["stock_id"]: p for p in after["positions"]}
    assert stock_id in positions
    assert float(positions[stock_id]["amount"]) > 0
    assert float(positions[stock_id]["avg_cost"]) >= price


def test_yui_trade_records_a_panel_banner(plugin) -> None:
    """Yui 动过手 ⇒ 面板载荷里有一条**横幅摘要**（序号会变）。"""
    run(plugin.join(mode="classic_arcade", seed=7309))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    assert run(plugin.market_context())["yui"]["seq"] == 0

    run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=0.1))
    yui = run(plugin.market_context())["yui"]
    assert yui["seq"] == 1
    assert yui["last"] is not None
    assert yui["last"]["action"] == "buy"
    assert yui["last"]["stock_id"] == str(stocks[0]["id"])


def test_the_grant_pushes_the_authority_prompt(plugin) -> None:
    """「Prompt 放开」的落点：授权 ⇒ 真的把「你有权动手」告诉 Yui。"""
    from plugins.mock_stock_meow import meow

    assert "{MASTER_NAME}" in meow.YUI_AUTHORITY_PROMPT
    # 静态提示词里不许出现局中情报。
    for banned in ("heat", "seed", "反转", "真假", "surge", "crash"):
        assert banned not in meow.YUI_AUTHORITY_PROMPT.lower(), banned
    # 授权动作必须真的推那段提示词。
    body = _method_body(_init_text(), "llm_yui_grant")
    assert "YUI_AUTHORITY_PROMPT" in body
    assert "_push_proactive" in body


def test_the_story_prompt_mentions_the_authority_when_it_is_real(plugin) -> None:
    """剧情这一路：Yui 真的有权动手时，提示词里才写这件事。"""
    from plugins.mock_stock_meow import story as story_mod

    chapter = story_mod.load_story()["chapters"][0]
    base = {
        "day": 3,
        "cash": 120.0,
        "next_bill": 30.0,
        "initial_cash": 400.0,
        "total_assets": 300.0,
        "loss_ratio": 0.25,
        "trade_count": 4,
    }
    without = story_mod.chapter_prompt(chapter, dict(base))
    assert story_mod.STORY_AUTHORITY_LINE not in without

    with_authority = story_mod.chapter_prompt(chapter, {**base, "yui_may_act": True})
    assert story_mod.STORY_AUTHORITY_LINE in with_authority


# ══════════════════════════════════════════════════════════════ ③ 并发 / 死锁


def test_the_llm_tool_is_never_wrapped_in_the_lock() -> None:
    """``@llm_tool`` 的处理方法**绝不能**套 ``@_locked``。

    ``self._lock`` 是不可重入的 ``threading.Lock`` ——
    真套上去就会与它内部调用的 ``buy()`` 嵌套 ⇒ **死锁**。
    """
    text = _init_text()
    for name in ("llm_yui_trade", "llm_yui_mode", "llm_yui_grant"):
        index = text.find(f"async def {name}(")
        assert index != -1, name
        head = text[:index].rstrip().splitlines()[-3:]
        assert not any("@_locked" in line for line in head), f"{name} 不许套 @_locked"


def test_concurrent_yui_trades_do_not_deadlock(plugin) -> None:
    """并发调用**不卡死、不抛异常**。

    P23.0 的硬要求：Yui 链路不许引入死锁。

    这里刻意**同时**发起 4 笔：插件的全局锁是**不可重入**的
    ``threading.Lock``，而它在 asyncio 的单线程事件循环里被**阻塞式**
    拿到就会把整个循环冻住 —— 所以 Yui 端口必须**非阻塞**拿锁
    （拿不到就回一句"下一拍再来"）。
    """
    import asyncio

    run(plugin.join(mode="classic_arcade", seed=7310))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])

    async def main() -> list[Any]:
        return await asyncio.gather(
            *[
                plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.02)
                for _ in range(4)
            ],
            return_exceptions=True,
        )

    results = asyncio.run(asyncio.wait_for(main(), timeout=30))
    assert len(results) == 4
    ok_count = 0
    busy_count = 0
    for result in results:
        # **绝不抛异常**（异常 = 并发报错 = 宿主会弹红色警告）。
        assert not isinstance(result, BaseException), result
        assert isinstance(result, dict), result
        if result["is_error"] is False:
            ok_count += 1
        else:
            # 被挡下来的必须是**温和的"手上有别的活"**，不是崩溃。
            busy_count += 1
            assert result["error"]
    assert ok_count >= 1, "至少有一笔要真的成交"
    assert ok_count + busy_count == 4
    # 真正成交的那几笔都记成了 Yui 的单。
    assert len(_trade_reasons(plugin)) == ok_count
    assert set(_trade_reasons(plugin)) <= {"yui"}


def test_the_yui_port_never_blocks_the_event_loop(plugin) -> None:
    """锁被占着时，Yui 端口**立刻**返回，绝不阻塞。

    这就是 P11.1 的那条根因（阻塞式 ``acquire()`` 冻住事件循环 ⇒
    宿主那次 5 秒预算的上下文查询被排队 ⇒ 500 红色警告）。
    """
    import time

    run(plugin.join(mode="classic_arcade", seed=7312))
    run(plugin.yui_mode(mode="live"))
    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])

    plugin._lock.acquire()
    try:
        started = time.perf_counter()
        result = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1))
        elapsed = time.perf_counter() - started
    finally:
        plugin._lock.release()

    assert result["is_error"] is True
    assert elapsed < 1.0, f"锁忙时 Yui 端口不该阻塞（实际 {elapsed:.3f}s）"
    assert _trade_reasons(plugin) == []


def test_the_yui_port_takes_the_lock_without_blocking() -> None:
    """源码守卫：Yui 端口必须**非阻塞**拿锁（一个字都不许改成阻塞）。"""
    body = _method_body(_init_text(), "llm_yui_trade")
    assert "blocking=False" in body
    assert "with self._lock" not in body, "Yui 端口不许用 with self._lock（那是阻塞的）"
    assert "self._lock.release()" in body


def test_the_yui_action_never_holds_the_lock_while_trading(plugin) -> None:
    """``buy()`` / ``sell()`` 自己持锁；Yui 端口**只调它们**。"""
    text = _init_text()
    body = _method_body(text, "llm_yui_trade") + _method_body(text, "_yui_trade_locked")
    # Yui 走的是**玩家入口的同一段代码**（真身）。
    assert "await self._buy_impl(" in body
    assert "await self._sell_impl(" in body
    assert 'reason="yui"' in body
    # 而 ``buy()`` / ``sell()`` 确实只是壳（真身在 ``_buy_impl`` / ``_sell_impl``）。
    assert "await self._buy_impl(stock_id, amount, reason)" in _method_body(text, "buy")
    assert "await self._sell_impl(stock_id, amount, reason)" in _method_body(text, "sell")
    # Yui 绝不许自己改数值。
    for banned in ("player.cash =", "player.positions =", "execute_buy(", "execute_sell("):
        assert banned not in body, f"Yui 端口里不许出现 {banned}"


# ══════════════════════════════════════════════════════════════ ④ 耻辱柱


def test_the_shame_column_lists_bankrupt_players_by_day(plugin) -> None:
    """耻辱柱：历史破产记录，**按存活天数升序**（越短越丢人）。"""
    run(plugin.join(mode="classic_arcade", seed=7311))
    run(plugin._repo.fetch_all(
        "SELECT name, day, alive, death_reason, is_catgirl FROM players"
    ))
    # 造两条"破产"记录：一条第 2 天、一条第 5 天。
    run(plugin._repo.transaction([
        (
            "UPDATE players SET alive = 0, day = 2, death_reason = 'starved' "
            "WHERE is_catgirl = 0",
            (),
        ),
        (
            "INSERT INTO players "
            "(id, name, cash, mode, day, alive, initial_cash, trade_count, heat, "
            "total_fee_paid, tokens, strikes, loan_debt, death_reason, is_catgirl, "
            "created_at, updated_at) "
            "VALUES ('p_old', '老王', 0, 'classic_arcade', 5, 0, 100, 1, 0, 0, 0, 0, 0, "
            "'liquidated', 0, 0, 0)",
            (),
        ),
    ]))
    payload = _value(run(plugin.shame()))
    names = [str(row["name"]) for row in payload["rows"]]
    assert "老王" in names
    days = [int(row["day"]) for row in payload["rows"]]
    assert days == sorted(days), "必须按存活天数升序"
    assert payload["rows"][0]["day"] == 2
    # 猫娘（is_catgirl = 1）不许进耻辱柱。
    assert all(str(row["name"]) != "" for row in payload["rows"])


def test_the_shame_column_is_empty_when_nobody_died(plugin) -> None:
    """没人破产 ⇒ 空表（不是报错）。"""
    payload = _value(run(plugin.shame()))
    assert payload["rows"] == []
    assert payload["total"] == 0


# ══════════════════════════════════════════════════════════════ ⑤ 面板结构


def test_the_kline_is_the_main_visual_of_the_left_column() -> None:
    """K 线是**左列的主视觉**（P30.0：重新拉高到 34vh）。

    P23.0 时它是「占掉半个屏幕」的巨幅主视觉。P23.2 改成直播看板布局 ——
    左右分栏（60/40）之后它只占左列，高度必须**让位**给行情方块、
    持仓方块和右列的钱包区，否则整页又要被顶出纵向滚动条。

    P27.1：顶部加了**概览卡**（总资产 / 赚亏 / 现金红线）、K 线下面塞了
    **资产曲线**、底部又多了**三键操作台** ⇒ 纵向预算又紧了，
    K 线高度从 240/35vh/450 收到 **210 / 26vh / 330**。

    P30.0：左右改成 **70/30**，右列的操作件也压了一轮 ⇒ K 线重新拿回高度：
    **240 / 34vh / 460**（深色画布在左列里占满一整块，压迫感回来）。

    P31.0：左列下方的**行情卡**被挤扁了（截图里那排「今天在涨的这些」压成一条，
    巨号涨跌幅直接撑破方块）⇒ K 线**让回**一截高度给行情卡：
    **220 / 30vh / 400**。
    下限仍 ≥ 200px（p72 / p81 的「主视觉不许被砍矮」不变式）。
    """
    code = _panel_code()
    match = re.search(
        r'const KLINE_HEIGHT_CSS = "clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)', code
    )
    assert match, "K 线高度必须是 CSS 表达式"
    assert int(match.group(1)) == 220, "K 线下限必须是 220px"
    assert int(match.group(2)) == 30, "K 线必须占 30vh（一屏锁定布局）"
    assert int(match.group(3)) == 400, "K 线上限必须是 400px"
    # 柱子弹光是**内联** box-shadow（跟着每根柱子的涨跌走）。
    kline = code.split("function Kline(props", 1)[1].split("function ", 1)[0]
    assert "boxShadow:" in kline
    assert "rgba(242, 103, 127" in kline, "涨柱要带珊瑚粉的光晕"
    assert "rgba(53, 191, 160" in kline, "跌柱要带薄荷绿的光晕"


def test_the_trade_buttons_are_compact_but_still_obvious() -> None:
    """P30.0：按钮收到 **48px 一线**（44 / 4.8vh / 48），但配色不许变。

    P23.0 时是巨幅（76px / 11vh / 148px）。P23.2 收回来一次（56px），
    P30.0 又收一次 —— 三键搬进了**右侧 30% 窄列**，再大就把那一列撑爆。
    「掏钱买 / 割肉卖 / 做空」仍要一眼看得见，颜色是它们的命根子。
    """
    css = _panel_css()
    block = css.split(".rrm-root .neko-button.rrm-pill {", 1)[1].split("}", 1)[0]
    assert "height: clamp(44px" in block
    assert "font-size: clamp(18px" in block
    buy = css.split(".rrm-root .neko-button.rrm-pill-buy {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(90deg, #ff9db0 0%, #e8455f 100%)" in buy
    sell = css.split(".rrm-root .neko-button.rrm-pill-sell {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(90deg, #7fe3c8 0%, #23a887 100%)" in sell


def test_the_trade_area_can_be_folded() -> None:
    """操作区**可以折叠**（精调件收起来，两个巨号按钮永远在）。"""
    code = _panel_code()
    assert "tradeFineTuneOpen" in code
    assert "rrm-trade-finetune" in code
    assert "rrm-trade-summary" in code


def test_the_slot_machine_always_rolls() -> None:
    """盈亏数字**每次 tick 都滚**（不再看街机档开关），而且**更快**。"""
    code = _panel_code()
    start = code.index("const [pnlFlash")
    end = code.index("const buyRate =", start)
    body = code[start:end]
    assert "!comboEnabled" not in body, "数字滚动不许再被街机档开关卡住"
    assert "}, 16)" in body, "帧率必须比原来的 40ms 更快"
    assert "const duration = 420" in body
    # 赚闪绿 / 亏闪红。
    assert "pnlFlash" in code
    assert 'glow={pnlFlash}' in code


def test_the_crisis_effects_exist() -> None:
    """濒危发病三件套：抖动 / 躁动红线 / 全屏边缘红光。"""
    code = _panel_code()
    css = _panel_css()
    # ① 资金数字随机抖动（只切 transform）。
    assert "cashShake" in code
    assert "translate(" in code
    # ② 全屏边缘红光（box-shadow: inset，不需要定位）。
    assert ".rrm-root.rrm-crisis-glow" in css
    assert "box-shadow: inset 0 0 160px rgba(214, 72, 106, 0.30)" in css
    assert "min-height: 100vh" in css
    # ③ 危机态的红线深浅红切换。
    assert "gaugeAlarm" in code
    # 光敏性合规上限。
    assert "const CRISIS_GLOW_ON_MS = 180" in code
    assert "const CRISIS_GLOW_CYCLE_MS = 1400" in code


def test_the_shame_column_uses_the_host_datatable() -> None:
    """耻辱柱用宿主**原生** ``DataTable``（不自己画表格 = 不写危险 CSS）。"""
    code = _panel_code()
    assert "DataTable" in code
    assert 'h(DataTable' in code
    assert "rrm-shame-btn" in code
    # 列：名字 / 存活天数 / 破产原因。
    for key in ("panel.shame_col_name", "panel.shame_col_day", "panel.shame_col_reason"):
        assert key in code


def test_the_yui_banner_is_visible_in_the_panel() -> None:
    """Yui 动过手 ⇒ 面板上浮现**专属**的横幅（区别于玩家自己的操作）。"""
    code = _panel_code()
    assert "yuiBanner" in code
    assert "rrm-yui-banner" in code
    assert "panel.yui_banner_title" in code
    # 横幅靠序号（state.yui.seq）触发 —— 面板没有状态订阅。
    assert "yuiSeq" in code
    assert "state.yui" in code


def test_the_panel_does_not_add_dangerous_css() -> None:
    """P23.0 加了一堆新样式 —— 崩溃红线一条都不许破。"""
    css = _panel_css()
    for token in (
        "@keyframes",
        "position",
        "filter",
        "opacity",
        "clip-path",
        "inset:",
        "radial-gradient",
        "z-index",
    ):
        assert token not in css, token
    code = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
        "<div className=",
    ):
        assert token not in code, token


def test_the_css_template_string_is_not_truncated() -> None:
    """``RRM_CSS`` 是反引号模板字符串 —— 里面一个反引号都不许有。"""
    css = _panel_css()
    assert "`" not in css, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    assert "rrm-trade-finetune" in css


# ══════════════════════════════════════════════════════════════ ⑥ 文案 / 红线


def test_the_new_i18n_keys_exist() -> None:
    """P23.0 新增的键都在 i18n 里（文案不许硬编码在代码里）。"""
    table = _i18n()
    for key in (
        "panel.trade_finetune_open",
        "panel.trade_finetune_close",
        "panel.trade_summary",
        "panel.shame_button",
        "panel.shame_title",
        "panel.shame_empty",
        "panel.shame_col_name",
        "panel.shame_col_day",
        "panel.shame_col_reason",
        "panel.yui_banner_title",
        "panel.yui_banner_body",
        "panel.yui_action_buy",
        "panel.yui_action_sell",
        "panel.yui_mode_title",
        "panel.yui_mode_live",
        "panel.yui_mode_assist",
        "panel.yui_mode_spectate",
        "panel.yui_mode_hint",
        "yui.reject.spectate",
        "yui.reject.no_grant",
        "yui.reject.quota",
        "yui.mode.bad",
        "yui.mode.changed",
        "yui.grant.ok",
        "yui.bad_action",
        "yui.bad_ratio",
        "yui.no_amount",
        "yui.trade.done",
        "yui.danmaku",
        "error.bad_yui_mode",
    ):
        assert key in table, f"i18n 缺少 {key}"


def test_the_panel_i18n_keys_all_exist() -> None:
    """面板里每一个**静态** ``t("...")`` 键在 i18n 里都有条目。"""
    table = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _panel_code()))
    missing = sorted(key for key in used if key not in table)
    assert not missing, f"i18n 缺少面板用到的键：{missing}"


def test_the_llm_tools_are_registered() -> None:
    """三个 Yui 工具都在，且名字合法。"""
    text = _init_text()
    names = re.findall(r'@llm_tool\(\s*\n\s*name="([^"]+)"', text)
    for name in (
        "mock_stock_meow_yui_trade",
        "mock_stock_meow_yui_mode",
        "mock_stock_meow_yui_grant",
    ):
        assert name in names, name
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name), name


def test_the_engine_and_mode_layers_are_still_untouched() -> None:
    """P23.0 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


# ────────────────────────────────────────────────────────────────
# P23.0 补充守卫：依赖数组的 TDZ
#
# 官方 ``check-hosted-tsx`` 会用 TypeScript 编译器报
# "Block-scoped variable used before its declaration" ——
# 但它要 node + 联网装 TypeScript，**跑不进本仓库的自测**。
# 这里用 Python 复刻同一条规则：hook 的依赖数组在**渲染期**求值，
# 引用了还没声明的 const ⇒ 运行时 TDZ ⇒ 整个面板白屏。
#
# P23.0 的 Yui 横幅就踩过这个坑（``yuiSeq`` / ``yuiBanner`` 声明在
# 使用之后），静态正则守卫全绿、官方校验才报出来 —— 所以必须留一条
# 不依赖外部工具的自测。
#
# 判定口径与 TypeScript 对齐：
#   * 只有**同一个函数体**里的"后声明"才算数（模块级变量不算 ——
#     组件渲染时模块早就求值完了）；
#   * 函数参数在函数入口就绑定 ⇒ 不算 TDZ；
#   * 兄弟作用域里的同名变量互不相干。
# ────────────────────────────────────────────────────────────────

_CONTROL_KEYWORDS = frozenset(
    {
        "if", "for", "while", "switch", "catch", "with", "return",
        "typeof", "await", "in", "of", "do", "else", "case", "yield",
        "new", "delete", "void", "instanceof",
    }
)


def _blank_quoted(out: list[str], source: str, index: int) -> int:
    quote = source[index]
    length = len(source)
    index += 1
    while index < length:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        out[index] = " "
        index += 1
    return index


def _blank_template(out: list[str], source: str, index: int) -> int:
    length = len(source)
    index += 1
    while index < length:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            return index + 1
        out[index] = " "
        index += 1
    return index


def _blank_literals(source: str) -> str:
    """把字符串 / 模板字符串的**内容**换成空格（长度不变）。

    CSS 模板字符串里有几千个 ``{`` / ``}`` —— 不清掉就没法算作用域；
    保留长度 ⇒ 后面所有偏移量仍然对得上原文。
    """
    out = list(source)
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in "\"'":
            index = _blank_quoted(out, source, index)
            continue
        if char == "`":
            index = _blank_template(out, source, index)
            continue
        index += 1
    return "".join(out)


def _matching_open(source: str, close_index: int) -> int:
    depth = 0
    for index in range(close_index, -1, -1):
        char = source[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _word_before(source: str, index: int) -> str:
    end = index + 1
    start = end
    while start > 0 and (source[start - 1].isalnum() or source[start - 1] in "_$"):
        start -= 1
    return source[start:end]


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return parts


def _pattern_names(pattern: str) -> list[str]:
    """从解构模式里取出**绑定名**（``{ last: l }`` 取 ``l``）。"""
    names: list[str] = []
    for part in _split_top_level(pattern):
        part = part.strip()
        if not part:
            continue
        part = part.split("=")[0].strip()
        if ":" in part:
            part = part.split(":")[1].strip()
        match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", part)
        if match:
            names.append(match.group(0))
    return names


def _param_names(text: str) -> set[str]:
    """参数表里的**绑定名**。

    普通参数 ``props: PanelProps`` ⇒ 绑定名是 ``props``（冒号后面是类型）；
    解构参数 ``{ last: l }`` ⇒ 绑定名是 ``l``（冒号后面是绑定名）。
    """
    names: set[str] = set()
    for part in _split_top_level(text):
        part = part.strip()
        if not part:
            continue
        part = part.split("=")[0].strip()
        if part.startswith("{") or part.startswith("["):
            names.update(_pattern_names(part))
            continue
        base = part.split(":")[0].strip()
        match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", base)
        if match:
            names.add(match.group(0))
    return names


def _scope_header(source: str, brace_index: int) -> tuple[str, set[str]]:
    """判断 ``{`` 是不是**函数体**，并取出参数名。"""
    index = brace_index - 1
    while index >= 0 and source[index].isspace():
        index -= 1
    if index < 0:
        return "block", set()
    # 箭头函数：``(a) => {`` 或 ``a => {``
    if source[index] == ">" and index >= 1 and source[index - 1] == "=":
        cursor = index - 2
        while cursor >= 0 and source[cursor].isspace():
            cursor -= 1
        if cursor >= 0 and source[cursor] == ")":
            open_index = _matching_open(source, cursor)
            if open_index < 0:
                return "block", set()
            return "fn", _param_names(source[open_index + 1 : cursor])
        return "fn", _param_names(source[cursor + 1 : index - 1])
    # 普通函数 / 方法：``function f(a) {`` / ``f(a) {``
    if source[index] == ")":
        open_index = _matching_open(source, index)
        if open_index < 0:
            return "block", set()
        before = open_index - 1
        while before >= 0 and source[before].isspace():
            before -= 1
        if before >= 0 and _word_before(source, before) in _CONTROL_KEYWORDS:
            return "block", set()
        return "fn", _param_names(source[open_index + 1 : index])
    return "block", set()


def _scan_scopes(source: str) -> list[dict]:
    """扫出所有花括号作用域（区间 / 父级 / 函数容器 / 参数名）。"""
    scopes: list[dict] = [
        {"start": 0, "end": len(source), "parent": -1, "fn": -1, "params": set()}
    ]
    stack = [0]
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char == "{":
            kind, params = _scope_header(source, index)
            parent = stack[-1]
            scope = {
                "start": index,
                "end": -1,
                "parent": parent,
                "fn": len(scopes) if kind == "fn" else scopes[parent]["fn"],
                "params": params,
            }
            scopes.append(scope)
            stack.append(len(scopes) - 1)
            index += 1
            continue
        if char == "}":
            if len(stack) > 1:
                scopes[stack.pop()]["end"] = index
            index += 1
            continue
        index += 1
    for scope in scopes:
        if scope["end"] < 0:
            scope["end"] = length
    return scopes


def _innermost_scope(scopes: list[dict], offset: int) -> int:
    best = 0
    for index, scope in enumerate(scopes):
        if scope["start"] <= offset < scope["end"]:
            if scope["start"] >= scopes[best]["start"]:
                best = index
    return best


def _scope_chain(scopes: list[dict], scope_id: int) -> list[int]:
    chain: list[int] = []
    current = scope_id
    while current != -1:
        chain.append(current)
        current = scopes[current]["parent"]
    return chain


def _top_level_arguments(call: str) -> list[str]:
    """按**顶层逗号**切开 hook 调用的实参（嵌套括号里的逗号不算）。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in call:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return parts


def _dependency_references(expression: str) -> list[str]:
    """依赖项里**真正被读取**的标识符。

    - ``pending`` ⇒ ``["pending"]``
    - ``story.pending`` ⇒ ``["story"]``（``pending`` 是属性名，不是变量）
    - ``money(x)`` / ``a ? b : c`` ⇒ ``[]``（读不到裸变量）
    """
    match = re.fullmatch(
        r"([A-Za-z_$][A-Za-z0-9_$]*)"
        r"(?:\s*\?*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)*",
        expression.strip(),
    )
    return [match.group(1)] if match else []


_DECL_RE = re.compile(
    r"\b(?:const|let|var)\s+"
    r"(?:\[(?P<array>[^\]]*)\]|\{(?P<object>[^}]*)\}|(?P<simple>[A-Za-z_$][A-Za-z0-9_$]*))"
)
_HOOK_RE = re.compile(r"\buse(?:Effect|Memo|Callback|LayoutEffect)\s*\(")


def _panel_source() -> str:
    """面板原文（去掉字符串内容，偏移量与原文一一对应）。"""
    return _blank_literals(_PANEL.read_text(encoding="utf-8"))


def test_hook_dependencies_never_reference_a_later_declaration() -> None:
    """依赖数组不许引用**后声明**的变量（TDZ ⇒ 面板白屏）。"""
    source = _panel_source()
    scopes = _scan_scopes(source)

    # 声明归到各自的作用域。
    declarations: dict[int, list[tuple[int, str]]] = {}
    for match in _DECL_RE.finditer(source):
        scope_id = _innermost_scope(scopes, match.start())
        if match.group("array") is not None:
            names = _pattern_names(match.group("array"))
        elif match.group("object") is not None:
            names = _pattern_names(match.group("object"))
        else:
            names = [match.group("simple")]
        bucket = declarations.setdefault(scope_id, [])
        for name in names:
            bucket.append((match.start(), name))

    offenders: list[str] = []
    for hook in _HOOK_RE.finditer(source):
        open_index = hook.end() - 1
        depth = 0
        end_index = -1
        for index in range(open_index, len(source)):
            char = source[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth == 0:
                    end_index = index
                    break
        if end_index < 0:
            continue
        arguments = _top_level_arguments(source[open_index + 1 : end_index])
        if not arguments:
            continue
        deps = arguments[-1].strip()
        if not (deps.startswith("[") and deps.endswith("]")):
            continue

        hook_offset = hook.start()
        chain = _scope_chain(scopes, _innermost_scope(scopes, hook_offset))
        container = scopes[chain[0]]["fn"]
        same_body = [
            scope_id for scope_id in chain if scopes[scope_id]["fn"] == container
        ]
        bound_early: set[str] = set()
        for scope_id in same_body:
            bound_early.update(scopes[scope_id]["params"])
        for offset, name in [
            item for scope_id in same_body for item in declarations.get(scope_id, [])
        ]:
            if offset < hook_offset:
                bound_early.add(name)

        later: dict[str, int] = {}
        for scope_id in same_body:
            for offset, name in declarations.get(scope_id, []):
                if offset > hook_offset and name not in bound_early:
                    if name not in later or offset < later[name]:
                        later[name] = offset

        for token in [
            name
            for part in _split_top_level(deps[1:-1])
            for name in _dependency_references(part)
        ]:
            if token in later:
                line = source.count("\n", 0, later[token]) + 1
                hook_line = source.count("\n", 0, hook_offset) + 1
                offenders.append(
                    f"{token}（第 {line} 行声明，第 {hook_line} 行被依赖数组引用）"
                )
    assert not offenders, "依赖数组引用了后声明的变量（TDZ）：\n" + "\n".join(offenders)
