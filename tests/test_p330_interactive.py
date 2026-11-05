# -*- coding: utf-8 -*-
"""P33.0 守卫：**行情卡点击联动 + 退出后的幽灵播报自毁**。

直播前的最后一块拼图，这一版把两件事钉死：

1. **行情卡点击联动** —— 点左下「今天在涨的这些」里的方块卡 ⇒ 主 K 线
   立刻切到这只、Select 下拉框同步、那张卡有**看得见的选中态**；
   同时后台事件队列里给 Yui 递一条**只含事实**的轻量提示词，
   让她能顺嘴搭腔。插件**不替她写台词**（P13 契约）。
2. **退出自杀机制** —— 玩家点【退出对局】或对局进入结算状态后，
   心跳立刻变成空转、待推送队列清空、**任何** ``push_message`` /
   卡片投递都在闸门前 return：这一局结束后，屏幕上**不许**再冒出
   Yui 的对话或弹窗（观众眼里的「幽灵播报」）。

**红线**：引擎层 / 模式层零改动。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from conftest import FakeCtx, engine_mode_code_changes, make_plugin, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"


# ---------------------------------------------------------------- 工具


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_css() -> str:
    """抽出 ``RRM_CSS`` 模板字符串的正文。"""
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号"
    return rest[:end]


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _panel_source()
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        end = rest.find("\n`\n")
        assert end > 0, "找不到 RRM_CSS 的结束反引号"
        source = head + "\n/* <RRM_CSS> */\n" + rest[end + 3 :]
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _method_body(source: str, name: str) -> str:
    """取某个方法（``async def xxx`` / ``def xxx``）的源码正文。"""
    marker = f"def {name}("
    assert marker in source, f"找不到方法 {name}"
    tail = source.split(marker, 1)[1]
    lines = tail.split("\n")
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "\n".join(body)


def _new_plugin(tmp_path: Path, monkeypatch: Any, mode: str, seed: int) -> Any:
    """自建一个插件实例（数据根目录隔离到 ``tmp_path``）。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    instance = make_plugin(FakeCtx(config_path=Path("mock_stock_meow.toml")))
    run(instance.on_startup())
    run(instance.join(mode=mode, seed=seed))
    return instance


def _stocks(plugin: Any) -> list[dict[str, Any]]:
    payload = run(plugin.market_context())
    return list(payload["stocks"])


def _reasons(plugin: Any) -> list[str]:
    return [str(item[1]) for item in plugin._yui_pending_prompts]


def _prompts(plugin: Any) -> list[str]:
    return [str(item[0]) for item in plugin._yui_pending_prompts]


# ══════════════════════════════════════════════════════ ① 行情卡点击联动


def test_the_watch_stock_entry_is_wired_on_both_hooks() -> None:
    """``watch_stock`` 必须同时挂 ``@ui.action`` 与 ``@plugin_entry``。"""
    source = _init_text()
    assert '@ui.action(\n        id="watch_stock",' in source
    assert '@plugin_entry(\n        id="watch_stock",' in source
    # 面板调用的入口名要对得上。
    assert '.call("watch_stock"' in _panel_code()


def test_the_watch_stock_returns_the_facts(plugin) -> None:
    """点了某只 ⇒ 入口回一份**事实**（标的 / 现价 / 涨跌幅）。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    stock = _stocks(plugin)[0]

    result = run(plugin.watch_stock(stock_id=str(stock["id"])))
    assert result.is_ok(), result
    payload = result.value
    assert payload["action"] == "watch_stock"
    assert payload["stock_id"] == str(stock["id"])
    assert payload["name"]
    assert isinstance(payload["price"], float)
    assert isinstance(payload["change"], float)


def test_the_watch_stock_feeds_yui_a_fact_prompt(plugin) -> None:
    """切标的 ⇒ Yui 的事件队列里多一条**轻量事实**。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    stock = _stocks(plugin)[0]

    before = len(plugin._yui_pending_prompts)
    result = run(plugin.watch_stock(stock_id=str(stock["id"])))
    assert result.is_ok(), result

    assert len(plugin._yui_pending_prompts) == before + 1, "必须入队一条"
    assert "yui_watch_stock" in _reasons(plugin)
    prompt = _prompts(plugin)[-1]
    # 只给事实：标的名称要出现在提示词里。
    name = str(stock.get("display_name") or stock.get("name") or "")
    assert name and name in prompt, "提示词必须点名他盯的那只"


def test_the_watch_stock_prompt_carries_no_performance_directives(plugin) -> None:
    """提示词**只给事实** —— 不许替 Yui 写好那句话（P13 契约）。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    stock = _stocks(plugin)[0]
    run(plugin.watch_stock(stock_id=str(stock["id"])))
    prompt = _prompts(plugin)[-1]

    forbidden = (
        "请务必",
        "顺便提",
        "台词",
        "嘲讽",
        "口吻",
        "表演",
        "主动开口",
        "扮演",
    )
    for word in forbidden:
        assert word not in prompt, f"提示词里出现了表演指令：{word}"


def test_the_watch_stock_rejects_an_unknown_stock(plugin) -> None:
    """标的不存在 ⇒ 明确报错，不许瞎编一只。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    result = run(plugin.watch_stock(stock_id="FAKE-not-a-stock"))
    assert not result.is_ok()
    assert result.err().code == "stock_not_found"


def test_the_watch_stock_needs_a_running_game(plugin) -> None:
    """没开局就不许切标的（否则会往空市场上递事实）。"""
    result = run(plugin.watch_stock(stock_id="FAKE-0001"))
    assert not result.is_ok()


def test_the_panel_wires_the_tile_click_to_the_kline() -> None:
    """面板：点方块卡 ⇒ 切主 K 线 + 叫后端递事实。"""
    code = _panel_code()
    assert "focusStock" in code, "面板必须有 focusStock 这个动作"
    body = code.split("const focusStock =", 1)[1].split("\n}", 1)[0]
    assert "setKlineStockPick" in body, "点了必须切主 K 线"
    assert '"watch_stock"' in body, "点了必须叫后端递事实"


def test_the_kline_select_follows_the_focused_stock() -> None:
    """K 线的 Select 跟着走（value 绑的是当前选中的标的）。"""
    code = _panel_code()
    assert "value={klineStockId}" in code, "Select 必须受控于 klineStockId"
    # 行情网格把选中态与点击回调都传给了方块卡。
    assert "selected: String(stock.id) === String(klineStockId)" in code
    assert "onClick: () => focusStock(stock)" in code


def test_the_selected_tile_has_a_visible_state() -> None:
    """被选中的那张卡必须**一眼看得出来**（描边 + 底色 + 光晕）。"""
    css = _panel_css()
    assert ".rrm-slot-selected" in css, "缺少选中态样式"
    block = css.split(".rrm-root .rrm-slot.rrm-slot-selected {", 1)[1].split("}", 1)[0]
    assert "outline:" in block, "选中态必须有描边"
    assert "background-image:" in block, "选中态必须有底色"
    # 光晕（外圈）也不许少。
    assert "0 0 0 4px" in css, "选中态必须有外圈光晕"
    # 面板的 CSS 红线一条都不许破。
    for banned in ("@keyframes", "position", "filter", "opacity", "clip-path", "z-index"):
        assert banned not in css, f"CSS 里出现了禁用词：{banned}"


# ══════════════════════════════════════════════════════ ② 退出自杀机制


def test_the_broadcast_gate_starts_closed(plugin) -> None:
    """没开局 ⇒ 闸门是关的（默认状态不许播报）。"""
    assert plugin._is_game_active is False
    assert plugin._game_ended is False


def test_the_join_opens_the_broadcast_gate(plugin) -> None:
    """开局 ⇒ 闸门打开。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    assert plugin._is_game_active is True
    assert plugin._game_ended is False


def test_the_deactivate_clears_the_queue_and_the_todos(plugin) -> None:
    """退场 ⇒ 队列清空、各种待办归零。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    plugin._yui_queue_prompt("还没说出口的话", "yui_watch_stock", 4, day=1)
    plugin._story_state = {"chapter": "x"}
    plugin._loan_prompt_at = time.time()
    plugin._loan_prompt_day = 3
    plugin._pending_event = {"kind": "harvest"}
    plugin._intuition_watch = [{"stock": "FAKE-1"}]
    assert plugin._yui_pending_prompts

    plugin._deactivate_game("test")

    assert plugin._is_game_active is False
    assert plugin._game_ended is True
    assert plugin._yui_pending_prompts == [], "待推送队列必须清空"
    assert plugin._story_state is None
    assert plugin._loan_prompt_at == 0.0
    assert plugin._loan_prompt_day == 0
    assert plugin._pending_event is None
    assert plugin._intuition_watch == []


def test_the_proactive_push_is_muted_after_the_exit(plugin) -> None:
    """退场之后 ⇒ 主动搭话一律被拦（幽灵播报的第一道闸）。"""
    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    run(plugin.join(mode="classic_beginner", seed=20261105))
    calls.clear()  # 开局那几声招呼不算在内

    plugin._deactivate_game("test")
    assert run(plugin._push_proactive("还有人吗", reason="test")) is False
    assert calls == [], "退场之后不许再推任何东西"


def test_the_player_initiated_replies_still_get_through(plugin) -> None:
    """玩家**主动点的**招呼不受闸门限制（不然"呼叫猫娘"就永远没反应）。"""
    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    run(plugin.join(mode="classic_beginner", seed=20261105))
    calls.clear()  # 开局那几声招呼不算在内

    plugin._deactivate_game("test")
    assert run(plugin._push_proactive("在吗", reason="call", allow_inactive=True)) is True
    assert len(calls) == 1


def test_the_cards_are_muted_after_the_exit(plugin) -> None:
    """退场之后 ⇒ 不许再投卡片（观众眼里的幽灵弹窗）。"""
    from plugins.mock_stock_meow.cards import build_taunt_card

    created: list[dict[str, Any]] = []
    plugin.ctx.create_card = lambda **kwargs: created.append(kwargs)
    run(plugin.join(mode="classic_beginner", seed=20261105))
    created.clear()  # 开局那几张卡不算在内

    card = build_taunt_card(trigger="on_player_default", text="嘴上说说而已")
    plugin._deactivate_game("test")
    run(plugin._push_card(card))
    assert created == [], "退场之后不许再投卡片"


def test_the_prompt_queue_is_dropped_after_the_exit(plugin) -> None:
    """退场之后 ⇒ 攒着的提示词一条都不推，队列直接清掉。"""
    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    run(plugin.join(mode="classic_beginner", seed=20261105))
    calls.clear()  # 开局那几声招呼不算在内
    plugin._yui_queue_prompt("还没说出口的话", "yui_watch_stock", 4, day=1)
    assert plugin._yui_pending_prompts

    plugin._deactivate_game("test")
    run(plugin._flush_yui_prompts())
    assert calls == [], "退场之后不许再推提示词"
    assert plugin._yui_pending_prompts == [], "退场之后队列必须清空"


def test_the_heartbeat_turns_into_a_no_op_after_the_exit(plugin, monkeypatch) -> None:
    """退场之后 ⇒ 心跳**空转**：不跑 tick、不跑剧情、不跑看门狗。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    calls: list[str] = []

    async def _story() -> None:
        calls.append("story")

    async def _loan() -> None:
        calls.append("loan")

    async def _idle() -> None:
        calls.append("idle")

    monkeypatch.setattr(plugin, "_story_tick", _story)
    monkeypatch.setattr(plugin, "_loan_prompt_watchdog", _loan)
    monkeypatch.setattr(plugin, "_idle_meow_watchdog", _idle)

    plugin._deactivate_game("test")
    result = run(plugin._heartbeat_body())

    assert result.is_ok(), result
    payload = result.value
    assert payload["inactive"] is True, "退场之后心跳必须自报空闲"
    assert payload["due"] == 0
    assert calls == [], f"退场之后这些看门狗一个都不许跑：{calls}"


def test_the_settlement_closes_the_gate(plugin) -> None:
    """对局进入结算状态 ⇒ 闸门关闭。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    result = run(plugin.settle())
    assert result.is_ok(), result
    assert plugin._is_game_active is False
    assert plugin._game_ended is True


def test_the_bankruptcy_closes_the_gate(plugin) -> None:
    """玩家点【退出对局】⇒ 闸门关闭。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    result = run(plugin.declare_bankrupt())
    assert result.is_ok(), result
    assert plugin._is_game_active is False
    assert plugin._game_ended is True


def test_a_new_game_reopens_the_gate(plugin) -> None:
    """下一局 ⇒ 闸门重新打开（上一局的自毁状态不许带过来）。"""
    run(plugin.join(mode="classic_beginner", seed=20261105))
    # 正常退场（会清掉库里的席位）。
    run(plugin.declare_bankrupt())
    assert plugin._is_game_active is False
    assert plugin._game_ended is True

    run(plugin.join(mode="classic_beginner", seed=20261106))
    assert plugin._is_game_active is True
    assert plugin._game_ended is False


def test_the_latch_beats_the_database(plugin) -> None:
    """闩锁上着 ⇒ 就算库里还有席位，这一局也不许复活。

    结算流程是「先闭嘴、后清席位」—— 中间那段时间心跳随时可能插进来，
    如果它只看库，就会把刚关掉的闸门重新打开。
    """
    run(plugin.join(mode="classic_beginner", seed=20261105))
    assert run(plugin._game_is_active()) is True

    plugin._deactivate_game("test")
    # 库里的席位**还在**（还没落库清空）⇒ 闩锁必须压住它。
    assert run(plugin._game_is_active()) is False
    assert plugin._is_game_active is False


def test_a_restart_mid_game_reopens_the_gate(plugin) -> None:
    """插件进程重启（状态位丢失）但对局还在库里 ⇒ 闸门自己回来。

    反过来只认状态位的话，市场还在走、Yui 却哑了（真机事故）。
    """
    run(plugin.join(mode="classic_beginner", seed=20261105))
    # 模拟重启：内存里的两个标志全丢。
    plugin._is_game_active = False
    plugin._game_ended = False

    assert run(plugin._game_is_active()) is True
    assert plugin._is_game_active is True, "重启后必须把广播闸门重新打开"


# ══════════════════════════════════════════════════════ ③ 源码级红线


def test_the_heartbeat_checks_the_gate_before_anything_else() -> None:
    """心跳体里：闸门检查必须**排在跑 tick 之前**。"""
    body = _method_body(_init_text(), "_heartbeat_body")
    check = body.index("_game_is_active()")
    assert check < body.index("_run_ticks"), "闸门必须排在跑 tick 之前"
    assert check < body.index("_story_tick"), "闸门必须排在剧情之前"
    assert check < body.index("_flush_yui_prompts"), "闸门必须排在推提示词之前"


def test_every_push_path_checks_the_gate() -> None:
    """每一个投递口都得先看闸门。"""
    source = _init_text()
    for name in (
        "_push_proactive",
        "_push_card",
        "_push_story_chapter",
        "_flush_yui_prompts",
    ):
        body = _method_body(source, name)
        assert "_is_game_active" in body, f"{name} 没看广播闸门"


def test_the_engine_and_the_modes_are_untouched() -> None:
    """红线：引擎层 / 模式层零改动。"""
    assert engine_mode_code_changes() == []
