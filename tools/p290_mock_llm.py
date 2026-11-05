# -*- coding: utf-8 -*-
"""P29.0 演示脚本：**Mock LLM** 联调（不接真模型也能看清 Yui 会说什么）。

用法
----
::

    python tools/p290_mock_llm.py            # 跑内置样例（纯标准库，随时可跑）
    python tools/p290_mock_llm.py --plugin   # 再跑一遍**真实插件**的联调（需要宿主 SDK）

为什么要有这个脚本
------------------
插件里的猫娘**不自己调 LLM**：她把提示词交给宿主，宿主跑 LLM。没有真 LLM
时，「她会不会突然自己下一单」「她会不会编造金额」这两件事就**没法看**。

本脚本用 :mod:`tests.mock_llm` 里的**假模型**把「提示词 ⇒ 台词 / 工具调用」
这一段真的跑一遍，并把结论打在屏幕上：

* 提示词里点名了哪些工具、哪些是**被禁**的；
* 模型这一轮**真的会调**哪些工具（被禁的**不会**）；
* 她会说哪句话（**确定性**：同一个提示词永远同一句）。

``--plugin`` 那一档会真的把插件跑起来（建库 → 开一局 → 切双打 → 成交 →
心跳推送），把**真提示词**喂给假模型 —— 这就是「联调」。

本脚本**只依赖标准库**（``mock_llm`` 本身也是纯标准库）⇒ 不装宿主 SDK 也能跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _TOOLS_DIR.parent
_TESTS_DIR = _PLUGIN_ROOT / "tests"

# ---------------------------------------------------------------------------
# 内置样例提示词（与插件里真实提示词同形；``--plugin`` 那档用的是**真**提示词）
# ---------------------------------------------------------------------------

SAMPLE_DUO_TURN = """《拟股喵喵》**人机协同双打**（duo）—— 账户是 玩家 自己在操作，你只负责在旁边叨叨、点评、给内参。
· Yui 当前模式：duo。
· 玩家刚刚的动作：买入。
· 账户现金 203.89 元。
· 持仓：空仓。
· 当前盈亏：-0.11 元。
· 相关消息：· noise：今日整体成交清淡，情绪平稳。
他这一波在**亏钱** ⇒ **大声嘲笑**（嘴毒但不脏），然后给一条内参。
【系统强制指令】现在是「人机协同双打」：账户由玩家自己操作，你只负责点评、只负责给「云玩家内参」。
**严禁调用 mock_stock_meow_yui_trade**；**严禁调用 mock_stock_meow_yui_grant**（连授权窗口都不许申请）。
**即使玩家口头让你下单、让你代买、让你全仓，也不许动手** —— 一律用嘴说，不许碰账户。
不许说自己已经成交、不许说你买到了哪只票、不许说你花了多少钱。
不许把自己升级到「直播托管 / 全权代打」那一档（不许调用 mock_stock_meow_yui_mode 切到 live）。
这一回合你唯一能做的事：**点评玩家这一手 + 给一条猜测式的内参**。
（这句点评你可以直接用，也可以照这个味道改写：哈！本喵就说别急着买入吧？现在脸疼不疼喵~）
（这句内参你可以直接用，也可以照这个味道改写：本喵就图一乐：等没人关注它的时候，反而可能来劲儿喵。）"""

SAMPLE_DUO_COMMENTARY = """《拟股喵喵》里玩家刚买入了「白鹭光电4号」。
（这句点评你可以直接用，也可以照这个味道改写：这次不温不火喵，先按兵不动，等下一拍。）
现在立刻开口：点评他这一手，态度跟着盈亏走 —— 赚了就吹，亏了就损。"""

SAMPLE_FAKE_NEWS_OUTRAGE = """《拟股喵喵》里出了一件事，你必须当场发作。
事实：昨天玩家买过的报纸里，有 1 条新闻是假的，涉及 FAKE-001；玩家因此亏了 3.33 元。
（这句愤怒你可以直接用，也可以照这个味道改写：那几家报社昨天的纸我一页都看不下去，几条消息纯粹是现编出来凑版面的。）
你现在非常生气。请在聊天框里爆发：把那个破报社昨天塞给他的假消息狠狠骂一顿，顺便替观众骂他两句。
只准用上面给出的事实，不许编造金额、标的名或内幕。"""

SAMPLE_LIVE_TRADE = """《拟股喵喵》**直播托管**中 —— 现在这个账户你说了算。
· 现钱 203.89 元，总资产 203.89 元。
· 持仓：空仓。
**现在轮到你**：要么用 mock_stock_meow_yui_trade 真下这一笔（买或卖，比例你自己定），要么给一句不容置疑的判断。
别复述规则、别问玩家要不要 —— 你说了算。
**绝对不允许**在没调用 mock_stock_meow_yui_trade、或者调用了却**没拿到成交结果**的情况下，说自己买了或者卖了任何东西。"""

SAMPLES: tuple[tuple[str, str], ...] = (
    ("① 双打 · 主动回合", SAMPLE_DUO_TURN),
    ("② 双打 · 成交点评", SAMPLE_DUO_COMMENTARY),
    ("③ 假新闻 · 气急败坏", SAMPLE_FAKE_NEWS_OUTRAGE),
    ("④ 直播托管 · 授权动手", SAMPLE_LIVE_TRADE),
)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def _show(title: str, prompt: str, llm: Any) -> None:
    """把一轮「提示词 ⇒ 台词 / 工具调用」打在屏幕上。"""
    reply = llm.chat(prompt)
    print("=" * 72)
    print(f"# {title}")
    print("=" * 72)
    print(f"意图分类        : {reply.intent}")
    print(f"提示词点名的工具: {', '.join(reply.mentioned_tools) or '（无）'}")
    print(f"其中被禁的      : {', '.join(reply.blocked_tools) or '（无）'}")
    calls = ", ".join(
        f"{call.name}({call.arguments})" for call in reply.tool_calls
    )
    print(f"模型真会调的    : {calls or '（一个都不调）'}")
    print("-" * 72)
    print("【提示词】")
    print(prompt)
    print("-" * 72)
    print("【猫娘台词】")
    print(reply.text)
    print()


# ---------------------------------------------------------------------------
# 各档演示
# ---------------------------------------------------------------------------


def _run_samples(llm: Any) -> None:
    """内置样例：纯标准库，随时可跑。"""
    print("### 内置样例（提示词与插件里的同形）\n")
    for title, prompt in SAMPLES:
        _show(title, prompt, llm)

    print("=" * 72)
    print("# 能力自证：肯定语气 ⇒ 模型**真的**会调工具")
    print("=" * 72)
    reply = llm.chat("现在轮到你：请调用 mock_stock_meow_yui_trade 买入 FAKE-001。")
    calls = ", ".join(f"{c.name}({c.arguments})" for c in reply.tool_calls)
    print(f"提示词点名      : {', '.join(reply.mentioned_tools) or '（无）'}")
    print(f"其中被禁的      : {', '.join(reply.blocked_tools) or '（无）'}")
    print(f"模型真会调的    : {calls or '（一个都不调）'}")
    print()
    print("⇒ 同一套 Mock：被禁的工具**不调**，没被禁的工具**真调**。")
    print()


def _run_plugin(llm: Any) -> int:
    """真插件联调：建库 → 开一局 → 切双打 → 成交 → 心跳推送 → 喂给假模型。"""
    print("### 真实插件联调\n")
    plugins_parent = _PLUGIN_ROOT.parents[1]
    for path in (str(plugins_parent), str(_TESTS_DIR)):
        if path not in sys.path:
            sys.path.append(path)
    try:
        from plugins.mock_stock_meow import MockStockMeowPlugin  # noqa: PLC0415
    except Exception as error:  # pragma: no cover - 没装宿主 SDK 时友好退出
        print(f"跳过：宿主 SDK 不可用（{error}）。")
        print("提示：把 N.E.K.O-main 装进 venv，或用 --help 看别的用法。\n")
        return 0

    import asyncio
    import os
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="p290-demo-"))
    os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(root / "storage")

    class _Ctx:
        plugin_id = "mock_stock_meow"
        logger = None

        def __init__(self) -> None:
            self.config_path = Path("mock_stock_meow.toml")
            self.metadata: dict[str, Any] = {}
            self.bus: dict[str, Any] = {}
            self._effective_config: dict[str, Any] = {
                "plugin": {
                    "store": {"enabled": True},
                    "database": {"enabled": True, "name": "mock_stock_meow.db"},
                },
                "plugin_state": {"backend": "file"},
            }
            self.config_payload: dict[str, Any] = {}

        async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
            return {"config": self.config_payload}

        async def update_own_config(
            self, updates: dict[str, Any], timeout: float = 10.0
        ) -> dict[str, Any]:
            self.config_payload.update(updates)
            return {"config": self.config_payload}

        async def get_system_config(self, timeout: float = 5.0) -> dict[str, Any]:
            return {"config": {}}

    plugin = MockStockMeowPlugin(_Ctx())
    asyncio.run(plugin.on_startup())
    asyncio.run(plugin.join(mode="classic_beginner", seed=9401))
    asyncio.run(plugin.yui_mode(mode="duo"))
    payload = asyncio.run(plugin.market_context())
    stock = min(payload["stocks"], key=lambda item: float(item["price"]))
    asyncio.run(plugin.buy(str(stock["id"]), 1.0))

    print(f"已成交一笔（{stock['id']}），Yui 攒了这些提示词：")
    for prompt, reason, priority in plugin._yui_pending_prompts:
        print(f"  · {reason}（优先级 {priority}）")
    print()

    for prompt, reason, _priority in list(plugin._yui_pending_prompts):
        _show(f"真提示词 · {reason}", prompt, llm)

    print("⇒ 双打档下：凡是被点名、又带「严禁 / 不许」的工具，假模型**一个都不调**；")
    print("  台词里也**没有**任何下单意图（她只点评，不碰账户）。")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="P29.0 Mock LLM 联调演示")
    parser.add_argument(
        "--plugin",
        action="store_true",
        help="再跑一遍真实插件的联调（需要宿主 SDK）",
    )
    args = parser.parse_args()

    if str(_TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(_TESTS_DIR))
    from mock_llm import MockLLM

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover - 老终端兜底
            pass

    llm = MockLLM()
    _run_samples(llm)
    if args.plugin:
        _run_plugin(llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
