# -*- coding: utf-8 -*-
"""P28.0 守卫 ②：**人机协同双打**（``duo`` 档）—— 直播模式彻底分化。

第四档模式的硬契约
------------------

1. **Yui 一根手指都不碰玩家的账户** —— 交易工具硬拒，**授权窗口也不许开**
   （开了窗口就等于给她留了一条「可以动手」的路；提示词管不住模型，
   工具层才管得住）。
2. **LLM 不许自己升到 ``duo``** —— 模式只许玩家在面板上按。
3. **她照样要说话** —— 双打档也要主动开回合（点评**不占**交易配额）。
4. **态度跟着盈亏走** —— 赚 ⇒ 吹捧；亏 ⇒ 嘲笑；持平 ⇒ 中立；安静档 ⇒ 不打扰。
5. **内参是猜测，不是内幕** —— 这件事必须在提示词里**明说**，
   否则她会把胡编当事实推给观众。

面板那一半（两个独立按钮）由本文件末尾的 ``test_duo_panel_*`` 守着。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


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


def _trade_reasons(plugin: Any) -> list[str]:
    """本局所有成交的来源（``"player"`` / ``"yui"``）。"""
    reasons: list[str] = []
    try:
        trades = plugin._trade_log()
    except Exception:  # noqa: BLE001 - 守卫测试不该被内部实现卡死
        return reasons
    for trade in trades or []:
        try:
            reasons.append(str(getattr(trade, "reason", "")))
        except Exception:  # noqa: BLE001
            continue
    return reasons


# ══════════════════════════════════════════════════════════ ① 模式本身


def test_duo_is_the_fourth_mode() -> None:
    """``duo`` 是**第四档**独立模式，不是 ``assist`` 的别名。"""
    from plugins.mock_stock_meow import (
        YUI_MODE_ASSIST,
        YUI_MODE_DUO,
        YUI_MODE_LIVE,
        YUI_MODE_SPECTATE,
        YUI_MODES,
    )

    assert YUI_MODE_DUO == "duo"
    assert YUI_MODE_DUO != YUI_MODE_ASSIST
    for mode in (YUI_MODE_LIVE, YUI_MODE_DUO, YUI_MODE_ASSIST, YUI_MODE_SPECTATE):
        assert mode in YUI_MODES, mode


def test_the_llm_cannot_self_promote_to_duo(plugin) -> None:
    """**LLM 不许自己升到 duo** —— 模式只许玩家在面板上按。

    （与 ``live`` 同等待遇：模型自己把自己提成「能动手」是事故。）
    """
    run(plugin.join(mode="classic_arcade", seed=8301))
    result = run(plugin.llm_yui_mode(mode="duo"))
    assert result["is_error"] is True
    assert plugin._yui_current_mode() != "duo"


def test_the_llm_switchable_modes_exclude_duo() -> None:
    """``YUI_LLM_SWITCHABLE_MODES`` 里**不许**有 ``duo``。"""
    from plugins.mock_stock_meow import YUI_LLM_SWITCHABLE_MODES, YUI_MODE_DUO

    assert YUI_MODE_DUO not in YUI_LLM_SWITCHABLE_MODES


def test_the_player_can_switch_into_duo(plugin) -> None:
    """玩家**可以**切到 duo（面板上的第二个按钮）。"""
    run(plugin.join(mode="classic_arcade", seed=8302))
    run(plugin.yui_mode(mode="duo"))
    assert plugin._yui_current_mode() == "duo"
    payload = run(plugin.market_context())
    assert payload["yui"]["mode"] == "duo"


# ══════════════════════════════════════════════════════════ ② 硬防线


def test_duo_refuses_to_trade(plugin) -> None:
    """**双打**：交易工具**硬拒**（账户归玩家）。"""
    run(plugin.join(mode="classic_arcade", seed=8303))
    run(plugin.yui_mode(mode="duo"))
    stocks = run(plugin.market_context())["stocks"]
    result = run(
        plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=1.0)
    )
    assert result["is_error"] is True
    assert result["output"]["ok"] is False
    assert result["error"]
    # 一笔都不许成交。
    assert _trade_reasons(plugin) == []


def test_duo_refuses_to_open_the_grant_window(plugin) -> None:
    """**连授权窗口都不许开** —— 开了就等于给她留了一条「可以动手」的路。"""
    run(plugin.join(mode="classic_arcade", seed=8304))
    run(plugin.yui_mode(mode="duo"))
    denied = run(plugin.llm_yui_grant())
    assert denied["is_error"] is True
    assert denied["output"]["ok"] is False
    # 授权没开 ⇒ 就算闸门放行她也动不了（双保险）。
    assert plugin._yui_is_granted() is False


def test_duo_rejection_uses_a_dedicated_message() -> None:
    """拒绝理由用**专用**的 i18n 键（不复用 spectate 那条）。"""
    text = _init_text()
    body = _method_body(text, "_yui_gate")
    assert "yui.reject.duo" in body
    assert "yui.reject.duo" in _i18n()


# ══════════════════════════════════════════════════════════ ③ 她照样说话


def test_duo_prompt_carries_the_hard_directive_and_the_persona() -> None:
    """双打提示词必须带**硬约束**与**人格**，且点名禁掉两个工具。"""
    from plugins.mock_stock_meow.meow import (
        YUI_DUO_DIRECTIVE,
        YUI_DUO_PERSONA,
        yui_duo_prompt,
    )

    prompt = yui_duo_prompt(
        mode="duo",
        action="buy",
        pnl=0.0,
        cash=1000.0,
        positions="空仓",
        news="今天没有新消息。",
        talk_level="normal",
    )
    assert YUI_DUO_DIRECTIVE in prompt
    assert YUI_DUO_PERSONA in prompt
    # 硬约束要点名禁掉交易工具**和**授权工具。
    assert "mock_stock_meow_yui_trade" in prompt
    assert "mock_stock_meow_yui_grant" in prompt
    # 内参是**猜测**，这件事必须写在提示词里。
    assert "猜测" in prompt or "瞎猜" in prompt


def test_duo_prompt_reacts_to_the_pnl() -> None:
    """赚 ⇒ 吹捧；亏 ⇒ 嘲笑；持平 ⇒ 中立。"""
    from plugins.mock_stock_meow.meow import yui_duo_prompt

    common = {
        "mode": "duo",
        "action": "sell",
        "cash": 1000.0,
        "positions": "空仓",
        "news": "",
        "talk_level": "normal",
    }
    win = yui_duo_prompt(pnl=100.0, **common)
    lose = yui_duo_prompt(pnl=-100.0, **common)
    even = yui_duo_prompt(pnl=0.0, **common)
    assert "吹捧" in win
    assert "嘲笑" in lose
    assert "中立" in even


def test_duo_commentary_follows_the_pnl() -> None:
    """**点评的态度跟着盈亏走** —— 赚与亏必须是两句不一样的话。"""
    from plugins.mock_stock_meow.meow import yui_duo_commentary

    win = yui_duo_commentary(action="sell", pnl=100.0, symbol_name="妖股甲")
    lose = yui_duo_commentary(action="sell", pnl=-100.0, symbol_name="妖股甲")
    even = yui_duo_commentary(action="sell", pnl=0.0, symbol_name="妖股甲")
    assert win and lose and even
    assert win != lose, "赚和亏的点评不许是同一句"
    # 素材是随机挑的 ⇒ 名字**不一定**每句都带；这里只守「态度分得开」。
    assert win != even or lose != even


def test_duo_commentary_is_silent_in_the_quiet_level() -> None:
    """安静档 ⇒ **不打扰**（点评是空串）。"""
    from plugins.mock_stock_meow.meow import yui_duo_commentary

    quiet = yui_duo_commentary(
        action="sell", pnl=100.0, symbol_name="妖股甲", talk_level="quiet"
    )
    assert quiet == ""


def test_the_duo_persona_has_tone_rules() -> None:
    """人格规矩**不许**是空的（否则她会用自己的默认人格乱演）。"""
    from plugins.mock_stock_meow.duo_persona import (
        DUO_TALK_LEVELS,
        DUO_TONE_RULES,
        build_duo_tip,
    )

    assert "quiet" in DUO_TALK_LEVELS
    assert "normal" in DUO_TALK_LEVELS
    assert "chatty" in DUO_TALK_LEVELS
    assert len(DUO_TONE_RULES) >= 3
    tip = build_duo_tip(talk_level="normal")
    assert tip, "内参素材不许是空的"


def test_a_player_trade_queues_a_duo_commentary(plugin) -> None:
    """玩家每成交一笔 ⇒ **攒**一条点评（**不**在成交路径里直接推）。

    只入队、由心跳锁外推 —— 这是 P11.1「500 红色警告」的根因修复。
    """
    run(plugin.join(mode="classic_arcade", seed=8305))
    run(plugin.yui_mode(mode="duo"))
    stocks = run(plugin.market_context())["stocks"]
    _value(run(plugin.buy(str(stocks[0]["id"]), 1.0)))
    reasons = [item[1] for item in plugin._yui_pending_prompts]
    assert "yui_duo_commentary" in reasons, plugin._yui_pending_prompts


def test_duo_commentary_is_queued_only_in_duo_mode(plugin) -> None:
    """别的模式**不许**冒用双打点评。"""
    run(plugin.join(mode="classic_arcade", seed=8306))
    run(plugin.yui_mode(mode="assist"))
    stocks = run(plugin.market_context())["stocks"]
    _value(run(plugin.buy(str(stocks[0]["id"]), 1.0)))
    reasons = [item[1] for item in plugin._yui_pending_prompts]
    assert "yui_duo_commentary" not in reasons


def test_the_duo_turn_is_not_blocked_by_the_trade_quota() -> None:
    """**日配额只管交易，不管点评** —— 她越能说，直播越热闹。

    源码级守卫：配额闸门**必须**被 ``live`` 限定住。
    """
    body = _method_body(_init_text(), "_collect_yui_live_prompt")
    assert "YUI_MODE_DUO" in body, "双打档必须能主动开回合"
    assert re.search(
        r"if mode == YUI_MODE_LIVE and self\._yui_quota_left", body
    ), "配额闸门只对 live 生效"


# ══════════════════════════════════════════════════════════ ④ 面板两个按钮


def test_duo_panel_has_two_independent_buttons() -> None:
    """面板上是**两个独立按钮**（代打 / 双打），不是一个三选一。"""
    panel = _panel_text()
    assert "panel.yui_duo_live_title" in panel
    assert "panel.yui_duo_team_title" in panel
    assert "panel.yui_duo_live_hint" in panel
    assert "panel.yui_duo_team_hint" in panel


def test_duo_panel_keeps_the_old_i18n_keys() -> None:
    """**旧键不许删** —— 宿主可能还有别处在读它们。"""
    table = _i18n()
    for key in (
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
        "error.bad_yui_mode",
    ):
        assert key in table, key


def test_duo_panel_keys_exist_in_i18n() -> None:
    """面板用到的新键**必须**在 i18n 里存在。"""
    table = _i18n()
    for key in (
        "panel.yui_duo_live_title",
        "panel.yui_duo_team_title",
        "panel.yui_duo_live_hint",
        "panel.yui_duo_team_hint",
        "panel.yui_duo_badge_live",
        "panel.yui_duo_badge_team",
        "yui.reject.duo",
    ):
        assert key in table, key


def test_duo_badges_exist_in_the_panel() -> None:
    """双打档有两个徽标（代打中 / 双打中）—— 状态一眼可见。"""
    panel = _panel_text()
    assert "panel.yui_duo_badge_live" in panel
    assert "panel.yui_duo_badge_team" in panel


def test_no_new_ai_behavior_literal() -> None:
    """``ai_behavior="respond",`` 的字面量**不许增加**（仓库钉死两处）。"""
    count = _init_text().count('ai_behavior="respond",')
    assert count == 2, count
