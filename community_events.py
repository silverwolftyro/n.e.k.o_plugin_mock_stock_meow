"""P26.0：群友随机事件池（**插件层**）。

两个彩蛋，都是"致敬群友"的：

* **浅忆QanYi 的「天降 Token」** —— 玩家快交不上生活费时，有极小概率被投钱。
  群友的设定是"像救世主一样来发钱"，所以这笔钱**得真的救命**：
  刚好够交今天 **和** 明天两笔生活费（少一分，明天就交不上，
  要挨二道贩子那一刀）。
* **云玩家的「免费内参」** —— 每天开盘时可能塞来一份内参。
  内参**可能看着靠谱**（挑最近动能最强的那只），**也可能是抽象的**
  （挑最贴近退市地板的那只）。玩家在面板上**看不到具体内容**，
  只有一个 buff 标签；内容只走 ``push_message`` 给 Yui。

四条红线（P26.0 的设计约束，缺一不可）：

1. **引擎层 / 模式层零改动** —— 本模块只做"插件层的事"：
   掷点、算钱、拼事件。``engine/`` 与 ``modes/`` 一行都不动。
2. **随机源必须是系统熵** —— 绝不用玩家可选的 ``seed``。
   玩家开局能自己挑 seed；用 seed 派生掷点的话，玩家可以枚举 seed
   把救助**稳定刷**出来 ⇒ "永远必亏"的核心当场作废。
3. **只用公开信息** —— 事件里只准出现价格、K 线这类公开数据；
   不碰 heat / seed / 反转日 / 新闻真假（P4 §11）。
4. **一天最多一次** —— 两件事都只在**日界**评估，所以天然一天一次。
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CommunityEvent",
    "EVENT_KIND_INTEL",
    "EVENT_KIND_TOKEN",
    "EVENT_POOL",
    "INTEL_CHANCE",
    "INTEL_DOOMED_CHANCE",
    "MAX_TOKEN_RESCUES_PER_GAME",
    "TOKEN_RESCUE_CHANCE",
    "build_intel_event",
    "build_token_rescue_event",
    "pick_intel_target",
    "roll",
    "token_rescue_amount",
]


#: 天降 Token 的触发概率（玩家极度缺钱时）。
#:
#: 5% 是"极小概率"：一局 30 天、玩家天天缺钱，也就_expected_ 1~2 次。
TOKEN_RESCUE_CHANCE = 0.05

#: 云玩家免费内参的触发概率（每天开盘）。
INTEL_CHANCE = 0.25

#: 内参里"抽象"那一路的占比（另一半是"看着靠谱"）。
INTEL_DOOMED_CHANCE = 0.5

#: 一局里最多能捡到几次天降 Token —— 保住"永远必亏"的平衡底线。
MAX_TOKEN_RESCUES_PER_GAME = 3

#: 事件种类。
EVENT_KIND_TOKEN = "token_rescue"
EVENT_KIND_INTEL = "intel"

#: **群友事件池**（插件层）。以后加新彩蛋，往这个元组里塞一条即可。
EVENT_POOL: tuple[str, ...] = (EVENT_KIND_TOKEN, EVENT_KIND_INTEL)

#: 面板上的分类名（插件层的文案；面板代码本身零硬编码中文）。
_KIND_LABELS = {
    EVENT_KIND_TOKEN: "群友投喂",
    EVENT_KIND_INTEL: "群友内参",
}

#: 掷点用的随机源。
#:
#: **必须**是系统熵（``SystemRandom``）—— 玩家能自己挑 seed，
#: 用 seed 派生的掷点会被枚举 ⇒ 救助能被稳定刷出来，"永远必亏"当场作废。
_SYSTEM_RANDOM = random.SystemRandom()

#: 事件序号（同一个进程内单调递增，方便日志与去重）。
_SEQ = itertools.count(1)


@dataclass(frozen=True)
class CommunityEvent:
    """一条群友随机事件（**插件层**自己的载荷）。

    面板只拿 ``public_payload()``（白名单字段）；``hint`` 只喂 Yui。
    """

    id: str
    kind: str
    kind_label: str
    title: str
    detail: str
    day: int
    amount: float = 0.0
    cash_before: float = 0.0
    living_cost: float = 0.0
    next_living_cost: float = 0.0
    buff_tag: str | None = None
    hint: str = ""

    def public_payload(self) -> dict[str, Any]:
        """给面板的**白名单**载荷（P4 §11：一个秘密字段都不许带）。

        刻意**不含** ``hint`` —— 内参的具体内容只给 Yui 看。
        """
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "title": self.title,
            "detail": self.detail,
            "day": int(self.day),
            "amount": round(float(self.amount), 2),
            "buff_tag": self.buff_tag,
        }


def roll(*, chance: float) -> bool:
    """掷一次点（**系统熵**）。

    ``chance`` 是概率（0~1）。用 ``<`` 而不是 ``<=``，边界上更保守。
    """
    return _SYSTEM_RANDOM.random() < float(chance)


def token_rescue_amount(
    cash: float, living_cost: float, next_living_cost: float
) -> float:
    """天降 Token 的金额。

    **刚好够交今天 + 明天两笔生活费**：

    * 少一分 ⇒ 明天交不上（要挨二道贩子那一刀），"救世主"就白来了；
    * 多一分 ⇒ 那不是"救场"，那是"发钱"，味道就不对了。

    玩家本来就缺钱 ⇒ 这笔钱相对整个市场仍然是"杯水车薪"。
    """
    needed = round(float(living_cost) + float(next_living_cost) - float(cash), 2)
    return needed if needed > 0.0 else 0.0


def build_token_rescue_event(
    *,
    day: int,
    cash_before: float,
    living_cost: float,
    next_living_cost: float,
) -> CommunityEvent:
    """拼一条「天降 Token」事件。"""
    amount = token_rescue_amount(cash_before, living_cost, next_living_cost)
    return CommunityEvent(
        id=f"{EVENT_KIND_TOKEN}-{next(_SEQ):04d}",
        kind=EVENT_KIND_TOKEN,
        kind_label=_KIND_LABELS[EVENT_KIND_TOKEN],
        title="浅忆QanYi 投下了几个 Token。",
        detail=(
            f"账户到账 {amount:.2f} 元 —— 刚好够今天和明天两天的生活费。"
        ),
        day=int(day),
        amount=amount,
        cash_before=round(float(cash_before), 2),
        living_cost=round(float(living_cost), 2),
        next_living_cost=round(float(next_living_cost), 2),
        buff_tag=None,
        hint="",
    )


def _momentum(stock: Any) -> float:
    """标的最近一段的**动能**（只用公开的 K 线）。

    没有 K 线（刚上市）时按 0 处理。
    """
    kline = list(getattr(stock, "kline", []) or [])
    if len(kline) < 2:
        return 0.0
    try:
        first = float(kline[0][1])
        last = float(kline[-1][1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if first <= 0.0:
        return 0.0
    return (last - first) / first


def pick_intel_target(
    market: Any, engine_config: Any
) -> tuple[str, str, str, str] | None:
    """给内参挑一只票。

    返回 ``(stock_id, name, verdict, hint)``；没有可挑的票时返回 ``None``。

    * ``verdict == "momentum"`` —— 最近动能最强的那只（看着靠谱）；
    * ``verdict == "doomed"``   —— 价格最低、最贴近退市地板的那只（极其抽象）。

    ``hint`` 是**内参本身的话**（只给 Yui 看，面板不许显示）。

    两条都只用**公开信息**（价格 + K 线 + 引擎配置里的退市阈值），
    不碰任何隐藏信号（P4 §11）。
    """
    stocks = list(getattr(market, "stocks", {}).values())
    alive = [stock for stock in stocks if bool(getattr(stock, "alive", True))]
    if not alive:
        return None

    market_config = getattr(engine_config, "market", None)
    floor = float(getattr(market_config, "delisting_floor", 0.01) or 0.01)
    ratio = float(getattr(market_config, "delisting_buffer_ratio", 3.0) or 3.0)
    buffer_price = floor * ratio

    if roll(chance=INTEL_DOOMED_CHANCE):
        # 「抽象」那一路：最贴近退市地板的那只。
        target = min(
            alive,
            key=lambda stock: (float(getattr(stock, "price", 0.0)), str(stock.id)),
        )
        verdict = "doomed"
    else:
        # 「看着靠谱」那一路：最近动能最强的那只。
        target = max(
            alive,
            key=lambda stock: (_momentum(stock), str(stock.id)),
        )
        verdict = "momentum"

    name = str(getattr(target, "name", "") or getattr(target, "id", ""))
    price = float(getattr(target, "price", 0.0) or 0.0)
    if verdict == "doomed":
        hint = (
            f"「{name}」已经贴着退市地板了（现在 {price:.4f} 元，地板 {buffer_price:.4f} 元），"
            "马上就要退市 —— 抄底要趁早。"
        )
    else:
        hint = f"「{name}」下一波肯定涨，闭眼冲。"
    return str(getattr(target, "id", "")), name, verdict, hint


def build_intel_event(*, day: int, target: tuple[str, str, str, str]) -> CommunityEvent:
    """拼一条「云玩家免费内参」事件。

    ``target`` 是 ``pick_intel_target`` 的返回值。
    """
    stock_id, name, verdict, hint = target
    return CommunityEvent(
        id=f"{EVENT_KIND_INTEL}-{next(_SEQ):04d}",
        kind=EVENT_KIND_INTEL,
        kind_label=_KIND_LABELS[EVENT_KIND_INTEL],
        title="云玩家塞了一份内参。",
        # 面板**不显示**具体内容（那是给 Yui 看的）：只留一个 buff 标签。
        detail="",
        day=int(day),
        amount=0.0,
        cash_before=0.0,
        living_cost=0.0,
        next_living_cost=0.0,
        buff_tag="情报：云玩家版",
        # ``hint`` 是**内参的原话**（不加任何前缀）——
        # 前缀由插件层拼提示词时统一加，重复一遍就成"套娃"了。
        hint=hint,
    )
