# -*- coding: utf-8 -*-
"""P24.0 守卫：**Yui 直播托管主动化** + **话痨频率** + **事件联动** + **模糊匹配**。

真机复盘出来的三个病：

* 切到「直播托管」后，Yui **嘴上说**随便操作，实际**一笔都没下**；
* 新闻弹出来她**一声不吭**；
* 她说「流萤物流1号」，工具只认 ``FAKE-001`` ⇒ 一句「找不到标的」，直播冷场。

所以这一版钉死的是**四条链路**：

1. **主动回合** —— ``live`` 模式下每隔 N 个 tick 真的攒一条提示词，
   提示词里带现金 / 持仓 / 当天行情，并**明确**要求她用 ``yui_trade`` 下单。
   三道限流缺一不可：tick 间隔 / 日配额 / 墙钟闸门。
2. **话痨级别** —— 三档（安静 / 正常 / 话痨），它就是那个 N 的来源。
3. **事件联动** —— 新闻 / 收割 / 爆仓 ⇒ 打断她，让她立刻说话；
   **优先级高于**常规主动回合。
4. **模糊匹配** —— ``yui_trade`` 的 ``stock_id`` 四层查找（精确 → 子串），
   找不到也要**温和**。

两条**红线**贯穿全文件：

* **引擎层 / 模式层零改动**；
* **提示词不许带局中秘密**（heat / seed / 反转日 / **新闻真假与雷**）——
  连"只对雷新闻和假新闻说话"都不行：玩家能从"她理我了没"**反推**新闻类型。
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


# ---------------------------------------------------------------- 工具


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _method_body(text: str, name: str) -> str:
    """取某个方法的源码正文（到下一个同缩进的方法定义为止）。"""
    marker = f"def {name}("
    index = text.find(marker)
    assert index != -1, f"找不到方法 {name}"
    rest = text[index:]
    stops = [pos for pos in (rest.find("\n    def ", 1), rest.find("\n    async def ", 1)) if pos != -1]
    stop = min(stops) if stops else -1
    return rest if stop == -1 else rest[:stop]


def _strip_py_comments(source: str) -> str:
    """去掉 Python 的 ``#`` 注释（**注释也参与红线扫描**，别被它带偏）。"""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - 源码坏了
        return source
    killed = {token.start[0] for token in tokens if token.type == tokenize.COMMENT}
    return "\n".join(
        line for number, line in enumerate(source.splitlines(), start=1) if number not in killed
    )


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
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source, "面板里找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _plugin_constants() -> dict[str, Any]:
    """从插件包里取本轮新增的常量（不重复硬编码）。"""
    import importlib

    plugin_module = importlib.import_module("plugins.mock_stock_meow")
    return {
        "QUIET": plugin_module.YUI_TALK_QUIET,
        "NORMAL": plugin_module.YUI_TALK_NORMAL,
        "CHATTY": plugin_module.YUI_TALK_CHATTY,
        "LEVELS": tuple(plugin_module.YUI_TALK_LEVELS),
        "DEFAULT": plugin_module.YUI_TALK_LEVEL_DEFAULT,
        "INTERVALS": dict(plugin_module.YUI_TALK_TICK_INTERVALS),
        "MIN_GAP": float(plugin_module.YUI_TALK_MIN_GAP_SECONDS),
    }


def _session(plugin: Any) -> Any:
    """拿一份**真**会话（跟引擎用的是同一份模型）。"""
    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    return run(load_session(plugin._repo, plugin.state, engine_config=default_engine_config()))


def _pending(plugin: Any) -> list[tuple[str, str, int]]:
    return list(plugin._yui_pending_prompts)


def _reasons(plugin: Any) -> list[str]:
    return [str(item[1]) for item in _pending(plugin)]


# ══════════════════════════════════════════════════════════════ ① 话痨级别


def test_the_three_talk_levels_map_to_tick_intervals() -> None:
    """三档话痨级别 ⇒ 三个不同的「每隔几个 tick」。

    安静档的间隔是 ``0`` —— 那就是「**不主动开**」，
    只在出大事（新闻 / 收割 / 爆仓）时才说话。
    """
    consts = _plugin_constants()
    assert consts["LEVELS"] == ("quiet", "normal", "chatty")
    assert consts["DEFAULT"] == "normal", "默认必须是「正常」"
    assert consts["INTERVALS"]["quiet"] == 0, "安静档不许主动开回合"
    assert consts["INTERVALS"]["normal"] == 3, "正常档是每 3 个 tick 一次"
    assert consts["INTERVALS"]["chatty"] == 1, "话痨档是每个 tick 一次"
    # 三档必须**互不相同**（否则「可调」就是假的）。
    assert len(set(consts["INTERVALS"].values())) == 3


def test_the_default_talk_level_is_normal(plugin) -> None:
    """装上插件的第一秒 ⇒ 话痨级别是「正常」（安全默认值）。"""
    consts = _plugin_constants()
    assert plugin._yui_talk_level_now() == consts["DEFAULT"]
    assert plugin._yui_talk_interval() == consts["INTERVALS"][consts["DEFAULT"]]
    payload = run(plugin.market_context())
    assert payload["yui"]["talk_level"] == consts["DEFAULT"]
    assert payload["yui"]["talk_interval"] == consts["INTERVALS"][consts["DEFAULT"]]


def test_setting_the_talk_level_persists_it(plugin) -> None:
    """设置话痨级别 ⇒ **落库** + 缓存 + 载荷三处一致。"""
    consts = _plugin_constants()
    result = run(plugin.yui_talk_level(level="chatty"))
    assert result.is_ok(), result
    payload = result.unwrap()
    assert payload["level"] == consts["CHATTY"]
    assert payload["interval"] == consts["INTERVALS"][consts["CHATTY"]]

    # 内存缓存
    assert plugin._yui_talk_level_now() == consts["CHATTY"]
    assert plugin._yui_talk_interval() == 1

    # 落库
    row = run(plugin._repo.fetch_one("SELECT value FROM settings WHERE key = ?", ("yui_talk_level",)))
    assert row is not None
    assert str(row["value"]) == consts["CHATTY"]

    # 面板载荷
    assert run(plugin.market_context())["yui"]["talk_level"] == consts["CHATTY"]


def test_an_unknown_talk_level_is_rejected(plugin) -> None:
    """非法档位 ⇒ 拒绝，且**不改变**当前设置。"""
    before = plugin._yui_talk_level_now()
    result = run(plugin.yui_talk_level(level="super_chatty"))
    assert result.is_err()
    assert plugin._yui_talk_level_now() == before


def test_the_talk_level_survives_a_restart(plugin) -> None:
    """话痨级别是**设置项**：重启后还在（不能每次开服都回到默认）。"""
    run(plugin.yui_talk_level(level="quiet"))
    # 模拟重启：清掉内存缓存，重新读一次设置。
    plugin._settings_map_cache = None
    plugin._yui_talk_level = ""
    run(plugin._ensure_settings_cache())
    assert plugin._yui_talk_level_now() == "quiet"


# ══════════════════════════════════════════════════════════════ ② 主动回合


def test_live_mode_collects_a_turn_on_the_right_tick(plugin) -> None:
    """``live`` 模式：每隔 N 个 tick 攒一条「该你动了」的提示词。"""
    run(plugin.join(mode="classic_arcade", seed=7401))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)

    session.market.tick = 1
    plugin._yui_pending_prompts = []
    plugin._collect_yui_live_prompt(session)
    assert _pending(plugin) == [], "第 1 拍不该开回合（间隔是 3）"

    session.market.tick = 3
    plugin._collect_yui_live_prompt(session)
    assert len(_pending(plugin)) == 1, "第 3 拍必须攒一条"
    prompt, reason, priority = _pending(plugin)[0]
    assert reason == "yui_live_turn"
    assert priority == 3


def test_only_live_mode_collects_proactive_turns(plugin) -> None:
    """非 ``live`` 模式**绝不**主动开回合（辅助式 / 纯看戏都算）。"""
    run(plugin.join(mode="classic_arcade", seed=7402))
    session = _session(plugin)
    session.market.tick = 3
    for mode in ("assist", "spectate"):
        run(plugin.yui_mode(mode=mode))
        plugin._yui_pending_prompts = []
        plugin._collect_yui_live_prompt(session)
        assert _pending(plugin) == [], f"{mode} 模式不该主动开回合"


def test_quiet_mode_never_collects_a_proactive_turn(plugin) -> None:
    """「安静」档 ⇒ 不主动开回合（只剩重大事件那条路）。"""
    run(plugin.join(mode="classic_arcade", seed=7403))
    run(plugin.yui_mode(mode="live"))
    run(plugin.yui_talk_level(level="quiet"))
    session = _session(plugin)
    session.market.tick = 12
    plugin._yui_pending_prompts = []
    plugin._collect_yui_live_prompt(session)
    assert _pending(plugin) == []


def test_the_turn_is_rate_limited_by_the_wall_clock(plugin) -> None:
    """墙钟闸门：两次主动回合之间至少隔 ``YUI_TALK_MIN_GAP_SECONDS``。

    没有这道闸，``skip_day`` 之类的快进会一口气跑十几个 tick
    ⇒ 「每 3 拍开一次」瞬间变成「开五次」，直播直接刷屏。
    """
    consts = _plugin_constants()
    run(plugin.join(mode="classic_arcade", seed=7404))
    run(plugin.yui_mode(mode="live"))
    run(plugin.yui_talk_level(level="chatty"))
    session = _session(plugin)

    session.market.tick = 1
    plugin._yui_pending_prompts = []
    plugin._collect_yui_live_prompt(session)
    assert len(_pending(plugin)) == 1

    # 紧接着下一拍：被墙钟闸门挡住。
    session.market.tick = 2
    plugin._collect_yui_live_prompt(session)
    assert len(_pending(plugin)) == 1, "10 秒内不许再开第二个回合"

    # 把"上次"推到过去 ⇒ 闸门放行。
    plugin._yui_talk_last_at = plugin._yui_talk_last_at - consts["MIN_GAP"] - 1.0
    session.market.tick = 3
    plugin._collect_yui_live_prompt(session)
    assert len(_pending(plugin)) == 2


def test_the_turn_stops_once_the_daily_quota_is_gone(plugin) -> None:
    """日配额用完 ⇒ 不再开回合（她连单都下不了，开回合只是白刷屏）。"""
    import importlib

    plugin_module = importlib.import_module("plugins.mock_stock_meow")

    run(plugin.join(mode="classic_arcade", seed=7405))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    # 把今天的配额"用光"。
    plugin._yui_trade_day = int(session.player.day)
    plugin._yui_trade_count = int(plugin_module.YUI_TRADES_PER_DAY)
    assert plugin._yui_quota_left(int(session.player.day)) == 0

    session.market.tick = 3
    plugin._yui_pending_prompts = []
    plugin._collect_yui_live_prompt(session)
    assert _pending(plugin) == [], "配额用完就不该再开回合"


def test_the_live_prompt_carries_cash_positions_and_the_movers(plugin) -> None:
    """提示词必须带**现金 / 持仓 / 当天行情**，并给标名编号。

    真机上的「找不到标的」就是这么来的 —— 她只拿到名字，
    工具却只认 ``FAKE-003``。所以这里**连编号一起给**。
    """
    run(plugin.join(mode="classic_arcade", seed=7406))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    prompt = plugin._yui_live_prompt(session)

    assert "现钱" in prompt, "提示词里没有现金"
    assert f"{float(session.player.cash):.2f}" in prompt, "提示词里没有现金数字"
    assert "总资产" in prompt, "提示词里没有总资产"
    assert "持仓" in prompt, "提示词里没有持仓"

    # 行情：至少一只票的 **编号**必须在（格式：名称（FAKE-003））。
    stock = next(iter(session.market.stocks.values()))
    assert f"（{stock.id}）" in prompt, "行情里必须带标的编号"
    assert str(stock.name) in prompt, "行情里必须带标的名称"

    # 明确要求她**真的下单**。
    assert "mock_stock_meow_yui_trade" in prompt, "提示词里没有交易工具"
    assert "要么" in prompt, "提示词没有给出「下单或给判断」的选择"


def test_the_live_prompt_never_leaks_engine_secrets(plugin) -> None:
    """提示词里**不许**出现局中秘密（P4 §11）。"""
    run(plugin.join(mode="classic_arcade", seed=7407))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    prompt = plugin._yui_live_prompt(session)
    lowered = prompt.lower()
    for banned in ("heat", "seed", "reversal", "反转", "accuracy", "prey_index"):
        assert banned not in lowered, f"提示词里泄露了 {banned}"
    for banned in ("真新闻", "假新闻", "雷", "真伪", "accuracy"):
        assert banned not in prompt, f"提示词里泄露了 {banned}"


def test_the_prompt_never_names_the_news_type(plugin) -> None:
    """新闻提示词里**连类型都不许读**（结构上就不可能泄露）。"""
    body = _method_body(_init_text(), "_yui_news_prompt")
    assert ".type" not in body, "新闻提示词不许读新闻类型"
    for banned in ("thunder", "fake", "true", "vague", "noise"):
        assert banned not in body.lower(), f"新闻提示词里出现了 {banned}"


# ══════════════════════════════════════════════════════════════ ③ 事件联动


def test_a_new_news_item_is_pushed_to_yui(plugin) -> None:
    """新闻出来了 ⇒ 立刻攒一条插话（**每一条**都推）。"""
    from engine.models import NewsItem

    run(plugin.join(mode="classic_arcade", seed=7408))
    session = _session(plugin)
    stock_id = next(iter(session.market.stocks))
    session.market.news_today = [
        NewsItem(
            id="news-p240-1",
            day=session.market.day,
            type="thunder",
            stock_id=stock_id,
            content="主力资金全线扫货",
            accuracy=None,
            direction="bullish",
        )
    ]
    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)

    assert len(_pending(plugin)) == 1
    prompt, reason, priority = _pending(plugin)[0]
    assert reason == "yui_news"
    assert priority >= 5, "新闻插话必须比常规回合优先"
    assert "主力资金全线扫货" in prompt, "提示词里没有新闻正文"


def test_the_same_news_is_not_pushed_twice(plugin) -> None:
    """同一条新闻**只**推一次（否则每拍都刷一遍）。"""
    from engine.models import NewsItem

    run(plugin.join(mode="classic_arcade", seed=7409))
    session = _session(plugin)
    stock_id = next(iter(session.market.stocks))
    session.market.news_today = [
        NewsItem(
            id="news-p240-2",
            day=session.market.day,
            type="true",
            stock_id=stock_id,
            content="老王宣布亲自下场",
            accuracy=None,
            direction="bullish",
        )
    ]
    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)
    assert len(_pending(plugin)) == 1

    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)
    assert _pending(plugin) == [], "同一条新闻不许推第二次"


def test_a_liquidation_interrupts_yui(plugin) -> None:
    """被收割（斩杀线）⇒ 强制打断，她必须发表感言。"""
    run(plugin.join(mode="classic_arcade", seed=7410))
    session = _session(plugin)
    # 只留**这一件事**（当天自带的那几条新闻另有其测试）。
    session.market.news_today = []
    stock_id = next(iter(session.market.stocks))
    outcome = SimpleNamespace(
        liquidations=[
            SimpleNamespace(stock_id=stock_id, concentration=0.85, trigger="concentration")
        ]
    )
    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, outcome, True)

    assert len(_pending(plugin)) == 1
    prompt, reason, priority = _pending(plugin)[0]
    assert reason == "yui_harvest"
    assert priority >= 8, "收割必须插到常规回合前面"
    assert "85%" in prompt, "提示词里没有集中度"


def test_a_bankruptcy_interrupts_yui(plugin) -> None:
    """爆仓 ⇒ 这一局结束了，她必须说点什么（优先级最高）。"""
    run(plugin.join(mode="classic_arcade", seed=7411))
    session = _session(plugin)
    session.market.news_today = []
    session.player.alive = False
    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), True)

    assert len(_pending(plugin)) == 1
    _prompt, reason, priority = _pending(plugin)[0]
    assert reason == "yui_bankrupt"
    assert priority >= 9, "爆仓的优先级必须最高"


def test_a_player_who_was_already_dead_is_not_announced_again(plugin) -> None:
    """本来就死着的玩家不该被"又爆仓一次"（``was_alive`` 是判据）。"""
    run(plugin.join(mode="classic_arcade", seed=7412))
    session = _session(plugin)
    session.market.news_today = []
    session.player.alive = False
    plugin._yui_pending_prompts = []
    plugin._collect_yui_event_prompts(session, SimpleNamespace(liquidations=[]), False)
    assert _reasons(plugin) == []


def test_major_events_outrank_the_regular_turn(plugin) -> None:
    """事件优先级**高于**常规主动回合 —— 重大事件永远先说。"""
    run(plugin.join(mode="classic_arcade", seed=7413))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    session.market.news_today = []
    session.market.tick = 3
    plugin._yui_pending_prompts = []
    plugin._collect_yui_live_prompt(session)
    assert _reasons(plugin) == ["yui_live_turn"]

    outcome = SimpleNamespace(
        liquidations=[
            SimpleNamespace(stock_id=next(iter(session.market.stocks)), concentration=0.9, trigger="t")
        ]
    )
    plugin._collect_yui_event_prompts(session, outcome, True)
    reasons = _reasons(plugin)
    assert reasons[-1] == "yui_harvest", "事件必须排在常规回合**之后**（先攒的先推）"
    priorities = [item[2] for item in _pending(plugin)]
    assert priorities[-1] > priorities[0], "事件的优先级必须更高"


# ══════════════════════════════════════════════════════════════ ④ 模糊匹配


def _first_stock_name(plugin: Any) -> tuple[str, str]:
    session = _session(plugin)
    stock = next(iter(session.market.stocks.values()))
    return str(stock.id), str(stock.name)


def test_yui_can_trade_by_exact_id(plugin) -> None:
    """精确 id 当然要认（这是老行为，不许被改坏）。"""
    run(plugin.join(mode="classic_arcade", seed=7414))
    run(plugin.yui_mode(mode="live"))
    stock_id, _name = _first_stock_name(plugin)
    result = run(plugin.llm_yui_trade(stock_id=stock_id, action="buy", ratio=0.1))
    assert result["is_error"] is False, result
    assert result["output"]["ok"] is True


def test_yui_can_trade_by_exact_name(plugin) -> None:
    """精确**名字**也要认 —— 她说的往往不是编号。"""
    run(plugin.join(mode="classic_arcade", seed=7415))
    run(plugin.yui_mode(mode="live"))
    _stock_id, name = _first_stock_name(plugin)
    result = run(plugin.llm_yui_trade(stock_id=name, action="buy", ratio=0.1))
    assert result["is_error"] is False, result
    assert result["output"]["ok"] is True


def test_yui_can_trade_by_a_partial_name(plugin) -> None:
    """**子串**匹配 —— 真机事故的正解。

    她说「流萤物流」/「流萤物流1号」，工具只认 ``FAKE-001`` ⇒ 冷场。
    现在按子串找得到（群友名册里有「老王」「小明」这类两三个字的名字）。
    """
    run(plugin.join(mode="classic_arcade", seed=7416))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    target = next(
        stock for stock in session.market.stocks.values() if len(str(stock.name)) >= 2
    )
    fragment = str(target.name)[:1]
    assert fragment, "样本标的名字太短"

    resolved = plugin._resolve_yui_stock(fragment, session.market)
    assert resolved is not None, f"子串 {fragment!r} 找不到标的"
    assert str(resolved.id) == str(target.id)

    result = run(plugin.llm_yui_trade(stock_id=fragment, action="buy", ratio=0.1))
    assert result["is_error"] is False, result
    assert result["output"]["ok"] is True


def test_an_unknown_stock_gets_a_gentle_answer(plugin) -> None:
    """实在找不到 ⇒ **温和**地说，别甩一句冷冰冰的"找不到标的"。"""
    run(plugin.join(mode="classic_arcade", seed=7417))
    run(plugin.yui_mode(mode="live"))
    result = run(plugin.llm_yui_trade(stock_id="根本没有这只票", action="buy", ratio=0.1))
    assert result["is_error"] is True
    message = str(result["error"] or "")
    assert message.strip(), "温和失败也要有话"
    assert "没找到你说的这只" in message, message
    # 一笔都不许成交。
    rows = run(plugin._repo.fetch_all("SELECT reason FROM trades"))
    assert rows == []


def test_a_fuzzy_trade_really_costs_cash(plugin) -> None:
    """模糊匹配下的成交**真的扣款**（真机验收点：切托管后 Yui 下单并扣钱）。"""
    run(plugin.join(mode="classic_arcade", seed=7418))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    target = next(
        stock for stock in session.market.stocks.values() if len(str(stock.name)) >= 2
    )
    fragment = str(target.name)[:1]

    before = run(plugin.market_context())
    result = run(plugin.llm_yui_trade(stock_id=fragment, action="buy", ratio=0.2))
    assert result["is_error"] is False, result
    after = run(plugin.market_context())

    assert float(after["cash"]) < float(before["cash"]), "现金必须真的少"
    positions = {p["stock_id"]: p for p in after["positions"]}
    assert str(target.id) in positions, "持仓必须真的加上"
    assert float(positions[str(target.id)]["amount"]) > 0


def test_the_resolver_prefers_a_living_stock(plugin) -> None:
    """多候选时 ⇒ 先要**还活着**的那只。"""
    run(plugin.join(mode="classic_arcade", seed=7419))
    session = _session(plugin)
    stocks = list(session.market.stocks.values())
    assert len(stocks) >= 2
    # 造一个"同名但已退市"的假货：把它塞进市场字典（同一个名字）。
    dead = SimpleNamespace(
        id="FAKE-DEAD",
        name=str(stocks[0].name),
        alive=False,
        price=1.0,
        prev_price=1.0,
    )
    session.market.stocks["FAKE-DEAD"] = dead
    try:
        resolved = plugin._resolve_yui_stock(str(stocks[0].name), session.market)
        assert resolved is not None
        assert str(resolved.id) != "FAKE-DEAD", "已退市的票不许被选中"
    finally:
        session.market.stocks.pop("FAKE-DEAD", None)


# ══════════════════════════════════════════════════════════════ ⑤ Prompt


def test_the_authority_prompt_no_longer_repeats_the_permission() -> None:
    """授权宣告不许再复读一遍「系统把账户交给本喵了」。

    真机截图里每次切模式都要念一遍这段 —— 又长又没信息量。
    现在直接说**准备怎么做**。
    """
    from plugins.mock_stock_meow import meow

    prompt = meow.YUI_AUTHORITY_PROMPT
    for banned in (
        "系统真的把账户交给本喵了",
        "系统把账户交给本喵了",
        "交给你了",
    ):
        assert banned not in prompt, f"授权宣告还在复读：{banned}"
    # 换成「你打算怎么做」的口气。
    assert "准备" in prompt or "打算" in prompt, "授权宣告没说要准备怎么做"
    assert "mock_stock_meow_yui_trade" in prompt, "授权宣告里没有交易工具"
    # 而且**明令**她别再复述授权 —— 这正是这一版的核心诉求。
    assert "别复述授权" in prompt, "授权宣告没有禁止复读机式宣告"


def test_the_authority_prompt_still_has_no_engine_secrets() -> None:
    """授权宣告里**不许**出现局中秘密。"""
    from plugins.mock_stock_meow import meow

    lowered = meow.YUI_AUTHORITY_PROMPT.lower()
    for banned in ("heat", "seed", "reversal", "反转", "accuracy", "prey_index"):
        assert banned not in lowered, banned


def test_the_authority_prompt_is_not_empty() -> None:
    """（防止有人把整段清空当作"优化"。）"""
    from plugins.mock_stock_meow import meow

    assert len(meow.YUI_AUTHORITY_PROMPT.strip()) > 40


#: P13 契约：提示词写**事实与权限**，不是台词。
#: 这些词一个都不许出现在给 Yui 的提示词里。
P13_FORBIDDEN_DIRECTIVES = (
    "用你自己的语气",
    "请立刻主动开口",
    "嘲讽",
    "假装",
    "冷漠",
    "温柔",
    "口吻",
    "腔调",
    "演绎",
    "表演",
    "台词",
    "务必",
    "一定要",
    "提醒玩家",
    "顺便提",
)


def test_the_new_prompts_carry_no_performance_directive(plugin) -> None:
    """P13 契约：本轮新增的三条提示词都**不许**带表演指令。"""
    from engine.models import NewsItem
    from plugins.mock_stock_meow import meow

    run(plugin.join(mode="classic_arcade", seed=7421))
    run(plugin.yui_mode(mode="live"))
    session = _session(plugin)
    item = NewsItem(
        id="news-p240-3",
        day=session.market.day,
        type="true",
        stock_id=next(iter(session.market.stocks)),
        content="测试用的一条消息",
        accuracy=None,
        direction="bullish",
    )
    prompts = {
        "live": plugin._yui_live_prompt(session),
        "news": plugin._yui_news_prompt(session, item),
        "event": plugin._yui_event_prompt("测试标题", "测试细节"),
        "authority": meow.YUI_AUTHORITY_PROMPT,
    }
    for label, prompt in prompts.items():
        for token in P13_FORBIDDEN_DIRECTIVES:
            assert token not in prompt, f"{label} 的提示词里出现了表演指令「{token}」：\n{prompt}"


def test_the_new_prompts_do_not_appear_in_the_old_directive_blacklist() -> None:
    """源码级：**``__init__.py``** 里不许再出现 P12 那批写死的句子。"""
    removed = (
        "请立刻主动开口",
        "用你自己的语气",
        "别迎合他",
        "别把他骂到自闭",
        "把选择的代价说清楚",
        "顺便提一句",
        "可以只投一部分",
        "别一上来就梭哈",
    )
    text = _init_text()
    for phrase in removed:
        assert phrase not in text, f"__init__.py 里还留着 P12 的表演指令：{phrase}"


# ══════════════════════════════════════════════════════════════ ⑥ 并发契约


def test_the_tick_only_collects_and_never_pushes(plugin) -> None:
    """tick 里**只攒不推**（P11.1：持锁 await 会冻住事件循环）。"""
    body = _strip_py_comments(_method_body(_init_text(), "_tick_once"))
    assert "push_message" not in body, "tick 里不许直接 push_message"
    assert "_push_proactive" not in body, "tick 里不许直接推话"
    assert "_collect_yui_event_prompts" in body, "tick 里没有攒事件提示词"
    assert "_collect_yui_live_prompt" in body, "tick 里没有攒主动回合"


def test_the_heartbeat_flushes_the_queue_outside_the_lock() -> None:
    """真正的推送发生在**心跳**里（锁外）。"""
    text = _init_text()
    flush = _strip_py_comments(_method_body(text, "_flush_yui_prompts"))
    assert "_push_proactive" in flush, "心跳没有把攒好的提示词推出去"
    # P26.0：心跳拆成了「有界外壳 ``market_tick`` + 真身 ``_heartbeat_body``」
    # （外壳负责硬超时兜底）⇒ 两段一起看。
    shell = _strip_py_comments(_method_body(text, "market_tick"))
    assert "_heartbeat_body()" in shell, "心跳外壳没有调用真身"
    heartbeat = shell + _strip_py_comments(_method_body(text, "_heartbeat_body"))
    assert "_flush_yui_prompts" in heartbeat, "心跳里没有冲刷提示词队列"


def test_the_push_never_raises(plugin) -> None:
    """宿主接不住 ``push_message`` ⇒ **绝不抛异常**（绝不能把心跳带崩）。"""
    run(plugin.join(mode="classic_arcade", seed=7420))
    plugin._yui_pending_prompts = [("随便说点什么", "yui_live_turn", 3)]
    run(plugin._flush_yui_prompts())
    assert _pending(plugin) == [], "队列必须被清空"


# ══════════════════════════════════════════════════════════════ ⑦ 面板


def test_the_talk_picker_sits_next_to_the_mode_picker() -> None:
    """【话痨级别】必须**紧挨着** Yui 模式选择器。"""
    code = _panel_code()
    assert "function YuiTalkPicker" in code, "面板里没有话痨级别选择器"
    # 空闲态与局中态各一份，都跟在 YuiModePicker 后面。
    assert code.count("<YuiTalkPicker") == 2, "空闲页与局中页各要有一份"
    for index in [m.start() for m in re.finditer(r"<YuiTalkPicker", code)]:
        window = code[max(0, index - 700) : index]
        assert "<YuiModePicker" in window, "话痨级别选择器必须挨着 Yui 模式选择器"


def test_the_talk_picker_has_three_pills() -> None:
    """三档：安静 / 正常 / 话痨。"""
    code = _panel_code()
    start = code.index("function YuiTalkPicker")
    body = code[start : code.index("\n}\n", start) + 3]
    for key in ("panel.yui_talk_quiet", "panel.yui_talk_normal", "panel.yui_talk_chatty"):
        assert key in body, f"话痨级别选择器里缺 {key}"
    assert "rrm-yui-talk-btn" in body


def test_the_talk_picker_reads_the_state_and_calls_the_action() -> None:
    """档位**从载荷读**、切换**走 action**。"""
    code = _panel_code()
    assert "yuiBlock?.talk_level" in code, "面板没有读 talk_level"
    assert 'callAction("yui_talk_level"' in code, "面板没有调 yui_talk_level"
    # 局中页那份必须带 compact（不许在直播看板里塞一整块）。
    anchor = 'className="rrm-anchor-status-bar rrm-live-toolbar"'
    assert anchor in code, "找不到局中工具条"
    live = code[code.index(anchor) :]
    match = re.search(r"<YuiTalkPicker[\s\S]*?/>", live)
    assert match, "局中页没有话痨级别选择器"
    assert "compact" in match.group(0), "局中页的话痨级别选择器必须是紧凑档"


def test_the_talk_picker_css_is_safe() -> None:
    """新增的 CSS 不许踩那四个雷。"""
    css = _panel_css()
    for banned in (
        "@keyframes",
        "position",
        "filter",
        "opacity",
        "clip-path",
        "inset:",
        "radial-gradient",
        "z-index",
    ):
        assert banned not in css, f"CSS 里出现了禁用词：{banned}"
    for match in re.finditer(r"animation:\s*([^;\n]+)", css):
        assert match.group(1).strip() in {"none", "none !important"}, match.group(0)
    # 话痨药丸要有自己的样式（不许跟模式的紫药丸共用一套选中态）。
    assert ".rrm-yui-talk-btn" in css
    assert ".rrm-yui-talk-btn-on" in css


def test_the_new_i18n_keys_exist() -> None:
    """新增文案必须进词典。"""
    i18n = _i18n()
    for key in (
        "panel.yui_talk_quiet",
        "panel.yui_talk_normal",
        "panel.yui_talk_chatty",
        "panel.yui_talk_title",
        "panel.yui_talk_hint",
        "yui.talk.bad",
        "yui.stock_not_found",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
        assert str(i18n[key]).strip(), f"{key} 不能是空串"


def test_the_new_i18n_keys_avoid_finance_jargon() -> None:
    """P24.0 新增的文案不许用金融黑话。"""
    i18n = _i18n()
    for key in (
        "panel.yui_talk_quiet",
        "panel.yui_talk_normal",
        "panel.yui_talk_chatty",
        "panel.yui_talk_title",
        "panel.yui_talk_hint",
        "yui.talk.bad",
        "yui.stock_not_found",
    ):
        text = str(i18n[key])
        for banned in ("仓位", "空头", "多头", "杠杆", "追高杀跌"):
            assert banned not in text, f"{key} 里出现了金融黑话 {banned}"


def test_the_panel_code_has_no_hardcoded_chinese() -> None:
    """面板**代码**零硬编码中文（文案必须走 i18n）。"""
    offenders = [
        (number, line.strip())
        for number, line in enumerate(_panel_code().split("\n"), start=1)
        if re.search(r"[\u4e00-\u9fff]", line)
    ]
    assert not offenders, f"面板里有硬编码中文文案：{offenders}"


# ══════════════════════════════════════════════════════════════ ⑧ 红线


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层**零改动**（P24.0 只加插件层逻辑）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎 / 模式层被改了：{changed}"


def test_the_engine_and_mode_files_were_not_touched_in_this_round() -> None:
    """再钉一次：本轮**不许**碰 ``engine/`` 与 ``modes/`` 目录。"""
    for layer in ("engine", "modes"):
        for path in (_PLUGIN_ROOT / layer).rglob("*.py"):
            assert "__pycache__" in path.parts or True  # 只为遍历
    changed = engine_mode_code_changes()
    assert changed == [], f"引擎 / 模式层被改了：{changed}"
