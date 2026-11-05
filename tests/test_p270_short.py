# -*- coding: utf-8 -*-
"""P27.0 守卫：**直播版最终强化**的四件事。

明天上直播，这一版钉死的是：

1. **极简做空机制**（任务一）—— 倍率（1/10/50）+ 保证金；
   标的跌了赚「跌幅 × 倍率 × 保证金」，涨了扣；保证金亏光 ⇒ 爆仓。
2. **Wehos 专属破产彩蛋**（任务二）—— 名字里含有「Wehos」且因破产结算时，
   结算页给专属卡片，而不是普通破产画面。
3. **玩家 vs Yui 资产折线图**（任务三）—— Yui 前 10 拍极差，10 拍之后稳定
   上升，**最终必定比玩家高**。
4. **新手教程「报纸与团队」**（任务四）—— 开局白拿团队与高级报纸，
   后面要自己买报纸（``buy_report`` 动作真的兑现）。

贯穿全文件的红线：

* **引擎层 / 模式层零改动** —— 全部干预都在插件层（``short_book.py`` +
  ``settings`` 表）⇒ 500 局基线逐字节不变；
* **面板只给白名单字段** —— 做空持仓行一个多余字段都不给；
* **面板文案零金融术语** —— 「仓位」这类词不许进 ``panel.*``。
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
_SHORT_BOOK = _PLUGIN_ROOT / "short_book.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_STORY = _PLUGIN_ROOT / "story.json"
_TUTORIAL = _PLUGIN_ROOT / "tutorial.py"
_EGGS = _PLUGIN_ROOT / "name_easter_eggs.py"


# ---------------------------------------------------------------- 工具


def _plugin_module() -> Any:
    """插件包（``plugins.mock_stock_meow``）。"""
    return importlib.import_module("plugins.mock_stock_meow")


def _short_book() -> Any:
    return importlib.import_module("plugins.mock_stock_meow.short_book")


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_bytes().decode("utf-8"))


def _panel_source() -> str:
    return _PANEL.read_bytes().decode("utf-8")


def _session(plugin: Any) -> Any:
    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    return run(
        load_session(plugin._repo, plugin.state, engine_config=default_engine_config())
    )


def _join(
    plugin: Any, mode: str = "classic_beginner", seed: int = 20261121, name: str = ""
) -> Any:
    run(plugin.join(mode=mode, seed=seed, player_name=name))
    return _session(plugin)


def _fresh_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str) -> Any:
    """另起一个**独立数据根目录**的插件实例（换个档位就得换实例）。"""
    from conftest import FakeCtx, make_plugin

    root = tmp_path / tag
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(root / "storage"))
    instance = make_plugin(FakeCtx(config_path=root / "plugin.toml"))
    assert run(instance.on_startup()).is_ok()
    return instance


def _top_up_cash(plugin: Any, session: Any, amount: float) -> Any:
    """直接给玩家塞钱（只给单元测试用，不走业务动作）。"""
    import time as _time

    run(
        plugin._repo.transaction(
            [
                (
                    "UPDATE players SET cash = ?, updated_at = ? WHERE id = ?",
                    (float(amount), _time.time(), session.player.id),
                )
            ]
        )
    )
    return _session(plugin)


def _walk_keys(payload: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_walk_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            keys.extend(_walk_keys(item))
    return keys


def _forbidden_tokens() -> tuple[str, ...]:
    module = importlib.import_module("test_visibility")
    return tuple(module.FORBIDDEN_TOKENS)


def _first_stock(session: Any) -> Any:
    """拿一只**活着**的标的。"""
    for stock in session.market.stocks.values():
        if stock.alive:
            return stock
    raise AssertionError("市场里没有活着的标的")


# ══════════════════════════════════════════════════════════════════════
# ① 引擎层 / 模式层零改动（红线）
# ══════════════════════════════════════════════════════════════════════


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """P27.0 一行都没碰引擎层与模式层。"""
    assert engine_mode_code_changes() == []


def test_the_short_book_lives_in_the_plugin_layer() -> None:
    """做空账本必须住在**插件层**，不许混进引擎。"""
    assert _SHORT_BOOK.exists(), "缺 short_book.py"
    for layer in ("engine", "modes"):
        for path in (_PLUGIN_ROOT / layer).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_bytes().decode("utf-8")
            assert "short_book" not in text, f"{path} 引用了插件层的做空账本"
            assert "ShortPosition" not in text, f"{path} 引用了插件层的做空账本"


# ══════════════════════════════════════════════════════════════════════
# ② 任务一：做空账本的纯算术
# ══════════════════════════════════════════════════════════════════════


def test_the_leverage_choices_are_exactly_one_ten_and_fifty() -> None:
    book = _short_book()
    assert book.SHORT_LEVERAGES == (1, 10, 50)
    assert book.DEFAULT_SHORT_LEVERAGE == 10
    assert book.SHORT_MAX_POSITIONS >= 1
    assert book.SHORT_MIN_MARGIN >= 1.0


@pytest.mark.parametrize("raw,expected", [(1, 1), ("10", 10), (50.0, 50)])
def test_a_valid_leverage_is_normalized(raw: Any, expected: int) -> None:
    assert _short_book().normalize_leverage(raw) == expected


@pytest.mark.parametrize("raw", [0, 2, 5, 20, 100, -10, None, "abc", "", [1]])
def test_an_unknown_leverage_is_rejected_outright(raw: Any) -> None:
    """认不得就报「认不得」—— **不做静默兜底**。"""
    assert _short_book().normalize_leverage(raw) is None


def test_the_payout_is_the_drop_times_the_leverage_times_the_margin() -> None:
    """跌 10% ⇒ 赚 10% × 倍率 × 保证金。"""
    book = _short_book()
    position = book.open_short("FAKE-001", margin=100.0, leverage=10, price=100.0, tick=1)
    mark = book.mark_short(position, 90.0)
    assert mark.pnl == pytest.approx(100.0, rel=1e-9)  # 10% × 10 × 100
    assert mark.margin_left == pytest.approx(200.0, rel=1e-9)
    assert mark.liquidated is False


def test_a_rising_market_takes_the_margin_away() -> None:
    """涨 10% ⇒ 倒扣 10% × 倍率 × 保证金。"""
    book = _short_book()
    position = book.open_short("FAKE-001", margin=100.0, leverage=10, price=100.0, tick=1)
    mark = book.mark_short(position, 110.0)
    assert mark.pnl == pytest.approx(-100.0, rel=1e-9)
    assert mark.margin_left == pytest.approx(0.0, rel=1e-9)


def test_the_loss_never_exceeds_the_margin() -> None:
    """爆仓时最多亏掉当初押进去的那笔保证金。"""
    book = _short_book()
    position = book.open_short("FAKE-001", margin=50.0, leverage=50, price=100.0, tick=1)
    mark = book.mark_short(position, 200.0)  # 涨了一倍 ⇒ 早该爆了
    assert mark.liquidated is True
    assert mark.margin_left == 0.0
    assert mark.pnl == pytest.approx(-50.0, rel=1e-9)
    assert mark.pnl >= -position.margin


def test_the_anchor_rolls_so_a_tick_is_never_counted_twice() -> None:
    """锚点滚动 ⇒ 下一拍算的是「这一拍」的涨跌。"""
    book = _short_book()
    position = book.open_short("FAKE-001", margin=100.0, leverage=10, price=100.0, tick=1)
    first = book.mark_short(position, 90.0)
    rolled = book.roll_short(position, 90.0)
    assert rolled.entry_price == pytest.approx(90.0)
    second = book.mark_short(rolled, 90.0)
    assert second.pnl == pytest.approx(0.0, abs=1e-9)
    assert first.pnl == pytest.approx(100.0, rel=1e-9)


def test_the_position_rows_only_carry_the_whitelisted_fields() -> None:
    """面板上的空头行**一个多余字段都不给**（P4 §11）。"""
    book = _short_book()
    position = book.open_short("FAKE-001", margin=100.0, leverage=10, price=100.0, tick=3)
    rows = book.short_positions_to_rows([position], {"FAKE-001": 95.0})
    assert len(rows) == 1
    assert set(rows[0]) == {
        "stock_id",
        "margin",
        "leverage",
        "entry_price",
        "price",
        "pnl",
        "margin_left",
        "liquidated",
    }
    for token in ("heat", "seed", "reversal", "prey"):
        assert token not in json.dumps(rows[0], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# ③ 任务一：做空动作（插件层）
# ══════════════════════════════════════════════════════════════════════


def test_opening_a_short_takes_the_margin_out_of_the_cash(plugin) -> None:
    """开仓 ⇒ 保证金当场从现金里扣掉。"""
    session = _join(plugin)
    stock = _first_stock(session)
    before = float(session.player.cash)
    result = run(plugin.short(stock_id=stock.id, margin=100.0, leverage=10))
    assert result.is_ok(), result
    payload = result.value
    assert payload["leverage"] == 10
    assert payload["cash_after"] == pytest.approx(before - 100.0, rel=1e-6)
    assert len(payload["positions"]) == 1


def test_a_short_costs_a_ticket_that_the_engine_never_knows_about(plugin) -> None:
    """做空只动插件层的账本 ⇒ 引擎层的持仓纹丝不动。"""
    session = _join(plugin)
    stock = _first_stock(session)
    positions_before = len(session.player.positions)
    run(plugin.short(stock_id=stock.id, margin=100.0, leverage=1))
    after = _session(plugin)
    assert len(after.player.positions) == positions_before


def test_an_unknown_leverage_is_refused(plugin) -> None:
    session = _join(plugin)
    stock = _first_stock(session)
    result = run(plugin.short(stock_id=stock.id, margin=100.0, leverage=7))
    assert not result.is_ok()
    assert "倍率" in str(result.err())


def test_a_margin_below_the_floor_is_refused(plugin) -> None:
    session = _join(plugin)
    stock = _first_stock(session)
    result = run(plugin.short(stock_id=stock.id, margin=0.01, leverage=10))
    assert not result.is_ok()


def test_more_margin_than_cash_is_refused(plugin) -> None:
    session = _join(plugin)
    stock = _first_stock(session)
    result = run(plugin.short(stock_id=stock.id, margin=1e9, leverage=10))
    assert not result.is_ok()


def test_the_same_stock_cannot_be_shorted_twice(plugin) -> None:
    session = _join(plugin)
    stock = _first_stock(session)
    assert run(plugin.short(stock_id=stock.id, margin=10.0, leverage=1)).is_ok()
    result = run(plugin.short(stock_id=stock.id, margin=10.0, leverage=1))
    assert not result.is_ok()
    assert "已经有一笔空头" in str(result.err())


def test_the_number_of_open_shorts_is_capped(plugin) -> None:
    """同时最多几笔空头 —— 不许"满屏做空"。"""
    book = _short_book()
    session = _join(plugin)
    alive = [stock for stock in session.market.stocks.values() if stock.alive]
    assert len(alive) >= book.SHORT_MAX_POSITIONS, "市场里的标的不够开满上限"
    opened = 0
    for stock in alive:
        result = run(plugin.short(stock_id=stock.id, margin=10.0, leverage=1))
        if result.is_ok():
            opened += 1
        else:
            assert "满了" in str(result.err())
            break
    assert opened == book.SHORT_MAX_POSITIONS


def test_the_short_book_survives_a_plugin_restart(plugin) -> None:
    """账本存在 ``settings`` 表里 ⇒ 插件重启后还在。"""
    session = _join(plugin)
    stock = _first_stock(session)
    run(plugin.short(stock_id=stock.id, margin=42.0, leverage=10))
    raw = run(plugin._repo.get_setting("p270_short_book"))
    data = json.loads(raw)
    assert data["player_id"] == session.player.id
    assert len(data["positions"]) == 1
    assert data["positions"][0]["margin"] == pytest.approx(42.0)


def test_the_short_book_is_a_plugin_layer_ledger_not_a_schema_change(plugin) -> None:
    """不许给数据库加列 —— 只用既有的 ``settings`` 表。"""
    rows = run(plugin._repo.fetch_all("PRAGMA table_info(players)"))
    columns = {str(row[1]) for row in rows}
    assert "short_positions" not in columns
    assert "short_margin" not in columns
    assert "leverage" not in columns


# ══════════════════════════════════════════════════════════════════════
# ④ 任务一：面板载荷
# ══════════════════════════════════════════════════════════════════════


def test_the_panel_carries_the_short_options_and_the_positions(plugin) -> None:
    session = _join(plugin)
    stock = _first_stock(session)
    run(plugin.short(stock_id=stock.id, margin=100.0, leverage=10))
    payload = run(plugin.market_context())
    state = payload.get("state", payload)
    assert "short_positions" in state
    assert "short_options" in state
    options = state["short_options"]
    assert list(options["leverages"]) == [1, 10, 50]
    assert options["default_leverage"] == 10
    assert options["max_positions"] >= 1
    assert options["min_margin"] >= 1.0
    assert len(state["short_positions"]) == 1


def test_the_panel_never_leaks_the_hidden_fields(plugin) -> None:
    """P4 §11：面板载荷里不许有 heat / seed / 反转日 / 猎物。"""
    session = _join(plugin)
    run(plugin.short(stock_id=_first_stock(session).id, margin=10.0, leverage=1))
    payload = run(plugin.market_context())
    for key in _walk_keys(payload):
        lowered = key.lower()
        for token in _forbidden_tokens():
            assert token not in lowered, f"面板载荷泄漏了 {token}"


def test_the_panel_short_copy_has_no_finance_jargon() -> None:
    """面板文案零金融术语。"""
    table = _i18n()
    for key, value in table.items():
        if not key.startswith("panel."):
            continue
        for banned in ("仓位", "执行买入", "执行卖出", "浮盈亏", "摊薄成本"):
            assert banned not in value, f"{key} 里有金融术语「{banned}」：{value}"


def test_the_panel_short_copy_exists() -> None:
    table = _i18n()
    for key in (
        "panel.short_title",
        "panel.short_hint",
        "panel.short_stock",
        "panel.short_leverage",
        "panel.short_margin",
        "panel.short_execute",
        "panel.short_execute_sub",
        "panel.short_positions",
        "panel.short_empty",
        "panel.short_row",
        "panel.short_pnl",
        "panel.short_bad_margin",
        "panel.short_cash",
    ):
        assert key in table, f"缺 i18n 键 {key}"
        assert table[key].strip(), f"i18n 键 {key} 是空的"


def test_the_panel_does_not_hardcode_the_leverage_table() -> None:
    """倍率表只能来自后端载荷 —— 面板不许写死 1/10/50。"""
    source = _panel_source()
    assert "short_options" in source
    assert "shortLeverages" in source
    for literal in ("[1, 10, 50]", "(1, 10, 50)"):
        assert literal not in source, f"面板里写死了倍率表：{literal}"


def test_the_panel_has_a_short_button_next_to_the_buy_one() -> None:
    """做空按钮必须真在面板里（不是只有后端）。"""
    source = _panel_source()
    assert 'callAction("short"' in source
    assert "rrm-pill-short" in source
    assert "panel.short_execute" in source


# ══════════════════════════════════════════════════════════════════════
# ⑤ 任务三：玩家 vs Yui 的资产曲线
# ══════════════════════════════════════════════════════════════════════


def test_yui_loses_for_the_first_ten_ticks_and_then_climbs() -> None:
    """前 10 拍极差（每拍往下走），10 拍之后**波动**上行。

    P31.0：上升段不再是一条直线 —— 每一拍都有涨有跌（**锯齿**），
    像真的行情那样一跳一跳的；但**整体**仍然往上走，
    末点依旧严格高于玩家。
    """
    book = _short_book()
    player = [100.0] * 30
    yui = book.yui_opponent_curve(player, 100.0)
    assert len(yui) == len(player)
    flat = book.YUI_OPPONENT_FLAT_TICKS
    for index in range(1, flat):
        assert yui[index] < yui[index - 1], f"第 {index} 拍不该往上走"
    ups = sum(1 for index in range(flat + 1, len(yui)) if yui[index] > yui[index - 1])
    downs = sum(1 for index in range(flat + 1, len(yui)) if yui[index] < yui[index - 1])
    assert ups > 0, "上升段总得往上走"
    assert downs > 0, "上升段必须有下跌（锯齿）—— 不能是一条平滑直线"
    assert yui[-1] > yui[flat], "整体趋势仍然向上"


def test_yui_always_ends_above_the_player() -> None:
    """最终必定比玩家高 —— 玩家崩了也高。"""
    book = _short_book()
    for player in ([100.0] * 30, [100.0, 50.0, 10.0, 1.0], [5.0] * 12, [0.0] * 3):
        yui = book.yui_opponent_curve(list(player), 100.0)
        if not yui:
            continue
        assert yui[-1] > player[-1], f"Yui 末点 {yui[-1]} 没有超过玩家 {player[-1]}"


def test_the_opponent_curve_is_empty_without_a_player_curve() -> None:
    assert _short_book().yui_opponent_curve([], 100.0) == []


def test_the_equity_block_gives_two_curves_of_the_same_length(plugin) -> None:
    session = _join(plugin)
    plugin._equity_points = [100.0, 101.0, 102.0]
    plugin._equity_player_id = session.player.id
    block = plugin._equity_block(session)
    assert set(block) == {"player", "yui"}
    assert len(block["player"]) == len(block["yui"]) == 3


def test_the_panel_draws_two_lines_with_the_right_colours() -> None:
    """蓝线 = 玩家，红线 = Yui。"""
    source = _panel_source()
    assert "EQUITY_PLAYER_COLOR" in source
    assert "EQUITY_YUI_COLOR" in source
    assert "#5B8FF9" in source
    assert "#F2677F" in source
    assert "EquityBattle" in source
    assert "panel.equity_taunt" in source


def test_the_equity_copy_tells_the_player_the_truth() -> None:
    table = _i18n()
    for key in (
        "panel.equity_title",
        "panel.equity_player",
        "panel.equity_yui",
        "panel.equity_taunt",
        "panel.equity_empty",
    ):
        assert key in table, f"缺 i18n 键 {key}"
    assert "猫娘" in table["panel.equity_taunt"]


def test_the_panel_has_no_hardcoded_chinese() -> None:
    """面板代码零硬编码中文（一律走 i18n）。"""
    source = _panel_source()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    source = re.sub(r"`(?:[^`\\]|\\.)*`", "", source, flags=re.S)
    source = re.sub(r'"(?:[^"\\]|\\.)*"', '""', source)
    source = re.sub(r"'(?:[^'\\]|\\.)*'", "''", source)
    assert not re.search(r"[一-鿿]", source), "面板里出现了硬编码中文"


# ══════════════════════════════════════════════════════════════════════
# ⑥ 任务二：Wehos 专属破产彩蛋
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Wehos", True),
        ("wehos", True),
        ("Wehos_QA", True),
        ("WehosCat", True),
        ("群里的小 Wehos", True),
        ("Weho", False),
        ("Wecos", False),
        ("", False),
    ],
)
def test_the_developer_cat_keyword_is_a_contains_match(name: str, expected: bool) -> None:
    module = importlib.import_module("plugins.mock_stock_meow.name_easter_eggs")
    assert module.is_developer_cat(name) is expected


def test_the_special_ending_only_shows_up_for_a_dead_wehos(plugin) -> None:
    """命中条件 = 名字含 Wehos **且** 因破产结算。"""
    session = _join(plugin, name="Wehos")
    assert plugin._special_ending(session, dead=False) is None
    ending = plugin._special_ending(session, dead=True)
    assert ending is not None
    assert ending["kind"] == "developer_cat"
    assert "开发猫" in ending["body"]
    assert "Yui" in ending["body"]


def test_a_normal_player_never_gets_the_special_ending(plugin) -> None:
    session = _join(plugin, name="路人甲")
    assert plugin._special_ending(session, dead=True) is None


def test_the_settlement_report_carries_the_special_ending(plugin) -> None:
    session = _join(plugin, name="Wehos")
    from engine.config import default_engine_config

    report = run(plugin._build_settlement_payload(session, default_engine_config()))
    assert "special_ending" in report
    assert report["special_ending"] is None  # 还活着 ⇒ 普通结算


def test_the_special_ending_copy_exists() -> None:
    table = _i18n()
    assert "special.developer_cat_title" in table
    assert "special.developer_cat_body" in table
    assert "开发猫" in table["special.developer_cat_body"]


def test_the_panel_renders_the_special_ending_card() -> None:
    source = _panel_source()
    assert "specialEnding" in source
    assert "rrm-special-ending" in source
    assert "special_ending" in source


def test_the_special_ending_card_skips_the_ordinary_bankrupt_screen() -> None:
    """专属结局时**不走**普通破产三屏。"""
    source = _panel_source()
    assert "sceneScreen < 3 && !specialEnding" in source


def test_the_special_ending_source_has_no_banned_words() -> None:
    """专属卡文案本身必须干净（去死亡化守卫）。"""
    table = _i18n()
    for key in ("special.developer_cat_title", "special.developer_cat_body"):
        value = table[key]
        for banned in ("死亡", "墓", "遗", "天台"):
            assert banned not in value, f"{key} 里有禁词「{banned}」：{value}"


# ══════════════════════════════════════════════════════════════════════
# ⑦ 任务四：报纸与团队
# ══════════════════════════════════════════════════════════════════════


def test_the_first_story_chapter_mentions_the_team_and_the_paper() -> None:
    story = json.loads(_STORY.read_bytes().decode("utf-8"))
    text = json.dumps(story, ensure_ascii=False)
    assert "团队" in text
    assert "报纸" in text


def test_the_story_situation_field_has_no_polite_request() -> None:
    """``situation`` 是唯一进 prompt 的字段 ⇒ 里面不许有「请」。"""
    story = json.loads(_STORY.read_bytes().decode("utf-8"))
    for chapter in story.get("chapters", []):
        situation = str(chapter.get("situation", ""))
        assert "请" not in situation, f"{chapter.get('id')} 的 situation 里出现了「请」"


def test_the_tutorial_starts_with_the_team_and_the_paper() -> None:
    module = importlib.import_module("plugins.mock_stock_meow.tutorial")
    steps = module.TIER_STEPS["classic_beginner"]
    first = steps[0]
    title_key = getattr(first, "title_key")
    body_key = getattr(first, "body_key")
    assert title_key.endswith(".title")
    assert body_key.endswith(".body")
    assert title_key == "tutorial.team.market.title"
    assert body_key == "tutorial.team.market.body"
    fact = str(getattr(first, "fact", ""))
    assert "团队" in fact or "报纸" in fact


def test_the_tutorial_copy_keys_exist() -> None:
    table = _i18n()
    assert "tutorial.team.market.title" in table
    assert "tutorial.team.market.body" in table
    body = table["tutorial.team.market.body"]
    assert "团队" in body
    assert "报纸" in body
    assert "自己买报纸" in body


def test_the_beginner_and_the_arcade_share_the_first_step() -> None:
    """P19.0 契约：入门档**前 2 步**复用街机档的文案 —— 团队与报纸那段也一样。

    P28.0：共享范围收窄到前 2 步（看盘 / 下单）。街机档后两步换成
    **专属的砍减口径文案**（街机档没有台账也没有水位条），
    入门档继续用「看持仓 / 看水位」那套（入门档两样都有）。
    """
    module = importlib.import_module("plugins.mock_stock_meow.tutorial")
    arcade = module.TIER_STEPS["classic_arcade"]
    beginner = module.TIER_STEPS["classic_beginner"]
    for index in range(2):
        assert beginner[index].title_key == arcade[index].title_key, index
        assert beginner[index].body_key == arcade[index].body_key, index
    for index in (2, 3):
        assert beginner[index].title_key != arcade[index].title_key, index
        assert beginner[index].body_key != arcade[index].body_key, index


def test_buying_a_paper_is_free_for_the_first_two_tiers(plugin) -> None:
    """新手前两档白拿 —— 那就是「老爸给的高级报纸」。"""
    session = _join(plugin, mode="classic_beginner")
    before = float(session.player.cash)
    result = run(plugin.buy_report())
    assert result.is_ok(), result
    after = _session(plugin)
    assert after.player.cash == pytest.approx(before, rel=1e-6)
    assert after.player.analyses_used <= 0


def test_buying_a_paper_costs_money_for_the_later_tiers(
    plugin, tmp_path, monkeypatch
) -> None:
    """后面几档要自己掏钱。"""
    other = _fresh_plugin(tmp_path, monkeypatch, "advanced")
    session = _join(other, mode="classic_advanced")
    before = float(session.player.cash)
    result = run(other.buy_report())
    assert result.is_ok(), result
    after = _session(other)
    assert after.player.cash == pytest.approx(before - 60.0, rel=1e-6)
    assert after.player.analyses_used <= 0


def test_the_daily_paper_limit_is_enforced(plugin, tmp_path, monkeypatch) -> None:
    """一天最多买三份。"""
    other = _fresh_plugin(tmp_path, monkeypatch, "limit")
    session = _join(other, mode="classic_advanced")
    session = _top_up_cash(other, session, 5000.0)
    for _ in range(3):
        assert run(other.buy_report()).is_ok()
    result = run(other.buy_report())
    assert not result.is_ok()
    assert "够了" in str(result.err())


def test_the_paper_price_comes_from_the_backend(plugin, tmp_path, monkeypatch) -> None:
    """面板不硬编码价格与模式。"""
    _join(plugin, mode="classic_beginner")
    payload = run(plugin.market_context())
    state = payload.get("state", payload)
    assert state["report_free"] is True
    assert state["report_price"] == 0.0

    other = _fresh_plugin(tmp_path, monkeypatch, "priced")
    _join(other, mode="classic_advanced")
    payload2 = run(other.market_context())
    state2 = payload2.get("state", payload2)
    assert state2["report_free"] is False
    assert state2["report_price"] > 0.0


def test_the_panel_has_a_buy_paper_button(plugin) -> None:
    source = _panel_source()
    assert 'callAction("buy_report"' in source
    assert "panel.report_buy" in source


def test_buying_a_paper_does_not_change_the_engine_schema(plugin) -> None:
    """「报纸」复用既有的分析额度机制 ⇒ 不许加列、不许改每日次数。"""
    rows = run(plugin._repo.fetch_all("PRAGMA table_info(players)"))
    columns = {str(row[1]) for row in rows}
    assert "reports_bought" not in columns
    assert "papers" not in columns
    module = importlib.import_module("plugins.mock_stock_meow")
    assert module.ANALYSES_PER_DAY == 2


# ══════════════════════════════════════════════════════════════════════
# ⑧ 版本与文件纪律
# ══════════════════════════════════════════════════════════════════════


def test_the_new_files_are_committed_in_the_plugin_layer() -> None:
    """P27.0 的新代码必须住在插件层。"""
    for relative in ("short_book.py", "name_easter_eggs.py", "tutorial.py"):
        assert (_PLUGIN_ROOT / relative).exists(), f"缺 {relative}"


def test_the_i18n_file_is_valid_json_with_flat_keys() -> None:
    table = _i18n()
    assert isinstance(table, dict)
    for key in table:
        assert isinstance(key, str)
        assert "." in key, f"i18n 键 {key} 不是点分键"


def test_the_eggs_module_exposes_the_developer_cat_helper() -> None:
    source = _EGGS.read_bytes().decode("utf-8")
    assert "DEVELOPER_CAT_KEYWORD" in source
    assert "def is_developer_cat" in source


def test_the_short_book_module_has_no_database_dependency() -> None:
    """账本只做算术 —— 不许 import 数据库或宿主。"""
    source = _SHORT_BOOK.read_bytes().decode("utf-8")
    for banned in ("import sqlite3", "from engine", "import engine", "plugin.sdk"):
        assert banned not in source, f"short_book.py 不该依赖 {banned}"


def test_the_init_module_imports_the_short_book_relatively() -> None:
    source = _INIT.read_bytes().decode("utf-8")
    assert "from .short_book import" in source
    assert "import short_book" not in source
