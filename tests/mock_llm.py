# -*- coding: utf-8 -*-
"""P29.0：**Mock LLM**（测试夹具）—— 让直播时的 Yui 表现**绝对可控**。

为什么需要它
------------
插件里的猫娘（Yui）**不自己调 LLM**：它把提示词通过宿主的
``ctx.push_message(visibility=[], ai_behavior="respond",
parts=[{"type": "text", "text": prompt}], source=..., target_lanlan=...)``
推给宿主，**由宿主跑 LLM**，再把猫娘气泡显示出来。

没有真 LLM 环境时，直播里的 Yui 就成了**不可测的黑盒**：她说什么、会不会
突然去下单，全靠运气。本模块补上这一层：

1. :class:`RecordingCtx` —— 给 ``FakeCtx`` **补一个可记录的**
   ``push_message``：把每一次推送的 ``visibility`` / ``ai_behavior`` /
   ``parts`` / ``source`` / ``target_lanlan`` / ``priority`` 记进列表，
   并返回一个**假的宿主受理结果**（可 ``await``、不抛异常）。
2. :class:`MockLLM` —— **假的大模型**：给定提示词 ⇒ 输出一段**确定性**的
   猫娘台词；并且能**识别提示词里点名的工具**，从而模拟
   「模型如果被指示调用工具就真的会调」。

确定性从哪来
------------
台词由 **提示词的 SHA256** 决定（固定种子 + 固定模板池）⇒
**同一个提示词永远得到同一段台词**；既不依赖真随机，也不污染全局随机源。

「被禁止的工具」怎么认
--------------------
按**行**判定：某个工具名出现的**每一行**里都带着禁止词（严禁 / 不许 /
不允许 / 禁止 / 不得 / 不准）⇒ 这个工具是**被禁的**，模型**不会**去调它；
只要有一行是**肯定语气**（例如「要么用 ``mock_stock_meow_yui_trade`` 真下
这一笔」）⇒ 模型**就会**去调它。

这正是双打档（``duo``）的硬契约在提示词层面的样子：那两个工具名**出现在
提示词里**，但只出现在「**严禁调用**」的句子里 ⇒ Mock LLM 不会下单。

不依赖插件包
------------
本模块**只 import 标准库** ⇒ ``tools/p290_mock_llm.py`` 可以直接 ``import``
它跑演示，**不需要**宿主 SDK。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "GRANT_TOOL",
    "KNOWN_TOOLS",
    "MODE_TOOL",
    "MockLLM",
    "MockReply",
    "MockToolCall",
    "ORDER_INTENT_MARKERS",
    "PROHIBITION_MARKERS",
    "RecordedPush",
    "RecordingCtx",
    "TRADE_TOOL",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 交易工具（Yui 亲手下单）。
TRADE_TOOL = "mock_stock_meow_yui_trade"
#: 授权工具（玩家明示授权）。
GRANT_TOOL = "mock_stock_meow_yui_grant"
#: 模式切换工具（Yui 自己换档）。
MODE_TOOL = "mock_stock_meow_yui_mode"

#: Mock LLM 认识的全部工具。
KNOWN_TOOLS: tuple[str, ...] = (TRADE_TOOL, GRANT_TOOL, MODE_TOOL)

#: 判定「这一行是禁止语气」的词。
PROHIBITION_MARKERS: tuple[str, ...] = (
    "严禁",
    "不许",
    "不允许",
    "不准",
    "禁止",
    "不得",
    "不要调用",
    "别调用",
)

#: 「模型自己**要下单**」的标志词（第一人称 / 代玩家下单）。
#:
#: 刻意**不含**裸的「买入 / 卖出」—— 点评玩家这一手时说「他这一波买入」
#: 是**转述**，不是 Yui 自己要下单；两者的性质完全不同。
ORDER_INTENT_MARKERS: tuple[str, ...] = (
    "我下单",
    "我买入",
    "我卖出",
    "我买了",
    "我卖了",
    "我全仓",
    "我满仓",
    "我来买",
    "我来卖",
    "帮你买",
    "帮你卖",
    "帮你下单",
    "帮你操作",
    "买到了",
    "卖出了",
    "已经成交",
    "已成交",
)


# ---------------------------------------------------------------------------
# 台词模板池（**固定** ⇒ 确定性）
# ---------------------------------------------------------------------------

#: 假新闻爆发时的愤怒开场白（每句都带「气 / 生气 / 骂」这类情绪词）。
_ANGER_POOL: tuple[str, ...] = (
    "气死我了！那个破报社收着钱塞假消息，把人往坑里带，太过分了喵！",
    "我生气了啊！昨天那批消息居然是编的，还装得跟真的一样，气死本喵了喵！",
    "骂他一顿！这种糊弄人的破报纸，骗完人还收钱，太缺德了喵！",
)

#: 双打点评的开场白（**绝不含**任何下单意图）。
_DUO_LEAD_POOL: tuple[str, ...] = (
    "接住这一手喵——",
    "本喵说两句喵：",
    "好家伙，",
    "喵，本喵就在旁边看着：",
)

#: 直播托管 / 通用场合的开场白。
_GENERIC_LEAD_POOL: tuple[str, ...] = (
    "本喵盯着盘面呢，",
    "喵，行情这一块本喵看得挺清楚，",
    "本喵先搭个话，",
)

#: 直播托管：**模型确实会动手**时的台词（只有工具真被批准时才会走到）。
_TRADE_LEAD_POOL: tuple[str, ...] = (
    "本喵动手了，",
    "既然账户交给我了，",
    "喵，本喵先落一单，",
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _has_prohibition(line: str) -> bool:
    """这一行是不是**禁止语气**。"""
    text = str(line or "")
    return any(marker in text for marker in PROHIBITION_MARKERS)


def _pick(pool: tuple[str, ...], prompt: str, seed: int) -> str:
    """从模板池里挑一条 —— **由提示词决定**（同一个提示词永远挑同一条）。"""
    if not pool:
        return ""
    digest = hashlib.sha256(f"{seed}\n{prompt}".encode("utf-8")).digest()
    return pool[digest[0] % len(pool)]


def _extract_after(prompt: str, prefix: str) -> str:
    """取「以 ``prefix`` 开头的那一行」在 ``prefix`` 之后的正文。"""
    for line in str(prompt or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _extract_material(prompt: str, prefix: str) -> str:
    """取提示词里「这句素材你可以直接用：（……）」括号里的正文。"""
    for line in str(prompt or "").splitlines():
        if prefix not in line:
            continue
        tail = line.split(prefix, 1)[1]
        if tail.endswith("）"):
            tail = tail[: -len("）")]
        return tail.strip()
    return ""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MockToolCall:
    """模型这一轮**真的会调**的工具。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MockReply:
    """模型这一轮的完整输出。"""

    #: 猫娘台词（纯文本）。
    text: str
    #: 意图分类（``duo_commentary`` / ``duo_turn`` / ``fake_news_outrage`` / ...）。
    intent: str
    #: 模型**真的会调**的工具（被禁的不在里面）。
    tool_calls: tuple[MockToolCall, ...] = ()
    #: 提示词里点名、但**被禁止**的工具。
    blocked_tools: tuple[str, ...] = ()
    #: 提示词里点名过的全部工具。
    mentioned_tools: tuple[str, ...] = ()


@dataclass
class RecordedPush:
    """一次 ``push_message`` 的记录。"""

    visibility: list[str]
    ai_behavior: str | None
    parts: list[dict[str, Any]]
    source: str
    target_lanlan: str | None
    priority: int
    #: 宿主收到的**全部**关键字参数（便于排查）。
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """这一条推送里的**提示词正文**（多段则拼起来）。"""
        chunks = [
            str(part.get("text", ""))
            for part in self.parts
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(chunk for chunk in chunks if chunk)


class FakePushResult:
    """假的宿主受理结果：**可 await**（await 完返回它自己），不抛异常。"""

    def __init__(self, *, submitted: bool = True, message_id: str = "mock-push") -> None:
        self.submitted = bool(submitted)
        self.message_id = str(message_id)

    def __await__(self) -> Any:
        async def _result() -> FakePushResult:
            return self

        return _result().__await__()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"FakePushResult(submitted={self.submitted}, message_id={self.message_id!r})"


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLM:
    """**假的大模型**：提示词 ⇒ 猫娘台词（确定性）+ 工具调用计划。

    用法::

        llm = MockLLM()
        reply = llm.chat(prompt)
        reply.text            # 猫娘会说的那句话
        reply.tool_calls      # 模型这一轮**真的会调**的工具
        reply.blocked_tools   # 提示词里点名、但被禁止的工具
    """

    def __init__(self, *, seed: int = 20260905) -> None:
        self.seed = int(seed)
        #: 历次回复（按调用顺序）。
        self.replies: list[MockReply] = []
        #: 历次**真的**发生的工具调用。
        self.tool_calls: list[MockToolCall] = []

    # ── 工具识别 ──────────────────────────────────────────────────────

    def scan_tools(self, prompt: str) -> tuple[list[str], list[str]]:
        """扫一遍提示词：返回 ``(被点名的工具, 被禁止的工具)``。"""
        text = str(prompt or "")
        mentioned: list[str] = []
        blocked: list[str] = []
        for name in KNOWN_TOOLS:
            if name not in text:
                continue
            mentioned.append(name)
            lines = [line for line in text.splitlines() if name in line]
            # **每一行**都带禁止词 ⇒ 这个工具是被禁的。
            if lines and all(_has_prohibition(line) for line in lines):
                blocked.append(name)
        return mentioned, blocked

    def plan_tool_calls(self, prompt: str) -> list[MockToolCall]:
        """模型这一轮**真的会调**的工具（被禁的不算）。"""
        mentioned, blocked = self.scan_tools(prompt)
        calls: list[MockToolCall] = []
        for name in mentioned:
            if name in blocked:
                continue
            calls.append(
                MockToolCall(name=name, arguments=self._arguments_for(prompt, name))
            )
        return calls

    @staticmethod
    def _arguments_for(prompt: str, name: str) -> dict[str, Any]:
        """尽力而为地抠出工具参数（**只从提示词里抠**，绝不自己编）。"""
        line = ""
        for candidate in str(prompt or "").splitlines():
            if name in candidate:
                line = candidate
                break
        if name != TRADE_TOOL or not line:
            return {}
        arguments: dict[str, Any] = {}
        match = re.search(r"(?<![A-Za-z0-9_])([A-Za-z]{1,6}-?\d{1,6})(?![A-Za-z0-9_])", line)
        if match:
            arguments["stock_id"] = match.group(1)
        if "买" in line or "buy" in line.lower():
            arguments["action"] = "buy"
        elif "卖" in line or "sell" in line.lower():
            arguments["action"] = "sell"
        return arguments

    # ── 台词 ──────────────────────────────────────────────────────────

    def classify(self, prompt: str) -> str:
        """给提示词分类（决定用哪套模板）。"""
        text = str(prompt or "")
        if "只准用上面给出的事实" in text or "你必须当场发作" in text:
            return "fake_news_outrage"
        if "态度跟着盈亏走" in text:
            return "duo_commentary"
        if "人机协同双打" in text:
            return "duo_turn"
        if "直播托管" in text:
            return "live_turn"
        if "刚出了一条" in text:
            return "news"
        return "generic"

    def reply(self, prompt: str) -> str:
        """只取台词（不想关心工具调用时用这个）。"""
        return self.chat(prompt).text

    def chat(self, prompt: str) -> MockReply:
        """完整的一轮：台词 + 工具调用计划。"""
        text = str(prompt or "")
        mentioned, blocked = self.scan_tools(text)
        calls = self.plan_tool_calls(text)
        intent = self.classify(text)
        reply = MockReply(
            text=self._render(intent, text, bool(calls)),
            intent=intent,
            tool_calls=tuple(calls),
            blocked_tools=tuple(blocked),
            mentioned_tools=tuple(mentioned),
        )
        self.replies.append(reply)
        self.tool_calls.extend(calls)
        return reply

    def _render(self, intent: str, prompt: str, will_trade: bool) -> str:
        """按意图拼台词。"""
        if intent == "fake_news_outrage":
            return self._render_outrage(prompt)
        if intent == "duo_commentary":
            return self._render_duo_commentary(prompt)
        if intent == "duo_turn":
            return self._render_duo_turn(prompt)
        if intent == "live_turn":
            return self._render_live_turn(prompt, will_trade)
        return self._render_generic(prompt)

    def _render_outrage(self, prompt: str) -> str:
        """假新闻爆发：**事实 + 愤怒**（事实逐字来自提示词 ⇒ 绝不编造）。"""
        anger = _pick(_ANGER_POOL, prompt, self.seed)
        opinion = _extract_material(
            prompt, "（这句愤怒你可以直接用，也可以照这个味道改写："
        )
        fact = _extract_after(prompt, "事实：")
        parts = [anger]
        if opinion:
            parts.append(opinion)
        if fact:
            parts.append(fact)
        return "\n".join(parts)

    def _render_duo_commentary(self, prompt: str) -> str:
        """双打点评：只复述素材（**绝不下单**）。"""
        lead = _pick(_DUO_LEAD_POOL, prompt, self.seed)
        material = _extract_material(
            prompt, "（这句点评你可以直接用，也可以照这个味道改写："
        )
        if not material:
            return lead
        return f"{lead}{material}"

    def _render_duo_turn(self, prompt: str) -> str:
        """双打主动回合：点评 + 一条**猜测式**内参。"""
        lead = _pick(_DUO_LEAD_POOL, prompt, self.seed)
        commentary = _extract_material(
            prompt, "（这句点评你可以直接用，也可以照这个味道改写："
        )
        tip = _extract_material(prompt, "（这句内参你可以直接用，也可以照这个味道改写：")
        parts = [lead]
        if commentary:
            parts.append(commentary)
        if tip:
            parts.append(tip)
        return "\n".join(parts)

    def _render_live_turn(self, prompt: str, will_trade: bool) -> str:
        """直播托管：工具真被批准时，模型**确实会动手**。"""
        if will_trade:
            lead = _pick(_TRADE_LEAD_POOL, prompt, self.seed)
            return f"{lead}这单本喵接了。"
        lead = _pick(_GENERIC_LEAD_POOL, prompt, self.seed)
        return f"{lead}给一句不容置疑的判断。"

    def _render_generic(self, prompt: str) -> str:
        lead = _pick(_GENERIC_LEAD_POOL, prompt, self.seed)
        material = _extract_material(
            prompt, "（这句点评你可以直接用，也可以照这个味道改写："
        )
        if material:
            return f"{lead}{material}"
        return f"{lead}本喵先说这一句。"


# ---------------------------------------------------------------------------
# 记录式 ctx
# ---------------------------------------------------------------------------


class RecordingCtx:
    """给 ``FakeCtx`` **补一个可记录的** ``push_message``。

    * 构造时把 ``push_message`` 装到**被包的 ctx 实例**上 ⇒
      宿主的 ``SdkContext`` 转发过去时就能被记录；
    * 其余成员（``plugin_id`` / ``logger`` / ``config_path`` / ``metadata`` /
      ``bus`` / ``get_own_config`` / ...）一律**转发**给被包的 ctx ⇒
      本对象自己也能直接当 ctx 传给插件。

    记录下来的是每一次推送的 ``visibility`` / ``ai_behavior`` / ``parts`` /
    ``source`` / ``target_lanlan`` / ``priority``。
    """

    def __init__(self, ctx: Any, llm: MockLLM | None = None) -> None:
        self._base = ctx
        self.llm = llm if llm is not None else MockLLM()
        #: 历次推送（按顺序）。
        self.messages: list[RecordedPush] = []
        self._install()

    def __getattr__(self, name: str) -> Any:
        """转发给被包的 ctx（常规查找失败时才触发）。"""
        try:
            base = object.__getattribute__(self, "_base")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(base, name)

    def _install(self) -> None:
        """把 ``push_message`` 装到被包的 ctx 上。"""
        try:
            self._base.push_message = self.push_message
        except Exception:  # pragma: no cover - 只读 ctx 也不该炸
            pass

    def push_message(self, **kwargs: Any) -> FakePushResult:
        """假的宿主 ``push_message``：记下来，返回一个**可 await**的受理结果。"""
        parts = kwargs.get("parts")
        self.messages.append(
            RecordedPush(
                visibility=list(kwargs.get("visibility") or []),
                ai_behavior=kwargs.get("ai_behavior"),
                parts=list(parts or []),
                source=str(kwargs.get("source") or ""),
                target_lanlan=kwargs.get("target_lanlan"),
                priority=int(kwargs.get("priority") or 0),
                raw=dict(kwargs),
            )
        )
        return FakePushResult(message_id=f"mock-push-{len(self.messages)}")

    # ── 便利方法 ──────────────────────────────────────────────────────

    def clear(self) -> None:
        """清空记录。"""
        self.messages.clear()

    @property
    def prompts(self) -> list[str]:
        """历次推送的提示词正文。"""
        return [message.text for message in self.messages]

    def last_prompt(self) -> str:
        """最近一次推送的提示词正文（没有就空串）。"""
        if not self.messages:
            return ""
        return self.messages[-1].text
