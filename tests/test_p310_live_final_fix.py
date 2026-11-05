# -*- coding: utf-8 -*-
"""P31.0 守卫：**解禁跳过 + 真实波动折线 + 行情卡片排版修复**。

这一版是直播前的最后一轮修复，钉死的是**行为契约**：

1. **跳过本日全档位解禁** —— 模式闸门拆掉了，街机 / 入门 / 无尽一律可跳
   （「每天只能跳一次」的安全网**保留**）。
2. **折线图必须有真实波动** —— Yui 那条线不再是「一条平滑上升的斜线」，
   上升段**既有涨也有跌**（锯齿），而且**确定性**（刷新不乱抖）；
   玩家那条线读的是**每 tick 的 total_assets**；画布用**原生 Canvas**
   并按**实际显示宽度 × 像素比**绘制（不再把 320px 拉成整行）。
3. **行情卡片不许被压扁** —— 行情卡有最小高度、每个方块 ``min-height: 100px``、
   巨号涨跌胶囊有 ``max-height`` + ``overflow: hidden`` + 行高限制。

**红线**：引擎层 / 模式层零改动；一屏不滚动（``100vh`` 锁定）。
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import FakeCtx, engine_mode_code_changes, make_plugin, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_BOOK = _PLUGIN_ROOT / "short_book.py"


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


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


def _panel_css() -> str:
    """面板 CSS 字符串（``RRM_CSS`` 那一段）。"""
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source, "面板里找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "面板里找不到 RRM_CSS 的结束反引号"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _method_body(source: str, name: str) -> str:
    """截出某个方法的函数体（到下一个同级定义/注释块为止）。"""
    for marker in (f"    def {name}(", f"    async def {name}("):
        if marker in source:
            break
    else:
        raise AssertionError(f"源码里找不到 {name}")
    _, _, tail = source.partition(marker)
    kept: list[str] = []
    for line in tail.split("\n")[1:]:
        if (
            line.startswith("    def ")
            or line.startswith("    @")
            or line.startswith("    # ---")
            or line.startswith("    async def ")
        ):
            break
        kept.append(line)
    return "\n".join(kept)


def _short_book():
    """按插件的加载方式拿到 ``short_book`` 模块。"""
    from plugins.mock_stock_meow import short_book

    return short_book


# ══════════════════════════════════════════════════════════════ ① 跳过解禁


def test_the_skip_day_mode_gate_is_gone() -> None:
    """``skip_day`` 里**不许**再有模式闸门。"""
    body = _method_body(_init_text(), "skip_day")
    assert 'session.mode != "infinite"' not in body, "跳过本日的模式闸门还在"
    assert "skip_day_classic_forbidden" not in body, "经典档不该再被拒绝"


def test_the_daily_limit_still_guards_the_skip() -> None:
    """「每天只能跳一次」的安全网**必须留着**（防的是同一天狂按）。"""
    body = _method_body(_init_text(), "skip_day")
    assert "skip_day_already_used" in body
    assert "skip_day_used" in body


def _skip_once(mode: str, seed: int) -> dict:
    """开一局、跳一天，返回跳过的载荷。"""
    instance = make_plugin(FakeCtx(config_path=Path("mock_stock_meow.toml")))
    run(instance.on_startup())
    run(instance.join(mode=mode, seed=seed))
    result = run(instance.skip_day())
    assert result.is_ok(), f"{mode} 跳过本日失败：{result!r}"
    payload = result.unwrap()
    assert payload["action"] == "skip_day"
    assert int(payload["day"]) == 2, f"{mode} 跳过后没有推进到第 2 天"
    return dict(payload)


def test_the_arcade_tier_can_skip_the_day(tmp_path, monkeypatch) -> None:
    """街机档也能跳过本日。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    _skip_once("classic_arcade", 7101)


def test_the_beginner_tier_can_skip_the_day(tmp_path, monkeypatch) -> None:
    """入门档也能跳过本日。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    _skip_once("classic_beginner", 7102)


def test_the_endless_tier_can_still_skip_the_day(tmp_path, monkeypatch) -> None:
    """无尽档照旧能跳（解禁不许把原有能力弄丢）。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    _skip_once("infinite", 7103)


# ══════════════════════════════════════════════════════════════ ② 真实波动


def test_the_yui_curve_wobbles_instead_of_gliding() -> None:
    """Yui 的对手曲线：上升段**既有涨也有跌**（不许再是平滑斜线）。"""
    book = _short_book()
    player = [100.0] * 30
    yui = book.yui_opponent_curve(player, 100.0)
    flat = book.YUI_OPPONENT_FLAT_TICKS
    ups = sum(1 for i in range(flat + 1, len(yui)) if yui[i] > yui[i - 1])
    downs = sum(1 for i in range(flat + 1, len(yui)) if yui[i] < yui[i - 1])
    assert ups > 0, "上升段总得往上走"
    assert downs > 0, "上升段必须出现下跌 —— 那才是「心跳」"
    # 前 10 拍仍然「极差」（每拍往下走）。
    for index in range(1, flat):
        assert yui[index] < yui[index - 1], f"第 {index} 拍不该往上走"


def test_the_yui_curve_is_deterministic() -> None:
    """曲线**必须确定性** —— 面板每次刷新都重算，用真随机会让折线乱抖。"""
    book = _short_book()
    player = [100.0, 101.0, 99.0, 102.0] * 5
    first = book.yui_opponent_curve(list(player), 100.0)
    second = book.yui_opponent_curve(list(player), 100.0)
    assert first == second


def test_the_yui_curve_still_ends_above_the_player() -> None:
    """末点仍然严格高于玩家（哪怕玩家崩了）。"""
    book = _short_book()
    for player in ([100.0] * 30, [100.0, 50.0, 10.0, 1.0], [5.0] * 12, [0.0] * 3):
        yui = book.yui_opponent_curve(list(player), 100.0)
        if not yui:
            continue
        assert yui[-1] > player[-1], f"Yui 末点 {yui[-1]} 没有超过玩家 {player[-1]}"


def test_the_player_line_reads_the_real_asset_history() -> None:
    """玩家那条线读的是**每 tick 的 total_assets**（真实资产历史）。"""
    init = _init_text()
    body = _method_body(init, "_record_equity_point")
    assert "player.total_asset(prices)" in body, "资产曲线必须记 total_asset"
    assert "_equity_points.append" in body
    # 每一拍都要记（不是只在结算时记）。
    tick_body = _method_body(init, "_tick_once")
    assert "_record_equity_point" in tick_body


def test_the_equity_chart_uses_a_native_canvas() -> None:
    """折线图必须是**原生 Canvas**（不许引外部库）。"""
    code = _panel_code()
    assert 'h("canvas"' in code, "折线图必须用原生 canvas"
    assert 'getContext("2d")' in code
    assert "beginPath" in code and "lineTo" in code
    # 不许引任何外部图表库。
    for banned in ("Chart(", "echarts", "d3.", "plotly"):
        assert banned not in code, banned


def test_the_equity_canvas_matches_its_display_size() -> None:
    """画布按**实际显示宽度 × 像素比**铺 —— 不再把 320px 拉成整行。"""
    code = _panel_code()
    start = code.index("function EquityBattle(")
    body = code[start : code.index("\n}\n", start)]
    assert "clientWidth" in body, "画布要按实际显示宽度绘制"
    assert "devicePixelRatio" in body, "画布要按像素比放大（否则拉糊）"
    assert "setTransform" in body


def test_the_equity_chart_is_tall_enough_to_see_the_jag() -> None:
    """折线画布要高一点 —— 锯齿要看得见才叫「心跳」。"""
    code = _panel_code()
    assert "height: 76" in code, "折线画布高度必须 ≥ 76px"


# ══════════════════════════════════════════════════════════════ ③ 行情排版


def test_the_market_card_keeps_a_usable_height() -> None:
    """行情卡不许被上面的巨幅 K 线压扁。"""
    css = _panel_css()
    rule = css.split(".rrm-root.rrm-live-page .rrm-anchor-market-grid {", 1)[1].split("}", 1)[0]
    assert "min-height:" in rule, "行情卡必须有最小高度"


def test_every_market_block_has_a_minimum_height() -> None:
    """每个行情方块至少 100px 高（不许被网格压成一条）。"""
    css = _panel_css()
    rule = css.split(".rrm-root .neko-card.rrm-slot {", 1)[1].split("}", 1)[0]
    assert "min-height: 100px" in rule
    assert "overflow: hidden" in rule, "撑出去的部分必须裁掉"


def test_the_giant_change_pill_cannot_break_the_card() -> None:
    """巨号涨跌胶囊：行高受限 + 有上限 + 裁切。"""
    css = _panel_css()
    rule = css.split(".rrm-root .rrm-slot .rrm-change-pill {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in rule
    assert "max-height:" in rule
    assert "line-height:" in rule
    assert "white-space: nowrap" in rule


def test_the_kline_gave_some_height_back_to_the_market() -> None:
    """K 线让回一截高度给行情卡（仍然明显高于 P27.1 的 210/26vh/330）。"""
    code = _panel_code()
    match = re.search(
        r'const KLINE_HEIGHT_CSS = "clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)', code
    )
    assert match, "K 线高度必须是 CSS 表达式"
    assert int(match.group(1)) == 220
    assert int(match.group(2)) == 30
    assert int(match.group(3)) == 400


# ══════════════════════════════════════════════════════════════ ④ 红线


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """**红线**：引擎层 / 模式层一个字节都不许动。"""
    assert engine_mode_code_changes() == [], "引擎层 / 模式层被改动了"


def test_the_page_is_still_locked_to_one_screen() -> None:
    """一屏锁定不许破（``100vh`` + ``overflow: hidden``）。"""
    css = _panel_css()
    block = css.split(".rrm-root.rrm-live-page {", 1)[1].split("}", 1)[0]
    assert "height: 100vh" in block
    assert "overflow: hidden" in block
    # 危险 CSS 一条都不许新加。
    for banned in ("@keyframes", "position", "filter", "opacity", "clip-path", "z-index"):
        assert banned not in css, banned
