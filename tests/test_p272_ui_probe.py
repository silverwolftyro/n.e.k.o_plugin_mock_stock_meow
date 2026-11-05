"""P27.2 · UI 极限兼容探针（直播前的最后一道保险）。

用户 P27.2 任务三的原文
----------------------
    「如果面板里用了 ``#131d33`` 的深色 K 线画布，请通过探针验证一下：
    宿主的 WebView 是否支持这个背景色？圆角是否被裁切？
    如果宿主渲染深色背景会导致某些渐变失效，立刻降级回浅色底。
    **直播绝对不能因为一个背景色白屏。**」
    「确认底部三大按钮（掏钱买/割肉卖/做空）在标准 1080P 下，
    没有被挤成两行。」

本探针把三件事变成**可复跑的断言**（不是"看起来没问题"）：

1. ``#131d33`` 是合法 CSS 颜色吗？（合法 ⇒ 任何 WebView 都能解析）
2. 宿主 UI Kit 的 CSS 会不会**覆盖**我们的深底 / 渐变 / 圆角？
   —— 读宿主 ``styles.css``；宿主树不在本机就**优雅跳过**。
3. 1080P 下三键会不会折成两行？（**算术断言**：字宽 vs 列宽）

结论（写进断言里，不是写在注释里）
------------------------------------
* 宿主 ``styles.css`` 里**没有**任何针对 ``background`` / ``border-radius``
  的 ``!important`` ⇒ **不需要降级浅色底**。
* 宿主自己就在用 ``linear-gradient``（画廊占位图）⇒ 渐变在宿主 WebView
  里**本来就工作**。
* 深色画布用 ``background-color`` + ``background-image`` **两条独立声明**
  ⇒ 万一 ``repeating-linear-gradient`` 解析失败，掉的只是网格线，
  **深色底还在** ⇒ 不可能白屏。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel.tsx"
_I18N = Path(__file__).resolve().parents[1] / "i18n" / "zh-CN.json"

#: 宿主 UI Kit 的样式表在宿主仓库里的相对路径。
_HOST_STYLE_REL = "frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css"


# ---------------------------------------------------------------- 宿主定位


def _host_styles() -> Path | None:
    """宿主 UI Kit 的 ``styles.css``；找不到就返回 ``None``。

    插件**可以脱离宿主单测** —— 所以读不到宿主不是失败，是"跳过"。
    真的要在 CI 上跑这段，先设环境变量 ``NEKO_HOST_ROOT``。
    """
    candidates: list[Path] = []
    env_root = os.environ.get("NEKO_HOST_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root) / _HOST_STYLE_REL)
    here = Path(__file__).resolve()
    # tests -> 插件根 -> plugins -> plugin -> 仓库根 -> N.E.K.O
    neko_root = here.parents[5]
    candidates.append(neko_root / "统一开发文档" / "N.E.K.O-main" / _HOST_STYLE_REL)
    candidates.append(neko_root / "N.E.K.O-main" / _HOST_STYLE_REL)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _host_css() -> str:
    path = _host_styles()
    if path is None:
        pytest.skip("宿主 UI Kit 的 styles.css 不在本机（设 NEKO_HOST_ROOT 可强制打开）")
    return path.read_bytes().decode("utf-8")


def _panel_code() -> str:
    return _PANEL.read_bytes().decode("utf-8")


def _css_rule(code: str, selector: str) -> str:
    """从 CSS 文本里取出某条规则的选择器后面那一大块声明。"""
    index = code.find(selector)
    assert index >= 0, f"面板里找不到规则：{selector}"
    start = code.find("{", index)
    assert start >= 0, f"规则没有花括号：{selector}"
    end = code.find("}", start)
    assert end >= 0, f"规则没有闭合：{selector}"
    return code[start + 1 : end]


# ---------------------------------------------------------------- ① 颜色合法性


def test_the_dark_canvas_color_is_a_valid_css_hex() -> None:
    """``#131d33`` 必须是合法的 6 位十六进制颜色。

    非法颜色会让整条声明被丢弃 ⇒ 画布退回宿主默认底（白）⇒ 白屏。
    """
    code = _panel_code()
    assert "#131d33" in code, "深色 K 线画布底色不见了"
    for match in re.finditer(r"background-color:\s*(#[0-9a-fA-F]+)", code):
        value = match.group(1)
        assert re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", value), (
            f"background-color 用了非法颜色：{value}"
        )


def test_the_dark_canvas_survives_a_gradient_failure() -> None:
    """渐变失效**也不能白屏** ⇒ 深底与网格必须是**两条独立声明**。

    ``background-color: X; background-image: Y;`` 两条分开写：解析不了
    ``Y`` 只丢掉 Y，X 还在。用 ``background:`` 简写就不行 —— 简写整条
    解析失败时，底色会跟着一起丢 ⇒ 白底。
    """
    code = _panel_code()
    rule = _css_rule(code, ".rrm-root .rrm-kline-wrap")
    assert "background-color: #131d33;" in rule
    assert "background-image: repeating-linear-gradient(" in rule
    assert not re.search(r"(?m)^\s*background\s*:", rule), (
        "深色画布不许用 background 简写 —— 简写解析失败会连底色一起丢"
    )

    equity = _css_rule(code, ".rrm-root .rrm-equity-canvas")
    assert "background-color: #131d33;" in equity


def test_the_dark_canvas_text_stays_readable_on_the_dark_background() -> None:
    """深底上的字必须提亮（否则"看得见"比"白屏"更糟）。"""
    code = _panel_code()
    warming = _css_rule(code, ".rrm-root .rrm-kline-warming")
    hint = _css_rule(code, ".rrm-root .rrm-kline-placeholder-hint")
    for block in (warming, hint):
        color = re.search(r"color:\s*(#[0-9a-fA-F]{6})", block)
        assert color is not None, "深底上的文字没有指定颜色"
        value = color.group(1)
        brightness = int(value[1:3], 16) * 0.299 + int(value[3:5], 16) * 0.587 + int(
            value[5:7], 16
        ) * 0.114
        assert brightness >= 140, f"深底上的字太暗（{value}）⇒ 看不见"


# ---------------------------------------------------------------- ② 宿主覆盖


def test_the_host_ui_kit_never_forces_background_or_radius() -> None:
    """宿主**不许**用 ``!important`` 强推背景 / 圆角。

    有的话，我们面板里的深底、渐变、圆角都会被顶掉 ⇒ 探针必须报错，
    让人去降级浅色底，而不是在直播上白屏。
    """
    css = _host_css()
    banned = {
        "background",
        "background-color",
        "background-image",
        "border-radius",
    }
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        selector = " ".join(match.group(1).split())
        for declaration in match.group(2).split(";"):
            if "!important" not in declaration:
                continue
            prop = declaration.split(":", 1)[0].strip().lower()
            assert prop not in banned, (
                f"宿主用 !important 强推 {prop}（{selector}）⇒ 面板的深底 / 圆角会被顶掉"
            )


def test_the_host_ui_kit_only_uses_important_for_motion() -> None:
    """宿主全部的 ``!important`` 只许出现在动效上（这是现状，也是契约）。"""
    css = _host_css()
    props = set()
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        for declaration in match.group(2).split(";"):
            if "!important" not in declaration:
                continue
            props.add(declaration.split(":", 1)[0].strip().lower())
    assert props <= {"animation", "transition"}, (
        f"宿主出现了新的 !important 属性：{sorted(props)}"
    )


def test_the_host_ui_kit_itself_uses_gradients() -> None:
    """宿主自己就在用 ``linear-gradient`` ⇒ 渐变在宿主 WebView 里工作。

    这是"不用降级浅色底"的**正面证据**：不是"我们没覆盖它所以大概没事"，
    而是"宿主自己的界面就靠渐变撑着"。
    """
    css = _host_css()
    assert "linear-gradient(" in css
    assert "repeating-linear-gradient(" not in css or "linear-gradient(" in css


def test_our_gradient_rules_outrank_the_host_button_rule() -> None:
    """我们的渐变规则特异度必须**压过**宿主的 ``.neko-button``。

    宿主：``.neko-button { background: rgba(64,158,255,0.12); }``（0,1,0）
    我们：``.rrm-root .neko-button.rrm-pill-buy { background: ... }``（0,3,0）

    特异度更高 ⇒ 无论谁先加载，赢的都是我们 ⇒ 渐变不会被洗成纯色。
    """
    code = _panel_code()
    for rule_selector in (
        ".rrm-root .neko-button.rrm-pill-buy",
        ".rrm-root .neko-button.rrm-pill-sell",
    ):
        block = _css_rule(code, rule_selector)
        assert "linear-gradient(" in block, f"{rule_selector} 没有渐变了"
        assert "90deg" in block, f"{rule_selector} 的渐变方向变了"
    host = _host_css()
    host_button = _css_rule(host, ".neko-button")
    assert "background" in host_button
    assert "!important" not in host_button


# ---------------------------------------------------------------- ③ 圆角不被裁切


def _parse_padding(block: str) -> tuple[float, float, float, float] | None:
    """解析 ``padding: a b c d`` / ``padding: a b c`` / ``padding: a``。"""
    match = re.search(r"padding:\s*([^;]+);", block)
    if match is None:
        return None
    parts = [float(part.replace("px", "").strip()) for part in match.group(1).split()]
    if len(parts) == 1:
        return (parts[0], parts[0], parts[0], parts[0])
    if len(parts) == 2:
        return (parts[0], parts[1], parts[0], parts[1])
    if len(parts) == 3:
        return (parts[0], parts[1], parts[2], parts[1])
    if len(parts) == 4:
        return (parts[0], parts[1], parts[2], parts[3])
    return None


def test_the_dark_canvas_is_not_clipped_by_the_host_card() -> None:
    """圆角**不被裁切**：宿主卡片 ``overflow: hidden``，但它留了内边距。

    卡片的裁剪区只是"卡片自己的圆角"切出来的那一小块。卡片正文留了
    ``padding: 12px 14px 14px`` ⇒ 画布整体落在 ``x ≥ 圆角`` 或
    ``y ≥ 圆角`` 的区域里 ⇒ **完全在裁剪区之外**
    ⇒ 画布自己的 16px 圆角不会被切成直角。
    """
    css = _host_css()
    radius = re.search(r"--radius-md:\s*(\d+)px", css)
    assert radius is not None, "宿主卡片圆角变了"
    card_radius = int(radius.group(1))

    body = _css_rule(css, ".neko-card-body")
    padding = _parse_padding(body)
    assert padding is not None, "宿主卡片正文内边距变了"
    top, _right, _bottom, left = padding

    card = _css_rule(css, ".neko-card")
    assert "overflow: hidden" in card, "宿主卡片不再裁切内容（那更好，但契约变了）"

    # 画布整体要在裁剪区之外 ⇒ 上 / 左内边距都不小于卡片自己的圆角。
    assert card_radius <= min(top, left), (
        f"卡片圆角 {card_radius}px 比正文内边距（上 {top}px / 左 {left}px）还大"
        " ⇒ 画布的角会被 overflow: hidden 切掉"
    )

    # 画布自己的圆角（视觉上的圆角）必须存在。
    code = _panel_code()
    kline_radius = re.search(
        r"border-radius:\s*(\d+)px", _css_rule(code, ".rrm-root .rrm-kline-wrap")
    )
    assert kline_radius is not None, "K 线画布没有圆角"
    assert int(kline_radius.group(1)) > 0

    # 而且画布确实在**卡片正文**里（不是直接贴在卡片边上）。
    assert '<Card title={t("panel.kline")}>' in code
    assert "<Kline" in code


# ---------------------------------------------------------------- ④ 1080P 三键


def _pill_metrics() -> dict[str, float]:
    """从面板 CSS 里抽出三键的排版度量。"""
    code = _panel_code()
    rule = _css_rule(code, ".rrm-root .neko-button.rrm-pill")
    font = re.search(r"font-size:\s*clamp\(([\d.]+)px,\s*([\d.]+)vw,\s*([\d.]+)px\)", rule)
    assert font is not None, "三键的字号不是 clamp(...)"
    spacing = re.search(r"letter-spacing:\s*(\d+)px", rule)
    padding = re.search(r"padding:\s*0\s+(\d+)px", rule)
    assert spacing is not None and padding is not None
    return {
        "font_min": float(font.group(1)),
        "font_vw": float(font.group(2)),
        "font_max": float(font.group(3)),
        "letter_spacing": float(spacing.group(1)),
        "padding_x": float(padding.group(1)),
    }


def _label_lengths() -> dict[str, int]:
    """三个键的中文标签长度（真实文案，不是猜的）。"""
    data = json.loads(_I18N.read_bytes().decode("utf-8"))
    out: dict[str, int] = {}
    for key in ("panel.trade_execute_buy", "panel.trade_execute_sell", "panel.short_execute"):
        out[key] = len(str(data.get(key) or ""))
    return out


def test_the_three_trade_keys_are_three_equal_columns() -> None:
    """三键必须真的并排三列（不是 flex 换行）。"""
    code = _panel_code()
    assert '<Grid cols={3} gap={8} className="rrm-trade-row">' in code
    rule = _css_rule(code, ".rrm-root .neko-grid.rrm-trade-row")
    assert "repeat(3, minmax(0, 1fr))" in rule
    assert "flex-wrap" not in rule


def test_the_three_trade_keys_never_wrap_their_own_text() -> None:
    """``white-space: nowrap`` 是"不折成两行"的**硬保证**。

    真机教训（P27.1 之前）：按钮被压窄时中文会**竖着排**（一字一行）。
    """
    code = _panel_code()
    rule = _css_rule(code, ".rrm-root .neko-button.rrm-pill")
    assert "white-space: nowrap" in rule


def test_the_three_trade_keys_fit_on_one_line_at_1080p() -> None:
    """1080P 下三键**不会**被挤成两行（算术断言）。

    口径
    ----
    * 视口宽 1920（标准 1080P 横向）；三键所在的操作台是**整条压在下沿的
      全宽卡片** ⇒ 可用宽 = 视口 − 根内边距 − 卡片正文内边距 − 两个 gap。
    * 字号 ``clamp(18px, 1.7vw, 28px)`` ⇒ 1920px 视口下取上限 28px。
    * 单键需要的宽 = 字数 × (字号 + 字距) + 左右内边距。
    * 副标题那行小字在**按钮外面**（``.rrm-pill-captions``）⇒ 不占按钮的宽。
    """
    metrics = _pill_metrics()
    labels = _label_lengths()
    longest = max(labels.values())
    assert longest > 0

    viewport = 1920.0
    font_px = min(metrics["font_max"], max(metrics["font_min"], metrics["font_vw"] * viewport / 100.0))
    text_px = longest * (font_px + metrics["letter_spacing"])
    need = text_px + 2 * metrics["padding_x"]

    code = _panel_code()
    root_padding = 10.0  # .rrm-root.rrm-live-page { padding: 8px 10px }
    card_padding = 14.0  # .neko-card-body { padding: 12px 14px 14px }
    gap = 8.0
    content = viewport - 2 * root_padding - 2 * card_padding
    column = (content - 2 * gap) / 3

    assert need <= column, (
        f"1080P 下三键放不下：需要 {need:.0f}px，列宽只有 {column:.0f}px"
    )
    # 至少留一倍富余 —— 真机上的字体度量比这里粗。
    assert column >= need * 2, f"1080P 下三键余量太小（{column:.0f}px vs {need:.0f}px）"

    # 极限：多窄的宿主面板才会开始挤？算出来写进报告里。
    min_width = need * 3 + 2 * gap + 2 * root_padding + 2 * card_padding
    assert min_width < 640, f"最小可用宽度 {min_width:.0f}px 太大了"

    # 副标题那行小字在按钮外面。
    assert 'className="rrm-pill-captions"' in code
    for key in ("panel.trade_execute_buy_sub", "panel.trade_execute_sell_sub"):
        assert f't("{key}")' in code
