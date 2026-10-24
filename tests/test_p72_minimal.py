# -*- coding: utf-8 -*-
"""P7.2 的守卫：UI 极简重构 + 终极抽象补全。

这一阶段的硬要求（和 0.8.1 的防崩守卫一样，全部用**源码扫描 + 载荷断言**
锁死，防止以后有人手滑把它们改回去）：

1. **UI 极简**：行情表/持仓表不许再出现专业列（成本价 / 可卖 / 占比 / 标签），
   必须有走势小折线；操作区必须是一键梭哈 / 一键割肉。
2. **K 线是主视觉**：K 线卡片必须在面板的最上面（源码顺序守卫）。
3. **文案去金融化**：零花钱 / 家底 / 棺材本、梭哈 / 割肉、底裤被扒；
   三档字体（圆润 / 变细 / 闪红）必须在，但**不许**用 CSS 动画实现。
4. **名册实体化**：群友按**波动率排名**绑定到真实槽位（引擎零改动）。
5. **猫娘弹幕**：每次点击（梭哈 / 割肉 / 跳过本日 / 借高利贷 / 领工资 /
   拒绝二道贩子 / 花钱分析）都要刷新一句嘴臭。
6. **导演台**：``boom`` 指令（赛博弹窗轰炸）必须在纯净版被挡住。
7. **数值零改动**：引擎层与模式层一个字节都不许动；500 局基线逐位一致。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_ROSTER_FILE = _PLUGIN_ROOT / "community_roster.json"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _panel_source()
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    code = "\n".join(kept)
    marker = "const RRM_CSS = `"
    if marker in code:
        head, _, rest = code.partition(marker)
        _, _, tail = rest.partition("`")
        code = head + "\n/* <RRM_CSS> */\n" + tail
    return code


def _panel_css() -> str:
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source
    _, _, rest = source.partition(marker)
    body, _, _ = rest.partition("`")
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _function_source(code: str, name: str, until: str) -> str:
    """抽出 ``function <name>(`` 到下一个 ``function <until>(`` 之间的源码。

    P8.0 起行情 / 持仓不再是**表格**，而是**方块卡片网格**（``StockTile`` /
    ``PositionTile`` 组件）。所以这些守卫改成直接盯组件本体，而不是去
    ``</Card>`` 之间切一刀 —— 表格结构已经不存在了。
    """
    start = code.find(f"function {name}(")
    assert start > 0, f"面板里必须有 {name} 组件"
    end = code.find(f"function {until}(", start)
    assert end > start, f"面板里 {name} 后面必须有 {until} 组件"
    return code[start:end]


def _run(coro: Any) -> Any:
    return asyncio_run(coro)


def _value(result: Any) -> dict[str, Any]:
    """Ok 结果拆包（和 test_p7_effects 的 _value 一个口径）。

    ``market_context()`` 直接返回 dict，别的入口返回 ``Ok`` —— 两种都收。
    """
    if isinstance(result, dict):
        return result
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------- 数值零改动


def test_engine_and_mode_layers_are_still_untouched() -> None:
    """P7.2 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "engine", "modes"],
        cwd=str(_PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    assert not changed, f"引擎层/模式层不许改动：{changed}"


# ---------------------------------------------------------------- UI 极简


def test_market_tiles_have_no_professional_columns() -> None:
    """行情方块卡（P8.0）删掉成本价 / 可卖 / 占比 / 标签，只留名称 + 走势 + 现价 + 涨跌。

    P7.2 时代这是「行情表」的守卫；P8.0 把表格换成**方块卡片网格**，
    不变式不变 —— 还是那四样信息，多一样都不许。
    """
    code = _panel_code()
    tile = _function_source(code, "StockTile", "PositionTile")
    for banned in ("panel.cost", "panel.sellable", "panel.weight", "panel.tag"):
        assert banned not in tile, f"行情卡里不许再出现专业列 {banned}"
    # 必须有：名称、走势小折线、现价、超大涨跌。
    assert "stock.display_name || stock.name" in tile
    assert "Sparkline" in tile
    assert "visual.trend_title" in tile
    assert "visual.price_title" in tile
    assert "visual.change_title" in tile
    assert "money(stock.price)" in tile
    assert "changeText(stock.change)" in tile
    assert 'fontSize: "26px"' in tile


def test_position_tiles_only_keep_name_value_and_pnl() -> None:
    """持仓方块卡（P8.0）只留名称 / 当前市值 / 大号盈亏。"""
    code = _panel_code()
    tile = _function_source(code, "PositionTile", "Kline")
    for banned in ("panel.amount", "panel.cost", "panel.sellable", "panel.weight", "panel.tag"):
        assert banned not in tile, f"持仓卡里不许再出现 {banned}"
    assert "position.display_name || position.name" in tile
    assert "visual.pnl_title" in tile
    assert "unrealized_pnl" in tile
    assert "market_value" in tile
    assert 'fontSize: "26px"' in tile


def test_change_numbers_are_huge_and_colored() -> None:
    """涨跌数字必须**超大号 + 红绿**（红/绿走宿主变量）。"""
    code = _panel_code()
    assert 'fontSize: "26px"' in code
    assert "changeColor(stock.change)" in code
    assert "var(--success)" in code
    assert "var(--danger)" in code


def test_sparkline_is_present_and_safe() -> None:
    """走势小折线：用 canvas，画不了就降级成柱子（绝不能抛错）。"""
    code = _panel_code()
    assert "function Sparkline(" in code
    assert 'h("canvas"' in code
    assert "getContext(\"2d\")" in code
    # 拿不到 2D 上下文时必须降级，而不是崩面板。
    assert "setBroken(true)" in code


def test_kline_is_the_first_card_and_full_width() -> None:
    """K 线是**主视觉**：在面板最上面，而且自适应铺满全宽。

    P8.0 把它从 190px 加高到 232px（它是上半区的主视觉）。这里不写死数字 ——
    守卫盯的是「**够高**」这个不变式，具体数值以后还能再调。
    """
    code = _panel_code()
    playing = code.split('t("panel.trade")', 1)[0]
    kline_at = playing.find('t("panel.kline")')
    market_at = playing.find('t("visual.market_title")')
    holdings_at = playing.find('t("visual.holdings_title")')
    assert kline_at > 0, "面板里必须有 K 线卡片"
    assert kline_at < market_at < holdings_at, "K 线必须在行情表与持仓表**之前**"
    # 全宽：柱子用 flex 撑开，而不是固定宽度挤在左边。
    assert 'flex: "1 1 0"' in code
    # 主视觉：高度不能低于 200px。
    match = re.search(r"const height = (\d+)", code)
    assert match, "K 线必须有明确的高度"
    assert int(match.group(1)) >= 200, f"K 线是主视觉，高度只有 {match.group(1)}px"


def test_trade_area_is_one_click_only() -> None:
    """操作区是**快餐按钮**：一键梭哈 / 一键割肉。"""
    code = _panel_code()
    assert "visual.buy_all" in code
    assert "visual.sell_all" in code
    # 不许再有数量输入框（下拉框也只用来选标的）。
    assert "NumberInput" not in code
    assert "buyAllAmount" in code
    assert "sellAllAmount" in code


def test_kline_is_always_present_in_the_payload(plugin) -> None:
    """K 线是**主视觉**：载荷里必须至少有一只标的带 K 线。

    没有持仓、也没有爆炸标的时，如果不多带一只，K 线卡片一开局就是空的 ——
    主视觉不能是空白。
    """
    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    # 走两个 tick，让 K 线真的有数据（第 0 tick 时所有标的都是空的）。
    _run(plugin._tick_once())
    _run(plugin._tick_once())
    payload = _value(_run(plugin.market_context()))
    assert payload["kline_stock_ids"], "载荷里必须有带 K 线的标的"
    with_kline = [stock for stock in payload["stocks"] if stock.get("kline")]
    assert with_kline, "至少要有一只标的带 K 线数据"


# ---------------------------------------------------------------- 文案去金融化


def test_financial_jargon_is_replaced() -> None:
    """总资产 → 零花钱 / 家底 / 棺材本；强制平仓 → 底裤被扒。"""
    table = _i18n()
    assert table["visual.assets_rich"] == "零花钱"
    assert table["visual.assets_broke"] == "家底"
    assert table["visual.assets_dying"] == "棺材本"
    assert table["visual.force_liquid"] == "底裤被扒"
    assert table["visual.hint_rich"] == "老爸给的钱"
    assert table["visual.hint_broke"] == "省着点花，要交房租了"
    assert table["visual.hint_dying"] == "下顿饭的钱"
    code = _panel_code()
    for key in (
        "visual.assets_rich",
        "visual.assets_broke",
        "visual.assets_dying",
        "visual.hint_rich",
        "visual.hint_broke",
        "visual.hint_dying",
        "visual.force_liquid",
    ):
        assert key in code, f"面板里必须用 {key}"


def test_three_stage_fonts_are_css_only_and_safe() -> None:
    """三档字体只改字体，**不许**引入任何动画 / 定位 / 滤镜。"""
    css = _panel_css()
    assert ".rrm-font-rich" in css
    assert ".rrm-font-broke" in css
    assert ".rrm-font-dying" in css
    assert "font-family" in css
    assert "font-weight: 300" in css
    for banned in ("@keyframes", "position", "filter", "opacity", "animation:"):
        if banned == "animation:":
            # 只允许 animation: none（0.8.1 的入场动画关闭口径）。
            assert css.count("animation: none") >= 1
            continue
        assert banned not in css, f"字体档位的 CSS 里不许出现 {banned}"


def test_dying_flash_uses_a_timer_not_animation() -> None:
    """濒死档「数字偶尔闪红」用 **JS 定时器**切颜色（0.8.0 崩 UI 的坑）。"""
    code = _panel_code()
    assert "flashOn" in code
    assert "setFlashOn(true)" in code
    assert "setInterval" in code
    assert "flashColor" in code


def test_no_new_animation_or_position_in_the_panel_code() -> None:
    """P7.2 不许偷偷加回 0.8.0 那套把面板搞崩的写法。"""
    code = _panel_code()
    for banned in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
        "linear-gradient",
        "<div className=",
    ):
        assert banned not in code, f"面板代码里不许再出现 {banned}"


# ---------------------------------------------------------------- 名册实体化


def test_roster_has_concept_stocks() -> None:
    """名册里有群友概念股（熬夜修仙股 / 水群概念股）。"""
    data = json.loads(_ROSTER_FILE.read_text(encoding="utf-8"))
    titles = {member["title"] for member in data["members"]}
    assert "熬夜修仙股" in titles
    assert "水群概念股" in titles
    aliases = {member["alias"] for member in data["members"]}
    assert "星辰" in aliases
    assert "Wehos" in aliases
    assert len(data["members"]) >= 6


def test_roster_binds_by_volatility_without_touching_the_engine(plugin) -> None:
    """群友按**引擎算出的波动率排名**绑定：星辰 → 最稳，Wehos → 最野。

    这条守卫是「数值零改动」的关键：绑定**只改显示名**，不动 sigma。
    """
    from plugins.mock_stock_meow.storage.session import load_session

    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    session = _run(load_session(plugin._repo, plugin.state, engine_config=_run(plugin._engine_config())))
    assert session is not None
    market = session.market

    binding = plugin._roster_binding(market)
    assert binding, "本局必须有群友绑定"

    def _alias_of(stock_id: str) -> str:
        return str(binding[stock_id].get("alias") or "")

    sigmas = {
        stock_id: float(getattr(stock, "sigma", 0.0) or 0.0)
        for stock_id, stock in market.stocks.items()
    }
    stable_id = next(sid for sid in binding if _alias_of(sid) == "星辰")
    wild_id = next(sid for sid in binding if _alias_of(sid) == "Wehos")
    assert sigmas[stable_id] == min(sigmas.values()), "星辰必须绑到最稳的那只"
    assert sigmas[wild_id] == max(sigmas.values()), "Wehos 必须绑到最野的那只"
    # 绑定是**一一对应**的，不能两只群友抢同一个槽位。
    assert len(binding) == len(set(binding))


def test_panel_payload_carries_display_names_and_sparklines(plugin) -> None:
    """面板载荷带上群友显示名与走势小折线（行情表要用）。"""
    from plugins.mock_stock_meow.storage.session import load_session

    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    session = _run(load_session(plugin._repo, plugin.state, engine_config=_run(plugin._engine_config())))
    assert session is not None
    payload = _value(_run(plugin.market_context()))
    stocks = payload["stocks"]
    assert stocks
    assert all("spark" in stock for stock in stocks)
    assert all("display_name" in stock for stock in stocks)
    # 有名册绑定时，至少有一只带群友显示名。
    assert any(stock.get("roster_alias") for stock in stocks)
    # 初始现金也带上（面板要算「到现在赚/亏了多少」）。
    assert "initial_cash" in payload


# ---------------------------------------------------------------- 猫娘嘴臭


def _latest_danmaku(plugin: Any) -> str:
    """读回面板载荷里的最后一条弹幕。"""
    payload = _value(_run(plugin.market_context()))
    danmaku = payload["visual"]["danmaku"]
    assert danmaku, "点击之后必须有一条弹幕"
    return str(danmaku[-1]["text"])


def test_buy_and_analyze_push_a_danmaku(plugin) -> None:
    """梭哈 / 花钱分析 —— 都要挨骂。"""
    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    stocks = _value(_run(plugin.market()))["stocks"]
    stock_id = next(stock["id"] for stock in stocks if stock["alive"])

    _value(_run(plugin.buy(stock_id=stock_id, amount=1)))
    assert _latest_danmaku(plugin)

    _value(_run(plugin.analyze(stock_id=stock_id)))
    assert _latest_danmaku(plugin)


def test_daily_actions_push_a_danmaku(plugin) -> None:
    """领工资 / 借高利贷 / 跳过本日 —— 都要挨骂。"""
    _value(_run(plugin.join(mode="infinite", seed=9305)))

    _value(_run(plugin.daily_claim()))
    assert _latest_danmaku(plugin)

    _value(_run(plugin.daily_loan()))
    assert _latest_danmaku(plugin)

    _value(_run(plugin.skip_day()))
    assert _latest_danmaku(plugin)


def test_danmaku_box_is_pinned_to_the_bottom() -> None:
    """猫娘弹幕框是**面板最底下**的常驻卡片（文档流，不悬浮）。"""
    code = _panel_code()
    assert "danmakuCard" in code
    assert 't("visual.danmaku_title")' in code
    playing = code.split("state.stage !== \"playing\"", 1)[-1]
    assert playing.rfind("danmakuCard") > playing.rfind("visual.roster_title")


# ---------------------------------------------------------------- 导演台


def test_director_boom_is_blocked_in_the_pure_version(plugin, ctx) -> None:
    """纯净版下 boom 指令一律被挡（开源版不许有后台干预）。"""
    from tests.test_p7_effects import _enable_director, _error_code, run

    ctx.config_payload = {"director": {"enabled": False}}
    plugin._business_config_cache = None
    plugin._business_config_cached_at = 0.0
    assert _error_code(run(plugin.director(op="boom"))) == "director_disabled"


def test_director_boom_sets_the_countdown(plugin, ctx) -> None:
    """特制版下 boom 会把「赛博弹窗轰炸」的倒计时挂上（纯表现层）。"""
    from tests.test_p7_effects import _enable_director, run

    _enable_director(plugin, ctx)
    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = _value(_run(plugin.director(op="boom")))
    assert payload["op"] == "boom"
    assert payload["count"] == 50
    visual = _value(_run(plugin.market_context()))["visual"]
    assert visual["boom"]["active"] is True
    assert visual["boom"]["count"] == 50
    assert visual["boom"]["until"] > 0


def test_panel_has_the_boom_modal() -> None:
    """面板里有**关不掉**的小丑海弹窗。"""
    code = _panel_code()
    assert "boomModal" in code
    assert 'op: "boom"' in code
    assert "visual.boom_body" in code
    assert "🤡" in code


# ---------------------------------------------------------------- i18n / 配置


def test_i18n_has_the_new_keys() -> None:
    """新增文案必须都在 i18n 里（不许裸中文字面量）。"""
    table = _i18n()
    for key in (
        "visual.assets_rich",
        "visual.assets_broke",
        "visual.assets_dying",
        "visual.hint_rich",
        "visual.hint_broke",
        "visual.hint_dying",
        "visual.buy_all",
        "visual.sell_all",
        "visual.force_liquid",
        "visual.market_title",
        "visual.holdings_title",
        "visual.trend_title",
        "visual.price_title",
        "visual.change_title",
        "visual.pnl_title",
        "visual.danmaku_hint",
        "visual.boom_title",
        "visual.boom_body",
        "visual.boom_countdown",
        "visual.director_boom",
        "director.note.boom",
        "taunt.claim.1",
        "taunt.reject.1",
        "taunt.analyze.1",
    ):
        assert key in table, f"i18n 缺少 {key}"


def test_director_is_off_by_default_in_the_example_config() -> None:
    """双版本：开源纯净版默认关导演台。"""
    raw = (_PLUGIN_ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "enabled = false" in raw
    assert "boom_seconds" in raw
    assert "boom_emoji_count" in raw
