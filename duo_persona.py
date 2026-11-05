"""P28.0：**人机协同双打**（duo）的**人格素材层**。

这一层只干一件事：**给双打模式的 Yui 提供短句素材** ——
点评（吹捧 / 嘲笑 / 中立）、云玩家内参、话痨补刀。

为什么要单独成文件
------------------
「双打」这个人格的核心是**话多**：玩家自己动手，Yui 只能在旁边叨叨。
叨叨的话要**轮换**（同一句说十遍就腻）、要**短**（能直接进聊天框），
还要**分档**（安静 / normal / 话痨）。

层与层的边界
------------
* **纯插件层**：只用标准库 ``random`` / ``typing`` ——
  **不 import** ``engine`` / ``modes``，**不做** IO / DB。
* **纯素材**：这里只生成**句子**，不生成**事实** ——
  价格、盈亏、持仓都由 ``meow.yui_duo_prompt`` 那一层喂进来。
* **不许编造事实**：内参只能是**猜测**（见 :data:`DUO_TONE_RULES`）——
  不许出现具体价格 / 具体百分比 / 具体消息来源。

随机源
------
``rng`` 可选：不传就**新建一个** ``random.Random()`` 实例 ——
**不污染全局随机源**（全局随机源被引擎拿去算行情就会串味）。
"""

from __future__ import annotations

import random

__all__ = [
    "DUO_TALK_LEVELS",
    "DUO_TONE_RULES",
    "build_duo_chatter",
    "build_duo_commentary",
    "build_duo_praise",
    "build_duo_taunt",
    "build_duo_tip",
]


# ----------------------------------------------------------------------
# 语气规则
# ----------------------------------------------------------------------
#
# 这是**双打人格的规矩**。``meow.py`` 的 ``YUI_DUO_PERSONA`` 直接引用它
# ⇒ 提示词与素材**同一套口径**，不会各说各话。

#: 双打语气规则（喂给 LLM 的提示词 + 素材生成的自查清单）。
DUO_TONE_RULES: tuple[str, ...] = (
    "你是玩家旁边的「云玩家搭子」：玩家自己买卖，你只负责在旁边叨叨、"
    "点评、给「云玩家内参」（那是猜的，不是内幕）。",
    "点评要具体：说清楚他这一手动了什么、风险在哪，别泛泛而谈「涨了跌了」。",
    "内参必须是**猜测**口吻（我瞎猜啊 / 本喵押一个）—— "
    "但**要演得像真的在下判断**，可以错、可以离谱，本喵不负责。",
    "他亏钱时**大声嘲笑**（嘴毒但不脏、不做人身攻击）；"
    "他赚钱时**疯狂吹捧**（吹到离谱都行）。",
    "事实边界：不许编造具体内幕、不许编造具体数字、不许编造具体消息来源；"
    "内参里也不许出现具体价格、具体百分比。",
    "猫娘味：短句、语气词、偶尔一个「喵」；别写成研报，别复述规则。",
)


# ----------------------------------------------------------------------
# 素材池
# ----------------------------------------------------------------------
#
# 每类素材**至少 5 条**，随机轮换。
# 模板里的 ``{name}`` = 标的的说法，``{act}`` = 玩家这次的动作。

#: 吹捧（赚钱时）。
_PRAISE_POOL: tuple[str, ...] = (
    "漂亮喵！{name}这一波{act}得太漂亮了，本喵的耳朵都立起来了！",
    "发财了发财了！{name}这一手{act}得妙，本喵尾巴都摇起来了喵。",
    "高手！这就是传说中的随便出手都能赚喵？",
    "格局喵！别人还在犹豫呢，你已经先{act}了。",
    "稳！这波{act}比你上回强多了，本喵记下了喵。",
    "跟着你吃肉的喵感觉真好，{name}这边{act}得太舒服了！",
)

#: 嘲笑（亏钱时）—— 嘴毒，但**不脏**、**不做人身攻击**。
_TAUNT_POOL: tuple[str, ...] = (
    "噗——{name}这一波{act}，本喵看着都替你手心出汗喵。",
    "哈！本喵就说别急着{act}吧？现在脸疼不疼喵~",
    "这波操作……喵，本喵选择先吃口鱼干再看。",
    "啧，都这样了还要硬上？行吧行吧，英雄喵。",
    "别急别急，先把钱袋子收回来再操作喵，真的。",
    "你这手速，鼠标都救不了你喵~",
)

#: 中立点评（没赚没亏）。
_NEUTRAL_POOL: tuple[str, ...] = (
    "嗯，{name}那边{act}完了，先记下喵。",
    "不动声色喵，{name}这边先看着，怎么晃都行。",
    "这次不温不火喵，先按兵不动，等下一拍。",
    "喵，先记一笔，等有动静再说。",
    "嗯，不急着动手喵，先观望一下。",
    "{name}先摆着喵，{act}了也别急着下结论。",
)

#: 云玩家内参 —— **猜测 / 赌**的口吻。
#:
#: 不许出现具体价格、具体百分比、具体消息来源（这是事实边界，不是文风）。
_TIP_POOL: tuple[str, ...] = (
    "我瞎猜啊 —— {name}下一拍大概率先抖一下，信不信随你喵。",
    "纯猜的：{name}大概率先动，但本喵不说是先涨还是先跌喵。",
    "本喵押个方向：先冲后稳，具体的就不敢保证了喵。",
    "瞎蒙式的猜测：再这么磨叽下去，{name}大概率先撑不住喵。",
    "本喵就图一乐：等没人关注{name}的时候，反而可能来劲儿喵。",
    "猜一个：明天大概会有点小动静，信喵得自己拿主意喵。",
    "押注本喵的直觉：{name}最近有点飘，但飘多久本喵不敢说喵。",
)

#: 话痨档的补刀（多输出的一句）。
_CHATTER_POOL: tuple[str, ...] = (
    "再补一句喵：观众都在看你这波操作，稳住别慌。",
    "本喵说真的，能自己盯盘不乱按的玩家不多，夸你喵。",
    "哎对了，你这个手速比本喵按交易键还快喵。",
    "本喵在旁边看着都替你紧张喵，可别一把梭哈。",
    "多嘴一句：看盘别只盯一只，{name}之外也散一散喵。",
    "本喵就爱看这种自己冲的局，{name}这边本喵继续盯着喵。",
)


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------

#: 合法的三档话痨级别（与 ``__init__.py`` 的 ``YUI_TALK_LEVELS`` 同名同值）。
DUO_TALK_LEVELS: tuple[str, ...] = ("quiet", "normal", "chatty")


def _level(talk_level: str) -> str:
    """把话痨级别规整成三档之一（非法值回落 ``normal``）。"""
    value = str(talk_level or "").strip().lower()
    return value if value in DUO_TALK_LEVELS else "normal"


def _generator(rng: random.Random | None) -> random.Random:
    """取随机源：不传就**新建一个**实例（不污染全局随机源）。"""
    return rng if rng is not None else random.Random()


def _pick(pool: tuple[str, ...], generator: random.Random) -> str:
    """从素材池里随机挑一条。"""
    return pool[generator.randrange(len(pool))]


def _name_phrase(symbol_name: str) -> str:
    """标的的说法：有名就带上书名号，没名就统称「它」。"""
    text = str(symbol_name or "").strip()
    return f"「{text}」" if text else "它"


def _action_verb(action: str) -> str:
    """玩家这次的动作（只用于句子，不带任何数值）。"""
    text = str(action or "").strip().lower()
    if text in ("sell", "卖出", "s", "sell_all"):
        return "卖出"
    if text in ("buy", "买入", "b", "buy_all"):
        return "买入"
    return "操作"


def _say(template: str, **values: str) -> str:
    """把模板里的 ``{name}`` / ``{act}`` 换成本次的说法。

    刻意**不用** ``.format()`` / ``.replace()`` ——
    插件层不许自己展开占位符（那两招在 ``meow.py`` 里是被守卫盯着的）。
    """
    out = template
    for key, value in values.items():
        marker = "{" + key + "}"
        if marker in out:
            head, _, tail = out.partition(marker)
            out = head + value + tail
    return out


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------


def build_duo_praise(
    *,
    symbol_name: str = "",
    action: str = "",
    rng: random.Random | None = None,
) -> str:
    """吹捧一句（玩家赚钱时）。

    纯素材：**不含**价格 / 涨跌幅 / 消息来源 —— 那些只能由系统事实喂进来。

    ``action`` 可选：给了就知道他这手是买还是卖；不给就统称「操作」。
    """
    generator = _generator(rng)
    values = {"name": _name_phrase(symbol_name), "act": _action_verb(action)}
    return _say(_pick(_PRAISE_POOL, generator), **values)


def build_duo_taunt(
    *,
    symbol_name: str = "",
    action: str = "",
    rng: random.Random | None = None,
) -> str:
    """嘲笑一句（玩家亏钱时）。

    嘴毒但**不脏**、**不做人身攻击** —— 只嘲操作，不嘲人。

    ``action`` 可选：给了就知道他这手是买还是卖；不给就统称「操作」。
    """
    generator = _generator(rng)
    values = {"name": _name_phrase(symbol_name), "act": _action_verb(action)}
    return _say(_pick(_TAUNT_POOL, generator), **values)


def build_duo_tip(
    *,
    symbol_name: str = "",
    talk_level: str = "normal",
    rng: random.Random | None = None,
) -> str:
    """一条「云玩家内参」（**猜测 / 赌**的口吻）。

    安静档返回**空串**（安静档不吐素材）—— 与点评同一套口径。
    """
    if _level(talk_level) == "quiet":
        return ""
    generator = _generator(rng)
    return _say(_pick(_TIP_POOL, generator), name=_name_phrase(symbol_name))


def build_duo_chatter(
    *,
    symbol_name: str = "",
    rng: random.Random | None = None,
) -> str:
    """话痨档的**补刀**（多输出的一句）。"""
    generator = _generator(rng)
    return _say(_pick(_CHATTER_POOL, generator), name=_name_phrase(symbol_name))


def build_duo_commentary(
    *,
    action: str,
    pnl: float,
    symbol_name: str = "",
    talk_level: str = "normal",
    rng: random.Random | None = None,
) -> str:
    """一条**短**点评（1~3 句）。

    * 赚钱（``pnl > 0``）⇒ **吹捧**；
    * 亏钱（``pnl < 0``）⇒ **嘲笑**；
    * 没赚没亏（``pnl == 0``）⇒ **中立点评**。

    安静档返回**空串**；话痨档比正常档**多一句**。
    """
    level = _level(talk_level)
    if level == "quiet":
        return ""
    generator = _generator(rng)
    values = {"name": _name_phrase(symbol_name), "act": _action_verb(action)}
    profit = float(pnl)
    if profit > 0.0:
        primary = _say(_pick(_PRAISE_POOL, generator), **values)
    elif profit < 0.0:
        primary = _say(_pick(_TAUNT_POOL, generator), **values)
    else:
        primary = _say(_pick(_NEUTRAL_POOL, generator), **values)
    if level == "chatty":
        return f"{primary} {_say(_pick(_CHATTER_POOL, generator), **values)}"
    return primary
