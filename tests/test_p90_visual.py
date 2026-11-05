"""P9.0 守卫：视觉绝杀 —— 强制复刻设计图的「画风 / 质感 / 光影」。

0.9.1 的真机截图被玩家评为「白底加黑字的毛坯房」，结算页那张黄色「破产猫」
更是被点名。P9.0 把那张设计图当**唯一标尺**，把面板的视觉层整个重写了一遍。

这里钉死的全是**设计图里能量出来的规格**（色值 / 圆角 / 阴影 / 字号 /
渐变角度 / 结构），不是「好看」这种主观描述 —— 每一条都能被机器验证，
下次谁再把界面改回白底黑字，测试立刻红。

另外把红线再钉一遍：引擎层零改动、危险 CSS 继续禁用、素材仍然内联。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_ASSETS_DIR = _PLUGIN_ROOT / "ui" / "assets"
_TOML = _PLUGIN_ROOT / "plugin.toml"


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
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
    return "\n".join(kept)


def _panel_code() -> str:
    source = _strip_comments(_panel_text())
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        body, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    body, _, _ = rest.partition("`")
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


def _block(css: str, selector: str) -> str:
    assert selector in css, f"CSS 里找不到 {selector}"
    return css.split(selector, 1)[1].split("}", 1)[0]


# ---------------------------------------------------------------- ① 背景与卡片


def test_background_is_the_design_gradient() -> None:
    """整个面板铺**浅蓝 → 纯白**的纵向渐变（不许再是纯白底）。

    P10 微调：底色再**通透**一档 —— 顶部那片天蓝压得更透，中段快速褪成
    近白，卡片于是像是「浮」在浅蓝空气里而不是贴在色块上。

    P21.0：底色**叠两层**（上面一层淡蓝晕 + 下面这层主渐变），
    并用 ``background-size: cover`` 在大屏下铺满。
    """
    css = _panel_css()
    base = "linear-gradient(180deg, #e4efff 0%, #f4faff 48%, #fbfdff 100%)"
    assert base in css, "背景必须有浅蓝到纯白的纵向渐变"
    # 面板根节点上要有（渐变铺在 .rrm-root 上，而不是 body 上）。
    root = _block(css, ".rrm-root {")
    assert base in root, "渐变必须挂在 .rrm-root 上"
    # P21.0：沉浸式底色（两层 + cover）。
    assert "background-size: cover" in root
    assert root.count("linear-gradient(") >= 2, "底色要叠两层（全屏下才不显单薄）"


def test_cards_are_glassy_with_soft_blue_shadow() -> None:
    """卡片 = 半透明白 + 20px 大圆角 + **柔而大**的蓝影 + 白色高光描边。

    P10 微调：影子从「10px 30px」改成「18px 46px」，模糊半径翻倍 ⇒ 影子更散、
    更软，卡片才有「浮在浅蓝背景上」的轻盈感。
    """
    css = _panel_css()
    block = _block(css, ".rrm-root .neko-card {")
    assert "background: rgba(255, 255, 255, 0.85)" in block
    assert "border-radius: 20px" in block
    assert "box-shadow: 0 18px 46px rgba(133, 163, 214, 0.15)" in block
    assert "border: 1px solid rgba(255, 255, 255, 0.6)" in block


def test_page_title_is_24px_bold_navy() -> None:
    """标题 24px 加粗深蓝 ``#1F2D3D``。"""
    css = _panel_css()
    block = _block(css, ".rrm-root .neko-page-title {")
    assert "font-size: 24px" in block
    assert "font-weight: 700" in block
    assert "#1f2d3d" in block


def test_badges_are_light_blue_tags() -> None:
    """状态徽标 = 浅蓝胶囊 Tag（``#E4EFFF`` 底 + ``#5B8FF9`` 字）。"""
    css = _panel_css()
    block = _block(css, ".rrm-root .neko-badge {")
    assert "background: #e4efff" in block
    assert "color: #5b8ff9" in block
    assert "border-radius: 999px" in block


# ---------------------------------------------------------------- ② 胶囊大按钮


def test_trade_buttons_use_the_design_gradients() -> None:
    """梭哈 / 割肉按钮必须是那两条横向渐变。

    P23.0：**红涨绿跌**全局统一之后，这一对按钮也翻成了
    「买 = 赌徒红 / 卖 = 薄荷绿」—— 跟中文炒股界面里最扎手的
    「买红卖绿」同源，也正是这一版要的「赌徒感」。
    """
    css = _panel_css()
    buy = _block(css, ".rrm-root .neko-button.rrm-pill-buy {")
    assert "linear-gradient(90deg, #ff9db0 0%, #e8455f 100%)" in buy
    assert "color: #ffffff" in buy, "梭哈按钮文字要用白字（红底上才够扎眼）"
    sell = _block(css, ".rrm-root .neko-button.rrm-pill-sell {")
    assert "linear-gradient(90deg, #7fe3c8 0%, #23a887 100%)" in sell
    assert "color: #04352a" in sell, "割肉按钮文字要用深绿（绿底上才够清楚）"


def test_trade_buttons_are_tall_and_round() -> None:
    """按钮**够大够圆**：高度 ≈48px、圆角 ≥14px、字号 ≥18px 加粗。

    P21.0：三个值都改成 **clamp()**（下限不变，上限随全屏放大）——
    这里守的是「**下限不许退**」。

    P23.0：下限整体抬了一档（56 → 76 / 28 → 34 / 18 → 24）——
    那一版叫「极简纯爽」，屏幕上最该有的就是这两个按钮。

    P23.2：又**收回去**一档（76 → 56 / 34 → 26 / 24 → 18）——
    直播看板的右栏只有 40% 宽，巨号按钮会把**滑块和预览数字**挤没。

    P30.0：再收一档（56 → 44 / 26 → 14 / 24 → 18）——
    三键搬进了**右侧 30% 窄列**（不再压整条下沿当砖头），
    高度锁在 48px 一线、圆角收紧 ⇒ 「两块砖头」变回「三个按钮」。
    """
    css = _panel_css()
    block = _block(css, ".rrm-root .neko-button.rrm-pill {")
    assert "height: clamp(44px" in block
    assert "border-radius: clamp(14px" in block
    assert "font-size: clamp(18px" in block
    assert "font-weight: 800" in block
    assert "white-space: nowrap" in block, "中文不许竖排"


def test_trade_buttons_keep_the_compact_row() -> None:
    """紧凑并排（等宽、间距 8、整排居中）不许被重构弄丢。

    P27.1：底部操作台从「买 / 卖两键」变成「掏钱买 / 割肉卖 / 做空三键」
    ⇒ 等宽三列。不变式仍是「整排居中 + 间距收紧」。
    """
    code = _panel_code()
    css = _panel_css()
    assert "<Grid cols={2} gap={8}" in code
    block = _block(css, ".rrm-root .neko-grid.rrm-trade-row {")
    assert "repeat(3, minmax(0, 1fr))" in block, "三键必须等宽三列"
    assert "justify-content: center" in block


def test_trade_buttons_have_sub_captions() -> None:
    """设计图里两个大按钮下面各有一行小字。

    P12：文案跟着按钮一起改了（「All In / Stop Loss」不再是事实，现在是按仓位买），
    所以小字换成了「按上面的仓位买 / 卖」，但**层次结构不变**。
    """
    code = _panel_code()
    assert "rrm-pill-captions" in code
    assert "rrm-pill-caption-buy" in code
    assert "rrm-pill-caption-sell" in code
    i18n = _i18n()
    assert "panel.trade_execute_buy_sub" in i18n
    assert "panel.trade_execute_sell_sub" in i18n


# ---------------------------------------------------------------- ③ 行情卡片


def test_stock_tiles_are_two_layer_glassy_cards() -> None:
    """行情卡 = 大圆角玻璃块 + 上下两层（名字/涨跌 · 走势/现价）。"""
    code = _panel_code()
    css = _panel_css()
    tile = code.split("function StockTile(", 1)[1].split("function PositionTile(", 1)[0]
    assert "rrm-tile-head" in tile, "上层必须是名字 + 涨跌"
    assert "rrm-tile-foot" in tile, "下层必须是走势 + 现价"
    assert "rrm-change-pill" in tile, "涨跌要变成胶囊徽标"
    assert "rrm-price" in tile
    block = _block(css, ".rrm-root .neko-card.rrm-tile {")
    assert "border-radius: 18px" in block
    assert "rgba(255, 255, 255, 0.88)" in block


def test_change_pills_are_red_up_and_green_down() -> None:
    """涨跌胶囊：**涨 = 红底红字，跌 = 绿底绿底**（P23.0 翻成中文炒股直觉）。

    P23.0 之前是「涨绿跌红」（欧美 / 加密配色）。这里钉死的是**语义**
    （红的那套是涨），不是具体色值。
    """
    css = _panel_css()
    assert "background: #fde9ed" in css, "涨胶囊要是红底"
    assert "color: #d6486a" in css, "涨胶囊要是红字"
    assert "background: #e7f8f0" in css, "跌胶囊要是绿底"
    assert "color: #17916f" in css, "跌胶囊要是绿字"


def test_sparkline_has_a_gradient_area() -> None:
    """迷你走势要有**渐变面积**（设计图里那条会发光的走势带）。"""
    code = _panel_code()
    spark = code.split("function Sparkline(", 1)[1].split("function RosterAvatar(", 1)[0]
    assert "createLinearGradient" in spark, "走势底下要有渐变面积"
    assert "addColorStop" in spark
    assert "ctx.arc(" in spark, "末端要有一个实心点"


def test_kline_bars_have_gradients_and_a_grid() -> None:
    """K 线柱子纵向渐变 + 底下垫网格线（设计图的光影）。"""
    code = _panel_code()
    css = _panel_css()
    kline = code.split("function Kline(", 1)[1]
    assert "rrm-kline-bar-up" in kline
    assert "rrm-kline-bar-down" in kline
    assert "rrm-kline-wrap" in kline
    assert ".rrm-root .rrm-kline-bar-up" in css
    assert "linear-gradient(180deg, #6fd8bd 0%" in css
    assert "repeating-linear-gradient(180deg" in css, "K 线区要有网格底"


# ---------------------------------------------------------------- ④ 破产配图


def test_the_yellow_potato_is_gone() -> None:
    """0.9.1 那张黄色「破产猫」**彻底删掉**（素材文件也不许留）。"""
    source = _panel_text()
    assert "MEOW_BANKRUPT" not in source, "不许再引用那张图"
    assert not (_ASSETS_DIR / "meow-bankrupt.png").exists(), "素材文件也要删掉"
    gen = (_PLUGIN_ROOT / "tools" / "gen_assets.py").read_text(encoding="utf-8")
    assert "draw_bankrupt_cat" not in gen, "生成脚本里不许再画那只猫"
    assert "meow-bankrupt.png" not in gen


def test_settlement_uses_a_css_wallet() -> None:
    """结算页配图改成**纯 CSS 拼的扁平空钱包**。"""
    code = _panel_code()
    assert "function BankruptArt(" in code
    assert "rrm-wallet" in code
    css = _panel_css()
    assert ".rrm-root .rrm-wallet {" in css
    assert ".rrm-root .rrm-wallet-flap {" in css
    assert ".rrm-root .rrm-wallet-clasp {" in css
    # 空钱包也得以 CSS 的方式画出来 —— 不许偷偷用素材
    wallet = code.split("function BankruptArt(", 1)[1].split("export default function Panel", 1)[0]
    assert "MEOW_" not in wallet


# ---------------------------------------------------------------- ⑤ 间距与文案


def test_card_gaps_are_wider() -> None:
    """卡片之间的间距（**紧凑档 8px** —— P23.2 直播看板布局）。

    P9.0 时是 16px（设计图说「别挤在一起」）；
    P23.2 改成直播看板布局 —— 间距压到 8px，把像素让给核心数据，
    而且**页面本身必须一屏装下**（不许出现纵向滚动条）。

    P30.0：行情方块放大一档（``minWidth`` 228 → 248）——
    「老虎机方块」要真的像方块，字号才撑得开。
    """
    code = _panel_code()
    assert "gap={8}" in code, "主 Stack 的间距必须是 8（直播紧凑布局）"
    assert "minWidth={248} gap={8}" in code


def test_new_strings_are_in_the_dictionary() -> None:
    """P9.0 新增的文案必须进词典。"""
    i18n = _i18n()
    for key in ("visual.buy_all_sub", "visual.sell_all_sub", "panel.settlement_image_alt"):
        assert key in i18n, f"i18n 里缺 {key}"
    assert "钱包" in i18n["panel.settlement_image_alt"]


# ---------------------------------------------------------------- ⑥ 红线


def test_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层**零改动**。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_dangerous_css_is_still_banned() -> None:
    """``position:absolute`` / ``filter`` 等危险写法继续禁用。"""
    css = _panel_css()
    for token in (
        "position: absolute",
        "position: fixed",
        "z-index",
        "filter:",
        "clip-path",
        "inset:",
        "opacity",
        "@keyframes",
        "radial-gradient",
    ):
        assert token not in css, f"注入的 CSS 里不许再出现 {token}"
    for match in re.finditer(r"(?<![\w-])content\s*:", css):
        raise AssertionError(f"注入的 CSS 里不许再出现 content: 属性（{match.group(0)}）")


def test_assets_are_still_inline_data_uris() -> None:
    """头像与梗图依然内联 base64（宿主不服务插件目录）。"""
    avatars = (_ASSETS_DIR / "avatars.ts").read_text(encoding="utf-8")
    assert "data:image/png;base64," in avatars
    assert "AVATARS" in avatars
    assert "MEOW_LOAN" in avatars
    code = _panel_code()
    assert 'from "./assets/avatars"' in code


def test_version_is_0_9_5() -> None:
    """版本号 = 0.10.5。"""
    assert 'version = "0.13.6-live-ultimate"' in _TOML.read_text(encoding="utf-8")
    init = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.13.6-live-ultimate"' in init
