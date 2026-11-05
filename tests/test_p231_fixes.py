# -*- coding: utf-8 -*-
"""P23.1 的守卫：**真机暴露的四个交互 / 渲染 Bug**。

这一轮没有新功能，全是**修事故**。四个事故各自对应一条硬契约：

1. **大 K 线开局塌陷** —— 引擎的 ``kline`` 是**空数组起步**，开局时面板拿到
   0 个点 ⇒ 走「空数据」分支 ⇒ 而那个分支**根本没给高度** ⇒ 54vh 的大图
   塌成一行字。契约：**K 线区永远不许塌**（占位背景也占满高度）。
2. **局中切不了 Yui 模式** —— 选择器只放在空闲态 ⇒ 主播「开面板就进游戏、
   进了游戏就切不了」。契约：**空闲态与局中共用同一份选择器**。
3. **Yui 开局误报失败** —— 玩家已经在局中时，工具回「已经在局里啦」，
   猫娘却说「开局失败了喵」。契约：**温和引导**，不是工具级错误。
4. **插件停止后的红错轰炸** —— 插件停了，面板还渲染按钮，一按就是一屏
   红色警告。契约：**只提示一次（黄色）**，面板整体停摆。

外加一条官方校验器的规则本地化：面板**全文**（含注释）都不许出现裸的
全局调用口 —— 官方 ``check-hosted-tsx`` 会因此判错，但那个校验器要
node + 联网装 TypeScript，**跑不进本仓库的自测**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_INIT = _PLUGIN_ROOT / "__init__.py"


# ---------------------------------------------------------------- 工具


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _strip_comments(source: str) -> str:
    """剥掉注释（保留行数，行号才对得上）。"""
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            kept.append("")
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            kept.append("")
            continue
        if stripped.startswith("//"):
            kept.append("")
            continue
        kept.append(line)
    return "\n".join(kept)


def _panel_code() -> str:
    """面板源码（去掉注释与 CSS 模板字符串）。"""
    source = _strip_comments(_PANEL.read_text(encoding="utf-8"))
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        _, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _PANEL.read_text(encoding="utf-8")
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    css, _, _ = rest.partition("`")
    return css


def _panel_text() -> str:
    """面板原文（含注释与 CSS）。"""
    return _PANEL.read_text(encoding="utf-8")


def _i18n() -> dict[str, str]:
    import json

    return json.loads(_I18N.read_text(encoding="utf-8"))


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _function_source(source: str, marker: str, stop: str = "\nfunction ") -> str:
    """截出某个函数 / 组件的源码（**按花括号配平**）。

    注意：参数表里可能带**类型字面量**（``props: { a: string }``），
    所以函数体的那对花括号是"括号深度回到 0 之后的第一个 ``{``"，
    不能简单地取第一个 ``{``。
    """
    index = source.find(marker)
    assert index >= 0, f"找不到 {marker}"
    depth = 0
    body_start = -1
    for position in range(index, len(source)):
        char = source[position]
        if char in "([{":
            if char == "{" and depth == 0:
                body_start = position
                break
            depth += 1
        elif char in ")]}":
            depth -= 1
    assert body_start >= 0, f"{marker} 找不到函数体"
    depth = 0
    for position in range(body_start, len(source)):
        char = source[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[index : position + 1]
    raise AssertionError(f"{marker} 的花括号没有配平")


# ══════════════════════════════════════════════════════════════════════
# ① 大 K 线：开局不许塌陷
# ══════════════════════════════════════════════════════════════════════


def test_the_kline_never_collapses_to_a_single_line() -> None:
    """K 线的「没数据」分支**也必须占满高度**（P23.1 真机事故）。"""
    code = _panel_code()
    kline = _function_source(code, "function Kline(props")

    # 旧的「一个点都没有」分支**没有高度** ⇒ 54vh 塌成一行字。
    # 现在两个分支合并成一个占位背景，高度由 KLINE_HEIGHT_CSS 给。
    assert "points.length === 0" not in kline, "K 线仍有一条无高度的空数据分支"
    assert "rrm-kline-placeholder" in kline, "K 线缺少占位背景"
    # 占位背景与真 K 线**各**给一次高度。
    assert kline.count("height,") >= 2, "占位背景没有给高度"
    assert "KLINE_HEIGHT_CSS" in kline


def test_the_kline_placeholder_uses_the_same_backdrop() -> None:
    """占位背景的底纹跟真 K 线一样 ⇒ 那块地方**一点也不塌**。"""
    css = _panel_css()
    assert ".rrm-root .rrm-kline-placeholder" in css, "缺少占位背景样式"
    wrap = css.split(".rrm-root .rrm-kline-wrap {", 1)[1].split("}", 1)[0]
    assert "background-image" in wrap, "真 K 线没有底纹"
    code = _panel_code()
    assert 'className: "rrm-kline-wrap rrm-kline-placeholder"' in code, (
        "占位背景必须复用真 K 线的那套底纹"
    )


def test_the_kline_is_always_rendered_in_the_playing_page() -> None:
    """局中**永远**渲染 K 线组件（哪怕一个点都没有）。"""
    code = _panel_code()
    ternary = code.find("stocks.length === 0 ?")
    kline = code.find("<Kline")
    assert ternary >= 0 and kline >= 0
    # 三元的闭合标记在 K 线之前 ⇒ K 线**在三元外面**，不会被"没标的"分支吃掉。
    assert ")}" in code[ternary:kline], "K 线被塞进了 stocks.length === 0 的三元里"


def test_the_kline_selection_is_independent_from_the_trade_selection() -> None:
    """K 线的下拉框**不跟交易台共用**选择态（P23.1 真机事故）。"""
    code = _panel_code()
    assert "klineStockPick" in code, "K 线没有自己的选择态"
    row = code.split('className="rrm-kline-row"', 1)[1]
    row = row.split("/>", 1)[0]
    assert "setKlineStockPick" in row, "K 线下拉框没写自己的选择态"
    assert "setSelectedStock" not in row, "K 线下拉框还在跟交易台共用选择态"


def test_the_kline_auto_fetches_when_the_stock_has_no_bundled_data() -> None:
    """选了没带数据的标的 ⇒ **自动拉一次**（不用玩家手点按钮）。"""
    code = _panel_code()
    assert "klineAutoFetched" in code, "缺少自动拉取的去重标记"
    effect = code.split("const klineAutoFetched", 1)[1]
    assert 'props.api.call(' in effect, "自动拉取没有走宿主调用口"
    assert '"kline"' in effect, "自动拉取调的不是 kline 动作"


# ══════════════════════════════════════════════════════════════════════
# ② Yui 模式：局中也能切
# ══════════════════════════════════════════════════════════════════════


def test_the_yui_mode_picker_is_shared_by_idle_and_playing() -> None:
    """选择器抽成组件 ⇒ 空闲态与局中**各有一份**（P23.1 真机死锁）。"""
    code = _panel_code()
    assert "function YuiModePicker" in code, "Yui 模式选择器没有抽成组件"
    assert code.count("<YuiModePicker") >= 2, "局中页没有 Yui 模式选择器"


def test_switching_yui_mode_while_playing_works(plugin) -> None:
    """**局中**切到直播托管 ⇒ 成功，且游戏状态一点没动。"""
    run(plugin.join(mode="classic_arcade", seed=9101))
    before = run(plugin.market_context())
    result = run(plugin.yui_mode(mode="live"))
    assert result.is_ok(), result
    assert result.unwrap()["mode"] == "live"
    after = run(plugin.market_context())
    assert after["stage"] == "playing"
    assert after["day"] == before["day"]
    assert after["cash"] == before["cash"]
    # 局中载荷里也能看到当前模式（面板的局中选择器靠它高亮）。
    assert after["yui"]["mode"] == "live"


def test_switching_yui_mode_does_not_touch_the_settlement_state(plugin) -> None:
    """切模式只是改设置项 ⇒ **不碰结算状态**（P23.1 验收要求）。"""
    run(plugin.join(mode="classic_arcade", seed=9102))
    run(plugin.yui_mode(mode="live"))
    payload = run(plugin.market_context())
    assert payload.get("settlement") is None
    assert payload["stage"] == "playing"


# ══════════════════════════════════════════════════════════════════════
# ③ Yui 开局：温和引导，不是失败
# ══════════════════════════════════════════════════════════════════════


def test_llm_join_guides_when_the_player_is_already_in_game(plugin) -> None:
    """玩家已在局中 ⇒ **温和引导**（``is_error`` 为假）。"""
    run(plugin.join(mode="classic_arcade", seed=9103))
    result = run(plugin.llm_join(mode="classic_arcade"))
    assert result["is_error"] is False, result
    assert result["output"]["ok"] is True
    assert result["output"]["already_in_game"] is True
    assert result["output"]["message"]


def test_the_join_mode_aliases_are_normalized() -> None:
    """猫娘说「街机」「入门」「无尽」⇒ 都要认得出来。"""
    text = _init_text()
    assert "def _normalize_join_mode" in text
    module = __import__("plugins.mock_stock_meow", fromlist=["_normalize_join_mode"])
    normalize = getattr(module, "_normalize_join_mode")
    assert normalize("classic_arcade") == "classic_arcade"
    assert normalize("arcade") == "classic_arcade"
    assert normalize("街机") == "classic_arcade"
    assert normalize("beginner") == "classic_beginner"
    assert normalize("入门") == "classic_beginner"
    assert normalize("无尽") == "infinite"
    assert normalize("infinite") == "infinite"
    # 认不出来 ⇒ 回落街机档（最适合"来一局"这种随口一提）。
    assert normalize("???") == "classic_arcade"
    assert normalize("") == "classic_arcade"


def test_llm_join_accepts_an_alias_mode(plugin) -> None:
    """别名档位也能开出一局（以前会被判 invalid_mode）。"""
    result = run(plugin.llm_join(mode="arcade"))
    assert result["is_error"] is False, result
    payload = run(plugin.market_context())
    assert payload.get("stage") == "playing"
    assert payload.get("mode") == "classic_arcade"


def test_the_llm_join_tool_accepts_an_identity() -> None:
    """``identity`` 也是合法参数（认不出来就回落默认，别报错）。"""
    text = _init_text()
    assert "identity" in text
    assert '"identity"' in text


def test_the_join_already_message_is_in_the_dictionary() -> None:
    table = _i18n()
    assert "llm.join.already" in table
    assert table["llm.join.already"]


# ══════════════════════════════════════════════════════════════════════
# ④ 插件停止：只提示一次 + 面板整体停摆
# ══════════════════════════════════════════════════════════════════════


def test_the_panel_never_mentions_the_global_api_object() -> None:
    """官方 TSX 校验器的规则本地化（它扫**全文**，注释也算）。

    ``/(^|[^\\w.])api\\./`` —— 注释里写一句带那个裸词的话也会被判错。
    """
    text = _panel_text()
    offenders = [
        (number, line.strip())
        for number, line in enumerate(text.split("\n"), start=1)
        if re.search(r"(^|[^\w.])api\.", line)
    ]
    assert not offenders, f"面板里出现了裸的全局调用口：{offenders}"


def test_the_offline_error_is_recognized_by_its_code() -> None:
    """认宿主的 ``PLUGIN_NOT_RUNNING``（**不许**写中文判据）。"""
    code = _panel_code()
    assert "function isPluginOfflineError" in code
    helper = _function_source(code, "function isPluginOfflineError")
    assert "PLUGIN_NOT_RUNNING" in helper
    assert not re.search(r"[\u4e00-\u9fff]", helper), "离线判定里不许写中文"


def test_the_offline_toast_is_a_warning_and_only_fires_once() -> None:
    """黄色提示**只报一次**（不再一条一条地刷红色警告）。"""
    code = _panel_code()
    handler = _function_source(code, "const markPluginOffline")
    assert 'showToast(t("panel.offline_toast"), "warning")' in handler
    assert "offlineNotified.current" in handler
    # 动作失败的两条路径都要先判离线，再决定要不要弹红色。
    assert "isPluginOfflineError(error)" in code
    assert "markPluginOffline()" in code


def test_the_panel_replaces_itself_when_the_plugin_is_offline() -> None:
    """插件离线 ⇒ 面板换成一块提示（没有满屏会失败的按钮）。"""
    code = _panel_code()
    assert "if (pluginOffline)" in code
    assert "rrm-offline" in code
    assert "panel.offline_title" in code
    assert "panel.offline_retry" in code
    css = _panel_css()
    assert ".rrm-root.rrm-offline" in css, "缺少离线时的置灰样式"
    assert ".rrm-root .neko-button.rrm-offline-retry" in css


def test_the_offline_copy_is_in_the_dictionary() -> None:
    table = _i18n()
    for key in (
        "panel.offline_toast",
        "panel.offline_still",
        "panel.offline_title",
        "panel.offline_body",
        "panel.offline_retry",
        "panel.offline_hint",
    ):
        assert key in table, key
        assert table[key]


# ══════════════════════════════════════════════════════════════════════
# ⑤ 红线
# ══════════════════════════════════════════════════════════════════════


def test_the_engine_and_mode_layers_are_still_untouched() -> None:
    """P23.1 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"
