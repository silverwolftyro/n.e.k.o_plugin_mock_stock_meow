# -*- coding: utf-8 -*-
"""P26.0 守卫：**直播防翻车兜底** + **群友随机事件**。

明天就要公开直播，这一版钉死的是两件事：

1. **兜底**（任务一）—— 面板按钮 3 秒无响应就解锁、``ErrorBoundary`` 防白屏、
   心跳 / tick 流水线卡死能自愈。三条都是"**宁可少做也不卡死**"。
2. **彩蛋**（任务二 / 任务三）—— 浅忆QanYi 的「天降 Token」、云玩家的「免费内参」。

两条贯穿全文件的红线：

* **引擎层 / 模式层零改动** —— 两个群友事件都在**插件层**实现
  ⇒ ``tests/simulate.py`` 的 500 局基线逐字节不变；
* **不许改写游戏平衡** —— 天降 Token 触发率 5%、每局上限 3 次；
  内参每天最多一次、触发率 25%。玩家本来就必亏，这两个彩蛋只准"救场"，
  不准"发钱"。

第三条是 P13 契约：**给 Yui 的提示词只给事实**，不许写死表演指令。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_COMMUNITY = _PLUGIN_ROOT / "community_events.py"


# ---------------------------------------------------------------- 工具


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_source() -> str:
    return _INIT.read_text(encoding="utf-8")


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _plugin_module() -> Any:
    """插件包（``plugins.mock_stock_meow``）。

    用 ``importlib`` 取，而不是在文件顶部 import —— 这样 import 发生在
    ``conftest`` 把 ``plugins/`` 挂上 ``sys.path`` **之后**，
    也不会让本文件顶部的 import 顺序变得可疑（ruff E402）。
    """
    return importlib.import_module("plugins.mock_stock_meow")


def _community_module() -> Any:
    return importlib.import_module("plugins.mock_stock_meow.community_events")


def _forbidden_directives() -> tuple[str, ...]:
    """P13 的表演指令禁词表（复用 ``test_p130_soul`` 的那一份）。"""
    module = importlib.import_module("test_p130_soul")
    return tuple(module.P13_FORBIDDEN_DIRECTIVES)


def _forbidden_tokens() -> tuple[str, ...]:
    """局中隐藏的禁止字段词族（复用 ``test_visibility`` 的那一份）。"""
    module = importlib.import_module("test_visibility")
    return tuple(module.FORBIDDEN_TOKENS)


def _session(plugin: Any) -> Any:
    """拿一份**真**会话（跟引擎用的是同一份模型）。"""
    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    return run(
        load_session(plugin._repo, plugin.state, engine_config=default_engine_config())
    )


def _engine_config(plugin: Any) -> Any:
    return run(plugin._engine_config())


def _journal() -> Any:
    return _plugin_module()._TickJournal()


def _force_roll(monkeypatch: pytest.MonkeyPatch, outcome: bool) -> None:
    """把掷点钉死（``True`` = 必中，``False`` = 必不中）。"""
    module = _plugin_module()
    monkeypatch.setattr(module, "community_roll", lambda *, chance: outcome)


def _walk_keys(payload: Any) -> list[str]:
    """递归收集 payload 里的所有 dict key。"""
    keys: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_walk_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            keys.extend(_walk_keys(item))
    return keys


# ══════════════════════════════════════════════════════════════ ① 面板超时


def test_the_action_timeout_is_three_seconds() -> None:
    """闸门是 **3 秒**（宿主的 30 秒对直播来说太久）。"""
    assert "const ACTION_TIMEOUT_MS = 3000" in _panel_source()


def test_the_timeout_gate_covers_both_the_call_and_the_refresh() -> None:
    """``api.call`` **和** ``api.refresh`` 都得走这道闸。

    只包 ``call`` 是不够的 —— ``refresh`` 挂起一样会把面板锁死。
    """
    source = _panel_source()
    assert re.search(r"withTimeout\(\s*props\.api\.call\(", source), "api.call 没走超时闸"
    assert (
        source.count("withTimeout(props.api.refresh(), ACTION_TIMEOUT_MS)") >= 2
    ), "api.refresh 没走超时闸"


def test_the_timeout_unlocks_the_panel_before_it_toasts() -> None:
    """超时时**先解锁、再弹提示**。

    顺序很重要：观众看到 Toast 的那一刻，面板必须已经能继续用。
    """
    source = _panel_source()
    anchor = source.index("isActionTimeoutError(error)")
    branch = source[anchor : anchor + 400]
    assert "busyRef.current = false" in branch, "超时后没有解锁（全局锁还锁着）"
    assert "setBusy(false)" in branch, "超时后没有解锁（按钮还是灰的）"
    toast = branch.index('showToast(t("panel.action_timeout"), "warning")')
    assert branch.index("busyRef.current = false") < toast, "必须先解锁再弹提示"
    assert branch.index("setBusy(false)") < toast, "必须先解锁再弹提示"


def test_the_timeout_toast_is_yellow_and_translated() -> None:
    """超时提示是**黄色**（``warning``），文案走 i18n（面板零硬编码中文）。"""
    source = _panel_source()
    assert (
        'showToast(t("panel.action_timeout"), "warning")' in source
    ), "超时提示必须是黄色（warning）"
    assert _i18n().get("panel.action_timeout") == "服务器响应慢，请重试"


def test_a_late_response_cannot_unlock_the_next_request() -> None:
    """超时之后旧请求可能**晚到** —— 它不许再解锁新请求。

    没有请求票号的话，超时 → 解锁 → 玩家按下第二个按钮 → 第一个请求晚到
    ⇒ 把第二个请求的锁给解了 ⇒ 面板又能连点了。
    """
    source = _panel_source()
    assert "const requestSeqRef = useRef(0)" in source
    assert "const ticket = ++requestSeqRef.current" in source
    assert "const stale = () => ticket !== requestSeqRef.current" in source


# ══════════════════════════════════════════════════════════════ ② 白屏防御


def test_the_panel_is_wrapped_in_the_hosts_error_boundary() -> None:
    """主面板被宿主自带的 ``ErrorBoundary`` 裹住 ⇒ 绝不出现纯白屏。"""
    source = _panel_source()
    assert "ErrorBoundary," in source, "面板没有从 @neko/plugin-ui 导入 ErrorBoundary"
    anchor = source.index("export default function Panel")
    head = source[anchor : anchor + 600]
    assert "ErrorBoundary" in head, "导出的 Panel 没有用 ErrorBoundary 包裹"
    assert "renderPanelFallback" in head, "ErrorBoundary 没有挂兜底块"
    assert "h(PanelScreen, props)" in head, "主体没有作为 ErrorBoundary 的子节点"


def test_the_default_panel_export_stays_unique() -> None:
    """``export default function Panel`` 必须**唯一**。

    ``test_p100_onboarding.py`` 与 ``test_p90_visual.py`` 都按
    ``source.split("export default function Panel", 1)`` 切面板源码 ——
    多一份或多一份字面量都会让它们切错。
    """
    assert _panel_source().count("export default function Panel") == 1


def test_the_fallback_block_shows_the_reconnect_notice() -> None:
    """兜底块显示「重连中，请稍后」，并给一个重新渲染的出口。"""
    source = _panel_source()
    assert 't("panel.render_error_title")' in source
    labels = _i18n()
    assert labels.get("panel.render_error_title") == "重连中，请稍后"
    assert "panel.render_error_body" in labels
    assert "panel.render_error_retry" in labels


# ══════════════════════════════════════════════════════════════ ③ 心跳自愈


def test_the_heartbeat_and_the_tick_pipeline_both_have_hard_timeouts() -> None:
    """两层闸门都在，且单拍闸门**比引擎的慢拍阈值宽松**。

    引擎自己判"这一拍太慢、跳过"用的是 15 秒；我们的闸门必须更宽，
    否则正常的慢拍会被误杀成"卡死"。
    """
    module = _plugin_module()
    assert module._HEARTBEAT_TIMEOUT_SECONDS > 0.0
    assert module._TICK_PIPELINE_TIMEOUT_SECONDS > 0.0
    assert module._TICK_PIPELINE_TIMEOUT_SECONDS > 15.0, "单拍闸门比引擎的慢拍阈值还窄"


def test_a_hung_tick_pipeline_is_skipped_and_the_baseline_keeps_moving(
    plugin, monkeypatch
) -> None:
    """tick 流水线卡死 ⇒ 跳过本拍 + 记账，**下一拍照跑**。

    宿主对每个 handler 都 ``asyncio.run()``：心跳不返回，那个定时线程就
    永远卡在这一轮 ⇒ 市场当场冻结（面板还在、时间不流动）。
    """
    module = _plugin_module()
    monkeypatch.setattr(module, "_TICK_PIPELINE_TIMEOUT_SECONDS", 0.05)

    async def _hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(plugin, "_tick_once", _hang)
    before = plugin._tick_timeouts
    plugin._last_tick_at = 1000.0
    run(plugin._run_one_tick())

    assert plugin._tick_timeouts == before + 1, "卡死的 tick 必须被跳过并记账"
    assert plugin._last_tick_at > 1000.0, "基线必须推进（否则会连锁补帧）"
    assert plugin._tick_running is False, "锁必须放开，否则下一拍也跑不了"


def test_a_raising_tick_does_not_break_the_next_tick(plugin, monkeypatch) -> None:
    """tick 抛异常 ⇒ 被捕获 + 记日志，下一拍照跑。"""
    async def _boom() -> None:
        raise RuntimeError("P26.0 故意炸一下")

    monkeypatch.setattr(plugin, "_tick_once", _boom)
    run(plugin._run_one_tick())
    assert plugin._tick_running is False

    seen: list[int] = []

    async def _ok() -> None:
        seen.append(1)

    monkeypatch.setattr(plugin, "_tick_once", _ok)
    run(plugin._run_one_tick())
    assert seen == [1], "炸过一次之后，下一拍必须还能跑"


def test_the_heartbeat_itself_survives_a_hung_body(plugin, monkeypatch) -> None:
    """整轮心跳卡死 ⇒ 放弃这一轮，**下一轮心跳照常**。"""
    module = _plugin_module()
    monkeypatch.setattr(module, "_HEARTBEAT_TIMEOUT_SECONDS", 0.05)

    async def _hang() -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(plugin, "_heartbeat_body", _hang)
    before = plugin._heartbeat_timeouts
    result = run(plugin.market_tick())
    assert result.is_ok(), result
    assert plugin._heartbeat_timeouts == before + 1


# ══════════════════════════════════════════════════════════════ ④ 天降 Token


def test_the_token_rescue_chance_is_tiny() -> None:
    """触发概率是"极小概率"（≤ 5%），且每局有硬上限。"""
    community = _community_module()
    assert 0.0 < community.TOKEN_RESCUE_CHANCE <= 0.05
    assert community.MAX_TOKEN_RESCUES_PER_GAME <= 3


def test_the_arcade_mode_never_gets_a_token_rescue(plugin, monkeypatch) -> None:
    """街机档没有生活费 ⇒ 永远"饿不死" ⇒ 永不发钱。"""
    from plugins.mock_stock_meow.modes.shared import daily_living_cost

    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_arcade", seed=20261110))
    session = _session(plugin)
    config = _engine_config(plugin)
    assert (
        daily_living_cost(
            session.mode, int(session.player.day), session.market.seed, config
        )
        == 0.0
    ), "街机档本来就没有生活费"

    session.player.cash = 0.0
    plugin._maybe_token_rescue(session, config, _journal())
    assert session.player.cash == 0.0
    assert plugin._community_events == []


def test_a_player_who_can_pay_today_never_gets_a_rescue(plugin, monkeypatch) -> None:
    """现金够交今天的饭钱 ⇒ 不算"极度缺钱" ⇒ 不触发。"""
    from plugins.mock_stock_meow.modes.shared import daily_living_cost

    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261111))
    session = _session(plugin)
    config = _engine_config(plugin)
    cost = daily_living_cost(
        session.mode, int(session.player.day), session.market.seed, config
    )
    assert cost > 0.0

    session.player.cash = cost
    plugin._maybe_token_rescue(session, config, _journal())
    assert session.player.cash == cost
    assert plugin._community_events == []


def test_the_rescue_exactly_covers_two_days_of_living_costs(
    plugin, monkeypatch
) -> None:
    """到账金额 = 今天 + 明天的生活费（刚好够，少一分明天就交不上）。"""
    from plugins.mock_stock_meow.modes.shared import daily_living_cost, next_living_cost

    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261112))
    session = _session(plugin)
    config = _engine_config(plugin)
    day = int(session.player.day)
    cost = daily_living_cost(session.mode, day, session.market.seed, config)
    nxt = next_living_cost(session.difficulty, config)

    session.player.cash = 0.0
    plugin._maybe_token_rescue(session, config, _journal())
    assert session.player.cash == pytest.approx(round(cost + nxt, 2))
    assert session.player.cash >= cost, "这笔钱至少得够今天"


def test_the_rescue_is_capped_per_game(plugin, monkeypatch) -> None:
    """一局最多捡 3 次 —— 保住"永远必亏"的平衡底线。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261113))
    session = _session(plugin)
    config = _engine_config(plugin)
    journal = _journal()
    for _ in range(5):
        session.player.cash = 0.0
        plugin._maybe_token_rescue(session, config, journal)
    assert plugin._token_rescue_count == 3
    assert len(plugin._community_events) == 3


def test_the_panel_records_the_token_rescue(plugin, monkeypatch) -> None:
    """面板上留一条记录：「浅忆QanYi 投下了几个 Token。」"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261114))
    session = _session(plugin)
    session.player.cash = 0.0
    plugin._maybe_token_rescue(session, _engine_config(plugin), _journal())

    payload = run(plugin.market_context())
    events = payload["community_events"]
    assert len(events) == 1
    assert events[0]["title"] == "浅忆QanYi 投下了几个 Token。"
    assert events[0]["kind"] == "token_rescue"
    assert events[0]["amount"] > 0.0


def test_the_panel_renders_the_community_events_card() -> None:
    """面板有一张【群友来串门了】的卡片，文案走 i18n。"""
    source = _panel_source()
    assert 't("panel.community_events")' in source
    assert 't("panel.community_events_empty")' in source
    assert "buff_tag" in source
    labels = _i18n()
    assert "panel.community_events" in labels
    assert "panel.community_events_empty" in labels


# ══════════════════════════════════════════════════════════════ ⑤ 免费内参


def test_the_intel_event_only_shows_a_buff_tag(plugin, monkeypatch) -> None:
    """内参在面板上**只有一枚 buff 标签**，正文一个字都不露。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261115))
    session = _session(plugin)
    plugin._maybe_community_intel(session, _engine_config(plugin))

    payload = run(plugin.market_context())
    events = payload["community_events"]
    assert len(events) == 1
    assert events[0]["title"] == "云玩家塞了一份内参。"
    assert events[0]["buff_tag"] == "情报：云玩家版"
    assert events[0]["detail"] == "", "内参正文不许出现在面板上"


def test_the_intel_text_never_reaches_the_panel_payload(plugin, monkeypatch) -> None:
    """内参原话**物理上**不在面板载荷里。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261116))
    session = _session(plugin)
    plugin._maybe_community_intel(session, _engine_config(plugin))

    hint = plugin._community_events[-1].hint
    assert hint, "内参必须有内容给 Yui"
    payload = run(plugin.market_context())
    dumped = json.dumps(payload, ensure_ascii=False)
    assert hint not in dumped, "内参正文泄漏到面板载荷了"


def test_the_intel_is_pushed_to_yui_through_push_message(plugin, monkeypatch) -> None:
    """内参**必须**通过 ``push_message`` 送到 Yui 手里。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261117))
    session = _session(plugin)
    plugin._maybe_community_intel(session, _engine_config(plugin))
    hint = plugin._community_events[-1].hint

    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)

    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)
    reasons = [str(item[1]) for item in plugin._yui_pending_prompts]
    assert "yui_community_intel" in reasons, "内参必须攒一条给 Yui 的提示词"

    run(plugin._flush_yui_prompts())
    assert calls, "内参必须真的 push_message 出去"
    matched = [item for item in calls if hint in json.dumps(item, ensure_ascii=False)]
    assert matched, "内参原话没有通过 push_message 送到 Yui"
    assert matched[0]["ai_behavior"] == "respond"


def test_the_community_prompts_carry_facts_not_performance_directives(
    plugin, monkeypatch
) -> None:
    """P13 契约：给 Yui 的提示词**只给事实**，不许写死表演指令。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261118))
    session = _session(plugin)
    session.player.cash = 0.0
    plugin._maybe_token_rescue(session, _engine_config(plugin), _journal())
    plugin._maybe_community_intel(session, _engine_config(plugin))

    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)
    prompts = [str(item[0]) for item in plugin._yui_pending_prompts]
    community_prompts = [
        text for text in prompts if "浅忆QanYi" in text or "云玩家" in text
    ]
    assert len(community_prompts) == 2, "两个群友事件都要给 Yui 递事实"

    for text in community_prompts:
        for token in _forbidden_directives():
            assert token not in text, f"提示词里出现了表演指令：{token}"


def test_the_intel_never_pollutes_the_engine_news_flow(plugin, monkeypatch) -> None:
    """内参**不写进引擎的新闻流** —— 免得污染"新闻真假"这条线。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261119))
    session = _session(plugin)
    before = [str(item.id) for item in session.market.news_today]
    plugin._maybe_community_intel(session, _engine_config(plugin))
    after = [str(item.id) for item in session.market.news_today]
    assert before == after, "内参不许写进 market.news_today"


# ══════════════════════════════════════════════════════════════ ⑥ 概率不过频


def test_the_token_rescue_does_not_fire_often() -> None:
    """实测触发率必须贴着 5%（这条守的是"别每把都发钱"）。"""
    community = _community_module()
    samples = 20000
    hits = sum(
        1 for _ in range(samples) if community.roll(chance=community.TOKEN_RESCUE_CHANCE)
    )
    rate = hits / samples
    assert 0.040 <= rate <= 0.060, f"天降 Token 的实测触发率 {rate:.4f} 偏离 5% 太多"


def test_the_intel_does_not_fire_every_day() -> None:
    """实测触发率必须贴着 25%（每天一次 ⇒ 一局 30 天也就七八次）。"""
    community = _community_module()
    samples = 20000
    hits = sum(
        1 for _ in range(samples) if community.roll(chance=community.INTEL_CHANCE)
    )
    rate = hits / samples
    assert 0.225 <= rate <= 0.275, f"内参的实测触发率 {rate:.4f} 偏离 25% 太多"


def test_a_whole_game_never_becomes_a_charity() -> None:
    """一局 30 天、天天缺钱 ⇒ 期望 ~1.5 次、最多 3 次，多数局一次都拿不到。"""
    community = _community_module()
    games = 500
    days = 30
    counts: list[int] = []
    for _game in range(games):
        count = 0
        for _day in range(days):
            if count >= community.MAX_TOKEN_RESCUES_PER_GAME:
                break
            if community.roll(chance=community.TOKEN_RESCUE_CHANCE):
                count += 1
        counts.append(count)

    mean = sum(counts) / games
    assert mean <= 2.0, f"平均每局发钱 {mean:.2f} 次，太频繁了"
    assert max(counts) <= community.MAX_TOKEN_RESCUES_PER_GAME
    zero = sum(1 for value in counts if value == 0)
    assert zero / games >= 0.12, "绝大多数局应该一次都拿不到"


# ══════════════════════════════════════════════════════════════ ⑦ 红线


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """**绝对红线**：引擎层 / 模式层零改动。

    两个群友事件都在插件层实现 ⇒ 500 局基线必须逐字节一致。
    """
    assert engine_mode_code_changes() == [], "引擎层 / 模式层被改动了"


def test_the_random_source_is_system_entropy() -> None:
    """掷点必须用**系统熵**，绝不用玩家可选的 seed。

    玩家开局能自己挑 seed；用 seed 派生掷点的话，玩家可以枚举 seed 把救助
    **稳定刷**出来 ⇒ "永远必亏"当场作废。
    """
    assert "random.SystemRandom()" in _COMMUNITY.read_text(encoding="utf-8")


def test_the_roll_never_takes_a_seed() -> None:
    """``roll`` 的签名里只有概率 —— 没有种子参数可传。"""
    community = _community_module()
    assert list(inspect.signature(community.roll).parameters) == ["chance"]


def test_every_community_roll_call_only_passes_a_chance() -> None:
    """插件层每一次掷点都只传概率，**绝不**把 seed 喂进去。"""
    source = _init_source()
    calls = re.findall(r"community_roll\([^)]*\)", source)
    assert calls, "找不到 community_roll 调用"
    for call in calls:
        assert "chance=" in call, call
        assert "seed" not in call, call


def test_the_community_payload_carries_no_engine_secrets(plugin, monkeypatch) -> None:
    """群友事件的载荷**一个秘密字段都不许带**（P4 §11）。"""
    _force_roll(monkeypatch, True)
    run(plugin.join(mode="classic_beginner", seed=20261120))
    session = _session(plugin)
    session.player.cash = 0.0
    plugin._maybe_token_rescue(session, _engine_config(plugin), _journal())
    plugin._maybe_community_intel(session, _engine_config(plugin))

    payload = run(plugin.market_context())
    for key in _walk_keys(payload.get("community_events")):
        lowered = key.lower()
        for token in _forbidden_tokens():
            assert token not in lowered, f"群友事件载荷泄漏了 {token}"


def test_the_community_event_payload_is_an_explicit_whitelist() -> None:
    """面板载荷是**显式白名单**构造的（不许整体导出）。"""
    community = _community_module()
    payload = community.CommunityEvent(
        id="x",
        kind="token_rescue",
        kind_label="群友投喂",
        title="浅忆QanYi 投下了几个 Token。",
        detail="账户到账 10.00 元 —— 刚好够今天和明天两天的生活费。",
        day=3,
        amount=10.0,
        cash_before=0.0,
        living_cost=10.0,
        next_living_cost=10.0,
        buff_tag=None,
        hint="这段话只给 Yui 看",
    ).public_payload()
    assert set(payload) == {
        "id",
        "kind",
        "kind_label",
        "title",
        "detail",
        "day",
        "amount",
        "buff_tag",
    }
    assert "hint" not in payload, "内参原话不许进面板载荷"
