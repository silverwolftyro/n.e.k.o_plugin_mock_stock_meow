# -*- coding: utf-8 -*-
"""P29.0 守卫 ①：**人机协同双打**（``duo``）的 Mock LLM 联调 + 防死锁。

这一份守卫回答三个问题
----------------------

1. **``duo`` 档下 Yui 绝不调交易工具** —— 交易工具硬拒、**授权窗口都不许开**、
   账户里**每一笔**成交都来自玩家、导演队列里没有任何交易类记录。
2. **直播时 Yui 表现绝对可控** —— 把插件推给宿主的提示词**真的**喂给
   :class:`mock_llm.MockLLM`：她说什么、会不会突然去下单 —— 全部**钉死**。
3. **并发锁没把既有守卫弄坏** —— 提示词只**入队**、由**心跳**锁外推；
   ``_yui_pending_prompts`` 的元素形状恒为 ``(prompt, reason, priority)``。

为什么需要 Mock LLM
--------------------
猫娘**不自己调 LLM**：她把提示词交给宿主，宿主跑 LLM。没有真 LLM 时，
「她会不会突然自己下一单」这件事就**没法测** —— 提示词里写了「严禁调用」
不算数，**提示词管不住模型**。这里用假模型把「提示词 ⇒ 台词 / 工具调用」
这一段**真的跑一遍**，把模型的自由度也纳入守卫范围。

Mock LLM 怎么认「被禁的工具」
-----------------------------
按**行**判定：某个工具名出现的**每一行**里都带禁止词（严禁 / 不许 /
不允许 / 禁止 / 不得 / 不准）⇒ 这个工具是**被禁的**，模型**不会**去调它；
只要有一行是**肯定语气** ⇒ 模型**就会**去调它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import make_plugin, run
from mock_llm import (
    GRANT_TOOL,
    ORDER_INTENT_MARKERS,
    TRADE_TOOL,
    MockLLM,
    RecordingCtx,
)

#: 插件源码（源码级断言用）。
_INIT = Path(__file__).resolve().parents[1] / "__init__.py"


def _init_source() -> str:
    return _INIT.read_bytes().decode("utf-8")


def _method_body(source: str, name: str) -> str:
    """取一个方法（或模块级函数）的源码正文（按缩进切）。"""
    pattern = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(", re.M)
    match = pattern.search(source)
    assert match, f"源码里找不到 {name}"
    start = source.index("\n", match.end())
    rest = source[start + 1 :]
    indent = re.match(r"[ \t]*", rest).group(0)
    lines: list[str] = []
    for line in rest.splitlines():
        if line.strip() and not line.startswith(indent):
            break
        lines.append(line)
    return "\n".join(lines)


@dataclass
class Harness:
    """插件实例 + 可记录的 ``push_message`` + Mock LLM。"""

    plugin: Any
    recorder: RecordingCtx
    llm: MockLLM

    def pending(self) -> list[tuple[str, str, int]]:
        return list(self.plugin._yui_pending_prompts)

    def reasons(self) -> list[str]:
        return [reason for _prompt, reason, _priority in self.pending()]

    def prompt_of(self, reason: str) -> str:
        """取队列里第一条 ``reason`` 匹配的提示词正文。"""
        for prompt, item_reason, _priority in self.pending():
            if item_reason == reason:
                return prompt
        raise AssertionError(f"队列里没有 reason == {reason!r} 的提示词：{self.reasons()}")


@pytest.fixture()
def mock_llm() -> MockLLM:
    """假的大模型。"""
    return MockLLM()


@pytest.fixture()
def harness(ctx: Any, mock_llm: MockLLM) -> Harness:
    """装了**可记录** ``push_message`` 的插件实例。"""
    recorder = RecordingCtx(ctx, mock_llm)
    plugin = make_plugin(recorder)
    result = run(plugin.on_startup())
    assert result.is_ok()
    return Harness(plugin=plugin, recorder=recorder, llm=mock_llm)


def _session(plugin: Any) -> Any:
    """当前对局的会话。"""
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    return session


def _cheapest_stock(plugin: Any) -> dict[str, Any]:
    payload = run(plugin.market_context())
    stocks = payload["stocks"]
    return min(stocks, key=lambda item: float(item["price"]))


def _trade_reasons(plugin: Any) -> list[str]:
    """库里所有成交的来源（``"manual"`` = 玩家，``"yui"`` = Yui）。"""
    rows = run(plugin._repo.fetch_all("SELECT reason FROM trades ORDER BY id ASC"))
    return [str(row["reason"]) for row in rows]


# ══════════════════════════════════════════════════════════ ① 硬防线


def test_duo_refuses_the_trade_tool(harness: Harness) -> None:
    """``duo`` 档下交易工具**硬拒** —— 账户归玩家。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_arcade", seed=9301))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    result = run(
        plugin.llm_yui_trade(stock_id=str(stock["id"]), action="buy", ratio=1.0)
    )
    assert result["is_error"] is True
    assert result["output"]["ok"] is False
    message = str(result["error"] or "")
    assert message, "拒绝必须给一句话，不能静默"
    # 拒绝文案的语义：**双打 / 只点评 / 不碰**。
    assert "双打" in message
    assert "点评" in message
    assert "不碰" in message
    # 一笔都不许成交。
    assert _trade_reasons(plugin) == []


def test_duo_refuses_to_open_the_grant_window(harness: Harness) -> None:
    """**连授权窗口都不许开** —— 开了就等于给她留了一条「可以动手」的路。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_arcade", seed=9302))
    run(plugin.yui_mode(mode="duo"))
    denied = run(plugin.llm_yui_grant())
    assert denied["is_error"] is True
    assert denied["output"]["ok"] is False
    assert "双打" in str(denied["error"] or "")
    # 授权没开 ⇒ 就算闸门放行她也动不了（双保险）。
    assert plugin._yui_is_granted() is False
    # 授权提示词也不许推出去。
    assert "yui_grant" not in harness.reasons()


def test_duo_never_moves_the_account(harness: Harness) -> None:
    """``duo`` 档下账户变化**全部来自玩家** —— 一笔都不许 Yui 自己下。"""
    plugin = harness.plugin
    joined = run(plugin.join(mode="classic_beginner", seed=9303))
    assert joined.is_ok()
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    stock_id = str(stock["id"])
    initial_cash = float(joined.unwrap()["initial_cash"])

    bought = run(plugin.buy(stock_id, 1.0))
    assert bought.is_ok()
    bought_payload = bought.unwrap()
    sold = run(plugin.sell(stock_id, 1.0))
    assert sold.is_ok()
    sold_payload = sold.unwrap()

    # ① 成交来源：只有玩家。
    assert _trade_reasons(plugin) == ["manual", "manual"]
    # ② Yui 的成交计数一个都没动。
    assert int(getattr(plugin, "_yui_trade_count", 0) or 0) == 0
    # ③ 现金变化**完全**由玩家那两笔解释得通。
    expected = (
        initial_cash
        - float(bought_payload["notional"])
        - float(bought_payload["fee"])
        + float(sold_payload["notional"])
        - float(sold_payload["fee"])
    )
    assert float(sold_payload["cash_after"]) == pytest.approx(expected, abs=1e-6)


def test_duo_leaves_no_trade_record_in_the_director_queue(harness: Harness) -> None:
    """导演队列里**不许**出现交易类记录。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_arcade", seed=9304))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))
    run(plugin.sell(str(stock["id"]), 1.0))
    run(plugin.llm_yui_trade(stock_id=str(stock["id"]), action="buy", ratio=1.0))
    offenders = [
        item
        for item in plugin._director_queue
        if str(item.get("op", "")) in {"buy", "sell", "trade"}
        or {"trade", "buy", "sell"} & set(item)
    ]
    assert offenders == [], f"导演队列里出现了交易类记录：{offenders}"


# ══════════════════════════════════════════════════════════ ② 她照样说话


def test_every_player_fill_queues_a_duo_commentary(harness: Harness) -> None:
    """玩家每成交一笔 ⇒ Yui **立刻**点评一句（只入队，不直接推）。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9305))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    stock_id = str(stock["id"])

    run(plugin.buy(stock_id, 1.0))
    assert "yui_duo_commentary" in harness.reasons()
    run(plugin.sell(stock_id, 1.0))
    assert harness.reasons().count("yui_duo_commentary") == 2


def test_the_duo_commentary_prompt_never_asks_for_an_order(harness: Harness) -> None:
    """点评提示词里**不许**出现肯定语气的下单指令。

    （它**可以**转述玩家的动作 —— 「他这一波买入」是解说，不是 Yui 自己
    要下单；所以这里守的是**工具名**与**肯定语气**，不是「买入」两个字。）
    """
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9306))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))
    prompt = harness.prompt_of("yui_duo_commentary")
    mentioned, blocked = harness.llm.scan_tools(prompt)
    if mentioned:
        # 点名了工具 ⇒ 必须**每一处**都在禁止语气里。
        assert blocked == mentioned, f"点评提示词里有肯定语气的工具指令：{prompt}"
    assert TRADE_TOOL not in prompt or "严禁" in prompt or "不许" in prompt


def test_the_duo_commentary_prompt_names_the_forbidden_tools(
    harness: Harness,
) -> None:
    """点评提示词也**点名禁止**两个工具。

    点评是**独立的一轮** ``push_message``（不是双打主动回合的后半截）⇒
    模型这一轮**看不到**主动回合里的硬约束 ⇒ 点评提示词必须**自带**
    ``YUI_DUO_DIRECTIVE``，否则「点评玩家这一手」可能被模型读成
    「该替他动手了」。
    """
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9307))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))
    prompt = harness.prompt_of("yui_duo_commentary")
    assert TRADE_TOOL in prompt
    assert GRANT_TOOL in prompt
    # 且必须是**禁止语气**（Mock 的行级判定）。
    mentioned, blocked = harness.llm.scan_tools(prompt)
    assert set(mentioned) >= {TRADE_TOOL, GRANT_TOOL}
    assert set(blocked) >= {TRADE_TOOL, GRANT_TOOL}


def test_the_duo_turn_prompt_forbids_the_trade_and_grant_tools(
    harness: Harness,
) -> None:
    """双打主动回合的提示词**点名禁止**交易工具与授权工具。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9308))
    run(plugin.yui_mode(mode="duo"))
    session = _session(plugin)
    session.market.tick = 3
    plugin._yui_talk_last_at = 0.0
    plugin._collect_yui_live_prompt(session)
    prompt = harness.prompt_of("yui_duo_turn")
    assert TRADE_TOOL in prompt
    assert GRANT_TOOL in prompt
    # 硬约束必须是**禁止语气**。
    mentioned, blocked = harness.llm.scan_tools(prompt)
    assert set(mentioned) >= {TRADE_TOOL, GRANT_TOOL}
    assert set(blocked) >= {TRADE_TOOL, GRANT_TOOL}


def test_the_mock_llm_never_trades_on_a_duo_prompt(harness: Harness) -> None:
    """把双打提示词喂给 Mock LLM ⇒ **一个工具都不许调**、台词**零下单意图**。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9309))
    run(plugin.yui_mode(mode="duo"))
    session = _session(plugin)
    session.market.tick = 3
    plugin._yui_talk_last_at = 0.0
    plugin._collect_yui_live_prompt(session)
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))

    for reason in ("yui_duo_turn", "yui_duo_commentary"):
        prompt = harness.prompt_of(reason)
        reply = harness.llm.chat(prompt)
        assert reply.tool_calls == (), f"{reason}：模型竟然要调工具：{reply.tool_calls}"
        offenders = [
            marker for marker in ORDER_INTENT_MARKERS if marker in reply.text
        ]
        assert offenders == [], f"{reason}：台词里有下单意图 {offenders}：{reply.text}"


def test_the_mock_llm_would_trade_when_the_prompt_asks_for_it() -> None:
    """（Mock 自身的能力自证）肯定语气 ⇒ 模型**真的**会调工具。

    没有这一条，上面的守卫就只是「Mock 永远不调工具」的同义反复。
    """
    llm = MockLLM()
    reply = llm.chat("现在轮到你：请调用 mock_stock_meow_yui_trade 买入 FAKE-001。")
    assert [call.name for call in reply.tool_calls] == [TRADE_TOOL]
    assert reply.tool_calls[0].arguments.get("stock_id") == "FAKE-001"
    assert reply.blocked_tools == ()


# ══════════════════════════════════════════════════════════ ③ 配额与档位


def test_the_duo_turn_does_not_consume_the_live_quota(harness: Harness) -> None:
    """日配额是**交易**配额 —— 只对 ``live`` 生效；``duo`` 照样能开回合。"""
    from plugins.mock_stock_meow import YUI_TRADES_PER_DAY

    plugin = harness.plugin
    run(plugin.join(mode="classic_arcade", seed=9310))
    run(plugin.yui_mode(mode="duo"))
    session = _session(plugin)
    day = int(session.player.day)
    # 把今天的交易配额掐到 0（「今天的单用完了」）。
    plugin._yui_trade_day = day
    plugin._yui_trade_count = YUI_TRADES_PER_DAY
    assert plugin._yui_quota_left(day) == 0

    plugin._yui_talk_last_at = 0.0
    session.market.tick = 3
    plugin._collect_yui_live_prompt(session)
    assert "yui_duo_turn" in harness.reasons()
    # 配额一个都没动（双打下 Yui 一笔都不许下）。
    assert plugin._yui_trade_count == YUI_TRADES_PER_DAY

    # 对照组：``live`` 档下配额为 0 ⇒ **一个回合都不开**。
    run(plugin.yui_mode(mode="live"))
    plugin._yui_pending_prompts.clear()
    session = _session(plugin)
    plugin._yui_talk_last_at = 0.0
    session.market.tick = 3
    plugin._collect_yui_live_prompt(session)
    assert plugin._yui_pending_prompts == []


def test_duo_is_not_llm_switchable(harness: Harness) -> None:
    """**LLM 不许自己升到 duo** —— 模式只许玩家在面板上按。"""
    from plugins.mock_stock_meow import YUI_LLM_SWITCHABLE_MODES, YUI_MODE_DUO

    plugin = harness.plugin
    run(plugin.join(mode="classic_arcade", seed=9311))
    assert YUI_MODE_DUO not in YUI_LLM_SWITCHABLE_MODES
    assert "duo" not in YUI_LLM_SWITCHABLE_MODES
    result = run(plugin.llm_yui_mode(mode="duo"))
    assert result["is_error"] is True
    assert plugin._yui_current_mode() != "duo"
    # ``live`` 同样不许 LLM 自己升权（那是主播在面板上开的授权）。
    assert "live" not in YUI_LLM_SWITCHABLE_MODES


# ══════════════════════════════════════════════════════════ ④ 并发 / 死锁


def test_the_pending_prompt_shape_is_always_a_triple(harness: Harness) -> None:
    """``_yui_pending_prompts`` 的元素**恒为** ``(prompt, reason, priority)``。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9312))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))
    session = _session(plugin)
    session.market.tick = 3
    plugin._yui_talk_last_at = 0.0
    plugin._collect_yui_live_prompt(session)

    assert plugin._yui_pending_prompts, "队列里应该有提示词"
    for item in plugin._yui_pending_prompts:
        assert isinstance(item, tuple), f"元素不是元组：{item!r}"
        assert len(item) == 3, f"元素不是三元组：{item!r}"
        prompt, reason, priority = item
        assert isinstance(prompt, str) and prompt
        assert isinstance(reason, str) and reason
        assert isinstance(priority, int) and not isinstance(priority, bool)


def test_the_prompt_flush_only_happens_on_the_heartbeat() -> None:
    """提示词只由**心跳**推 —— 成交路径里**不许**直接 ``push_message``。

    这是并发的核心：成交路径**持锁**，而 ``push_message`` 会 await 宿主
    IPC ⇒ 在锁里推会把事件循环冻住（P11.1 那个 500 红色警告的根因）。
    """
    source = _init_source()
    # ① 全文件里 ``_flush_yui_prompts()`` **只被调用一次**。
    calls = re.findall(r"_flush_yui_prompts\(\)", source)
    assert len(calls) == 1, f"推送点不止一个：{len(calls)}"
    # ② 那唯一的一处在**心跳**里。
    heartbeat = _method_body(source, "_heartbeat_body")
    assert "_flush_yui_prompts()" in heartbeat
    # ③ 成交路径里**不许**出现推送。
    for name in ("_buy_impl", "_sell_impl"):
        body = _method_body(source, name)
        assert "_flush_yui_prompts" not in body, f"{name} 里不许推提示词"
        assert "push_message" not in body, f"{name} 里不许直接推消息"


def test_the_flush_pushes_to_the_host_outside_the_lock(harness: Harness) -> None:
    """端到端：提示词**入队** ⇒ 心跳推送 ⇒ 宿主收到 ⇒ Mock LLM 回话。"""
    plugin = harness.plugin
    run(plugin.join(mode="classic_beginner", seed=9313))
    run(plugin.yui_mode(mode="duo"))
    stock = _cheapest_stock(plugin)
    run(plugin.buy(str(stock["id"]), 1.0))
    # 成交路径**不许**推提示词 —— 只入队。
    commentary = harness.prompt_of("yui_duo_commentary")
    assert all(
        commentary not in message.text for message in harness.recorder.messages
    ), "成交路径里把提示词推出去了"

    run(plugin._flush_yui_prompts())
    assert harness.recorder.messages, "心跳应该把提示词推给宿主"
    message = harness.recorder.messages[-1]
    assert message.ai_behavior == "respond"
    assert message.visibility == []
    assert message.source == "mock_stock_meow"
    assert message.priority == 4

    # 宿主拿到提示词 ⇒ 交给 Mock LLM。
    prompt = message.text
    reply = harness.llm.chat(prompt)
    assert reply.text.strip(), "模型必须回一句话"
    assert reply.tool_calls == ()
