# -*- coding: utf-8 -*-
"""P32.0 守卫：**结局文案分流 + 狂按快进感知 + 提示词队列熔断**。

真机事故（2026-11-05，街机档直播）暴露了三件事，这一版把它们钉死：

1. **结局文案分流** —— 玩家**活着**撑满 30 天走到结算日，结算页却挂着
   「你连这个游戏都玩不过」的反诈警告和【不服再来？（这次别梭哈）】的
   破防按钮。口径只有一条：**有没有真破产**（现金归零 / 负债）。
   存活 ⇒ 专属骄傲文案 + 正向按钮；破产 ⇒ 原来的反诈警告与破防文案
   **一个字都不许改**。
2. **狂按快进感知** —— 「每天只能跳一次」挡不住**连点**（跳过会把日子
   推进一天 ⇒ 下一次点击落在新的一天）。5 秒内点超过 3 次判定为
   【狂按快进】：立刻清空 Yui 的待推送队列，注入一句吐槽提示词，
   并在狂按期给她**限频**（别让她一直跳出来）。
3. **提示词队列熔断** —— 推送前，队列超过 3 条、或消息存放超过 5 秒、
   或同一天同类消息 ⇒ 只保留**最新一条**，旧的全部丢掉。

**红线**：引擎层 / 模式层零改动。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from conftest import FakeCtx, engine_mode_code_changes, make_plugin, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


# ---------------------------------------------------------------- 工具


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


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _pending_prompt_class() -> Any:
    """拿到插件层的待推送条目类型（``_YuiPendingPrompt``）。"""
    from plugins.mock_stock_meow import _YuiPendingPrompt

    return _YuiPendingPrompt


def _new_plugin(tmp_path: Path, monkeypatch: Any, mode: str, seed: int) -> Any:
    """自建一个插件实例（数据根目录隔离到 ``tmp_path``）。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    instance = make_plugin(FakeCtx(config_path=Path("mock_stock_meow.toml")))
    run(instance.on_startup())
    run(instance.join(mode=mode, seed=seed))
    return instance


def _reasons(plugin: Any) -> list[str]:
    return [str(item[1]) for item in plugin._yui_pending_prompts]


# ══════════════════════════════════════════════════════════ ① 结局文案分流


def test_the_bankruptcy_copy_is_untouched() -> None:
    """破产分支的原文案**一个字都不许改**（反诈警告 + 破防按钮）。"""
    i18n = _i18n()
    assert i18n["panel.settlement_title_dead"] == "结算报告：你的钱呢？"
    assert i18n["panel.settlement_again"] == "不服再来？（这次别梭哈）"
    assert "玩不过" in i18n["settlement.beat_down"]
    assert "反诈" in i18n["settlement.help"]
    # 引擎侧那份固定反诈文案也还在。
    from plugins.mock_stock_meow import ANTI_FRAUD_NOTICE

    assert "玩不过" in ANTI_FRAUD_NOTICE


def test_the_alive_branch_has_its_own_proud_copy() -> None:
    """存活结局必须有**专属**骄傲文案 + **正向**按钮。"""
    i18n = _i18n()
    for key in (
        "settlement.survived_praise",
        "settlement.survived_praise_more",
        "settlement.survived_footer",
        "panel.settlement_again_alive",
        "panel.alive_state",
        "panel.alive_state_value",
        "panel.alive_praise_label",
    ):
        assert key in i18n, f"i18n 缺 {key}"
    assert "{days}" in i18n["settlement.survived_praise"], "骄傲文案要带上存活天数"
    assert "撑了" in i18n["settlement.survived_praise"]
    # 正向按钮不许带嘲讽。
    for banned in ("不服", "梭哈", "玩不过"):
        assert banned not in i18n["panel.settlement_again_alive"], banned


def test_the_alive_copy_has_no_bankruptcy_tone() -> None:
    """存活文案不许出现破产语气（那是破产分支的专属）。"""
    i18n = _i18n()
    for key in (
        "settlement.survived_praise",
        "settlement.survived_praise_more",
        "settlement.survived_footer",
        "panel.settlement_again_alive",
        "panel.alive_state_value",
        "panel.alive_praise_label",
    ):
        text = i18n[key]
        for banned in ("玩不过", "天台", "别梭哈", "不服", "破产原因"):
            assert banned not in text, f"{key} 里出现了破产分支的语气：{banned}"


def test_the_panel_only_shows_the_notice_when_the_player_is_broke() -> None:
    """面板：反诈警告**只在破产时**出现。"""
    code = _panel_code()
    assert "report.anti_fraud_notice" in code, "破产分支的反诈警告不见了"
    index = code.index("report.anti_fraud_notice")
    window = code[max(0, index - 400) : index]
    assert "deadReport" in window, "反诈警告必须在 deadReport 分支里才渲染"


def test_the_panel_gives_survivors_a_positive_button() -> None:
    """面板：存活给正向按钮，破产仍旧给破防按钮。"""
    code = _panel_code()
    assert code.count('t("panel.settlement_again_alive")') == 1, "存活分支没有正向按钮"
    assert code.count('t("panel.settlement_again")') == 1, "破产分支的旧按钮不见了"
    index = code.index('t("panel.settlement_again_alive")')
    window = code[max(0, index - 400) : index]
    assert "deadReport" in window, "正向按钮必须按 deadReport 分流"


def test_the_panel_shows_the_proud_copy_for_survivors() -> None:
    """面板：存活结局要有专属骄傲文案，并且**不挂**"破产原因"标签。"""
    code = _panel_code()
    assert "settlement.survived_praise" in code, "面板没引用存活骄傲文案"
    assert "settlement.survived_praise_more" in code
    assert "settlement.survived_footer" in code
    assert "panel.alive_state" in code, "存活分支还挂着破产原因标签"
    assert "panel.alive_praise_label" in code


def test_the_settlement_payload_carries_the_survivor_praise(tmp_path, monkeypatch) -> None:
    """活着结算 ⇒ 载荷里带**专属骄傲文案**。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3201)
    result = run(instance.settle())
    assert result.is_ok(), f"结算失败：{result!r}"
    payload = result.unwrap()
    assert payload["survivor_praise"], "存活结算必须有专属骄傲文案"
    assert "撑了" in payload["survivor_praise"]
    # 反诈警告字段仍在（只是面板不再给存活者显示它）。
    assert payload["anti_fraud_notice"]


def test_the_broke_settlement_has_no_survivor_praise(tmp_path, monkeypatch) -> None:
    """真破产 ⇒ **不许**有骄傲文案（那是存活分支的专属）。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3202)
    run(
        instance._repo.transaction(
            [
                (
                    "UPDATE players SET alive = 0, death_reason = ? "
                    "WHERE is_catgirl = 0",
                    ("starved",),
                )
            ]
        )
    )
    result = run(instance.settle())
    assert result.is_ok(), f"结算失败：{result!r}"
    payload = result.unwrap()
    assert payload["survivor_praise"] == "", "破产结局不许出现骄傲文案"
    assert payload["anti_fraud_notice"], "破产结局必须保留反诈警告"


# ══════════════════════════════════════════════════════════ ② 狂按快进


def test_three_rapid_clicks_are_not_a_fast_forward(tmp_path, monkeypatch) -> None:
    """5 秒内点 3 次 ⇒ **不算**狂按（阈值是"超过 3 次"）。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3211)
    for _ in range(3):
        run(instance.skip_day())
    assert instance._skip_fast_count == 0, "3 次点击不该判定为狂按"
    assert instance._skip_fast_until == 0.0


def test_four_rapid_clicks_trigger_the_fast_forward(tmp_path, monkeypatch) -> None:
    """5 秒内点 4 次 ⇒ 判定为【狂按快进】。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3212)
    for _ in range(4):
        run(instance.skip_day())
    assert instance._skip_fast_count == 1, "4 次点击必须判定为狂按"
    assert instance._skip_fast_until > time.time(), "狂按期必须已经生效"


def test_the_fast_forward_clears_the_backlog(tmp_path, monkeypatch) -> None:
    """狂按 ⇒ 待推送队列被**清空**，只留那一句吐槽。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3213)
    for _ in range(4):
        run(instance.skip_day())
    reasons = _reasons(instance)
    assert reasons == ["yui_fast_forward"], f"狂按后队列里不该有积压：{reasons}"
    assert len(instance._yui_pending_prompts) == 1


def test_the_fast_forward_injects_the_taunt(tmp_path, monkeypatch) -> None:
    """狂按 ⇒ 注入**吐槽**提示词（吐槽，不分析行情，一句话搞定）。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3214)
    for _ in range(4):
        run(instance.skip_day())
    prompt = str(instance._yui_pending_prompts[0][0])
    assert "你赶着投胎啊" in prompt, prompt
    assert "不要分析行情了" in prompt, prompt
    assert "一句话搞定" in prompt, prompt
    assert "根本不看你的分析" in prompt, prompt


def test_the_fast_forward_limits_how_often_yui_speaks(tmp_path, monkeypatch) -> None:
    """狂按期 ⇒ 她**不许**一直跳出来（限频闸门）。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3215)
    calls: list[dict[str, Any]] = []
    instance.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    # 手动进入狂按期。
    instance._skip_fast_until = time.time() + 60.0
    instance._yui_pending_prompts = [("第一条", "yui_live_turn", 3)]
    run(instance._flush_yui_prompts())
    assert len(calls) == 1, "狂按期的第一句必须说出去"
    instance._yui_pending_prompts = [("第二条", "yui_live_turn", 3)]
    run(instance._flush_yui_prompts())
    assert len(calls) == 1, "狂按期她不许一直跳出来"


def test_the_skip_day_records_the_click_intervals(tmp_path, monkeypatch) -> None:
    """``skip_day`` 入口必须**记点击时刻**（这是判定的原料）。"""
    instance = _new_plugin(tmp_path, monkeypatch, "classic_arcade", 3216)
    assert instance._skip_click_times == []
    run(instance.skip_day())
    assert len(instance._skip_click_times) == 1
    run(instance.skip_day())
    assert len(instance._skip_click_times) == 2


# ══════════════════════════════════════════════════════════ ③ 队列熔断


def test_the_prune_drops_expired_messages(plugin) -> None:
    """存放超过 5 秒的消息 ⇒ 推送前**直接丢掉**。"""
    entry = _pending_prompt_class()
    now = time.time()
    plugin._yui_pending_prompts = [
        entry("过期的", "yui_news", 5, enqueued_at=now - 30.0, day=3),
        entry("新鲜的", "yui_news", 5, enqueued_at=now, day=3),
    ]
    kept = plugin._prune_yui_queue()
    assert [str(item[0]) for item in kept] == ["新鲜的"]


def test_the_prune_keeps_only_the_newest_of_the_same_day_and_kind(plugin) -> None:
    """同一天同类 ⇒ 只推最新的，旧的全部 pop 掉。"""
    entry = _pending_prompt_class()
    now = time.time()
    plugin._yui_pending_prompts = [
        entry("旧新闻", "yui_news", 5, enqueued_at=now, day=3),
        entry("新新闻", "yui_news", 5, enqueued_at=now, day=3),
    ]
    kept = plugin._prune_yui_queue()
    assert [str(item[0]) for item in kept] == ["新新闻"]


def test_the_keep_all_reasons_are_never_collapsed(plugin) -> None:
    """双打点评 / 重大事件必须**条条送到**（不许被同天同类折叠）。"""
    entry = _pending_prompt_class()
    now = time.time()
    plugin._yui_pending_prompts = [
        entry("点评甲", "yui_duo_commentary", 4, enqueued_at=now, day=3),
        entry("点评乙", "yui_duo_commentary", 4, enqueued_at=now, day=3),
    ]
    kept = plugin._prune_yui_queue()
    assert len(kept) == 2, "双打点评（每成交一笔就点评一句）不许被折叠"


def test_the_prune_keeps_only_the_newest_when_the_queue_is_long(plugin) -> None:
    """队列超过 3 条 ⇒ 只留最新一条。"""
    entry = _pending_prompt_class()
    now = time.time()
    plugin._yui_pending_prompts = [
        entry(f"第{index}条", f"reason_{index}", 5, enqueued_at=now, day=index)
        for index in range(1, 6)
    ]
    kept = plugin._prune_yui_queue()
    assert len(kept) == 1, f"长队必须熔断成一条：{[item[0] for item in kept]}"
    assert str(kept[0][0]) == "第5条"


def test_the_queue_entries_are_still_triples(plugin) -> None:
    """队列元素**恒为** ``(prompt, reason, priority)`` 三元组。"""
    entry = _pending_prompt_class()
    plugin._yui_pending_prompts = [
        entry("甲", "yui_news", 5, enqueued_at=time.time(), day=1),
        entry("乙", "yui_live_turn", 3, enqueued_at=time.time(), day=1),
    ]
    for item in plugin._yui_pending_prompts:
        assert isinstance(item, tuple), f"元素不是元组：{item!r}"
        assert len(item) == 3, f"元素不是三元组：{item!r}"
        prompt, reason, priority = item
        assert isinstance(prompt, str) and prompt
        assert isinstance(reason, str) and reason
        assert isinstance(priority, int) and not isinstance(priority, bool)


def test_the_expired_messages_really_leave_the_queue(plugin) -> None:
    """过期消息必须**真的离开队列**（不许只是"看不见"）。"""
    entry = _pending_prompt_class()
    plugin._yui_pending_prompts = [
        entry("过期的", "yui_news", 5, enqueued_at=time.time() - 30.0, day=3)
    ]
    run(plugin._flush_yui_prompts())
    assert plugin._yui_pending_prompts == [], "过期消息必须离开队列"


def test_the_flush_only_pushes_the_newest_of_a_long_queue(plugin) -> None:
    """长队 ⇒ 心跳只推**最新一条**，旧的全部丢掉。"""
    entry = _pending_prompt_class()
    calls: list[dict[str, Any]] = []
    plugin.ctx.push_message = lambda **kwargs: calls.append(kwargs)
    # P33.0：广播闸门默认是关的（没开局 ⇒ 不许播报）。
    # 这条测的是**熔断本身**，所以手动把闸门打开。
    plugin._is_game_active = True
    now = time.time()
    plugin._yui_pending_prompts = [
        entry(f"第{index}条", f"reason_{index}", 5, enqueued_at=now, day=index)
        for index in range(1, 6)
    ]
    run(plugin._flush_yui_prompts())
    assert len(calls) == 1, f"长队只能推一条：{len(calls)}"
    assert "第5条" in str(calls[0]), "推出去的必须是最新那条"
    assert plugin._yui_pending_prompts == [], "推完队列必须清空"


def test_the_flush_is_still_only_called_by_the_heartbeat() -> None:
    """推送点仍然只有心跳那一个（持锁 await 会冻住事件循环）。"""
    source = _init_text()
    assert len(re.findall(r"_flush_yui_prompts\(\)", source)) == 1


# ══════════════════════════════════════════════════════════ ④ 红线


def test_the_engine_and_modes_are_untouched() -> None:
    """引擎层 / 模式层的**代码**零改动。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"
