"""P23.2 守卫：**直播看板布局**（一屏尽览，绝不出现纵向滚动条）。

这一版把局中面板从「一列流水账」重构成「左右分栏的直播看板」，
目标只有一条：**观众不用滚动就能看全所有核心信息**。

所以这里钉死的是**结构契约**，不是像素：

1. **左右分栏** —— 宿主 ``Split``，``ratio="7fr 3fr"``（= 70/30）。
   宿主 ``Grid`` / ``Columns`` 只能等宽，做不到 70/30；
   ``Split`` 把 ``ratio`` 原样写进 ``--split-template`` ⇒ 原生比例。
2. **锁死一屏** —— 局中根容器 ``height: 100vh`` + ``overflow: hidden``，
   而且**每一级**子容器都要 ``min-height: 0``
   （grid / flex 子项默认 ``min-height: auto``，不写这行它们不肯被压缩，
   内容溢出就会被**裁掉** —— 裁切比滚动更糟）。
3. **只有列表能滚** —— 行情 / 持仓两张卡各自内嵌宿主原生 ``ScrollArea``，
   页面本身一条滚动条都不出。
4. **压扁冗余控制区** —— 节奏控制与 Yui 模式都压成**一行工具条**里的小药丸。
5. **妖股优先** —— 行情 / 持仓按「爆炸标签 → 波动率 → 当天振幅」排序，
   让最有节目效果的票排在前排。
6. **文字极简** —— 局中只留核心；次要信息收进宿主原生 ``Modal``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


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


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _live_page_code() -> str:
    """局中页那一段代码（从 ``rrm-live-page`` 那一行到函数结束）。"""
    code = _panel_code()
    marker = 'rrm-live-page'
    assert marker in code, "局中页没有 rrm-live-page 这个作用域类"
    return code[code.index(marker) :]


# ══════════════════════════════════════════════════════════════ ① 左右分栏


def test_the_live_page_is_split_into_two_columns() -> None:
    """局中页用宿主 ``Split`` 分成左右两列，比例 7:3（= 70/30）。

    P30.0：从 60/40 改成 **70/30** —— 左列让巨幅 K 线当主角，
    右列只留「动手用的」：操作卡 / 持仓 / 每日动作 / 聊天框。
    """
    code = _panel_code()
    assert "<Split" in code, "局中页没有用宿主的 Split"
    assert 'ratio="7fr 3fr"' in code, "左右分栏比例必须是 7fr 3fr（70/30）"
    assert 'className="rrm-live-board"' in code, "分栏容器必须有 rrm-live-board"
    # 两列各是一个 Stack（宿主组件），类名统一，便于 CSS 收口。
    assert code.count('className="rrm-live-col"') == 2, "必须有且只有左右两列"
    # 宿主 Grid / Columns 是等宽的，做不到 70/30 —— 不许拿它们冒充分栏。
    assert 'className="rrm-live-col"' in code


def test_the_split_ratio_is_native_not_hand_rolled() -> None:
    """比例必须走宿主的 ``--split-template``，不许自己写 grid-template-columns。"""
    css = _panel_css()
    assert "--split-template" not in css, "不许自己覆写 --split-template"
    # 看板相关的规则块里不许出现 grid-template-columns
    # （买卖按钮行 / 预览行那两个等宽网格是另一回事，不在管辖范围内）。
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = rule.group(1).strip()
        if "grid-template-columns" not in rule.group(2):
            continue
        assert "rrm-trade-row" in selector or "rrm-trade-previews" in selector, (
            f"看板里不许自己写 grid-template-columns：{selector}"
        )


# ══════════════════════════════════════════════════════════════ ② 锁死一屏


def test_the_live_page_is_locked_to_one_screen() -> None:
    """局中根容器：``height: 100vh`` + ``overflow: hidden``。"""
    css = _panel_css()
    assert ".rrm-root.rrm-live-page" in css, "没有局中页的专属样式"
    block = css.split(".rrm-root.rrm-live-page {", 1)[1].split("}", 1)[0]
    assert "height: 100vh" in block, "局中页必须锁死 100vh"
    assert "overflow: hidden" in block, "局中页必须 overflow: hidden（页面本身不滚）"
    assert "padding: 8px 10px" in block, "内边距必须压到 8~10px"
    assert "gap: 8px" in block, "间距必须压到 8px"


def test_every_flex_level_can_shrink() -> None:
    """每一级子容器都要 ``min-height: 0``（否则内容溢出 = 被裁）。"""
    css = _panel_css()
    for selector in (
        ".rrm-root.rrm-live-page .rrm-live-stage { min-height: 0; }",
        ".rrm-root.rrm-live-page .rrm-live-col { min-height: 0; }",
    ):
        assert selector in css, f"缺少可压缩声明：{selector}"
    assert ".rrm-root.rrm-live-page .rrm-live-board" in css
    board = css.split(".rrm-root.rrm-live-page .rrm-live-board {", 1)[1].split("}", 1)[0]
    assert "min-height: 0" in board, "分栏容器必须 min-height: 0"
    assert "flex: 1 1 0" in board, "分栏容器必须吃掉主列剩下的高度"


def test_the_lock_is_only_on_the_live_page() -> None:
    """锁高**只**加在局中页 —— 空闲页 / 结算页内容多，锁了会被裁。"""
    css = _panel_css()
    root = css.split(".rrm-root {", 1)[1].split("}", 1)[0]
    assert "overflow" not in root, ".rrm-root 本身不许锁高（空闲页内容多，会被裁）"
    assert "min-height: 100vh" in root, ".rrm-root 保留最小区块高（空闲页不受影响）"
    # 其它页面的根容器仍然带 rrm-root（不带 rrm-live-page）。
    code = _panel_code()
    assert code.count('className="rrm-root"') >= 1, "空闲页 / 结算页仍然用 rrm-root"


# ══════════════════════════════════════════════════════════════ ③ 只有列表能滚


def test_only_the_two_lists_scroll() -> None:
    """行情 / 持仓两张卡各自内嵌宿主 ``ScrollArea``，页面本身不滚。"""
    code = _panel_code()
    assert "<ScrollArea" in code, "列表没有用宿主原生 ScrollArea"
    assert 'className="rrm-live-scroll"' in code, "滚动区必须有统一类名"
    live = _live_page_code()
    assert live.count("<ScrollArea") >= 2, "行情卡 + 持仓卡都要有滚动区"
    # 滚动区的高度用 vh 表达（红线：高度用 vh/vw，不写死 px）。
    assert re.search(r'<ScrollArea[\s\S]{0,200}height="clamp\(\d+px,\s*\d+vh', live), (
        "持仓列表的高度必须是 vh 表达式"
    )


def test_the_scroll_area_max_height_is_released() -> None:
    """宿主 ``ScrollArea`` 默认 ``max-height: 360px``，要放开才能吃满卡片。"""
    css = _panel_css()
    assert "max-height: none" in css, "必须放开 ScrollArea 的 360px 上限"


# ══════════════════════════════════════════════════════════════ ④ 压扁控制区


def test_the_pace_and_yui_modes_share_one_toolbar() -> None:
    """节奏控制 + Yui 模式都压进**同一行工具条**。"""
    code = _panel_code()
    assert 'className="rrm-anchor-status-bar rrm-live-toolbar"' in code, (
        "状态条必须是那行工具条（教程锚点还在它身上）"
    )
    live = _live_page_code()
    # 工具条里同时出现：Yui 选择器 + 节奏控制。
    toolbar = live[live.index("rrm-live-toolbar") :]
    assert "<YuiModePicker" in toolbar, "工具条里必须有 Yui 模式选择器"
    assert "paceToolbar" in toolbar, "工具条里必须有节奏控制"
    # 局中**不再**有独立的节奏卡与独立的 Yui 块。
    assert live.count("<YuiModePicker") == 1, "局中只能有一份 Yui 选择器"
    assert "{paceCard}" not in live, "局中不该再有独立的大节奏卡"


def test_the_yui_picker_has_a_compact_mode() -> None:
    """``YuiModePicker`` 的 compact 档：**只留徽标**，不要标题和说明。"""
    code = _panel_code()
    start = code.index("function YuiModePicker")
    body = code[start : code.index("\n}\n", start) + 3]
    assert "if (props.compact)" in body, "YuiModePicker 没有紧凑档"
    compact = body[body.index("if (props.compact)") : body.index("panel.yui_mode_title")]
    assert "rrm-yui-mode-inline" in compact, "紧凑档要是一排内联徽标"
    assert "panel.yui_mode_title" not in compact, "紧凑档不许留标题"
    assert "panel.yui_mode_hint" not in compact, "紧凑档不许留说明"


def test_the_pace_control_has_a_compact_mode() -> None:
    """``PaceControl`` 的 compact 档：一排药丸，不是一张卡。"""
    code = _panel_code()
    start = code.index("function PaceControl")
    body = code[start : code.index("\n}\n", start) + 3]
    assert "if (props.compact)" in body, "PaceControl 没有紧凑档"
    compact = body[body.index("if (props.compact)") : body.index("panel.pace_title")]
    assert "rrm-pace-inline" in compact, "紧凑档要是一排内联药丸"
    assert "panel.pace_title" not in compact, "紧凑档不许留「节奏」标题"


# ══════════════════════════════════════════════════════════════ ⑤ 妖股优先


def test_the_stocks_are_sorted_by_how_exciting_they_are() -> None:
    """行情按「爆炸标签 → 波动率 → 当天振幅」排序：妖股排前排。"""
    code = _panel_code()
    assert "sortStocksByHotness" in code, "没有按热度排序"
    assert "TAG_HOTNESS" in code, "没有标签权重表"
    assert "explosive" in code, "爆炸标签的权重没进表"
    assert "sigma" in code, "排序没有用波动率"
    hotness = code[code.index("function stockHotness") :]
    hotness = hotness[: hotness.index("\n}\n")]
    assert "stock.sigma" in hotness, "热度分必须用波动率"
    assert "stock.change" in hotness, "热度分必须用当天振幅"


def test_the_market_grid_uses_the_sorted_list() -> None:
    """行情方块用的是**排好序**的列表，不是原始顺序。"""
    code = _panel_code()
    assert "const sortedStocks = sortStocksByHotness(stocks)" in code
    assert "sortedStocks.slice(" in code, "排序后的列表才是渲染用的那份"
    assert "stocks.slice(" not in code, "不许再按原始顺序直接切"


def test_the_payload_carries_the_volatility() -> None:
    """载荷里必须带 ``sigma``（**只读**）—— 排序的数据来源。"""
    init = _INIT.read_text(encoding="utf-8")
    assert '"sigma": round(float(stock.sigma), 6)' in init, "载荷里没有 sigma"
    assert '"sigma": round(float(stock.sigma), 6) if stock is not None else 0.0' in init, (
        "持仓侧也要带 sigma"
    )


def test_the_engine_is_untouched() -> None:
    """引擎层 / 模式层**零改动**（P23.2 只加了一个只读展示字段）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎 / 模式层被改了：{changed}"


# ══════════════════════════════════════════════════════════════ ⑥ 文字极简


def test_the_live_page_has_no_long_headline() -> None:
    """局中页**不要** Page 的标题块（品牌名在标题行里已经有了）。"""
    live = _live_page_code()
    assert "panel.subtitle" not in live, "局中不该再显示那一长串副标题"
    assert "panel.title" not in live, "局中不该再有第二个标题"


def test_the_bill_only_shows_the_bad_news() -> None:
    """账单只留「交不起」那版 —— 「钱够了」是恒定噪音。"""
    code = _panel_code()
    assert "panel.bill_ok" not in code, "「钱够了」那版账单应该删掉"
    assert "panel.bill_warn" in code, "「交不起」的警告必须留着"


def test_the_secondary_blocks_live_in_a_modal() -> None:
    """名册 / 新闻 / 猫娘状态 / 弹幕收进宿主原生 ``Modal``。"""
    code = _panel_code()
    assert "panel.more_info" in code, "没有【更多】入口"
    assert "setExtraOpen(true)" in code, "【更多】按钮没有打开弹窗"
    assert "size=\"full\"" in code, "【更多】弹窗要用宿主的大尺寸弹窗"
    # 这四块**必须**在弹窗里（不再平铺在面板上）。
    modal_start = code.index("panel.more_info")
    modal = code[modal_start:]
    for block in ("visual.roster_title", "panel.news", "panel.catgirl", "danmakuCard"):
        assert block in modal, f"{block} 应该收进【更多】弹窗"


def test_the_daily_actions_stay_reachable() -> None:
    """每日动作（领工资 / 网贷 / 跳过）必须**留在面板里** ——
    教程要圈到「借网贷」那个按钮，藏进弹窗就圈不到了。"""
    live = _live_page_code()
    assert "rrm-anchor-loan-button" in live, "网贷按钮必须在局中面板里"
    assert "rrm-daily-actions" in live, "每日动作要单独一张卡"


# ══════════════════════════════════════════════════════════════ ⑦ 红线


def test_the_live_layout_still_avoids_dangerous_css() -> None:
    """布局重构**没有**引入新的危险 CSS。"""
    css = _panel_css()
    for banned in ("@keyframes", "position", "filter", "opacity", "clip-path", "z-index"):
        assert banned not in css, f"CSS 里出现了禁用词：{banned}"
    code = _panel_code()
    for banned in ('position: "absolute"', 'position: "fixed"', "zIndex", "clipPath"):
        assert banned not in code, f"代码里出现了禁用写法：{banned}"


def test_the_new_i18n_keys_exist() -> None:
    """新增文案必须进词典。"""
    i18n = _i18n()
    for key in (
        "panel.more_info",
        "panel.trade_finetune_title",
        "panel.daily_actions",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
    assert "仓位" not in i18n["panel.trade_finetune_title"], "标题不许用金融黑话"
