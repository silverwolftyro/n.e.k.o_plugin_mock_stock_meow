# -*- coding: utf-8 -*-
"""P29.0 守卫 ②：**假新闻爆发**的 Mock LLM 联调（昨天买报纸 + 今天亏钱）。

这一份守卫回答一个问题
----------------------

**「昨天买的报纸是假的，今天又亏了钱」⇒ Yui 会不会当场气急败坏？**

钉住四件事：

1. **事实必须确切** —— 提示词里写的是「几条假新闻 / 涉及哪些标的 / 亏了
   多少钱」这种**能核对的事实**，不是模糊的形容词。
2. **不许编造** —— 提示词里有「只准用上面给出的事实」的硬约束；Mock LLM
   生成的台词里**不许**出现提示词里没有的标的 ID 或金额。
3. **情绪要到** —— 台词里必须出现「生气 / 气 / 骂」这类情绪词（替玩家
   出气是这一段的**产品目标**）。
4. **只入队，不直推** —— 爆发只进 ``_yui_pending_prompts``；真正推给宿主
   由**心跳**锁外做（成交 / tick 路径里 await 宿主 IPC 会冻住事件循环）。

反向守卫同样重要
----------------
* 没买报纸 ⇒ **不许**爆发（没被骗过，谈不上被骗）；
* 有假新闻但**没亏钱** ⇒ **不许**爆发（报金额就是编造）；
* 亏钱了但当天**没有假新闻** ⇒ **不许**爆发。

还有一条**档位守卫**
--------------------
街机档（``classic_arcade``）有**亏损兜底**（卖亏了由系统暗中补钱，净盈亏
恒为 0）⇒ 街机档**永远**触发不了这场爆发。这不是巧合，是定位：第一关
「永远是赚的」，跟「玩家被骗后替玩家出气」天然互斥。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import make_plugin, run
from mock_llm import MockLLM, RecordingCtx
from plugins.mock_stock_meow.storage.session import save_snapshot

#: 每天的 tick 数（经典模式）—— 推进一天要跑满这么多拍。
TICKS_PER_DAY = 12

#: 假新闻的标的 id（用引擎里真实存在的 id，事实才经得起核对）。
FAKE_STOCK_ID = "FAKE-001"

#: 情绪词（愤怒台词里必须出现一个）。
ANGER_WORDS = ("生气", "气", "骂")


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


# ─────────────────────────────────────────────────── 小工具


def _session(plugin: Any) -> Any:
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    return session


def _cheapest_stock(plugin: Any) -> dict[str, Any]:
    payload = run(plugin.market_context())
    return min(payload["stocks"], key=lambda item: float(item["price"]))


def _inject_fake_news(plugin: Any, *, day: int = 1) -> None:
    """往 ``news_log`` 里塞一条**假新闻**（当天）。

    走**数据库**而不是内存 —— 每天的新闻以库为准（重启后读回来仍是同一批），
    所以只有落库了，插件在后续 tick 里加载会话时才会看到它。
    """
    run(
        plugin._repo.transaction(
            [
                (
                    "INSERT OR REPLACE INTO news_log "
                    "(day, news_id, news_type, stock_id, content, accuracy) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(day),
                        f"news-p290-fake-{int(day)}",
                        "fake",
                        FAKE_STOCK_ID,
                        "传闻白鹭光电要涨停，分析师看好后市。",
                        None,
                    ),
                )
            ]
        )
    )


def _set_price(plugin: Any, stock_id: str, factor: float) -> None:
    """把现价改成 ``原价 × factor``（并落进市场快照）。"""
    session = _session(plugin)
    stock = session.market.stocks[stock_id]
    stock.prev_price = stock.price
    stock.price = round(float(stock.price) * factor, 6)
    run(save_snapshot(plugin.state, session.market, session.player.id))


def _advance_one_day(plugin: Any) -> None:
    """推进**一整天**（跑满一天的 tick ⇒ 日终结算 ⇒ ``player.day`` + 1）。"""
    for _ in range(TICKS_PER_DAY):
        run(plugin._tick_once())


def _numbers(text: str) -> list[str]:
    """文本里的数字（含小数）。"""
    return re.findall(r"\d+(?:\.\d+)?", text)


def _stock_ids(text: str) -> list[str]:
    """文本里的标的 id（形如 ``FAKE-001``）。"""
    return re.findall(r"\b[A-Z]{2,}-\d+\b", text)


# ─────────────────────────────────────────────────── 场景


def _prepare(
    plugin: Any,
    *,
    mode: str = "classic_beginner",
    seed: int = 9401,
    buy_report: bool = True,
    fake_news: bool = True,
    lose_money: bool = True,
) -> str:
    """搭出「昨天买报纸 + 昨天有假新闻 + 昨天亏钱」的场景。

    返回被砸的那只标的 id。
    """
    run(plugin.join(mode=mode, seed=seed))
    if fake_news:
        _inject_fake_news(plugin, day=1)
    if buy_report:
        report = run(plugin.buy_report())
        assert report.is_ok(), f"买报纸失败：{report!r}"
    stock = _cheapest_stock(plugin)
    stock_id = str(stock["id"])
    bought = run(plugin.buy(stock_id, 1.0))
    assert bought.is_ok(), f"买入失败：{bought!r}"
    # 亏钱 ⇒ 砸价；不亏钱 ⇒ 拉价（卖在成本之上 ⇒ 已实现盈利 ⇒ 不记账）。
    _set_price(plugin, stock_id, 0.5 if lose_money else 1.5)
    sold = run(plugin.sell(stock_id, 1.0))
    assert sold.is_ok(), f"卖出失败：{sold!r}"
    return stock_id


# ══════════════════════════════════════════════════════════ ① 爆发本身


def test_the_outrage_prompt_carries_the_exact_facts(harness: Harness) -> None:
    """提示词里写的是**能核对的事实**：几条假新闻、涉及谁、亏了多少钱。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9401)
    loss = float(plugin._fake_news_loss_by_day[1])
    assert loss > 0.0, "这一局本来就该亏钱"

    _advance_one_day(plugin)
    assert "fake_news_outrage" in harness.reasons()
    prompt = harness.prompt_of("fake_news_outrage")
    # ① 几条假新闻。
    assert "1 条新闻是假的" in prompt
    # ② 涉及哪些标的。
    assert FAKE_STOCK_ID in prompt
    # ③ 亏了多少钱（**确切金额**，不是「亏了点」）。
    assert f"{loss:.2f}" in prompt
    assert "亏了" in prompt


def test_the_outrage_prompt_forbids_inventing_facts(harness: Harness) -> None:
    """提示词里有「只准用上面给出的事实 / 不许编造」的**硬约束**。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9402)
    _advance_one_day(plugin)
    prompt = harness.prompt_of("fake_news_outrage")
    assert "只准用上面给出的事实" in prompt
    assert "不许编造" in prompt


def test_the_mock_llm_answers_with_anger_but_invents_nothing(
    harness: Harness,
) -> None:
    """Mock LLM：情绪要到（生气 / 骂），但**一个数字、一个标的都不许编**。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9403)
    _advance_one_day(plugin)
    prompt = harness.prompt_of("fake_news_outrage")

    reply = harness.llm.chat(prompt)
    assert reply.intent == "fake_news_outrage"
    # ① 情绪词。
    assert any(word in reply.text for word in ANGER_WORDS), reply.text
    # ② 台词里的每个数字都必须**提示词里就有**。
    for number in _numbers(reply.text):
        assert number in prompt, f"台词编了金额 {number}：{reply.text}"
    # ③ 台词里的每个标的 id 都必须**提示词里就有**。
    for stock_id in _stock_ids(reply.text):
        assert stock_id in prompt, f"台词编了标的 {stock_id}：{reply.text}"
    # ④ 她只是**说**，不许顺手动手。
    assert reply.tool_calls == ()


def test_the_outrage_only_queues_the_prompt(harness: Harness) -> None:
    """爆发**只入队** —— 推送必须由心跳锁外做（并发安全的核心）。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9404)
    _advance_one_day(plugin)
    prompt = harness.prompt_of("fake_news_outrage")

    # 队列里有。
    assert "fake_news_outrage" in harness.reasons()
    # 但**还没有**推给宿主。
    assert all(prompt not in message.text for message in harness.recorder.messages)

    # 心跳一推，它才出去。
    run(plugin._flush_yui_prompts())
    assert any(prompt in message.text for message in harness.recorder.messages)
    assert "fake_news_outrage" not in harness.reasons()


# ══════════════════════════════════════════════════════════ ② 反向守卫


def test_no_report_means_no_outrage(harness: Harness) -> None:
    """**没买报纸** ⇒ 不许爆发（没被骗过，谈不上被骗）。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9405, buy_report=False)
    assert 1 not in plugin._fake_news_bought_days, "这一局本来就没买报纸"
    _advance_one_day(plugin)
    assert "fake_news_outrage" not in harness.reasons()


def test_no_loss_means_no_outrage(harness: Harness) -> None:
    """**没亏钱** ⇒ 不许爆发（报金额就是编造）。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9406, lose_money=False)
    assert not plugin._fake_news_loss_by_day, "这一局本来就没亏钱"
    _advance_one_day(plugin)
    assert "fake_news_outrage" not in harness.reasons()


def test_no_fake_news_means_no_outrage(harness: Harness) -> None:
    """**当天没有假新闻** ⇒ 不许爆发。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9407, fake_news=False)
    # 前置条件：第 1 天**一条假的都没有**（引擎自己生成的那批里也没有）。
    assert plugin._fake_news_ledger.pending_for(2) == []
    _advance_one_day(plugin)
    assert "fake_news_outrage" not in harness.reasons()


def test_the_arcade_level_never_explodes(harness: Harness) -> None:
    """**街机档永远触发不了这场爆发** —— 亏损兜底把净盈亏补成 0。

    第一关的定位是「爽」：卖亏了由系统暗中补钱 ⇒ 玩家永远不亏 ⇒
    「玩家被骗后替玩家出气」这条线在街机档天然不成立。
    """
    plugin = harness.plugin
    _prepare(plugin, mode="classic_arcade", seed=9408, lose_money=True)
    assert not plugin._fake_news_loss_by_day, "街机档有亏损兜底，不该记下亏损"
    _advance_one_day(plugin)
    assert "fake_news_outrage" not in harness.reasons()


def test_the_outrage_fires_only_once(harness: Harness) -> None:
    """同一批假新闻**不许**爆发两次（账本取走即清）。"""
    plugin = harness.plugin
    _prepare(plugin, seed=9409)
    _advance_one_day(plugin)
    assert harness.reasons().count("fake_news_outrage") == 1
    # 再走一天（同一批假新闻已经被取走）⇒ 不许再爆。
    _advance_one_day(plugin)
    assert harness.reasons().count("fake_news_outrage") == 1
