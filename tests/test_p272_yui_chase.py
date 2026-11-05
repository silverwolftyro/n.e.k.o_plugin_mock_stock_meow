# -*- coding: utf-8 -*-
"""P27.2 守卫：**Yui 追单**（自嗨）的三道防线 + live 日配额。

事故背景
--------
P27.1 的**成交回执**会**多触发一个 AI 回合**（``ai_behavior="respond"``）。
那个回合里模型可能顺手**再下一单** —— 玩家压根没同意的那一单已经成交了。
直播里这就是最难看的那种事故：**她替玩家追单**。

三道防线（这一版钉死的就是这三道）
----------------------------------

1. **提示词层** —— :data:`YUI_RECEIPT_CYCLE_DIRECTIVE`（同一份常量挂在
   授权提示词 / 直播主动回合 / 成交回执 / 失败回执上，口径不许分叉）：
   「【系统强制指令】本回合只允许做汇报，严禁再调用任何交易工具，
   直到玩家下一次主动和你说话。」
2. **代码层（回执周期锁）** —— 成交之后立刻上锁；玩家下一次主动操作
   （推进一拍 / 买卖 / 分析 …）或**下一个 tick** 才开锁。
   锁住期间 ``mock_stock_meow_yui_trade`` **直接拒绝**。
   **提示词管不住模型，工具层才管得住。**
3. **失败反馈层** —— 失败回执改成 ``ai_behavior="read"``
   （**只进上下文、不触发回合**）：失败本来就发生在她当前那一回合里，
   再触发一个回合只会让她"再试一次" ⇒ 自触发死循环。

贯穿全文件的红线：

* **引擎层 / 模式层零改动**；
* **提示词里不许出现局中秘密**（P4 §11 同一套口径）；
* **被拒的追单不许消耗日配额**（不然她会被配额"误伤"）；
* **被拒的追单不许推任何推送**（推了就是又触发一个回合 ⇒ 循环）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_MEOW = _PLUGIN_ROOT / "meow.py"


#: P4 §11「局中不泄露」的禁词（**含单字「雷」**）—— 与 P27.1 同一套口径。
_BANNED_IN_GAME = (
    "heat",
    "seed",
    "reversal",
    "反转",
    "accuracy",
    "prey_index",
    "真新闻",
    "假新闻",
    "雷",
    "真伪",
    "surge",
    "crash",
)


# ---------------------------------------------------------------- 工具


def _meow() -> Any:
    return __import__("plugins.mock_stock_meow.meow", fromlist=["meow"])


def _init_text() -> str:
    return _INIT.read_bytes().decode("utf-8")


def _method_body(text: str, name: str) -> str:
    """取某个方法的源码正文（到下一个同缩进的方法定义为止）。

    用 ``def {name}(`` 做锚点 ⇒ ``async def`` 也能命中。
    """
    marker = f"def {name}("
    index = text.find(marker)
    assert index != -1, f"源码里找不到 {name}"
    rest = text[index:]
    stops = [
        pos
        for pos in (rest.find("\n    def ", 1), rest.find("\n    async def ", 1))
        if pos != -1
    ]
    stop = min(stops) if stops else -1
    return rest if stop == -1 else rest[:stop]


class _PushRecorder:
    """宿主 ``push_message`` 的假实现（记下每次推送的原文与行为）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        return None

    def texts(self) -> list[str]:
        out: list[str] = []
        for call in self.calls:
            for part in call.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(str(part.get("text") or ""))
        return out

    def matching(self, needle: str) -> list[str]:
        return [text for text in self.texts() if needle in text]


def _attach_recorder(plugin: Any) -> _PushRecorder:
    recorder = _PushRecorder()
    plugin.ctx.push_message = recorder
    return recorder


def _start_live_session(plugin: Any) -> list[dict[str, Any]]:
    """开一局 + 让 Yui 拿到账户（live 模式）⇒ 返回当天可买的标的。"""
    run(plugin.join(mode="classic_arcade", seed=7309))
    run(plugin.yui_mode(mode="live"))
    return list(run(plugin.market_context())["stocks"])


def _trade_reasons(plugin: Any) -> list[str]:
    """成交记录里的来源标签（``manual`` = 玩家，``yui`` = Yui）。"""
    reasons: list[str] = []
    for row in plugin._repo.list_trades() if hasattr(plugin._repo, "list_trades") else []:
        reasons.append(str(row.get("reason") or ""))
    return reasons


# ═════════════════════════════════════════════════════ ① 提示词层


def test_the_receipt_cycle_directive_is_worded_as_a_hard_order() -> None:
    """约束词必须**严厉到位**（用户 P27.2 原文口径）。"""
    meow = _meow()
    directive = meow.YUI_RECEIPT_CYCLE_DIRECTIVE
    assert "【系统强制指令】" in directive
    assert "本回合只允许做汇报" in directive
    assert "严禁再调用任何交易工具" in directive
    assert "直到玩家下一次主动和你说话" in directive


def test_every_trade_prompt_carries_the_same_directive() -> None:
    """授权 / 直播主动回合 / 成交回执 / 失败回执 —— **同一份常量**。

    口径分叉 = 约束从另一个门漏进来 ⇒ 这里断言的是**同一份常量**。
    """
    meow = _meow()
    directive = meow.YUI_RECEIPT_CYCLE_DIRECTIVE

    assert directive in meow.YUI_AUTHORITY_PROMPT
    receipt = meow.yui_trade_receipt_prompt(
        action="buy",
        stock="某某科技",
        amount=12.5,
        price=3.25,
        money=40.625,
        cash_after=959.375,
    )
    assert directive in receipt
    failure = meow.yui_trade_failure_prompt(
        action="buy",
        stock="某某科技",
        reason="钱不够",
        cash=100.0,
    )
    assert directive in failure


def test_the_directive_does_not_leak_the_in_game_secrets() -> None:
    """约束词不许带局中秘密（P4 §11 同一套口径）。"""
    meow = _meow()
    text = meow.YUI_RECEIPT_CYCLE_DIRECTIVE.lower()
    for banned in _BANNED_IN_GAME:
        assert banned.lower() not in text, banned


def test_the_live_prompt_carries_the_directive(plugin: Any) -> None:
    """直播托管的**每一次**主动回合都带这条（不止授权那一刻）。"""
    meow = _meow()
    run(plugin.join(mode="classic_arcade", seed=7309))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None

    prompt = plugin._yui_live_prompt(session)
    assert meow.YUI_RECEIPT_CYCLE_DIRECTIVE in prompt
    lowered = prompt.lower()
    for banned in _BANNED_IN_GAME:
        assert banned.lower() not in lowered, banned


# ═════════════════════════════════════════════════════ ② 代码层（回执周期锁）


def test_a_second_trade_in_the_same_cycle_is_rejected(plugin: Any) -> None:
    """**追单**（收到回执后又下一单）⇒ 工具层**直接拒绝**。

    这是用户 P27.2 的核心诉求：她完成一笔交易 → 收到回执 → **不许自己追单**。
    """
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)
    target = stocks[0]

    first = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="buy", ratio=0.1)
    )
    assert first.get("is_error") is False
    assert first["output"]["ok"] is True
    assert recorder.matching("成交回执"), "第一笔必须推成交回执"

    cash_after = float(first["output"]["cash_after"])
    recorder.calls.clear()

    # 同一回执周期里的第二单 ⇒ 拒绝。
    chase = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="buy", ratio=0.1)
    )
    assert chase.get("is_error") is True
    assert chase["output"]["ok"] is False
    assert chase["output"].get("receipt_cycle") is True
    # 账户**没有**被动过。
    assert abs(float(run(plugin.market_context())["cash"]) - cash_after) < 0.01
    # 被拒的追单**不许推任何推送**（推了就是又触发一个回合 ⇒ 循环）。
    assert recorder.calls == [], recorder.texts()


def test_a_rejected_chase_does_not_consume_the_daily_quota(plugin: Any) -> None:
    """追单被拒**不许**吃掉日配额（否则她会被配额误伤）。"""
    _start_live_session(plugin)
    before = run(plugin.market_context())["yui"]["quota_left"]

    run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.05))
    locked = run(plugin.market_context())["yui"]["quota_left"]
    assert locked == before - 1, "成交一笔扣一格配额"

    # 追单被拒 ⇒ 配额**不变**。
    run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.05))
    assert run(plugin.market_context())["yui"]["quota_left"] == locked


def test_the_player_opening_a_new_turn_reopens_the_cycle(plugin: Any) -> None:
    """玩家**又开口了**（推进一拍）⇒ 回执周期结束，下一回合她可以再动手。"""
    _start_live_session(plugin)
    run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.05))
    assert plugin._yui_trade_cycle_is_locked() is True

    # 玩家点【推进一拍】⇒ 新的一回合。
    run(plugin.advance_tick())
    assert plugin._yui_trade_cycle_is_locked() is False


def test_the_player_buying_also_reopens_the_cycle(plugin: Any) -> None:
    """玩家自己**买卖**也是"开口" ⇒ 同样解开回执周期。"""
    stocks = _start_live_session(plugin)
    run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=0.05))
    assert plugin._yui_trade_cycle_is_locked() is True

    run(plugin.buy(str(stocks[0]["id"]), 1.0))
    assert plugin._yui_trade_cycle_is_locked() is False


def test_yuis_own_trade_does_not_reopen_the_cycle(plugin: Any) -> None:
    """**她自己**下的单不算"玩家开口"（否则锁等于没上）。"""
    stocks = _start_live_session(plugin)
    run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=0.05))
    assert plugin._yui_trade_cycle_is_locked() is True


def test_a_new_game_resets_the_cycle(plugin: Any) -> None:
    """新的一局 ⇒ 回执周期归零（上一局的账不许带过来）。"""
    _start_live_session(plugin)
    run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.05))
    assert plugin._yui_trade_cycle_is_locked() is True

    run(plugin.join(mode="classic_arcade", seed=7311))
    assert plugin._yui_trade_cycle_is_locked() is False


def test_the_panel_payload_exposes_the_cycle_state(plugin: Any) -> None:
    """面板载荷里能看见"这一轮她不许再动手"（可观测性）。"""
    _start_live_session(plugin)
    assert run(plugin.market_context())["yui"]["cycle_locked"] is False
    run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.05))
    assert run(plugin.market_context())["yui"]["cycle_locked"] is True


def test_the_source_keeps_the_yui_reason_apart_from_the_player_reason() -> None:
    """源码守卫：``_touch_player_action`` 必须区分**谁**在动账户。"""
    body = _method_body(_init_text(), "_touch_player_action")
    assert 'reason: str = "player"' in body
    assert '"yui"' in body
    assert "_yui_trade_cycle_unlock()" in body


def test_the_tick_pipeline_reopens_the_cycle() -> None:
    """源码守卫：**新的一拍 = 新的一回合**（自动模式下玩家可能一直不点）。"""
    body = _method_body(_init_text(), "_tick_once")
    assert "_yui_trade_cycle_unlock()" in body


# ═════════════════════════════════════════════════════ ③ 失败反馈层


def test_the_failure_receipt_does_not_trigger_a_new_turn(plugin: Any) -> None:
    """失败回执走 ``read``（**不触发** AI 回合）⇒ 断掉自触发循环。

    失败本来就在她当前那一回合里（是她在调工具），
    再用 ``respond`` 多触发一个回合 ⇒ 她会"再试一次" ⇒ 又一条失败回执 ⇒ 无限循环。
    """
    recorder = _attach_recorder(plugin)
    _start_live_session(plugin)

    run(plugin.llm_yui_trade(stock_id="根本没有这只票", action="buy", ratio=0.1))
    failures = recorder.matching("操作失败")
    assert len(failures) == 1, failures
    call = recorder.calls[-1]
    assert call.get("ai_behavior") == "read", call.get("ai_behavior")


def test_the_receipt_still_triggers_a_turn(plugin: Any) -> None:
    """成交回执**照旧**触发回合（她要开口汇报这一笔）。"""
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)

    run(plugin.llm_yui_trade(stock_id=str(stocks[0]["id"]), action="buy", ratio=0.05))
    receipts = recorder.matching("成交回执")
    assert len(receipts) == 1
    assert recorder.calls[-1].get("ai_behavior") == "respond"


def test_only_two_channels_use_respond() -> None:
    """全局守卫：``ai_behavior="respond"`` 仍然只有两条开口通道。"""
    import re

    init = _init_text()
    responds = re.findall(r'^\s+ai_behavior="respond",\s*$', init, re.M)
    assert len(responds) == 2, f"实际 {len(responds)} 处"


def test_the_read_channel_belongs_to_the_failure_receipt_alone() -> None:
    """``read``（只进上下文）这条通道**只许**失败回执用。

    别的通道改成 read = 猫娘该开口的时候不开口（直播冷场）；
    改成 respond = 多触发一个回合（自触发循环）。两头都是事故。
    """
    init = _init_text()
    assert 'ai_behavior="read",' in _method_body(init, "_push_proactive")
    assert 'ai_behavior="read",' in _method_body(init, "_push_yui_trade_failure")
    for name in ("_push_story_voice", "_push_card"):
        body = _method_body(init, name)
        assert 'ai_behavior="read"' not in body, name


# ═════════════════════════════════════════════════════ ④ live 日配额


def test_the_daily_quota_is_still_enforced(plugin: Any) -> None:
    """live 模式：**日配额**依然生效（超出即拒绝）。"""
    _start_live_session(plugin)
    yui = run(plugin.market_context())["yui"]
    total = int(yui["quota_total"])
    assert total >= 1
    assert int(yui["quota_left"]) == total

    for index in range(total):
        plugin._yui_trade_cycle_unlock()
        result = run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.01))
        assert result["is_error"] is False, (index, result)

    plugin._yui_trade_cycle_unlock()
    blocked = run(plugin.llm_yui_trade(stock_id="FAKE-001", action="buy", ratio=0.01))
    assert blocked["is_error"] is True
    assert "配额" in str(blocked.get("error") or "")
    assert run(plugin.market_context())["yui"]["quota_left"] == 0


def test_the_daily_quota_can_never_exceed_the_documented_upper_bound() -> None:
    """**上界断言**：任何路径都不可能突破用户口径的 60 单/天。

    真实上限比 60 严格得多（见下一条），但这条把"最多 60 次"钉死成
    一个**可执行的断言** —— 以后谁把配额调大，这里立刻红。
    """
    from plugins.mock_stock_meow import YUI_TRADES_PER_DAY

    assert 0 < int(YUI_TRADES_PER_DAY) <= 60


def test_the_real_daily_quota_is_strictly_tighter_than_sixty() -> None:
    """真实口径：live 模式每天**最多 3 单**（比 60 严格得多）。"""
    from plugins.mock_stock_meow import YUI_TRADES_PER_DAY

    assert int(YUI_TRADES_PER_DAY) == 3


# ═════════════════════════════════════════════════════ ⑤ 红线


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层**零改动**（干预只在插件层）。"""
    assert engine_mode_code_changes() == []


def test_the_prompt_layer_has_no_dead_words() -> None:
    """去死亡化文案守卫：提示词里不许出现那两个字。"""
    import re

    meow = _meow()
    text = "\n".join(
        [
            meow.YUI_AUTHORITY_PROMPT,
            meow.YUI_TRADE_FACTS,
            meow.YUI_RECEIPT_CYCLE_DIRECTIVE,
        ]
    )
    assert not re.search(r"[死亡墓遗]|天台", text)


def test_the_plugin_source_is_valid_python() -> None:
    """源码不许被改坏（AST 能解析）。"""
    ast.parse(_init_text())
    ast.parse(_MEOW.read_bytes().decode("utf-8"))
