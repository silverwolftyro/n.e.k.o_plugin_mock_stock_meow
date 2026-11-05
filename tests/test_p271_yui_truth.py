# -*- coding: utf-8 -*-
"""P27.1 守卫：**Yui 交易幻觉**的三层修复。

真机事故（模拟直播）::

    玩家说「深蓝科技」
    Yui 却说「已经全仓买了琥珀能源」
    随后又说「买成了不存在的股票」

根因不是她"记错了"，而是**她可以在没有成交结果的情况下自说自话** ——
提示词只给了她"有权动手"，没给"**只有工具说了算**"这条边界；
工具报错时，错误只在工具返回值里飘着，没人把「这一笔没成交」这件事
**讲给她听**。

三层修复（这一版钉死的就是这三层）
----------------------------------

1. **提示词层** —— :data:`YUI_TRADE_FACTS` 硬约束：没调用工具（或调用了却
   没拿到成交结果）就不许说自己买了/卖了什么；工具报错时照错误实说。
2. **失败反馈层** —— 每一个失败分支都把**那条错误原文**推给她
   （:meth:`_push_yui_trade_failure`），她只能照着说。
3. **实盘校验层** —— 成交之后把系统算出来的**真实结果**推给她
   （:meth:`_push_yui_trade_receipt`）：标的、数量、金额、余额都是真的。

贯穿全文件的红线：

* **引擎层 / 模式层零改动** —— 干预只在插件层（提示词 + 两条推送通道）；
* **回执里不许出现局中秘密** —— P4 §11 的扫描口径一样守；
* **回执不许替她演戏** —— P13 的表演指令一条都不许进回执；
* **失败回执绝不能长得像成交回执** —— 否则等于换了个姿势继续骗人。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INIT = _PLUGIN_ROOT / "__init__.py"
_MEOW = _PLUGIN_ROOT / "meow.py"
_SOUL_TEST = _PLUGIN_ROOT / "tests" / "test_p130_soul.py"


#: P4 §11「局中不泄露」的禁词（**含单字「雷」**）。
#: 与 ``test_p230_yui.py`` / ``test_p240_yui_live.py`` 用的是同一套口径。
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


def _literal(node: ast.AST) -> str:
    """把一个常量字符串节点求值成 ``str``（支持隐式拼接）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:  # pragma: no cover - 真出现带插值的描述就明确失败
                raise AssertionError("描述里出现了带插值的字符串")
        return "".join(parts)
    raise AssertionError(f"期望字符串常量，实际 {type(node).__name__}")


def _tool_description(name: str) -> str:
    """从源码里抽出某个 ``@llm_tool`` 的 ``description``。

    用 AST 而不是读宿主 SDK 的元数据 —— 这样**不依赖宿主版本**，
    而且改坏了描述（少一句、漏一句）这里立刻红。
    """
    tree = ast.parse(_INIT.read_bytes().decode("utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name != name:
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            label = getattr(decorator.func, "id", None) or getattr(
                decorator.func, "attr", None
            )
            if label != "llm_tool":
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "description":
                    return _literal(keyword.value)
    raise AssertionError(f"源码里找不到 {name} 的 llm_tool description")


def _p13_directives() -> tuple[str, ...]:
    """从 ``test_p130_soul.py`` 里**抽出来** P13 的表演指令清单。

    直接 AST 抽 ⇒ 那边一改，这里自动跟着变 ⇒ 不会各写一份然后漂移。
    """
    tree = ast.parse(_SOUL_TEST.read_bytes().decode("utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "P13_FORBIDDEN_DIRECTIVES":
                value = ast.literal_eval(node.value)
                return tuple(str(item) for item in value)
    raise AssertionError("test_p130_soul.py 里的 P13_FORBIDDEN_DIRECTIVES 不见了")


class _PushRecorder:
    """宿主 ``push_message`` 的假实现：把每次推送**原样记下来**。

    夹具里的 ``FakeCtx`` **没有** ``push_message`` ⇒ 插件层会走
    "宿主未提供 push_message" 的兜底分支 ⇒ 推送根本不会发生。
    所以这里直接往实例上挂一个，把插件推给 Yui 的**原文**抓出来。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        return None

    def texts(self) -> list[str]:
        """所有推送里的正文（按推送顺序）。"""
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


# ═════════════════════════════════════════════════════ ① 提示词层


def test_the_authority_prompt_forbids_trading_without_the_tool() -> None:
    """授权提示词里必须有「**没工具就没成交**」这条硬约束。"""
    meow = _meow()
    prompt = meow.YUI_AUTHORITY_PROMPT

    assert "绝对不允许" in prompt
    assert "mock_stock_meow_yui_trade" in prompt
    assert "严禁" in prompt
    assert "编造" in prompt
    # 授权口径本身不许被这次补丁改坏。
    assert "{MASTER_NAME}" in prompt


def test_the_authority_prompt_embeds_the_shared_trade_facts() -> None:
    """授权提示词**内嵌**那一份常量（不是抄了一遍 ⇒ 口径不分叉）。"""
    meow = _meow()
    assert meow.YUI_TRADE_FACTS in meow.YUI_AUTHORITY_PROMPT


def test_the_trade_facts_do_not_leak_the_in_game_secrets() -> None:
    """交易约束里不许出现局中秘密（P4 §11 同一套口径）。"""
    meow = _meow()
    text = meow.YUI_TRADE_FACTS.lower()
    for banned in _BANNED_IN_GAME:
        assert banned.lower() not in text, banned


def test_the_live_prompt_carries_the_same_trade_facts(plugin: Any) -> None:
    """直播托管的主动回合**也**必须带同一份约束。

    口径分叉 = 幻觉从另一个门进来 ⇒ 这里断言的是**同一份常量**。
    """
    meow = _meow()
    run(plugin.join(mode="classic_arcade", seed=7309))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None

    prompt = plugin._yui_live_prompt(session)
    assert meow.YUI_TRADE_FACTS in prompt
    lowered = prompt.lower()
    for banned in _BANNED_IN_GAME:
        assert banned.lower() not in lowered, banned


def test_the_tool_description_says_only_ok_true_means_a_fill() -> None:
    """工具描述里写死「只有 ok=true 才算成交」。"""
    description = _tool_description("llm_yui_trade")
    assert "只有本工具返回 ok=true 才算成交" in description
    assert "严禁编造" in description


# ═════════════════════════════════════════════════════ ② 成交回执


def test_a_filled_trade_pushes_the_real_receipt(plugin: Any) -> None:
    """成交 ⇒ 把**系统算出来的真实结果**推给她（实盘校验）。"""
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)
    target = stocks[0]

    result = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="buy", ratio=0.1)
    )
    assert result.get("is_error") is False
    output = result["output"]
    assert output["ok"] is True
    cash_after = float(output["cash_after"])

    receipts = recorder.matching("成交回执")
    assert len(receipts) == 1, f"应当恰好推一张成交回执，实际 {len(receipts)} 张"
    text = receipts[0]

    # 三段事实：买了多少股哪只票、花了多少、现在剩多少。
    assert "你刚才买入了" in text
    assert str(target["name"]) in text
    assert "花费" in text
    assert "现在账户剩" in text
    assert f"{cash_after:.2f}" in text
    # 余额必须与面板上的一致（回执不许自说自话）。
    assert abs(float(run(plugin.market_context())["cash"]) - cash_after) < 0.01


def test_the_tool_result_also_carries_the_receipt(plugin: Any) -> None:
    """回执**同时**放进工具返回值 —— 那一轮她就能看见事实。"""
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)
    target = stocks[0]

    result = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="buy", ratio=0.1)
    )
    output = result["output"]
    receipt = str(output.get("receipt") or "")
    assert receipt, "工具返回值里必须带回执"
    assert str(target["name"]) in receipt
    assert f"{float(output['cash_after']):.2f}" in receipt
    assert recorder.matching("成交回执")


def test_a_sell_receipt_says_sold_and_collects_the_money(plugin: Any) -> None:
    """卖出 ⇒ 回执说「卖出了 / 回款」，不许说成买入。

    P27.2：买入成交之后 Yui 进入**回执周期**（这一回合只许汇报），
    所以这一笔卖出属于「玩家又开口了」之后的新回合 —— 先把锁解开。
    """
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)
    target = stocks[0]

    run(plugin.llm_yui_trade(stock_id=str(target["id"]), action="buy", ratio=0.5))
    recorder.calls.clear()
    # 新的一回合（玩家又开口了）⇒ 回执周期锁解开。
    plugin._yui_trade_cycle_unlock()

    result = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="sell", ratio=1.0)
    )
    assert result.get("is_error") is False
    receipts = recorder.matching("成交回执")
    assert len(receipts) == 1
    text = receipts[0]
    assert "你刚才卖出了" in text
    assert "回款" in text
    assert "你刚才买入了" not in text


# ═════════════════════════════════════════════════════ ③ 失败反馈


def test_a_failed_trade_pushes_the_error_instead_of_a_fake_fill(plugin: Any) -> None:
    """找不到标的 ⇒ 如实说没成交，**绝不能**编一只票出来。"""
    recorder = _attach_recorder(plugin)
    _start_live_session(plugin)

    result = run(
        plugin.llm_yui_trade(stock_id="不存在的标的", action="buy", ratio=0.1)
    )
    assert result.get("is_error") is True
    reason = str(result.get("error") or "")

    failures = recorder.matching("操作失败")
    assert len(failures) == 1, f"应当恰好推一条失败回执，实际 {len(failures)} 条"
    text = failures[0]
    assert "没有成交" in text
    assert reason and reason in text
    # 失败回执**不许**长得像成交回执。
    assert "成交回执" not in text
    for forbidden in ("你刚才买入了", "你刚才卖出了", "现在账户剩"):
        assert forbidden not in text, forbidden
    # 但必须把「不许编」这条禁令明说给她。
    assert "严禁" in text
    assert "编造" in text


def test_the_failure_receipt_reports_the_real_cash(plugin: Any) -> None:
    """有对局时，失败回执要报**真实**的现金数（不许报一个假数）。"""
    recorder = _attach_recorder(plugin)
    _start_live_session(plugin)
    cash = float(run(plugin.market_context())["cash"])

    result = run(
        plugin.llm_yui_trade(stock_id=str(run(plugin.market_context())["stocks"][0]["id"]),
                             action="buy",
                             ratio=0.0)
    )
    assert result.get("is_error") is True
    text = recorder.matching("操作失败")[0]
    assert "账户现状" in text
    assert f"{cash:.2f}" in text
    # 比例非法 ⇒ 账户不该有任何变化。
    assert abs(float(run(plugin.market_context())["cash"]) - cash) < 0.01


def test_selling_without_a_position_reports_the_failure(plugin: Any) -> None:
    """空仓卖出 ⇒ 失败回执，不是"卖了个寂寞"。"""
    recorder = _attach_recorder(plugin)
    stocks = _start_live_session(plugin)
    target = stocks[0]

    result = run(
        plugin.llm_yui_trade(stock_id=str(target["id"]), action="sell", ratio=1.0)
    )
    assert result.get("is_error") is True
    text = recorder.matching("操作失败")[0]
    assert "没有成交" in text
    assert str(result.get("error") or "") in text


def test_every_failure_path_pushes_the_error(plugin: Any) -> None:
    """**每一个**失败分支都要推错误 —— 漏一个就是留一个编造的口子。"""
    recorder = _attach_recorder(plugin)
    _start_live_session(plugin)
    stock_id = str(run(plugin.market_context())["stocks"][0]["id"])

    cases: list[dict[str, Any]] = [
        {"stock_id": "不存在的标的", "action": "buy", "ratio": 0.1},
        {"stock_id": stock_id, "action": "buy", "ratio": 0.0},
        {"stock_id": stock_id, "action": "sell", "ratio": 1.0},
        {"stock_id": stock_id, "action": "飞线", "ratio": 0.1},
    ]
    for index, kwargs in enumerate(cases):
        before = len(recorder.matching("操作失败"))
        result = run(plugin.llm_yui_trade(**kwargs))
        assert result.get("is_error") is True, kwargs
        after = len(recorder.matching("操作失败"))
        assert after == before + 1, f"第 {index} 个失败分支没有推错误回执：{kwargs}"


def test_the_tool_result_is_still_an_error_after_the_push(plugin: Any) -> None:
    """推了失败回执**不等于**把错误吞了 —— 工具返回值照样是错误。"""
    recorder = _attach_recorder(plugin)
    _start_live_session(plugin)

    result = run(
        plugin.llm_yui_trade(stock_id="不存在的标的", action="buy", ratio=0.1)
    )
    assert result["output"]["ok"] is False
    assert result["is_error"] is True
    assert str(result["error"])
    assert recorder.matching("操作失败")


# ═════════════════════════════════════════════════════ ④ 文案守卫


def test_the_receipt_prompts_do_not_order_the_catgirl_to_pretend() -> None:
    """回执只讲事实，**不许**替她演戏（P13 契约精神）。"""
    meow = _meow()
    receipt = meow.yui_trade_receipt_prompt(
        action="buy",
        stock="某某科技",
        amount=12.5,
        price=3.25,
        money=40.625,
        cash_after=959.375,
    )
    failure = meow.yui_trade_failure_prompt(
        action="buy",
        stock="某某科技",
        reason="钱不够",
        cash=100.0,
    )
    for banned in _p13_directives():
        assert banned not in receipt, banned
        assert banned not in failure, banned


def test_the_receipt_prompts_do_not_leak_the_in_game_secrets() -> None:
    """回执里也不许出现局中秘密（P4 §11）。"""
    meow = _meow()
    receipt = meow.yui_trade_receipt_prompt(
        action="buy",
        stock="某某科技",
        amount=12.5,
        price=3.25,
        money=40.625,
        cash_after=959.375,
    )
    failure = meow.yui_trade_failure_prompt(
        action="sell",
        stock="某某科技",
        reason="没有可卖的持仓",
        cash=100.0,
    )
    for text in (receipt, failure):
        lowered = text.lower()
        for banned in _BANNED_IN_GAME:
            assert banned.lower() not in lowered, banned


def test_the_receipt_prompt_forbids_a_second_order() -> None:
    """回执末尾必须写明「这一轮不要再下单」⇒ 不会自触发循环。"""
    meow = _meow()
    receipt = meow.yui_trade_receipt_prompt(
        action="buy",
        stock="某某科技",
        amount=1.0,
        price=1.0,
        money=1.0,
        cash_after=1.0,
    )
    failure = meow.yui_trade_failure_prompt(
        action="buy", stock="某某科技", reason="钱不够"
    )
    assert "这一轮不要再下单" in receipt
    assert "这一轮不要再下单" in failure


def test_the_failure_prompt_marks_the_trade_as_not_filled() -> None:
    """失败回执必须写死「没有成交」+ 原始原因。"""
    meow = _meow()
    failure = meow.yui_trade_failure_prompt(
        action="sell", stock="某某科技", reason="没有可卖的持仓"
    )
    assert "没有成交" in failure
    assert "没有可卖的持仓" in failure
    assert "卖出" in failure
    assert "买入" not in failure


# ═════════════════════════════════════════════════════ ⑤ 红线


def test_the_release_version_is_pinned() -> None:
    """版本号钉死在 ``0.13.6-live-ultimate``（直播最终版）。"""
    root = Path(__file__).resolve().parents[1]
    toml = (root / "plugin.toml").read_bytes().decode("utf-8")
    init = (root / "__init__.py").read_bytes().decode("utf-8")
    assert 'version = "0.13.6-live-ultimate"' in toml
    assert 'PLUGIN_VERSION = "0.13.6-live-ultimate"' in init


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层**零改动**（500 局基线的地基）。"""
    assert engine_mode_code_changes() == []
