# -*- coding: utf-8 -*-
"""P34.0 守卫：**初始界面不再被中断** + **官方幽灵猫爪引导**。

这一版钉死两件事：

1. **初始界面（报名字那屏）渲染稳定** —— 输入框**绝不能**失焦、
   刚打的字**绝不能**凭空消失、整屏**绝不能**莫名其妙被刷新。

   真机事故（2026-11-05）的根因已经查实（不是猜的）：

   * 宿主运行时匹配**无 key** 的子节点是**从后往前**配的
     （``oldUnkeyed[unkeyedIndex--]``）⇒ **子节点数量一变**，
     排在它前面的兄弟节点会被**整个重建** ⇒ 输入框那个 DOM 节点换新 ⇒
     焦点丢失（宿主那份 ``restoreFocusState`` 只在旧节点 ``isConnected``
     时才还原焦点，重建之后就还原不了）。
   * 面板里 ``{cond ? <X/> : null}`` 这个形状到处都是 —— ``join`` 这个
     action 到位之前 ``joinAction`` 是 undefined，那一格就是 ``null``；
     action 一到位它变成元素 ⇒ **数量 +1** ⇒ 名字输入框当场被重建。
   * 载荷形状漂移（宿主超时回 ``state: {}``、插件回两键残缺载荷）
     ⇒ 整屏重画。

   所以这里的守卫钉的是**结构性契约**：子节点数量恒定、载荷形状恒定、
   名字草稿持久化、一次失败不许换整屏。

2. **官方「幽灵猫爪」引导** —— 素材 base64 内联、覆盖层不吃点击、
   ``requestAnimationFrame`` 缓动（**不许** ``absolute`` / ``@keyframes``）、
   蓝色描边高亮、到达切 click 图并代劳点击、玩家提前点就立刻收爪。

**红线**：引擎层 / 模式层零改动。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import FakeCtx, engine_mode_code_changes, make_plugin, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_ASSETS = _PLUGIN_ROOT / "ui" / "assets"


# ---------------------------------------------------------------- 工具


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_css() -> str:
    """抽出 ``RRM_CSS`` 模板字符串的正文。"""
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号"
    return rest[:end]


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


def _method_body(source: str, name: str) -> str:
    """取某个方法（``async def xxx`` / ``def xxx``）的源码正文。"""
    marker = f"def {name}("
    assert marker in source, f"找不到方法 {name}"
    tail = source.split(marker, 1)[1]
    lines = tail.split("\n")
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "\n".join(body)


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════ ① 后端：载荷形状恒定


def test_the_idle_payload_has_a_fixed_shape() -> None:
    """空闲态载荷的**键集合固定** —— 形状漂移 = 整屏重画 = 输入框失焦。"""
    body = _method_body(_init_text(), "_idle_context_payload")
    for key in (
        "stage",
        "notice",
        "player_name",
        "pace",
        "onboarding",
        "meow",
        "yui",
        "community_events",
    ):
        assert f'"{key}"' in body, f"空闲载荷里少了 {key}"


def test_every_degraded_exit_uses_the_same_idle_payload() -> None:
    """三个降级出口（拿不到锁 / 数据库不可用 / 正常空闲）**走同一个 helper**。"""
    source = _init_text()
    assert '_idle_context_payload("市场正在结算，稍等一下。")' in source, "拿不到锁的出口没走统一 helper"
    assert '_idle_context_payload("数据库不可用。")' in source, "数据库不可用的出口没走统一 helper"
    assert '_idle_context_payload("当前没有进行中的对局，请先加入一局。")' in source, (
        "正常空闲的出口没走统一 helper"
    )
    # 旧的**两键残缺载荷**不许再出现（它们就是形状漂移的源头）。
    # （只查 ``return`` 形状 —— 文档里提到它们是正常的。）
    assert 'return {"stage": "busy"}' not in source, "还在回两键残缺载荷"
    assert 'return {"stage": "p2-persistence"}' not in source, "还在回两键残缺载荷"


def test_the_idle_payload_never_changes_shape_at_runtime(tmp_path: Path, monkeypatch: Any) -> None:
    """运行时复核：正常空闲 / 拿不到锁 / 直接调 helper —— **键集合完全一致**。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    plugin = make_plugin(FakeCtx(config_path=Path("mock_stock_meow.toml")))
    run(plugin.on_startup())

    normal = run(plugin.market_context())
    assert normal.get("stage") == "idle", "刚启动就该是空闲态"

    direct = plugin._idle_context_payload("测试")
    assert set(direct) == set(normal), "helper 与正常空闲的键集合不一致"

    # 「拿不到锁」那条降级出口。
    plugin._context_cache = None
    plugin._lock.acquire()
    try:
        locked = run(plugin.market_context())
    finally:
        plugin._lock.release()
    assert set(locked) == set(normal), "拿不到锁时的键集合不一致"


def test_the_idle_heartbeat_warms_the_cache() -> None:
    """空闲态心跳要**焐热配置缓存** —— 否则面板的上下文请求要付一次冷 IPC。

    宿主那边的 UI 上下文只有 ~4.25 秒预算，冷启动就可能超时 ⇒ 回一份
    **空的** state ⇒ 面板整屏重画 ⇒ 初始界面被中断。
    """
    body = _method_body(_init_text(), "_heartbeat_body")
    assert "await self._engine_config()" in body, "空闲心跳没有焐热引擎配置缓存"
    assert "await self._ensure_settings_cache()" in body, "空闲心跳没有焐热设置缓存"


# ══════════════════════════════════════════════════ ② 面板：子节点数量恒定


def test_the_panel_has_a_constant_child_count_slot() -> None:
    """面板里必须有一个「恒定子节点数」的占位层 ``Slot``。"""
    code = _panel_code()
    assert "function Slot(" in code, "面板里没有 Slot 占位层"
    assert "if (!props.when) return null" in code, "Slot 没有「不显示」的分支"


def test_the_naming_card_has_a_constant_child_count() -> None:
    """**初始界面那张卡**（装着名字输入框）的子节点数量必须恒定。

    这一段是整个事故的核心：档位卡以前写成 ``{joinAction ? (...) : null}``，
    ``join`` action 到位的那一瞬间数量 +1 ⇒ 排在它前面的**输入框被重建**
    ⇒ 玩家正打着字，焦点当场丢失。
    """
    code = _panel_code()
    start = code.index("panel.name_input_label")
    end = code.index("panel.idle_modes_hint", start)
    block = code[start:end]
    assert "joinAction ?" not in block, "档位卡不许再直接用 null 切换"
    assert "h(Slot, {" in block, "档位卡必须包进 Slot"
    assert "Boolean(joinAction)" in block, "Slot 的开关必须是 joinAction"


def test_the_other_flippable_children_are_wrapped_too() -> None:
    """其余「会变 null」的孩子也一律包进 ``Slot``。"""
    code = _panel_code()
    assert "const boomModal = h(Slot, {" in code, "弹窗轰炸没包进 Slot"
    assert "const directorConsole = h(Slot, {" in code, "导演台没包进 Slot"
    assert "when: Boolean(critical)" in code, "病危警告条没包进 Slot"
    assert "when: Boolean(yuiBanner)" in code, "Yui 横幅没包进 Slot"
    assert "when: Boolean(state.director_progress)" in code, "导演进度条没包进 Slot"


def test_a_broken_payload_does_not_blank_the_screen() -> None:
    """坏载荷（宿主超时回空的 state）不许把整屏换成空白 —— 回退上一份好载荷。"""
    code = _panel_code()
    assert "lastGoodState" in code, "面板没有保留上一份好载荷"
    assert "incomingUsable" in code, "面板没有判断载荷可不可用"
    assert 'typeof incomingState.stage === "string"' in code, "载荷可用性判断写错了"


def test_the_name_draft_survives_a_rebuild() -> None:
    """名字草稿与「已改过」标记走 ``useLocalState``（宿主那份是**进程内**的）。

    屏幕被重建（重挂载 / 整屏重画）时，玩家刚打的字不会凭空消失。
    """
    code = _panel_code()
    assert 'useLocalState<string>("rrm.name_draft", "")' in code, "名字草稿没走持久化"
    assert 'useLocalState<string>("rrm.name_touched", "")' in code, "「已改过」标记没走持久化"


def test_one_failed_call_does_not_replace_the_whole_screen() -> None:
    """「插件已停止」的整屏切换放宽到**连续两次**失败。

    一次失败多半是瞬时的（宿主正忙着、插件刚好在重启）——
    一次就换屏 = 名字输入框当场被销毁。
    """
    code = _panel_code()
    assert "offlineStrikes.current += 1" in code, "没有失败计数"
    assert "if (offlineStrikes.current < 2)" in code, "一次失败就换屏了"
    assert "offlineStrikes.current = 0" in code, "成功之后没把计数清零"


def test_the_error_boundary_is_narrowed_down() -> None:
    """外层那道边界之外，给「容易炸的那几块」各套一层**内层**边界。

    一块炸了只炸那一块 ⇒ 名字输入框照样能用（外层边界会换掉**整屏**）。
    """
    code = _panel_code()
    assert "function SectionBoundary(" in code, "没有分区错误边界"
    assert "panel.section_error_body" in code, "分区边界的兜底块没有文案"
    assert "<SectionBoundary t={t}>" in code, "聊天框没套分区边界"


# ══════════════════════════════════════════════════════ ③ 官方幽灵猫爪


def test_the_ghost_cursor_assets_are_inlined_as_base64() -> None:
    """官方美术资源转成 base64 **内联**（面板跑在 opaque origin 的 iframe 里）。"""
    assets = (_ASSETS / "ghost_cursor.ts").read_text(encoding="utf-8")
    assert "export const GHOST_CURSOR_DEFAULT" in assets, "没有导出默认猫爪"
    assert "export const GHOST_CURSOR_CLICK" in assets, "没有导出点击猫爪"
    assert assets.count("data:image/png;base64,") == 2, "两张图都要内联成 data URI"


def test_the_ghost_cursor_pngs_are_real_png_files() -> None:
    """源素材是真的 PNG（不是占位文件）。"""
    for name in ("ghost-cursor-default.png", "ghost-cursor-click.png"):
        data = (_ASSETS / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} 不是 PNG"
        assert len(data) > 1000, f"{name} 太小，多半是占位文件"


def test_the_ghost_cursor_layer_never_blocks_the_clicks() -> None:
    """覆盖层是**装饰** —— 绝不能挡住玩家的点击。"""
    css = _panel_css()
    block = re.search(r"\.rrm-root \.rrm-ghost-layer\s*\{[^}]*\}", css)
    assert block, "面板里没有 .rrm-ghost-layer"
    body = block.group(0)
    assert "pointer-events: none" in body, "覆盖层没有 pointer-events: none"
    # 覆盖层**不占布局**（高度算 0，猫爪靠溢出画出来）。
    assert "height: 0" in body, "覆盖层占了布局高度"
    assert "overflow: visible" in body, "猫爪会被裁掉"
    # 网格叠放：与正文落在同一个格子里。
    assert "grid-area: 1 / 1 / 2 / 2" in body, "覆盖层没有用网格叠放"


def test_the_ghost_cursor_moves_with_rAF_and_never_uses_absolute() -> None:
    """轨迹移动走 ``requestAnimationFrame`` + 缓动函数，**绝不用** absolute / 关键帧。"""
    code = _panel_code()
    assert "requestAnimationFrame" in code, "猫爪没有用 rAF"
    assert "cancelAnimationFrame" in code, "猫爪没法被打断"
    assert "img.style.transform = `translate(" in code, "猫爪没有改 transform"
    assert "1 - Math.pow(1 - progress, 3)" in code, "缓动函数没了"
    for banned in ('position: "absolute"', 'position: "fixed"', "@keyframes"):
        assert banned not in code, f"猫爪用上了禁用写法：{banned}"


def test_the_ghost_cursor_uses_a_constant_style_object() -> None:
    """猫爪的初始 style 必须是**模块级常量**。

    宿主按**引用**比较 style：每次渲染新建一个对象字面量 ⇒ 面板一重画
    就把 transform 复位 ⇒ 飞行动画刚起步就被打断。
    """
    code = _panel_code()
    assert "const GHOST_CURSOR_STYLE = {" in code, "猫爪的初始 style 不是常量"
    assert "style: GHOST_CURSOR_STYLE" in code, "猫爪没有用这个常量"


def test_the_ghost_cursor_highlights_and_clicks_the_target() -> None:
    """目标加蓝色描边 ⇒ 猫爪飞过去 ⇒ 到达切 click 图并代劳点击。"""
    code = _panel_code()
    assert 'classList.add("rrm-ghost-target")' in code, "目标没有加高亮类"
    assert "img.src = GHOST_CURSOR_CLICK" in code, "到达时没有切 click 图"
    assert "target.click()" in code, "到达时没有代劳点击"
    css = _panel_css()
    block = re.search(r"\.rrm-ghost-target\s*\{[^}]*\}", css)
    assert block, "面板里没有 .rrm-ghost-target"
    assert "outline" in block.group(0), "高亮不是描边"


def test_the_ghost_cursor_never_signs_for_the_player() -> None:
    """目标是**可交互元素**时不代劳点击。

    教程有一个锚点就挂在【借钱】那个按钮上 —— 代劳等于替玩家签字借债。
    """
    code = _panel_code()
    assert 'target.closest("button, a, input, select, textarea")' in code, (
        "没有拦住可交互元素"
    )


def test_the_player_can_interrupt_the_ghost() -> None:
    """玩家**手动提前点了目标** ⇒ 立刻取消 RAF、光标消失。"""
    code = _panel_code()
    assert "selfClickRef" in code, "没有区分「自己点的」和「玩家点的」"
    assert 'target.addEventListener("click", onUserClick, true)' in code, (
        "没有在目标上装打断监听"
    )
    assert "stop()" in code, "打断之后没有取消动画"


def test_the_ghost_layer_is_always_mounted() -> None:
    """覆盖层**永远在**局中页里（不随引导增删）。

    宿主匹配**无 key** 的子节点是从后往前配的 —— 子节点数量一变，
    前面的兄弟节点会被整个重建（那正是要修的失焦毛病）。
    """
    code = _panel_code()
    assert "h(GhostCursorLayer, { anchor: ghostAnchor, enabled: ghostEnabled })" in code, (
        "覆盖层没有挂到局中页"
    )
    assert "const ghostEnabled = tutorialPhase === \"step\" && !tutorialWaiting" in code, (
        "猫爪没有跟教程同一条命脉"
    )


def test_the_new_i18n_keys_exist() -> None:
    """新增文案必须进词典（面板零硬编码中文）。"""
    i18n = _i18n()
    for key in ("panel.section_error_body", "panel.section_error_retry"):
        assert key in i18n, f"i18n 里缺 {key}"


# ══════════════════════════════════════════════════════════ 红线


def test_the_engine_is_untouched() -> None:
    """引擎层 / 模式层**零改动**（P34.0 全在插件层）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎 / 模式层被改了：{changed}"
