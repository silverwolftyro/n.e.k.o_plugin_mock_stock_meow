# -*- coding: utf-8 -*-
"""P28.0 守卫 ①：**第一关（街机档）极度降智与大砍**。

三条硬契约
----------

1. **引擎层 / 模式层零改动** —— 第一关的每一项「降智」都必须是**插件层**
   的运行时干预（配置覆写 + 条件拦截），否则 500 局基线就不可能逐字节一致。
2. **「上头即暴跌」在第一关永不触发** —— ``heat.peak_threshold`` 被抬到
   1e9，引擎的 ``is_at_peak`` 在任何现实局里都够不到。
3. **玩家在第一关永远不会亏** —— 卖出若亏了，系统**暗中补钱**，
   补完那笔的净盈亏恒为 0 ⇒ 现钱回到出发时的水平。

面板那一半（做空 / 杠杆 / 台账 / 水位条 / 账单 Alert / 连击徽章全部隐藏）
由本文件末尾的 ``test_arcade_ui_*`` 守着。

教程那一半（街机档不许把教程圈在**已经藏起来**的东西上）由
``test_arcade_tutorial_*`` 守着。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"

#: 街机档**看得见**的教程锚点全集（其余一律不许被教程指到）。
ARCADE_VISIBLE_ANCHORS = frozenset({"market_grid", "trade_console", "overview"})


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _cheapest_stock(payload: dict[str, Any]) -> dict[str, Any]:
    stocks = payload["stocks"]
    return min(stocks, key=lambda item: float(item["price"]))


def _guarded(panel: str, marker: str) -> bool:
    """某个 JSX 块是不是被 ``arcadeSimple`` 条件包住了（街机档隐藏）。

    **必须遍历全部出现位置** —— 同一个 className 常常同时出现在
    CSS 里和 JSX 里；只看第一处会命中 CSS 的那处（误判成「没藏」）。
    只要**有一处**出现在 ``arcadeSimple ? null`` 之后就算藏住了。
    """
    index = panel.find(marker)
    if index == -1:
        return False
    while index != -1:
        window = panel[max(0, index - 400) : index]
        if "arcadeSimple ? null" in window:
            return True
        index = panel.find(marker, index + 1)
    return False


# ══════════════════════════════════════════════════════════ ① 上头即暴跌失效


def test_arcade_peak_threshold_is_pushed_out_of_reach(plugin) -> None:
    """第一关：``heat.peak_threshold`` 被抬到 1e9 ⇒ 峰值永不成立。"""
    from plugins.mock_stock_meow import (
        ARCADE_PEAK_THRESHOLD,
        MODE_ARCADE,
        _config_for_player,
    )
    from plugins.mock_stock_meow.engine.config import EngineConfig

    base = EngineConfig()
    assert base.heat.peak_threshold == 40.0, "引擎默认值不许动"

    arcade = _config_for_player("路人甲", MODE_ARCADE, base)
    assert arcade.heat.peak_threshold == ARCADE_PEAK_THRESHOLD
    assert arcade.heat.peak_threshold >= 1.0e9

    # 后面几档**不受影响** —— 压迫感是后面几档的立身之本。
    for mode in ("classic_beginner", "classic_advanced", "endless"):
        other = _config_for_player("路人甲", mode, base)
        assert other.heat.peak_threshold == base.heat.peak_threshold, mode


def test_arcade_softening_does_not_touch_the_news_ratios() -> None:
    """第一关的降智**只**动峰值阈值，别的手伸得太长。

    查的是 ``_apply_arcade_softening`` 这个**专项降智函数**本身 ——
    ``_config_for_player`` 里还叠着 P16.0 的新手期放水（那不是降智，
    是所有档位都有的新手福利），不能拿它来比。
    """
    from plugins.mock_stock_meow import MODE_ARCADE, _apply_arcade_softening
    from plugins.mock_stock_meow.engine.config import EngineConfig

    base = EngineConfig()
    softened = _apply_arcade_softening(MODE_ARCADE, base)
    assert softened.news == base.news, "降智不许动新闻比例"
    assert softened.heat.peak_threshold != base.heat.peak_threshold

    # 别的档位**原样返回**（连副本都不该有）。
    for mode in ("classic_beginner", "classic_advanced", "classic_hell", "infinite"):
        assert _apply_arcade_softening(mode, base) is base, mode


def test_engine_and_mode_layers_are_untouched() -> None:
    """**红线**：``engine/`` 与 ``modes/`` 一个字节都不许动。"""
    assert engine_mode_code_changes() == [], "引擎层 / 模式层被改动了"


# ══════════════════════════════════════════════════════════ ② 卖亏了补钱


def test_arcade_player_never_loses_on_a_round_trip(plugin) -> None:
    """第一关：买完立刻卖 ⇒ 现钱回到出发时的水平（**不亏**）。"""
    joined = _value(run(plugin.join(mode="classic_arcade", seed=8101)))
    market = run(plugin.market_context())
    stock = _cheapest_stock(market)

    _value(run(plugin.buy(stock["id"], 1.0)))
    sold = _value(run(plugin.sell(stock["id"], 1.0)))
    assert sold["cash_after"] == pytest.approx(joined["initial_cash"], abs=1e-6)
    assert sold["position"] is None


def test_non_arcade_keeps_raw_fee_math(plugin) -> None:
    """**别的档位不受影响**：手续费照扣，亏损照算。"""
    joined = _value(run(plugin.join(mode="classic_beginner", seed=8102)))
    market = run(plugin.market_context())
    stock = _cheapest_stock(market)
    price = float(stock["price"])
    amount = 1.0

    _value(run(plugin.buy(stock["id"], amount)))
    sold = _value(run(plugin.sell(stock["id"], amount)))
    notional = price * amount
    expected_fee = notional * (0.005 + 0.001)
    assert sold["cash_after"] == pytest.approx(
        joined["initial_cash"] - notional * 0.003 - expected_fee, abs=1e-6
    )


def test_arcade_black_swan_never_fires(plugin) -> None:
    """第一关：黑天鹅（暴跌）**一律不触发**。"""
    run(plugin.join(mode="classic_arcade", seed=8103))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    # 赚到钱的那一刻（本来正是黑天鹅的触发点）也不排暴跌。
    run(plugin._maybe_force_black_swan(session, 999999.0, "S0"))
    assert plugin._director_queue == []


def test_black_swan_still_fires_in_other_modes(plugin) -> None:
    """**别的档位黑天鹅照常** —— 第一关的豁免不许外溢。"""
    run(plugin.join(mode="classic_beginner", seed=8106))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    # 把导演开关打开 —— 否则这道闸门本来就是关着的（测不到东西）。
    plugin._director_enabled_cache = True
    plugin._business_config_cache = {"director": {"force_black_swan": True}}
    # 造一笔持仓 ⇒ 黑天鹅才有东西可砸。
    session.player.positions["S0"] = _fake_position()
    run(plugin._maybe_force_black_swan(session, 999999.0, "S0"))
    assert plugin._director_queue, "别的档位的黑天鹅被第一关的豁免误伤了"


def _fake_position() -> Any:
    from plugins.mock_stock_meow.engine.models import Position

    return Position(stock_id="S0", amount=1.0, avg_cost=1.0)


# ══════════════════════════════════════════════════════════ ③ 载荷与面板


def test_arcade_payload_flag_is_true(plugin) -> None:
    """街机档的载荷里 ``arcade_simple`` 为 true。"""
    run(plugin.join(mode="classic_arcade", seed=8104))
    playing = run(plugin.market_context())
    assert playing["arcade_simple"] is True


def test_non_arcade_payload_flag_is_false(plugin) -> None:
    """别的档位 ``arcade_simple`` 为 false。"""
    run(plugin.join(mode="classic_beginner", seed=8105))
    playing = run(plugin.market_context())
    assert playing["arcade_simple"] is False


def test_arcade_ui_hides_the_heavy_widgets() -> None:
    """街机档：做空 / 杠杆 / 台账 / 水位条 / 账单 / 连击徽章**全部隐藏**。"""
    panel = _panel_text()
    # 契约行：前端与后端的判定口径一致（载荷缺 mode 时靠 arcade_simple 兜底）。
    assert (
        "const arcadeSimple = minimalUi || Boolean((state as any).arcade_simple)"
        in panel
    )
    # 根节点挂上 ``rrm-arcade-simple`` ⇒ CSS 能改列数（两键仍是一整行）。
    assert "rrm-arcade-simple" in panel
    # 逐个重元件都**必须**被 ``arcadeSimple`` 条件包住。
    for marker in (
        "rrm-anchor-position-grid",  # 持仓明细卡（多标的台账那一块）
        "rrm-short-panel",  # 杠杆 / 标的 / 保证金输入条
        "rrm-pill-short",  # 【做空】按钮
        "rrm-pill-caption-short",  # 【做空】副标题
        't("panel.short_title")',  # 【更多】里的做空持仓卡
    ):
        assert marker in panel, marker
        assert _guarded(panel, marker), f"{marker} 没有被 arcadeSimple 挡住"
    # 猫娘公开状态卡也藏起来。
    assert "catgirl && !arcadeSimple" in panel


def test_arcade_ui_hides_the_flag_driven_widgets() -> None:
    """靠**开关**藏的那几件：水位条 / 连击 / 集中度 / 台账 / 账单 / 家道徽标。"""
    panel = _panel_text()
    # 街机档不是 full 档 ⇒ 连击 / 集中度 / 台账恒 false。
    assert 'const fullUi = uiLevel === "full" && !arcadeSimple' in panel
    assert "const showCombo = fullUi" in panel
    assert "const showConcentration = fullUi" in panel
    assert "const showPlanLedger = fullUi" in panel
    # 街机档（minimal）⇒ 家道徽标 / 平仓线 / 账单 Alert 恒 false。
    assert "const showFamilyBadge = !minimalUi" in panel
    assert "const showKillLine = !minimalUi" in panel
    assert "const showBillAlert = !minimalUi" in panel
    # 水位条：街机档连**教程例外**都不给（连教程也不许把水位条叫回来）。
    assert 'const showGauge = fullUi || guardedUi || tutorialNeeds("gauge")' in panel
    assert "!arcadeSimple && tutorialAnchor === anchor" in panel


def test_arcade_ui_keeps_the_two_big_buttons() -> None:
    """街机档**只留**两个大按钮：掏钱买 / 割肉卖，且**不折行**。"""
    panel = _panel_text()
    assert "rrm-trade-row" in panel
    # 两键仍是一整行（不折行）—— 街机档的 CSS 把三列改成两列。
    assert re.search(r"rrm-arcade-simple[^`]*repeat\(2", panel), "街机档两键布局"


# ══════════════════════════════════════════════════════════ ④ 教程不许圈空气


def test_arcade_tutorial_never_anchors_a_hidden_widget() -> None:
    """街机档的教程**不许**把玩家圈到已经藏起来的元素上。

    街机档把「持仓台账」和「资金水位条」都砍了 ⇒ 教程的第 3 / 4 步
    必须改指**顶部概览**（总资产 / 盈亏 / 现钱都还在那儿）。
    """
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    anchors = [step.anchor for step in TIER_STEPS["classic_arcade"]]
    assert len(anchors) == 4, "街机档仍是 4 步"
    for anchor in anchors:
        assert anchor in ARCADE_VISIBLE_ANCHORS, f"街机档教程锚到了隐藏元素：{anchor}"


def test_the_panel_redirects_the_hidden_arcade_anchors() -> None:
    """面板有一张**兜底表**：隐藏锚点一律改指概览。

    后端哪天又把 ``gauge`` / ``position_grid`` 发过来，面板自己改道 ——
    不会出现「教程圈了一个不存在的东西」。
    """
    panel = _panel_text()
    assert "ARCADE_HIDDEN_ANCHORS" in panel
    assert 'gauge: "overview"' in panel
    assert 'position_grid: "overview"' in panel


def test_arcade_tutorial_steps_keep_their_own_copy() -> None:
    """街机档后两步有**自己的**文案（不跟入门档共用）。

    入门档有台账也有水位条，街机档没有 ⇒ 文案不能共用。
    """
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    arcade = TIER_STEPS["classic_arcade"]
    beginner = TIER_STEPS["classic_beginner"]
    arcade_keys = {step.title_key for step in arcade}
    beginner_keys = {step.title_key for step in beginner}
    assert "tutorial.arcade.simple_overview.title" in arcade_keys
    assert "tutorial.arcade.simple_cash.title" in arcade_keys
    # 后两步的文案键**不与入门档重叠**。
    assert not ({step.title_key for step in arcade[2:]} & beginner_keys)


# ══════════════════════════════════════════════════════════ ⑤ 其它红线


def test_arcade_mode_is_not_in_the_hard_modes() -> None:
    """第一关仍然享受新手期放水（它是前两关之一）。"""
    from plugins.mock_stock_meow import MODE_ARCADE, SOFTENED_MODES

    assert MODE_ARCADE in SOFTENED_MODES
    assert MODE_ARCADE == "classic_arcade"


def test_no_new_ai_behavior_literal() -> None:
    """``ai_behavior="respond",`` 的字面量**不许增加**（仓库钉死两处）。"""
    text = _init_text()
    count = text.count('ai_behavior="respond",')
    assert count == 2, count
