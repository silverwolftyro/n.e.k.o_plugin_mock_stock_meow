"""P28.0：假新闻的「活人感」与情绪爆发（**插件层**）。

这个模块只做两件事：

1. **记账** —— 记住「昨天玩家买过的报纸里有假新闻」；
2. **话术** —— 给 Yui 一条**事实陈述**（她据以开口的事实）+ 一条**愤怒素材**
   （她可以在聊天框里直接爆发的情绪）。

为什么要它
----------
玩家被骗了却**没人替他骂** —— 那股憋屈无处可去，于是市场显得"很干净"，
失真感就没了。本模块让 Yui **替玩家承担那股情绪**：报纸骗人，Yui 当场发火。

三条红线
--------
1. **不碰引擎、不碰模式层** —— 本模块不 import ``engine`` / ``modes``，
   引擎层与模式层一个字节都不改；
2. **不落库** —— 假新闻记账只存在**插件层内存**（state）里，要的是
   「活人感」，不是账本；
3. **不改任何游戏判定** —— 本模块的任何输出都只影响"Yui 说什么"，
   行情、heat、胜负、结算一律不受影响。

与 ``__init__.py`` 的接线契约（**由父代理实现，本模块不改 ``__init__.py``**）
--------------------------------------------------------------------------
::

    # 每日开盘：把当天的新闻记账（假新闻只留在插件层内存里）
    ledger = FakeNewsLedger()          # 挂在插件实例的 state 上
    ledger.record(player.day, market.news_today)

    # 次日开盘：取走昨天的假新闻
    items = ledger.consume(player.day)  # 二次调用返回 []（不会重复爆发）
    if items:
        fact = build_outrage_fact(
            day=player.day,
            fake_items=items,
            loss_amount=...,             # 玩家因此亏的钱；<=0 就中性表述
        )
        # 通过宿主 push_message 把 fact 推给 Yui（作为**事实输入**），
        # 并让 Yui 用 build_outrage_opinion(...) 的素材在聊天框里爆发：
        opinion = build_outrage_opinion(
            fake_items=items,
            talk_level=self._yui_talk_level_now(),
        )

两条话术契约
------------
* ``fact`` 是**事实**：只陈述传入的事实，不推销、不下指令、不编造。
* ``opinion`` 是**素材**：可以有情绪，但**不许**出现具体金额 / 公司名 /
  内幕等编造内容 —— 事实归事实，脾气归脾气。
"""

from __future__ import annotations

import random
from typing import Any, Iterable

__all__ = [
    "FAKE_NEWS_TYPE",
    "FakeNewsLedger",
    "OPINION_MATERIALS",
    "OPINION_MATERIALS_CHATTY",
    "OPINION_MATERIALS_NORMAL",
    "OPINION_MATERIALS_QUIET",
    "TALK_LEVEL_CHATTY",
    "TALK_LEVEL_NORMAL",
    "TALK_LEVEL_QUIET",
    "build_fake_news_outrage_payload",
    "build_outrage_fact",
    "build_outrage_opinion",
    "collect_fake_news",
    "is_fake_news_item",
]


#: 引擎 ``NewsItem.type`` 里代表**假新闻**的取值。
#:
#: 引擎的 ``type`` 取值是 ``true`` / ``fake`` / ``vague`` / ``noise`` / ``thunder``；
#: 假新闻在**插件层是可识别的**（面板侧另有脱敏逻辑，不在本模块职责内）。
FAKE_NEWS_TYPE = "fake"

#: Yui 的三档话痨程度（取值与 ``__init__.py`` 的 ``YUI_TALK_LEVELS`` 一致）。
TALK_LEVEL_QUIET = "quiet"
TALK_LEVEL_NORMAL = "normal"
TALK_LEVEL_CHATTY = "chatty"


#: 愤怒素材 · **安静档**（冷着脸说，气在字里行间）。
#:
#: 每条都必须**不依赖条数**（"混进去的消息"对一条 / 多条都成立），
#: 且**不许**出现金额、公司名、内幕等编造内容。
OPINION_MATERIALS_QUIET: tuple[str, ...] = (
    "昨天那几份报纸里混进去的消息，全是编的。",
    "昨天玩家买的那几份报纸里，有几条从头到尾没一句实话。",
    "昨天报纸上混进去的那几条假消息，写得还挺像回事。",
    "昨天那几份报纸里塞进来的假消息，一条比一条离谱。",
    "昨天报纸里混着的那几条，我一条条看过了，没有一条是真的。",
)

#: 愤怒素材 · **正常档**（标准发火，替玩家出气）。
OPINION_MATERIALS_NORMAL: tuple[str, ...] = (
    "昨天那破报社出的消息，混进去的几条全是编的，也好意思印出来卖钱。",
    "玩家昨天买的那几份报纸里混着假消息，写得跟真的一样，气得我直跳脚。",
    "又是昨天那份报纸！假消息一条接一条地塞进来，这不是耍玩家吗。",
    "昨天报纸里那几条假消息编得有鼻子有眼，读者要是照着它下单，那才是真被牵着走。",
    "那几家报社昨天的纸我一页都看不下去，几条消息纯粹是现编出来凑版面的。",
)

#: 愤怒素材 · **话痨档**（更激动，连珠炮似的往外蹦）。
OPINION_MATERIALS_CHATTY: tuple[str, ...] = (
    "昨天那破报社！假消息一条一条往里塞，还写得有鼻子有眼的，谁受得了这个！",
    "气坏我了 —— 昨天玩家买的那几份报纸里混着好几条编的消息，收钱的时候倒是挺积极！",
    "又是报纸！昨天那几张里头塞的假消息写得比真的还像，玩家能不气吗！",
    "我昨天就说了那几份报纸不能信，结果里头真混着假的，气得我半天没缓过来！",
    "昨天那批报纸混进去的假消息一条都没放过，编故事这种事真是既不缺德也不长记性！",
)

#: 话痨档位 → 素材池。
OPINION_MATERIALS: dict[str, tuple[str, ...]] = {
    TALK_LEVEL_QUIET: OPINION_MATERIALS_QUIET,
    TALK_LEVEL_NORMAL: OPINION_MATERIALS_NORMAL,
    TALK_LEVEL_CHATTY: OPINION_MATERIALS_CHATTY,
}

#: 缺字段的哨兵（区分"属性不存在"与"属性存在但取值为 None"）。
_MISSING = object()

#: 事实里点名的标的最多列几个（再多就不点名了，免得变成流水账）。
_MAX_NAMED_TARGETS = 3


def is_fake_news_item(item: Any) -> bool:
    """判断一条新闻是不是**假新闻**。

    判定口径：取得到 ``type`` 属性，且它的值（去首尾空白、忽略大小写）
    等于 ``"fake"``。

    * 取不到属性 ⇒ ``False``；
    * **任何异常都吞掉** ⇒ ``False``（这函数绝不让它把调用方带崩）。
    """
    try:
        raw = getattr(item, "type", _MISSING)
    except Exception:
        return False
    if raw is _MISSING or raw is None:
        return False
    try:
        text = str(raw).strip().lower()
    except Exception:
        return False
    return text == FAKE_NEWS_TYPE


def _read_attr(item: Any, name: str) -> tuple[bool, Any]:
    """读一个属性，返回 ``(是否读成功, 值)``。

    * 属性**不存在** ⇒ ``(True, None)``（调用方按"缺失字段用 None"处理）；
    * 属性读取**抛异常** ⇒ ``(False, None)``（调用方把整条跳过）。
    """
    try:
        value = getattr(item, name, _MISSING)
    except Exception:
        return False, None
    if value is _MISSING:
        return True, None
    return True, value


def _read_field(entry: Any, name: str) -> Any:
    """读一条记录上的字段 —— 字典与对象都认。

    （契约里 ``fake_items`` 是 ``list[dict]``，这里顺带兼容直接传引擎对象。）
    """
    try:
        if isinstance(entry, dict):
            return entry.get(name)
        return getattr(entry, name, None)
    except Exception:
        return None


def collect_fake_news(items: Iterable[Any]) -> list[dict]:
    """从引擎当天新闻里挑出**假新闻**。

    返回 ``[{"id", "day", "stock_id", "direction", "content"}, ...]``，
    保持原始顺序；**缺失的字段用 ``None``**。

    * 属性读取**抛异常**的那一条 ⇒ 跳过（不拖垮整批）；
    * ``items`` 本身不是可迭代对象 ⇒ 返回空列表；
    * **整体绝不抛错**。
    """
    rows: list[dict] = []
    if items is None:
        return rows
    try:
        iterator = iter(items)
    except TypeError:
        return rows

    while True:
        try:
            item = next(iterator)
        except StopIteration:
            break
        except Exception:
            break

        try:
            if not is_fake_news_item(item):
                continue
            fields: dict[str, Any] = {}
            broken = False
            for name in ("id", "day", "stock_id", "direction", "content"):
                ok, value = _read_attr(item, name)
                if not ok:
                    broken = True
                    break
                fields[name] = value
            if broken:
                continue
            rows.append(fields)
        except Exception:
            continue
    return rows


class FakeNewsLedger:
    """按天记账：记录某天的假新闻；次日开盘时取出并清空。

    **只存内存**（插件层 state），不落库 —— 要的是「活人感」，不是账本。

    语义：

    * ``record(day, items)`` —— 记录该天的假新闻并返回条数；
      **同一天重复调用是覆盖，不是累加**；
    * ``pending_for(day)`` —— 取「昨天」（``day - 1``）记的那批，**不消费**；
    * ``consume(day)`` —— 取走「昨天」那批并**清掉**（二次调用返回空列表）；
    * ``clear()`` —— 清空全部。
    """

    def __init__(self) -> None:
        self._by_day: dict[int, list[dict[str, Any]]] = {}

    def record(self, day: int, items: Iterable[Any]) -> int:
        """记下 ``day`` 那天的假新闻，返回记录条数。

        同一天重复调用 ⇒ **覆盖**（后一次说了算）。
        """
        key = _as_day(day)
        if key is None:
            return 0
        collected = collect_fake_news(items)
        if collected:
            self._by_day[key] = collected
        else:
            # 那天一条假的都没有 ⇒ 覆盖成"空"（把上一版也抹掉）。
            self._by_day.pop(key, None)
        return len(collected)

    def pending_for(self, day: int) -> list[dict]:
        """「昨天」（``day - 1``）记下的假新闻 —— **不消费**。"""
        key = _as_day(day)
        if key is None:
            return []
        return _copy_entries(self._by_day.get(key - 1, []))

    def consume(self, day: int) -> list[dict]:
        """取走「昨天」（``day - 1``）记下的假新闻，并**清掉**。

        二次调用返回空列表 ⇒ 同一批假新闻不会爆发两次。
        """
        key = _as_day(day)
        if key is None:
            return []
        return _copy_entries(self._by_day.pop(key - 1, []))

    def clear(self) -> None:
        """清空全部记账。"""
        self._by_day.clear()


def _as_day(day: Any) -> int | None:
    """把 ``day`` 规整成 ``int``；认不得就返回 ``None``。"""
    try:
        return int(day)
    except (TypeError, ValueError):
        return None


def _copy_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """复制一份（调用方拿到的列表改了也污染不到内部记账）。"""
    copied: list[dict[str, Any]] = []
    try:
        for entry in entries or []:
            copied.append(dict(entry))
    except Exception:
        return copied
    return copied


def _describe_targets(fake_items: list[dict]) -> str:
    """把假新闻涉及的标的拼成一句短语（用 ``stock_id``）。

    没有 ``stock_id`` 时写「某只票」；最多点名三个，再多就不点名了。
    """
    ids: list[str] = []
    has_unknown = False
    for entry in fake_items or []:
        raw = _read_field(entry, "stock_id")
        text = "" if raw is None else str(raw).strip()
        if not text:
            has_unknown = True
            continue
        if text not in ids:
            ids.append(text)

    if not ids:
        return "涉及某只票"

    shown = ids[:_MAX_NAMED_TARGETS]
    parts = list(shown)
    if has_unknown:
        parts.append("某只票")
    suffix = " 等标的" if len(ids) > len(shown) else ""
    return "涉及 " + "、".join(parts) + suffix


def build_outrage_fact(
    *,
    day: int,
    fake_items: list[dict],
    loss_amount: float = 0.0,
) -> str:
    """给 Yui 的**事实陈述**（一行中文）。

    只陈述传入的事实：昨天玩家买过的报纸里有几条是假的、涉及哪些标的、
    玩家因此亏了多少钱。

    * ``loss_amount > 0`` ⇒ 报出金额；
    * ``loss_amount <= 0`` ⇒ 改成中性表述（「行情被带偏」），
      **绝不硬编金额**；
    * **不许**推销、不许下指令、不许编造传入事实之外的任何细节。

    ``day`` 是「今天是第几天」—— 文案里统一说「昨天」，所以这个值只做
    防御性规整（接线对称用）。
    """
    _as_day(day)
    items = list(fake_items or [])
    count = len(items)
    if count <= 0:
        return "昨天玩家买过的报纸里没有出现假新闻。"

    try:
        loss = float(loss_amount)
    except (TypeError, ValueError):
        loss = 0.0

    targets = _describe_targets(items)
    if loss > 0.0:
        tail = f"玩家因此亏了 {loss:.2f} 元。"
    else:
        tail = "这些消息让行情被带偏了。"
    return f"昨天玩家买过的报纸里，有 {count} 条新闻是假的，{targets}；{tail}"


def _pool_for(talk_level: str) -> tuple[str, ...]:
    """按话痨档位取素材池（认不得就按「正常」档）。"""
    key = str(talk_level or "").strip().lower()
    return OPINION_MATERIALS.get(key, OPINION_MATERIALS_NORMAL)


def build_outrage_opinion(
    *,
    fake_items: list[dict],
    talk_level: str = "normal",
    rng: random.Random | None = None,
) -> str:
    """给 Yui 的**愤怒素材**（1~2 句，可直接进聊天框）。

    * 表达「昨天那破报社混进来的消息是编的」这类情绪；
    * **不许**出现具体金额 / 公司名 / 内幕 —— 那是编造，事实归事实；
    * ``rng`` 可选；不传就**新建**一个 ``random.Random()`` 实例
      （绝不动全局随机源）。
    """
    pool = _pool_for(talk_level)
    generator = rng if rng is not None else random.Random()
    try:
        return generator.choice(list(pool))
    except Exception:
        return pool[0]


def build_fake_news_outrage_payload(
    *,
    day: int,
    fake_items: list[dict],
    loss_amount: float = 0.0,
    talk_level: str = "normal",
    rng: random.Random | None = None,
) -> dict:
    """一次拿到 ``{"fact": ..., "opinion": ...}``。

    * ``fact`` —— 给 Yui 当**事实输入**（她据此开口）；
    * ``opinion`` —— 她可以采用的**愤怒素材**（她据此爆发）。
    """
    return {
        "fact": build_outrage_fact(
            day=day,
            fake_items=fake_items,
            loss_amount=loss_amount,
        ),
        "opinion": build_outrage_opinion(
            fake_items=fake_items,
            talk_level=talk_level,
            rng=rng,
        ),
    }
