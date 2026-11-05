# -*- coding: utf-8 -*-
"""P30.0 守卫：**紧急视觉重构**（压缩操作区 + HUD 密集化）。

这一版只动布局，钉死的是**结构契约**：

1. **顶部 HUD 一行** —— 身家 / 赚亏 / 现钱压成三格并排的薄片，
   字号统一 ``clamp(24px, 2.5vw, 36px)``（旧版是 72px 的巨字）。
2. **右列 30%** —— 宿主 ``Split`` 的 ``ratio`` 必须是 ``7fr 3fr``
   （不许自己写 ``grid-template-columns``）。
3. **底部整行操作台消失** —— 三键连同「标的 Select」与「买卖比例滑块」
   一起搬进**右侧独立操作卡**；按钮高度锁在 48px 一线。
4. **K 线更霸气** —— 高度 240 / 34vh / 460，深色画布占满左列。
5. **行情老虎机方块** —— 字号再放大一档。

**红线**：引擎层 / 模式层零改动；一屏不滚动（``100vh`` 锁定）。
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
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


def _live_code() -> str:
    """局中页那一段代码（从 ``rrm-live-page`` 那一行往后）。"""
    code = _panel_code()
    marker = "rrm-live-page"
    assert marker in code, "局中页没有 rrm-live-page 这个作用域类"
    return code[code.index(marker) :]


# ══════════════════════════════════════════════════════════════ ① 顶部 HUD


def test_the_top_three_numbers_are_one_flat_hud_row() -> None:
    """顶部三大数字压成**一行三格**的扁平 HUD。"""
    code = _panel_code()
    hud_grid = '<Grid cols={3} gap={8} className="rrm-anchor-overview rrm-live-overview rrm-live-hud">'
    assert hud_grid in code, "顶部概览必须是三格并排的 HUD"
    hud = code[code.index(hud_grid) : code.index(hud_grid) + 4000]
    # 三格都在同一张 Grid 里 ⇒ 天然排成一行。
    assert hud.count("<BigStat") == 3, "HUD 里必须有且只有三个大数字"


def test_the_hud_numbers_use_the_compact_font() -> None:
    """HUD 里的数字统一 ``clamp(24px, 2.5vw, 36px)``（旧版 72px 的巨字不许复活）。"""
    code = _panel_code()
    hud_grid = '<Grid cols={3} gap={8} className="rrm-anchor-overview rrm-live-overview rrm-live-hud">'
    hud = code[code.index(hud_grid) : code.index(hud_grid) + 2500]
    sizes = re.findall(r'size="(clamp\([^"]+\))"', hud)
    assert len(sizes) == 3, f"HUD 里应有三个字号声明，实际 {sizes}"
    for size in sizes:
        assert size == "clamp(24px, 2.5vw, 36px)", size
    # 旧版那三组巨字必须已经消失。
    for legacy in ("clamp(34px, 4.4vw, 72px)", "clamp(24px, 2.8vw, 44px)", "clamp(20px, 2.2vw, 34px)"):
        assert legacy not in code, f"旧的巨大字号还在：{legacy}"


def test_the_hud_hides_the_secondary_hints() -> None:
    """HUD 里只留「标签 + 数字」—— 提示小字一律藏掉（省留白）。"""
    css = _panel_css()
    assert ".rrm-root.rrm-live-page .rrm-live-hud .rrm-stat-hint" in css
    hud_block = css.split(".rrm-root.rrm-live-page .rrm-live-hud .rrm-stat-hint {", 1)[1].split("}", 1)[0]
    assert "display: none" in hud_block


# ══════════════════════════════════════════════════════════════ ② 7:3 分栏


def test_the_split_is_seven_thirty() -> None:
    """左右分栏必须是 70/30（宿主原生 ``Split``）。"""
    code = _panel_code()
    assert 'ratio="7fr 3fr"' in code
    assert 'ratio="3fr 2fr"' not in code, "旧比例 60/40 已经退场"
    css = _panel_css()
    assert "--split-template" not in css, "不许自己覆写 --split-template"


# ══════════════════════════════════════════════════════════════ ③ 操作区搬家


def test_the_bottom_full_width_console_is_gone() -> None:
    """底部那条**整行操作台**必须消失 —— 三键只在右列的操作卡里。"""
    code = _panel_code()
    assert code.count('<Card className="rrm-anchor-trade-console"') == 1, (
        "操作卡只能有一张（不许又搬回下沿）"
    )
    live = _live_code()
    console_at = live.index('<Card className="rrm-anchor-trade-console"')
    split_at = live.index("</Split>")
    assert console_at < split_at, "操作卡必须在左右分栏**之内**（右列）"


def test_the_operation_card_holds_the_picker_the_sliders_and_the_three_keys() -> None:
    """操作卡里必须同时有：标的 Select、买卖比例滑块、三键。"""
    live = _live_code()
    console_at = live.index('<Card className="rrm-anchor-trade-console"')
    block = live[console_at : console_at + 9000]
    assert "rrm-trade-sides" in block, "操作卡里要有买 / 卖两条仓位"
    assert block.count('className="rrm-select-row"') == 2, "买 / 卖两个标的 Select"
    assert "rrm-trade-slider" in block, "操作卡里要有比例滑块"
    assert "rrm-short-panel" in block, "操作卡里要有做空输入条"
    assert '<Grid cols={3} gap={8} className="rrm-trade-row">' in block, "三键要并排"
    # 三键必须在卡片的**底部** ⇒ 出现在「短面板」之后。
    assert block.index("rrm-short-panel") < block.index("rrm-trade-row")


def test_the_three_keys_are_one_line_of_48px_buttons() -> None:
    """按钮高度锁在 48px 一线（不再是两砖头），且不许折行。"""
    css = _panel_css()
    block = css.split(".rrm-root .neko-button.rrm-pill {", 1)[1].split("}", 1)[0]
    assert "height: clamp(44px" in block
    assert "font-size: clamp(18px" in block
    assert "white-space: nowrap" in block
    # 三键那一行仍然是**等宽三列**（折行 = 灾难）。
    row = css.split(".rrm-root .neko-grid.rrm-trade-row {", 1)[1].split("}", 1)[0]
    assert "repeat(3, minmax(0, 1fr))" in row
    assert "flex-wrap" not in row


def test_the_sliders_left_the_finetune_card() -> None:
    """滑块**不许**在精调卡里再出现一份（两处控件 = 玩家会懵）。"""
    live = _live_code()
    finetune_at = live.index("rrm-trade-finetune-card")
    console_at = live.index('<Card className="rrm-anchor-trade-console"')
    # 精调卡在操作卡**之后**（右列从上到下：操作卡 → 持仓 → 每日 → 聊天）。
    assert finetune_at > console_at
    finetune = live[finetune_at : finetune_at + 9000]
    assert "rrm-trade-slider" not in finetune, "滑块已经搬去操作卡了"
    assert "rrm-trade-sides" not in finetune


def test_the_right_column_has_a_chat_box() -> None:
    """右列最后要有「猫娘点评 / 聊天框」。"""
    live = _live_code()
    assert "rrm-live-chat" in live, "右列缺聊天框"
    i18n = _I18N.read_text(encoding="utf-8")
    assert '"panel.live_chat_title"' in i18n, "聊天框标题必须进词典"


# ══════════════════════════════════════════════════════════════ ④ K 线与老虎机


def test_the_kline_got_taller() -> None:
    """K 线重新拉高（220 / 30vh / 400）—— 压迫感回来了。

    P31.0：为了让下方的行情卡拿得到高度，K 线**让**回一截
    （P30.0 是 240 / 34vh / 460）—— 但仍然明显高于 P27.1 时代的
    210 / 26vh / 330，主视觉地位不变。
    """
    code = _panel_code()
    match = re.search(
        r'const KLINE_HEIGHT_CSS = "clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)', code
    )
    assert match, "K 线高度必须是 CSS 表达式"
    assert int(match.group(1)) == 220
    assert int(match.group(2)) == 30
    assert int(match.group(3)) == 400
    # 深色画布还在（压迫感的底子）。
    css = _panel_css()
    wrap = css.split(".rrm-root .rrm-kline-wrap {", 1)[1].split("}", 1)[0]
    assert "background-color: #131d33;" in wrap


def test_the_slot_cards_are_bigger() -> None:
    """行情老虎机方块：名称 / 现价放大，涨跌胶囊有上限。"""
    css = _panel_css()
    name_rule = css.split(".rrm-root .rrm-slot .rrm-stock-name {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px" in name_rule
    price_rule = css.split(".rrm-root .rrm-slot .rrm-price {", 1)[1].split("}", 1)[0]
    assert "font-size: 17px" in price_rule
    pill_rule = css.split(".rrm-root .rrm-slot .rrm-change-pill {", 1)[1].split("}", 1)[0]
    # P31.0：巨号涨跌胶囊**必须有上限**（不许撑破方块）。
    assert "font-size: clamp(24px" in pill_rule
    assert "overflow: hidden" in pill_rule
    assert "max-height:" in pill_rule
    # 方块本身也要更大。
    code = _panel_code()
    assert "minWidth={248} gap={8}" in code


# ══════════════════════════════════════════════════════════════ ⑤ 红线


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
