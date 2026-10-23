"""P7：社区抽象化、节目效果与双版本（回归防线）。

覆盖四件事：

1. **双版本**：纯净版（``director.enabled = false``，默认）下导演指令**恒报错**；
   特制版下才生效。
2. **引擎零改动**：导演暴跌走的是引擎**既有**的 ``apply_jump``，
   引擎层（``engine/``）与模式层（``modes/``）一个字节都没改。
3. **社区名册**：名册是**数据**（``community_roster.json``）；名册新闻只换
   **标题**，正文（引擎的方向信号）**一个字都不动**（P4 §11 反泄露铁律）。
4. **节目效果**：猫娘弹幕、三档视觉降级、面板里的 P7 特效都在位。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from conftest import run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_ROSTER_FILE = _PLUGIN_ROOT / "community_roster.json"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", ""))


def _set_business_config(plugin: Any, ctx: Any, payload: dict[str, Any]) -> None:
    """改业务配置并**清掉 5 秒缓存**（否则读到的还是旧值）。"""
    ctx.config_payload = dict(payload)
    plugin._business_config_cache = None
    plugin._business_config_cached_at = 0.0


def _enable_director(plugin: Any, ctx: Any, enabled: bool = True) -> None:
    _set_business_config(plugin, ctx, {"director": {"enabled": enabled}})


# ---------------------------------------------------------------- 双版本


def test_pure_version_rejects_every_director_command(plugin, ctx) -> None:
    """**开源纯净版（默认）**：导演指令一条都不能用。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    assert plugin._director_on() is False
    for op in ("crash", "loan", "welfare", "kill"):
        result = run(plugin.director(op=op, stock_id="FAKE-001"))
        assert _error_code(result) == "director_disabled", op


def test_pure_version_is_the_default(plugin) -> None:
    """没配 ``[director]`` 时就是纯净版（默认关）。"""
    assert plugin._director_on() is False


def test_director_version_enables_the_console(plugin, ctx) -> None:
    """特制版：导演台可用，面板载荷里也会亮出开关。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    assert plugin._director_on() is True
    payload = run(plugin.market_context())
    assert payload["visual"]["director"]["enabled"] is True


def test_director_rejects_an_unknown_op(plugin, ctx) -> None:
    """特制版下也不接受看不懂的指令。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    assert _error_code(run(plugin.director(op="explode"))) == "director_bad_op"


def test_director_rejects_an_unknown_stock(plugin, ctx) -> None:
    """暴跌指令指向不存在的标的时要有明确错误。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    assert (
        _error_code(run(plugin.director(op="crash", stock_id="FAKE-999")))
        == "director_stock_not_found"
    )


def test_director_kill_marks_the_player_dead(plugin, ctx) -> None:
    """立即斩杀：复用引擎既有的 ``apply_death``。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = _value(run(plugin.director(op="kill")))
    assert payload["op"] == "kill"
    assert payload["death_reason"] == "harvested"
    status = _value(run(plugin.status()))
    assert status["alive"] is False


def test_director_refuses_a_loan_in_classic_mode(plugin, ctx) -> None:
    """经典模式没有还款路径 —— 强制放贷就是白送钱，必须拒绝。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    result = run(plugin.director(op="loan"))
    assert _error_code(result) == "director_loan_classic_forbidden"


def test_director_loan_in_infinite_mode_uses_the_engine_amounts(plugin, ctx) -> None:
    """无限模式的强制放贷：金额与引擎配置**完全一致**（借 500 欠 600）。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="infinite", seed=9305)))
    before = _value(run(plugin.status()))["cash"]
    payload = _value(run(plugin.director(op="loan")))
    assert payload["loan_debt"] == pytest.approx(600.0)
    assert payload["cash"] == pytest.approx(before + 500.0)


def test_director_crash_is_queued_and_applied_on_the_next_tick(plugin, ctx) -> None:
    """暴跌是**排队制**：下一个 tick 才生效，且走引擎既有的 ``apply_jump``。"""
    _enable_director(plugin, ctx)
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = _value(run(plugin.director(op="crash", stock_id="FAKE-001")))
    assert payload["queued"] is True

    session = plugin._session_or_error  # noqa: F841 - 只是确认入口存在
    market = _market_of(plugin)
    stock = market.stocks["FAKE-001"]
    price_before = stock.price

    run(plugin._tick_once())

    market_after = _market_of(plugin)
    assert market_after.stocks["FAKE-001"].price < price_before, "暴跌必须真的跌"


def _market_of(plugin: Any) -> Any:
    """读回当前这一局的市场（走插件自己的落库/读库路径）。"""
    from plugins.reverse_rich_sim.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=run(plugin._engine_config())))
    assert session is not None
    return session.market


# ---------------------------------------------------------------- 引擎零改动


def test_engine_and_mode_layers_are_untouched_by_p7() -> None:
    """P7 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "engine", "modes"],
        cwd=str(_PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    assert not changed, f"引擎层/模式层不许改动：{changed}"


# ---------------------------------------------------------------- 社区名册


def test_roster_file_is_valid_json_with_members() -> None:
    """名册是**数据**：格式合法、成员齐全、抽象新闻池非空。"""
    data = json.loads(_ROSTER_FILE.read_text(encoding="utf-8"))
    assert data["version"] >= 1
    members = data["members"]
    assert len(members) >= 2
    aliases = {member["alias"] for member in members}
    assert "星辰" in aliases
    assert "Wehos" in aliases
    for member in members:
        assert member["stock_id"].startswith("FAKE-")
        assert member["headlines"], member["alias"]
        for headline in member["headlines"]:
            for banned in ("FX战士", "久留美", "股票", "期货", "外汇", "基金", "证券"):
                assert banned not in headline, (member["alias"], headline)


def test_roster_loads_from_the_plugin_root(plugin) -> None:
    """插件能读到名册（读不到就退化成空名册，但不能崩）。"""
    members = plugin._roster_members()
    assert members
    assert plugin._roster_for_stock("FAKE-001") is not None
    assert plugin._roster_for_stock("FAKE-999") is None


def test_roster_headline_does_not_touch_the_news_content(plugin, ctx) -> None:
    """名册新闻**只换标题**；正文（引擎的方向信号）一个字都不动。"""
    import dataclasses

    from plugins.reverse_rich_sim.engine.models import NewsItem

    plain = NewsItem(
        id="news-roster-1",
        day=1,
        type="true",
        stock_id="FAKE-777",
        content="FAKE-777新项目进度低于预期。",
        accuracy=0.7,
    )
    headline, alias, title = plugin._roster_news_headline(plain)
    assert alias is None, "非名册标的应当原样返回引擎标题"
    assert headline == "FAKE-777新项目进度低于预期。"
    assert plain.content == "FAKE-777新项目进度低于预期。"

    member_news = dataclasses.replace(plain, stock_id="FAKE-001")
    headline, alias, title = plugin._roster_news_headline(member_news)
    assert alias == "星辰"
    assert title == "定海神针股"
    assert headline.startswith("星辰：")
    assert member_news.content == "FAKE-777新项目进度低于预期。", "正文不许被名册改写"


def test_roster_news_detail_reports_the_alias(plugin) -> None:
    """``news_detail`` 会带上群友别名与称号。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    market = _market_of(plugin)
    if not market.news_today:
        pytest.skip("这一局没有新闻")
    item = market.news_today[0]
    detail = _value(run(plugin.news_detail(item.id)))
    # 名册槽位命中与否都合法：命中就必须带别名，没命中必须是 None。
    if detail["roster_alias"] is None:
        assert detail["headline"]
    else:
        assert detail["roster_alias"] in {"星辰", "Wehos", "老王", "小明"}


# ---------------------------------------------------------------- 猫娘弹幕


def test_buy_pushes_a_danmaku(plugin) -> None:
    """买入会弹一条猫娘弹幕（运行时没有卡片 API ⇒ 嘲讽改走面板弹幕）。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    stocks = _value(run(plugin.market()))["stocks"]
    stock_id = next(stock["id"] for stock in stocks if stock["alive"])
    _value(run(plugin.buy(stock_id=stock_id, amount=1)))
    payload = run(plugin.market_context())
    danmaku = payload["visual"]["danmaku"]
    assert danmaku, "买入之后必须有一条弹幕"
    assert danmaku[-1]["text"]


def test_sell_pushes_a_danmaku(plugin) -> None:
    """卖出也会弹弹幕。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    stocks = _value(run(plugin.market()))["stocks"]
    stock_id = next(stock["id"] for stock in stocks if stock["alive"])
    _value(run(plugin.buy(stock_id=stock_id, amount=1)))
    before = len(run(plugin.market_context())["visual"]["danmaku"])
    _value(run(plugin.sell(stock_id=stock_id, amount=1)))
    after = run(plugin.market_context())["visual"]["danmaku"]
    assert len(after) > before


# ---------------------------------------------------------------- 视觉降级


def test_visual_stage_starts_rich(plugin) -> None:
    """开局是"富二代"档。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = run(plugin.market_context())
    assert payload["visual"]["stage"] == "rich"
    assert payload["visual"]["critical"] is False


def test_visual_stage_turns_broke_after_the_threshold(plugin) -> None:
    """过了阈值就是"断供"档。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    player = _player_of(plugin)
    # 天数要**落库**才作数（面板每次都重新读库）。
    run(
        plugin._repo.transaction(
            [
                (
                    "UPDATE players SET day = ? WHERE id = ?",
                    (5, player.id),
                )
            ]
        )
    )
    payload = run(plugin.market_context())
    assert payload["visual"]["stage"] == "broke"


def test_visual_stage_is_dying_in_infinite_mode(plugin) -> None:
    """无限模式直接是"濒死"档。"""
    _value(run(plugin.join(mode="infinite", seed=9305)))
    payload = run(plugin.market_context())
    assert payload["visual"]["stage"] == "dying"


def test_visual_block_carries_no_hidden_fields(plugin) -> None:
    """``visual`` 块不许出现隐藏字段（heat / seed / 反转日…）。"""
    _value(run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = run(plugin.market_context())
    forbidden = ("heat", "seed", "reversal", "accuracy", "prey_index")

    def walk(node: Any) -> list[str]:
        keys: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                keys.append(str(key))
                keys.extend(walk(value))
        elif isinstance(node, (list, tuple)):
            for item in node:
                keys.extend(walk(item))
        return keys

    for key in walk(payload["visual"]):
        for token in forbidden:
            assert token not in key, key


def _player_of(plugin: Any) -> Any:
    from plugins.reverse_rich_sim.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=run(plugin._engine_config())))
    assert session is not None
    return session.player


# ---------------------------------------------------------------- 面板特效


def test_panel_contains_the_p7_effects() -> None:
    """面板里必须有 P7 的四类特效（源码扫描）。"""
    source = _PANEL.read_text(encoding="utf-8")
    # 病危闪烁 + 动画
    assert "@keyframes rrm-panic" in source
    assert "rrm-critical" in source
    assert "critical_emoji" in source
    # 荒诞弹窗（点不掉）
    assert "Modal" in source
    assert "closeOnBackdrop: false" in source
    assert "closeOnEscape: false" in source
    assert "visual.loan_modal_body" in source
    # Emoji 海
    assert "🤡" in source and "🪦" in source
    assert "visual.emoji_sea_title" in source
    # 按钮漂移
    assert "DriftButton" in source
    assert "driftRate={0.05}" in source
    assert "visual.drift_miss" in source
    # 弹幕悬浮窗 + 导演台（长按）
    assert "rrm-danmaku" in source
    assert "onPointerDown" in source
    assert "visual.director_title" in source
    # 三档降级
    assert "rrm-stage-rich" in source
    assert "rrm-stage-broke" in source
    assert "rrm-stage-dying" in source


def test_panel_emoji_seam_is_at_least_fifty() -> None:
    """Emoji 海至少 50 个（规格硬要求）。"""
    source = _PANEL.read_text(encoding="utf-8")
    assert "Math.max(50" in source


def test_pure_version_does_not_leak_the_director_hint() -> None:
    """开源纯净版**不许剧透**导演台。

    真机验收（0.8.0）发现：标题栏右端的「长按标题栏」提示是**无条件渲染**的，
    于是纯净版也看得见导演台的入口提示 —— 与「纯净版 = 无后台干预」的规格相悖。
    长按逻辑本身早被 ``startHold`` 的 ``directorEnabled`` 挡住了，但提示文字漏了。
    """
    source = _PANEL.read_text(encoding="utf-8")
    assert "directorEnabled" in source
    # 提示只能在 directorEnabled 为真时渲染。
    assert "directorEnabled\n      ? h(" in source
    # 长按本身也必须被开关挡住。
    assert "if (!directorEnabled) return" in source
    # 漂移按钮同样只在特制版生效。
    assert "const driftEnabled = directorEnabled" in source


def test_i18n_has_the_p7_keys() -> None:
    """P7 的键族都在 i18n 里（文案不许硬编码在代码里）。"""
    data = json.loads(_I18N_FILE.read_text(encoding="utf-8"))
    for key in (
        "error.director_disabled",
        "error.director_bad_op",
        "error.director_stock_not_found",
        "error.director_loan_classic_forbidden",
        "taunt.buy.1",
        "taunt.sell.1",
        "taunt.skip_day.1",
        "taunt.loan.1",
        "taunt.bankrupt.1",
        "director.note.crash",
        "director.note.loan",
        "director.note.welfare",
        "director.note.kill",
        "visual.stage_rich",
        "visual.stage_broke",
        "visual.stage_dying",
        "visual.cash_broke",
        "visual.drift_miss",
        "visual.loan_modal_body",
        "visual.danmaku_title",
        "visual.director_title",
        "visual.roster_title",
        "visual.emoji_sea_title",
    ):
        assert key in data, key


def test_p7_copy_is_configurable_not_hardcoded() -> None:
    """P7 的抽象文案必须走 i18n（规格：文案不许硬编码在代码里）。

    真机验收（0.8.0）发现两处漏网：

    1. 导演台的四条 ``note`` 是直接写死的中文字面量；
    2. 普通 ``daily_loan`` 入口压根没推弹幕（只有导演强制放贷那条推了），
       于是 ``taunt.loan.*`` 键族在正常玩法里永远用不上。

    修法：全部改成 ``i18n_text(key, fallback)`` 查表（沿用 P5/P6 的既有模式），
    键值落在 ``i18n/zh-CN.json``。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    for key in (
        "director.note.crash",
        "director.note.loan",
        "director.note.welfare",
        "director.note.kill",
        "taunt.bankrupt.1",
        "taunt.loan.1",
    ):
        assert f'"{key}"' in source, key
    # 导演台的 note 不许再是裸中文字面量。
    for literal in (
        '"note": "魔爪契约已生效。明天记得还，忘了就当你签的是遗嘱。"',
        '"note": "恭喜，你已经连续七天没去领那份不该领的东西了。"',
        '"note": "收工。"',
        '"note": f"安排 {stock.name} 在下一个 tick 崩给你看。"',
    ):
        assert literal not in source, literal
    # 破产弹幕不许再是裸字面量。
    assert 'self._push_danmaku("你按了破产。放心，天台的风是免费送你的。")' not in source


def test_config_example_has_the_director_and_visual_sections() -> None:
    """双版本开关与视觉阈值都写在配置里（**不是**写死在代码里）。"""
    text = (_PLUGIN_ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "[director]" in text
    assert "enabled = false" in text
    assert "[visual]" in text
    assert "broke_day_threshold" in text
    assert "emoji_sea_size" in text
