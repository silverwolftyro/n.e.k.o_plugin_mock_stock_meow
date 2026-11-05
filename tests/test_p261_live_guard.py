# -*- coding: utf-8 -*-
"""P26.1 守卫：**直播护栏**（危险感知 + 强制续命 + 导演模式）。

明天公开直播，这一版钉死的是三件事：

1. **伪预知**（任务一）—— 底层没有精确的未来推演，插件层只把**当前盘面
   最极端的公开数据**（波动率 / 持仓集中度）摆给 Yui，让她看起来能未卜先知。
   几拍之后把两个价格摆一起 —— 跌了是她的功劳，没跌也是好戏。
2. **强制续命锁**（任务二）—— ``[director] live_god_mode``：现金跌破生活费
   红线时**强制**垫一笔；满仓暴跌血量见底时安排一次「庄家洗盘」假反弹。
3. **导演模式**（任务三）—— live 回合里给 Yui 多两行**事实行**。

三条贯穿全文件的红线：

* **引擎层 / 模式层零改动** —— 全部干预都在插件层改内存里的
  ``player.cash`` 与 ``stock.price`` ⇒ 500 局基线逐字节不变；
* **P13 契约** —— 给 Yui 的提示词**只给事实**，零表演指令、零祈使句；
* **一切有硬上限** —— 垫付 6 次 / 假反弹 1 次 / 预警 8 次 —— 不做无限续命
  （那会毁掉"永远必亏"的教学前提）。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_LIVE_GUARD = _PLUGIN_ROOT / "live_guard.py"
_CONFIG_EXAMPLE = _PLUGIN_ROOT / "config.example.toml"


# ---------------------------------------------------------------- 工具


def _plugin_module() -> Any:
    """插件包（``plugins.mock_stock_meow``）。

    用 ``importlib`` 取，而不是在文件顶部 import —— 这样 import 发生在
    ``conftest`` 把 ``plugins/`` 挂上 ``sys.path`` **之后**，
    也不会让本文件顶部的 import 顺序变得可疑（ruff E402）。
    """
    return importlib.import_module("plugins.mock_stock_meow")


def _live_guard_module() -> Any:
    return importlib.import_module("plugins.mock_stock_meow.live_guard")


def _forbidden_directives() -> tuple[str, ...]:
    """P13 的表演指令禁词表（复用 ``test_p130_soul`` 的那一份）。"""
    module = importlib.import_module("test_p130_soul")
    return tuple(module.P13_FORBIDDEN_DIRECTIVES)


def _removed_phrases() -> tuple[str, ...]:
    """P12 那些写死的句子（源码级精确匹配）。"""
    module = importlib.import_module("test_p130_soul")
    return tuple(module.P13_REMOVED_PHRASES)


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


def _set_business_config(plugin: Any, ctx: Any, payload: dict[str, Any]) -> None:
    """改业务配置并**清掉 5 秒缓存**（否则读到的还是旧值）。"""
    ctx.config_payload = dict(payload)
    plugin._business_config_cache = None
    plugin._business_config_cached_at = 0.0
    run(plugin._business_config())


def _enable_god_mode(plugin: Any, ctx: Any) -> None:
    """把 ``[director]`` 的总开关与 ``live_god_mode`` 一起打开。"""
    _set_business_config(
        plugin, ctx, {"director": {"enabled": True, "live_god_mode": True}}
    )


def _disable_god_mode(plugin: Any, ctx: Any) -> None:
    """总开关开着，但 ``live_god_mode`` 关着（纯净版的默认形状）。"""
    _set_business_config(
        plugin, ctx, {"director": {"enabled": True, "live_god_mode": False}}
    )


def _join(plugin: Any, mode: str = "classic_beginner", seed: int = 20261121) -> Any:
    """开局并返回当前会话。"""
    run(plugin.join(mode=mode, seed=seed))
    return _session(plugin)


def _hot_stock(session: Any, sigma: float = 0.19) -> Any:
    """把所有标的的 sigma 压到最低，只留一只最高的（确定性）。"""
    market = session.market
    for stock in market.stocks.values():
        stock.sigma = 0.05
    stock_id = sorted(market.stocks)[0]
    stock = market.stocks[stock_id]
    stock.sigma = float(sigma)
    return stock


def _load_up(session: Any, stock_id: str, avg_cost: float | None = None) -> Any:
    """让玩家**满仓**压在一只票上（必要时做成浮亏）。"""
    from engine.models import Position

    stock = session.market.stocks[stock_id]
    if not stock.kline:
        stock.kline.append((0, float(stock.price)))
    cost = float(stock.price) if avg_cost is None else float(avg_cost)
    session.player.positions[stock_id] = Position(
        stock_id, amount=1000.0, avg_cost=cost
    )
    return stock


# ══════════════════════════════════════════════════════════════ ① 危险感知


def test_the_danger_threshold_is_reachable_in_the_real_sigma_range() -> None:
    """门槛必须**落得到** —— 引擎给标的抽的 sigma 只在 [0.05, 0.20] 里。

    引擎那个 ``sigma_explosive_min = 0.20`` 几乎永不触发 ⇒ 拿它当门槛
    的话，"预警"就是死代码。
    """
    guard = _live_guard_module()
    assert 0.05 <= guard.DANGER_SIGMA <= 0.20, "门槛落在引擎抽不到的地方"
    assert guard.DANGER_SIGMA < 0.20, "不能用引擎那个几乎不触发的 0.20"
    assert 0.0 < guard.DANGER_CONCENTRATION < 1.0


def test_the_pick_finds_the_hottest_stock(plugin) -> None:
    """波动率最高的那只票会被挑出来。"""
    guard = _live_guard_module()
    session = _join(plugin)
    hot = _hot_stock(session)
    signal = guard.pick_danger_signal(
        session.market, session.player.positions, session.market.prices()
    )
    assert signal is not None, "最热的那只票必须被挑出来"
    assert signal.stock_id == hot.id
    assert signal.sigma == pytest.approx(0.19)
    assert signal.sigma_rank == 1
    assert signal.total_symbols == len(session.market.stocks)


def test_the_pick_prefers_the_stock_the_player_is_loaded_up_on(plugin) -> None:
    """**持有的票优先** —— 集中度对玩家才有意义。"""
    guard = _live_guard_module()
    session = _join(plugin)
    market = session.market
    ids = sorted(market.stocks)
    cold_id, hot_id = ids[0], ids[-1]
    market.stocks[cold_id].sigma = 0.17
    market.stocks[hot_id].sigma = 0.19
    _load_up(session, cold_id)
    signal = guard.pick_danger_signal(
        market, session.player.positions, market.prices()
    )
    assert signal is not None
    assert signal.stock_id == cold_id, "满仓压着的那只票必须优先"
    assert signal.held is True
    assert signal.concentration == pytest.approx(1.0)


def test_the_pick_is_deterministic(plugin) -> None:
    """同一份盘面**永远**挑出同一只票 —— 否则 Yui 会自相矛盾。"""
    guard = _live_guard_module()
    session = _join(plugin)
    _hot_stock(session)
    first = guard.pick_danger_signal(
        session.market, session.player.positions, session.market.prices()
    )
    for _ in range(5):
        again = guard.pick_danger_signal(
            session.market, session.player.positions, session.market.prices()
        )
        assert again is not None
        assert again.stock_id == first.stock_id
        assert again.score == first.score


def test_a_calm_market_produces_no_signal(plugin) -> None:
    """盘面平静时**什么都不说** —— 预警多了就不叫直觉了。"""
    guard = _live_guard_module()
    session = _join(plugin)
    for stock in session.market.stocks.values():
        stock.sigma = 0.05
    assert (
        guard.pick_danger_signal(
            session.market, session.player.positions, session.market.prices()
        )
        is None
    )


def test_the_signal_text_carries_only_public_data(plugin) -> None:
    """事实行里只有**玩家自己看得见**的东西：波动率 / 排名 / 持仓占比。"""
    guard = _live_guard_module()
    session = _join(plugin)
    hot = _hot_stock(session)
    signal = guard.pick_danger_signal(
        session.market, session.player.positions, session.market.prices()
    )
    assert signal is not None
    text = guard.danger_signal_text(signal)
    assert hot.id in text
    assert "波动率" in text
    assert "最高" in text
    for token in _forbidden_tokens():
        assert token not in text.lower(), f"事实行泄漏了 {token}"


def test_the_live_prompt_carries_the_warning(plugin) -> None:
    """危险信号 ⇒ 给 Yui 攒一条提示词。"""
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    reasons = [str(item[1]) for item in plugin._yui_pending_prompts]
    assert "yui_intuition_warning" in reasons
    text = str(plugin._yui_pending_prompts[0][0])
    assert "盘面警示" in text


def test_the_warning_only_happens_in_live_mode(plugin) -> None:
    """伪预知**只在直播托管**里生效 —— 那是唯一会主动开回合的模式。"""
    session = _join(plugin)
    _hot_stock(session)
    for mode in ("assist", "spectate"):
        plugin._yui_mode = mode
        plugin._intuition_warnings = 0
        plugin._intuition_last_tick = -10_000
        plugin._yui_pending_prompts = []
        plugin._update_intuition(session)
        assert plugin._intuition_warnings == 0, f"{mode} 模式不该发预警"
        assert plugin._yui_pending_prompts == []


def test_the_settle_prompt_arrives_a_few_ticks_later(plugin) -> None:
    """几拍之后给**对账**提示词（两个价格摆一起）。"""
    guard = _live_guard_module()
    plugin._yui_mode = "live"
    session = _join(plugin)
    hot = _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    assert plugin._intuition_warnings == 1

    before = float(hot.price)
    hot.price = before * 0.8
    session.market.tick += guard.INTUITION_SETTLE_TICKS
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)

    reasons = [str(item[1]) for item in plugin._yui_pending_prompts]
    assert "yui_intuition_settle" in reasons
    text = str(plugin._yui_pending_prompts[0][0])
    assert "盘面更新" in text
    assert f"{before:.2f}" in text, "对账必须带上预警时的价格"
    assert f"{float(hot.price):.2f}" in text, "对账必须带上现在的价格"


def test_the_settle_waits_for_the_right_number_of_ticks(plugin) -> None:
    """还没到对账拍数 ⇒ 不许提前对账（否则"预知"就变成了"读答案"）。"""
    guard = _live_guard_module()
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    session.market.tick += max(1, guard.INTUITION_SETTLE_TICKS - 1)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    reasons = [str(item[1]) for item in plugin._yui_pending_prompts]
    assert "yui_intuition_settle" not in reasons


def test_the_warnings_are_throttled(plugin) -> None:
    """同一只票冷却期内**不重复**预警。"""
    guard = _live_guard_module()
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    assert plugin._intuition_warnings == 1

    session.market.tick += 1
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    assert plugin._intuition_warnings == 1, "冷却期内不许再预警"

    session.market.tick += guard.INTUITION_COOLDOWN_TICKS
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    assert plugin._intuition_warnings == 2, "冷却期过了才允许再预警"


def test_the_warnings_are_capped_per_game(plugin) -> None:
    """一局最多预警 ``INTUITION_MAX_WARNINGS`` 次。"""
    guard = _live_guard_module()
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._intuition_warnings = guard.INTUITION_MAX_WARNINGS
    plugin._intuition_last_tick = -10_000
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    assert plugin._intuition_warnings == guard.INTUITION_MAX_WARNINGS
    reasons = [str(item[1]) for item in plugin._yui_pending_prompts]
    assert "yui_intuition_warning" not in reasons


def test_the_intuition_prompt_carries_facts_not_performance_directives(plugin) -> None:
    """P13 契约：伪预知的提示词**只给事实**。"""
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    prompts = [str(item[0]) for item in plugin._yui_pending_prompts]
    assert prompts, "必须真的攒一条提示词"
    for text in prompts:
        for token in _forbidden_directives():
            assert token not in text, f"提示词里出现了表演指令：{token}"
        assert "请" not in text, "提示词不许带祈使句"


def test_the_intuition_prompt_leaks_no_engine_secrets(plugin) -> None:
    """伪预知只给**公开数据** —— 不碰 heat / seed / 反转日 / 新闻真假。"""
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._yui_pending_prompts = []
    plugin._update_intuition(session)
    for item in plugin._yui_pending_prompts:
        text = str(item[0]).lower()
        for token in ("heat", "seed", "reversal"):
            assert token not in text, f"提示词泄漏了 {token}"
        assert "反转" not in str(item[0])


def test_the_intuition_never_reaches_the_panel_payload(plugin) -> None:
    """伪预知**只给 Yui** —— 面板上不许出现盘面警示。"""
    plugin._yui_mode = "live"
    session = _join(plugin)
    _hot_stock(session)
    plugin._update_intuition(session)
    payload = run(plugin.market_context())
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "盘面警示" not in dumped, "伪预知泄漏到面板载荷了"
    assert "直觉" not in dumped, "伪预知泄漏到面板载荷了"
    for key in _walk_keys(payload):
        lowered = key.lower()
        for token in _forbidden_tokens():
            assert token not in lowered, f"面板载荷泄漏了 {token}"


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


# ══════════════════════════════════════════════════════════════ ② 强制续命


def test_the_god_mode_is_off_by_default() -> None:
    """``config.example.toml`` 里必须**默认 false**。

    「开源纯净版不做任何后台干预」是本项目的承诺 —— 直播特制的东西
    默认必须是关着的。
    """
    text = _CONFIG_EXAMPLE.read_text(encoding="utf-8")
    assert "live_god_mode = false" in text, "live_god_mode 必须默认关闭"
    section = text.split("[director]", 1)[1]
    assert "live_god_mode = true" not in section, "示例配置不许默认打开护栏"


def test_the_god_mode_needs_the_director_switch(plugin, ctx) -> None:
    """``live_god_mode`` 必须**同时** ``[director] enabled = true`` 才生效。"""
    _set_business_config(
        plugin, ctx, {"director": {"enabled": False, "live_god_mode": True}}
    )
    session = _join(plugin)
    config = _engine_config(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, config, _journal())
    assert session.player.cash == 0.0, "总开关关着 ⇒ 护栏不许动"


def test_a_broke_player_gets_a_forced_top_up(plugin, ctx) -> None:
    """现金跌破生活费红线 ⇒ **强制**垫一笔（不看概率）。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, config, _journal())
    assert session.player.cash > 0.0, "强制垫付必须到账"
    assert plugin._god_rescue_count == 1


def test_a_solvent_player_never_gets_a_top_up(plugin, ctx) -> None:
    """还交得上今天的饭钱 ⇒ 不算"撑不住" ⇒ 不触发。"""
    from plugins.mock_stock_meow.modes.shared import daily_living_cost

    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    cost = daily_living_cost(
        session.mode, int(session.player.day), session.market.seed, config
    )
    assert cost > 0.0
    session.player.cash = cost
    plugin._maybe_god_rescue(session, config, _journal())
    assert session.player.cash == cost
    assert plugin._god_rescue_count == 0


def test_the_arcade_mode_never_gets_a_top_up(plugin, ctx) -> None:
    """街机档没有生活费 ⇒ 永远"饿不死" ⇒ 永不发钱。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin, mode="classic_arcade")
    config = _engine_config(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, config, _journal())
    assert session.player.cash == 0.0
    assert plugin._god_rescue_count == 0


def test_the_forced_top_up_is_capped(plugin, ctx) -> None:
    """一局最多强制垫付 ``GOD_RESCUE_LIMIT`` 次。"""
    guard = _live_guard_module()
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    journal = _journal()
    for _ in range(guard.GOD_RESCUE_LIMIT + 4):
        session.player.cash = 0.0
        plugin._maybe_god_rescue(session, config, journal)
    assert plugin._god_rescue_count == guard.GOD_RESCUE_LIMIT
    assert guard.GOD_RESCUE_LIMIT <= 6, "护栏不能变成无限续命"


def test_the_forced_top_up_does_not_eat_the_random_budget(plugin, ctx) -> None:
    """强制垫付与随机救助**分开计数** —— 彩蛋不该被护栏挤掉。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, config, _journal())
    assert plugin._god_rescue_count == 1
    assert plugin._token_rescue_count == 0, "护栏不许动随机事件的额度"


def test_the_forced_top_up_shows_on_the_panel(plugin, ctx) -> None:
    """垫付也要在面板上留一条记录（与 P26.0 同一套事件形状）。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, _engine_config(plugin), _journal())
    payload = run(plugin.market_context())
    events = payload["community_events"]
    assert len(events) == 1
    assert events[0]["kind"] == "token_rescue"
    assert events[0]["amount"] > 0.0


def test_the_forced_top_up_reaches_yui(plugin, ctx) -> None:
    """垫付这件事**必须**通过 ``push_message`` 送到 Yui 手里。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, _engine_config(plugin), _journal())

    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    run(plugin._flush_yui_prompts())
    assert calls, "垫付必须真的 push_message 出去"
    dumped = json.dumps(calls, ensure_ascii=False)
    assert "浅忆QanYi" in dumped
    assert calls[0]["ai_behavior"] == "respond"


def test_the_forced_top_up_prompt_carries_facts_not_performance_directives(
    plugin, ctx
) -> None:
    """P13 契约：垫付提示词**只给事实**。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, _engine_config(plugin), _journal())
    prompts = [str(item[0]) for item in plugin._yui_pending_prompts]
    assert prompts
    for text in prompts:
        for token in _forbidden_directives():
            assert token not in text, f"提示词里出现了表演指令：{token}"
        assert "请" not in text, "提示词不许带祈使句"


def test_the_rally_is_queued_when_the_player_is_wiped_out(plugin, ctx) -> None:
    """满仓 + 血量见底 + 浮亏 ⇒ 排一次「庄家洗盘」。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    stock = _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    plugin._maybe_god_rally(session, config, session.market.prices())
    assert plugin._god_rally_pending is not None
    assert plugin._god_rally_pending["stock_id"] == stock_id
    assert float(stock.price) > 0.0


def test_the_rally_lands_on_the_next_tick(plugin, ctx) -> None:
    """假反弹在**下一拍**抬 10%。"""
    guard = _live_guard_module()
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    stock = _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    plugin._maybe_god_rally(session, config, session.market.prices())

    before = float(stock.price)
    plugin._apply_god_rally(session.market)
    assert float(stock.price) == pytest.approx(before * (1.0 + guard.GOD_RALLY_RATE))
    assert plugin._god_rally_count == 1
    assert plugin._god_rally_pending is None


def test_the_rally_keeps_the_kline_consistent(plugin, ctx) -> None:
    """K 线最后一点必须**跟着抬** —— 否则观众一眼就能看出破绽。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    stock = _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    plugin._maybe_god_rally(session, config, session.market.prices())
    plugin._apply_god_rally(session.market)
    assert stock.kline, "K 线不能是空的"
    assert float(stock.kline[-1][1]) == pytest.approx(float(stock.price))


def test_the_rally_leaves_the_previous_price_alone(plugin, ctx) -> None:
    """``prev_price`` 不动 —— 于是涨跌幅天然显示 +10%。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    stock = _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    prev = float(stock.prev_price)
    plugin._maybe_god_rally(session, config, session.market.prices())
    plugin._apply_god_rally(session.market)
    assert float(stock.prev_price) == pytest.approx(prev)


def test_the_rally_is_capped(plugin, ctx) -> None:
    """一局最多 ``GOD_RALLY_LIMIT`` 次假反弹 —— 错觉贵在稀有。"""
    guard = _live_guard_module()
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    for _ in range(guard.GOD_RALLY_LIMIT + 3):
        plugin._maybe_god_rally(session, config, session.market.prices())
        plugin._apply_god_rally(session.market)
    assert plugin._god_rally_count == guard.GOD_RALLY_LIMIT
    assert guard.GOD_RALLY_LIMIT == 1


def test_no_rally_without_the_switch(plugin, ctx) -> None:
    """开关关着 ⇒ 一次都不许排。"""
    _disable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    stock = _load_up(session, stock_id, avg_cost=float(session.market.stocks[stock_id].price) * 2)
    session.player.cash = 0.0
    before = float(stock.price)
    plugin._maybe_god_rally(session, config, session.market.prices())
    assert plugin._god_rally_pending is None
    plugin._apply_god_rally(session.market)
    assert float(stock.price) == pytest.approx(before)


def test_the_rally_needs_a_floating_loss(plugin, ctx) -> None:
    """没在亏的票不给"翻盘"的错觉。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    stock_id = sorted(session.market.stocks)[0]
    price = float(session.market.stocks[stock_id].price)
    _load_up(session, stock_id, avg_cost=price * 0.5)
    session.player.cash = 0.0
    plugin._maybe_god_rally(session, config, session.market.prices())
    assert plugin._god_rally_pending is None


def test_the_rally_needs_a_full_position(plugin, ctx) -> None:
    """没满仓 ⇒ 不排假反弹。

    两只票的**市值一样大** ⇒ 集中度 0.5，远不到 0.9 的门槛。
    """
    from engine.models import Position

    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    config = _engine_config(plugin)
    ids = sorted(session.market.stocks)[:2]
    for stock_id in ids:
        stock = session.market.stocks[stock_id]
        stock.kline.append((0, float(stock.price)))
        # 市值一样大 ⇒ 集中度 0.5
        session.player.positions[stock_id] = Position(
            stock_id, amount=1000.0 / float(stock.price), avg_cost=float(stock.price) * 2
        )
    session.player.cash = 0.0
    plugin._maybe_god_rally(session, config, session.market.prices())
    assert plugin._god_rally_pending is None


def test_the_god_mode_never_touches_the_engine_news_flow(plugin, ctx) -> None:
    """护栏**不写进引擎的新闻流** —— 免得污染"新闻真假"这条线。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    before = [str(item.id) for item in session.market.news_today]
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, _engine_config(plugin), _journal())
    after = [str(item.id) for item in session.market.news_today]
    assert before == after


# ══════════════════════════════════════════════════════════════ ③ 导演模式


def test_the_director_lines_appear_in_the_live_prompt(plugin, ctx) -> None:
    """直播护栏开着 ⇒ live 回合多两行**事实行**。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    text = plugin._yui_live_prompt(session)
    assert "直播还没收工" in text
    assert "玩家手上现在还能用的操作" in text


def test_the_director_lines_are_absent_without_the_switch(plugin, ctx) -> None:
    """开关关着 ⇒ 一行都不许多（纯净版零干预）。"""
    _disable_god_mode(plugin, ctx)
    session = _join(plugin)
    text = plugin._yui_live_prompt(session)
    assert "直播还没收工" not in text
    assert "玩家手上现在还能用的操作" not in text


def test_the_director_lines_carry_no_performance_directives(plugin, ctx) -> None:
    """P13 契约：导演块**只给事实**，不许写死表演指令。

    需求原文是"你必须大呼小叫地让他去借高利贷、去领低保" ——
    "大呼小叫"是表演指令，"必须……"是祈使句，一句话塞三个行为指令
    等于把 Yui 变成复读机。所以这里改写成等价的事实行。
    """
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    lines = plugin._yui_director_lines(session)
    assert lines
    for line in lines:
        for token in _forbidden_directives():
            assert token not in line, f"导演块里出现了表演指令：{token}"
        assert "请" not in line, "导演块不许带祈使句"


def test_the_director_lines_have_no_death_words(plugin, ctx) -> None:
    """玩家可见文案**禁死亡语义词** —— 需求原文那句"他要是死了"不许进。"""
    _enable_god_mode(plugin, ctx)
    for mode in ("classic_beginner", "infinite"):
        session = _join(plugin, mode=mode, seed=20261130)
        for line in plugin._yui_director_lines(session):
            for word in ("死", "亡", "墓", "遗", "天台"):
                assert word not in line, f"导演块里出现了死亡语义词：{word}"


def test_the_director_lines_are_facts_not_orders(plugin, ctx) -> None:
    """导演块是**世界设定 + 客观枚举**，不是对 Yui 的命令。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    joined = "\n".join(plugin._yui_director_lines(session))
    for phrase in ("你现在的任务", "你必须", "务必", "一定要", "大呼小叫"):
        assert phrase not in joined, f"导演块里出现了命令式口吻：{phrase}"


def test_the_classic_mode_options_do_not_mention_the_welfare(plugin, ctx) -> None:
    """经典模式**没有**低保、**没有**高利贷 —— 不能许诺不存在的按钮。"""
    guard = _live_guard_module()
    options = guard.player_options_text("classic")
    assert "领取工资" not in options
    assert "高利贷" not in options
    assert "卖出持仓" in options


def test_the_infinite_mode_options_do_mention_the_welfare(plugin, ctx) -> None:
    """无限模式**有**低保与高利贷 —— 如实枚举面板上真的存在的按钮。"""
    guard = _live_guard_module()
    options = guard.player_options_text("infinite")
    assert "领取工资" in options
    assert "高利贷" in options


# ══════════════════════════════════════════════════════════════ ④ 红线


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """**绝对红线**：引擎层 / 模式层零改动。

    全部干预都在插件层改内存里的 ``player.cash`` 与 ``stock.price``
    ⇒ 500 局基线必须逐字节一致。
    """
    assert engine_mode_code_changes() == [], "引擎层 / 模式层被改动了"


def test_the_new_module_does_not_touch_the_engine() -> None:
    """``live_guard.py`` **只**用引擎的公开读接口与内存写入。"""
    text = _LIVE_GUARD.read_text(encoding="utf-8")
    assert "from engine" not in text, "live_guard 不许 import 引擎内部"
    assert "import engine" not in text, "live_guard 不许 import 引擎内部"
    for forbidden in ("advance_market", "daily_settlement", "reversal_days"):
        assert forbidden not in text, f"live_guard 不许调 {forbidden}"


def test_the_removed_directives_are_not_back_in_the_sources() -> None:
    """P12 那些写死的句子，一个都不许再出现（源码级精确匹配）。"""
    for path in (_INIT, _LIVE_GUARD):
        text = path.read_text(encoding="utf-8")
        for phrase in _removed_phrases():
            assert phrase not in text, f"{path.name} 里还留着 P12 的表演指令：{phrase}"


def test_the_p13_words_do_not_appear_in_the_new_prompt_sources() -> None:
    """P13 禁词不许出现在**新提示词片段的源码**里。

    扫的是 ``live_guard.py`` —— 它就是拼提示词事实行的那个模块。
    源码级扫描比运行时扫描更严：连"模板里写着禁词"都不许。
    """
    text = _LIVE_GUARD.read_text(encoding="utf-8")
    for token in ("嘲讽", "假装", "冷漠", "温柔", "演绎", "表演", "台词"):
        assert token not in text, f"live_guard 里出现了表演指令词：{token}"


def test_the_god_mode_never_leaks_into_the_panel_payload(plugin, ctx) -> None:
    """护栏的状态**不进面板载荷** —— 观众看不到"我们改过盘面"。"""
    _enable_god_mode(plugin, ctx)
    session = _join(plugin)
    session.player.cash = 0.0
    plugin._maybe_god_rescue(session, _engine_config(plugin), _journal())
    payload = run(plugin.market_context())
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "live_god_mode" not in dumped
    assert "庄家洗盘" not in dumped
    for key in _walk_keys(payload):
        lowered = key.lower()
        for token in _forbidden_tokens():
            assert token not in lowered, f"面板载荷泄漏了 {token}"


def test_the_god_mode_is_reachable_from_the_config_section() -> None:
    """开关必须**只**挂在 ``[director]`` 段下（不放别的段）。"""
    text = _CONFIG_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"\[director\][^\[]*live_god_mode", text), "开关不在 [director] 段里"
    assert "live_god_mode" not in text.split("[director]", 1)[0], "开关只该在 [director] 段"


def test_the_version_is_bumped() -> None:
    """P26.1 的版本守卫。"""
    toml = _PLUGIN_ROOT.joinpath("plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.13.6-live-ultimate"' in toml
    assert 'PLUGIN_VERSION = "0.13.6-live-ultimate"' in _INIT.read_text(encoding="utf-8")
