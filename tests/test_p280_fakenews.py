# -*- coding: utf-8 -*-
"""P28.0 守卫 ③：**假新闻的「活人感」与情绪爆发**。

任务书原文
----------

> 如果昨天玩家买了一份报纸（新闻），且该新闻是**假的**（数据在插件层记录）。
> 今天开盘时，直接通过 ``push_message`` 给 Yui 发送事实：「玩家昨天买的报纸是假的，
> 导致他亏了钱。你现在非常生气。」Yui 必须在聊天框里爆发生气语音 / 文字，
> 代替玩家去承担被骗的情绪。

三条硬契约
----------

1. **假新闻只记在插件层** —— 引擎层与模式层**零改动**（500 局基线逐字节一致）。
2. **爆发条件三缺一不可** —— 昨天**买过报纸** + 昨天**确有假新闻** +
   昨天**真的亏了钱**。少一条就不许爆发。
3. **不许编造** —— 没亏钱就不许报金额（报金额就是编造）；
   事实与「愤怒素材」分两段推给 Yui，**台词由她自己写**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _fake_news_item(
    *,
    id: str = "n-fake-1",
    day: int = 1,
    stock_id: str | None = "S0",
    content: str = "内部消息：这只会涨停，信我。",
    kind: str = "fake",
):
    from plugins.mock_stock_meow.engine.models import NewsItem

    return NewsItem(
        id=id,
        day=day,
        type=kind,
        stock_id=stock_id,
        content=content,
        accuracy=None,
        direction="bullish",
    )


def _session(plugin: Any):
    """取当前这一局的会话对象。"""
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    return session


# ══════════════════════════════════════════════════════════ ① 纯模块


def test_collect_fake_news_only_picks_the_fake_ones() -> None:
    """只有 ``type == "fake"`` 的才算假新闻。"""
    from plugins.mock_stock_meow.fakenews import collect_fake_news

    items = [
        _fake_news_item(id="n1"),
        _fake_news_item(id="n2", kind="true"),
        _fake_news_item(id="n3", kind="noise"),
        _fake_news_item(id="n4", kind="vague"),
        _fake_news_item(id="n5", kind="thunder"),
    ]
    rows = collect_fake_news(items)
    assert len(rows) == 1
    assert rows[0]["id"] == "n1"
    assert rows[0]["stock_id"] == "S0"
    assert rows[0]["direction"] == "bullish"


def test_collect_fake_news_never_raises() -> None:
    """**整体绝不抛错** —— 坏数据不许把 tick 带崩。"""
    from plugins.mock_stock_meow.fakenews import collect_fake_news

    assert collect_fake_news(None) == []
    assert collect_fake_news(42) == []
    assert collect_fake_news([None, 1, "x"]) == []


def test_the_ledger_records_per_day_and_consumes_once() -> None:
    """账本语义：按天记、同天覆盖、**取走一次就没了**。"""
    from plugins.mock_stock_meow.fakenews import FakeNewsLedger

    ledger = FakeNewsLedger()
    assert ledger.record(1, [_fake_news_item(id="a")]) == 1
    assert ledger.record(1, [_fake_news_item(id="b")]) == 1
    # 同一天是**覆盖**不是累加。
    pending = ledger.pending_for(2)
    assert len(pending) == 1
    assert pending[0]["id"] == "b"

    # 不消费就还在。
    assert ledger.pending_for(2)
    consumed = ledger.consume(2)
    assert len(consumed) == 1
    assert ledger.consume(2) == [], "同一批假新闻不许爆发两次"
    assert ledger.pending_for(2) == []


def test_the_ledger_clear_wipes_everything() -> None:
    from plugins.mock_stock_meow.fakenews import FakeNewsLedger

    ledger = FakeNewsLedger()
    ledger.record(1, [_fake_news_item()])
    ledger.clear()
    assert ledger.pending_for(2) == []


def test_the_fact_reports_the_loss_only_when_there_is_one() -> None:
    """**没亏钱就不许报金额** —— 报金额就是编造。"""
    from plugins.mock_stock_meow.fakenews import build_outrage_fact

    items = [{"id": "n1", "day": 1, "stock_id": "S0", "direction": "bullish", "content": "x"}]
    with_loss = build_outrage_fact(day=2, fake_items=items, loss_amount=88.5)
    assert "88.50" in with_loss
    assert "S0" in with_loss

    without_loss = build_outrage_fact(day=2, fake_items=items, loss_amount=0.0)
    assert "88" not in without_loss
    assert "带偏" in without_loss


def test_the_fact_says_nothing_when_there_is_no_fake_news() -> None:
    from plugins.mock_stock_meow.fakenews import build_outrage_fact

    fact = build_outrage_fact(day=2, fake_items=[], loss_amount=100.0)
    assert "没有出现假新闻" in fact
    assert "100" not in fact


def test_the_opinion_never_carries_numbers_or_names() -> None:
    """愤怒素材**不许**带金额 / 标的名 —— 那是编造。"""
    from plugins.mock_stock_meow.fakenews import OPINION_MATERIALS, build_outrage_opinion

    for level in ("quiet", "normal", "chatty"):
        opinion = build_outrage_opinion(fake_items=[], talk_level=level)
        assert opinion
        assert not re.search(r"\d", opinion), f"{level} 档的素材里出现了数字：{opinion}"
    assert set(OPINION_MATERIALS) == {"quiet", "normal", "chatty"}


def test_the_payload_splits_fact_and_opinion() -> None:
    """事实与素材**分两段** —— 台词由 Yui 自己写。"""
    from plugins.mock_stock_meow.fakenews import build_fake_news_outrage_payload

    payload = build_fake_news_outrage_payload(
        day=2,
        fake_items=[{"id": "n1", "day": 1, "stock_id": "S0", "direction": None, "content": "x"}],
        loss_amount=12.0,
        talk_level="normal",
    )
    assert set(payload) == {"fact", "opinion"}
    assert "12.00" in payload["fact"]
    assert payload["opinion"]


# ══════════════════════════════════════════════════════════ ② 插件接线


def test_the_plugin_records_fake_news_by_the_news_day(plugin) -> None:
    """记账用**新闻自己的天数**（``market.day``），不是玩家的天数。

    两者在日终那一拍会错开；按新闻自己的天数记，「昨天的假新闻」才真的是
    玩家昨天看的那一批。
    """
    run(plugin.join(mode="classic_beginner", seed=8401))
    session = _session(plugin)
    session.market.day = 5
    session.market.news_today = [_fake_news_item(day=5)]
    plugin._record_fake_news(session)
    assert plugin._fake_news_ledger.pending_for(6), "第 5 天的假新闻没被记下来"


def test_the_outrage_needs_all_three_conditions(plugin) -> None:
    """**三缺一不可**：买过报纸 + 确有假新闻 + 真的亏了钱。"""
    run(plugin.join(mode="classic_beginner", seed=8402))
    session = _session(plugin)
    session.player.day = 2
    yesterday = 1

    def _queue_reasons() -> list[str]:
        return [item[1] for item in plugin._yui_pending_prompts]

    # ① 没买过报纸 ⇒ 不爆发。
    plugin._fake_news_ledger.record(yesterday, [_fake_news_item(day=yesterday)])
    plugin._fake_news_loss_by_day[yesterday] = 50.0
    plugin._maybe_fake_news_outrage(session)
    assert "fake_news_outrage" not in _queue_reasons()

    # ② 买了报纸，但**没有假新闻** ⇒ 不爆发。
    plugin._fake_news_bought_days.add(yesterday)
    plugin._fake_news_ledger.record(yesterday, [])
    plugin._maybe_fake_news_outrage(session)
    assert "fake_news_outrage" not in _queue_reasons()

    # ③ 买了报纸、也有假新闻，但**没亏钱** ⇒ 不爆发（报金额就是编造）。
    plugin._fake_news_ledger.record(yesterday, [_fake_news_item(day=yesterday)])
    plugin._fake_news_loss_by_day[yesterday] = 0.0
    plugin._maybe_fake_news_outrage(session)
    assert "fake_news_outrage" not in _queue_reasons()

    # ④ 三条全齐 ⇒ 爆发。
    # （上一轮已经把「昨天那批」消费掉了 ⇒ 这里**重新记一次**。）
    plugin._fake_news_ledger.record(yesterday, [_fake_news_item(day=yesterday)])
    plugin._fake_news_loss_by_day[yesterday] = 88.5
    plugin._maybe_fake_news_outrage(session)
    assert "fake_news_outrage" in _queue_reasons()


def test_the_outrage_prompt_carries_the_fact(plugin) -> None:
    """推给 Yui 的那段**必须带事实**（条数 / 标的 / 确切金额）。"""
    run(plugin.join(mode="classic_beginner", seed=8403))
    session = _session(plugin)
    session.player.day = 2
    plugin._fake_news_bought_days.add(1)
    plugin._fake_news_loss_by_day[1] = 88.5
    plugin._fake_news_ledger.record(1, [_fake_news_item(day=1, stock_id="S0")])
    plugin._maybe_fake_news_outrage(session)

    entries = [item for item in plugin._yui_pending_prompts if item[1] == "fake_news_outrage"]
    assert entries, "没有攒出假新闻爆发"
    prompt = entries[-1][0]
    assert "88.50" in prompt, prompt
    assert "S0" in prompt, prompt
    assert "生气" in prompt or "发作" in prompt, prompt
    # 不许替她把台词写死 —— 只给「素材」与「事实」。
    assert "只准用上面给出的事实" in prompt


def test_the_outrage_is_queued_not_pushed_inline(plugin) -> None:
    """爆发**只入队** —— 由心跳锁外推（持锁 await 宿主 IPC 会冻住事件循环）。"""
    text = _init_text()
    body_start = text.index("def _maybe_fake_news_outrage")
    body = text[body_start : body_start + 6000]
    # P32.0：入队统一走 ``_yui_queue_prompt``（它会记下入队时刻与游戏日，
    # 供推送前的熔断使用）；直接 ``append`` 也仍然算数。
    assert (
        "_yui_pending_prompts.append" in body or "_yui_queue_prompt" in body
    ), "爆发必须入队（不许在结算路径里直接推）"
    assert "push_message" not in body, "爆发不许在结算路径里直接推"


def test_the_outrage_never_fires_on_the_arcade_level(plugin) -> None:
    """**第一关不会触发假新闻愤怒** —— 那一关被兜底成「永远是赚的」。

    亏不了 ⇒ 报不出金额 ⇒ 也就没有愤怒。
    """
    run(plugin.join(mode="classic_arcade", seed=8404))
    session = _session(plugin)
    session.player.day = 2
    plugin._fake_news_bought_days.add(1)
    plugin._fake_news_ledger.record(1, [_fake_news_item(day=1)])
    # 街机档的卖出亏损被兜底成 0 ⇒ 账本里不会有亏损。
    assert 1 not in plugin._fake_news_loss_by_day
    plugin._maybe_fake_news_outrage(session)
    reasons = [item[1] for item in plugin._yui_pending_prompts]
    assert "fake_news_outrage" not in reasons


def test_the_fake_news_module_never_writes_engine_state() -> None:
    """假新闻模块**不许**写引擎状态（纯记账）。"""
    import ast

    tree = ast.parse((_PLUGIN_ROOT / "fakenews.py").read_text(encoding="utf-8"))
    forbidden = {"cash", "heat", "positions", "avg_cost", "alive", "day", "prices"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        targets: list[Any] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in forbidden:
                offenders.append(target.attr)
    assert not offenders, f"假新闻模块不许写引擎状态：{sorted(set(offenders))}"


def test_engine_and_mode_layers_are_untouched() -> None:
    """**红线**：``engine/`` 与 ``modes/`` 一个字节都不许动。"""
    assert engine_mode_code_changes() == [], "引擎层 / 模式层被改动了"


def test_no_new_ai_behavior_literal() -> None:
    """``ai_behavior="respond",`` 的字面量**不许增加**（仓库钉死两处）。"""
    count = _init_text().count('ai_behavior="respond",')
    assert count == 2, count
