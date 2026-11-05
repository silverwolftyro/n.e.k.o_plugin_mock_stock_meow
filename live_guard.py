"""P26.1：直播护栏（**插件层**）。

这一版解决的是"明天公开直播"的两件事：

1. **Yui 的「危险感知」（伪预知）** ——
   底层没有精确的未来推演（引擎不会告诉任何人明天涨还是跌），
   所以这里只做一件事：把**当前盘面最极端的公开数据**（波动率排名 +
   持仓集中度）整理成**事实**喂给 Yui。
   她的"未卜先知"其实是"她盯数据盯得比谁都细" ——
   至于那只票接下来几拍跌不跌，**由市场自己决定**
   （不跌也是好戏：她可以说"算你走运"）。

2. **强制续命锁（``live_god_mode``）** ——
   直播不能"几十分钟瞬间暴毙"。开着这个开关时：

   * 现金跌破生活费红线 ⇒ 强制垫付一笔
     （群友浅忆QanYi 的「天降 Token」，与 P26.0 同一套事件形状）；
   * 满仓暴跌、血量见底 ⇒ 给最大的一笔持仓安排一次「庄家洗盘」假反弹
     （**下一拍** +10%），让人还有"我还能翻盘"的错觉。

四条红线（P26.1 的设计约束，缺一不可）：

1. **引擎层 / 模式层零改动** —— 本模块只算数字、只改内存里的
   ``player.cash`` 与 ``stock.price``。
2. **默认全关** —— ``live_god_mode`` 走 ``[director]`` 段，
   必须同时 ``enabled = true`` 才生效；``config.example.toml`` 里默认 ``false``。
3. **只用公开信息** —— 波动率、持仓集中度、价格，都是玩家自己看得见的东西
   （面板上标的的"稳定/中性/爆炸"标签就是波动率的分档）；
   不碰 heat / seed / 反转日 / 新闻真假（P4 §11）。
4. **一切有硬上限** —— 垫付、假反弹、预警次数都有上限，
   不做"无限续命"（那会毁掉"永远必亏"的教学前提）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DANGER_CONCENTRATION",
    "DANGER_SIGMA",
    "DangerSignal",
    "GOD_RALLY_CONCENTRATION",
    "GOD_RALLY_LIMIT",
    "GOD_RALLY_RATE",
    "GOD_RESCUE_LIMIT",
    "INTUITION_COOLDOWN_TICKS",
    "INTUITION_MAX_WARNINGS",
    "INTUITION_SETTLE_TICKS",
    "IntuitionWatch",
    "apply_rally",
    "danger_signal_text",
    "player_options_text",
    "pick_danger_signal",
    "pick_rally_target",
    "settle_text",
]


#: 「波动率极高」的**绝对**门槛。
#:
#: 为什么不是引擎那个 ``sigma_explosive_min``（0.20）：标的的 sigma 是
#: ``uniform(0.05, 0.20)`` 抽出来的，落在 [0.05, 0.20] 里；
#: 用 0.20 当门槛 ⇒ 几乎永远不触发，"预警"就成了死代码。
#: 0.16 是这段区间里明显偏高的那一截。
DANGER_SIGMA = 0.16

#: 「集中度极度危险」的门槛（单一标的市值占持仓总市值的比例）。
DANGER_CONCENTRATION = 0.75

#: 「满仓」的门槛（假反弹的触发条件之一）。
GOD_RALLY_CONCENTRATION = 0.9

#: 一局里最多安排几次「庄家洗盘」假反弹 —— 错觉贵在稀有。
GOD_RALLY_LIMIT = 1

#: 假反弹的幅度（下一拍直接抬 10%）。
GOD_RALLY_RATE = 0.10

#: 一局里最多强制垫付几次 —— 与 P26.0 的随机救助**分开计数**，
#: 免得随机事件被"护栏"挤掉，也免得护栏变成无限续命。
GOD_RESCUE_LIMIT = 6

#: 预警之后**几拍**做一次对账（把"预警时的价格"和"现在的价格"摆一起）。
INTUITION_SETTLE_TICKS = 3

#: 同一只票的重复预警冷却（拍）。约等于一个游戏日。
INTUITION_COOLDOWN_TICKS = 12

#: 一局最多预警几次 —— 预警多了就不叫"直觉"了，叫"报丧"。
INTUITION_MAX_WARNINGS = 8


@dataclass(frozen=True)
class DangerSignal:
    """一次「危险感知」的观测结果（**插件层**自己的载荷）。

    全部字段都是**公开数据**：波动率、持仓集中度、价格、排名。
    """

    stock_id: str
    name: str
    sigma: float
    concentration: float
    sigma_rank: int
    total_symbols: int
    held: bool
    price: float
    score: int


@dataclass
class IntuitionWatch:
    """一条**已发出**的预警（用来几拍之后对账）。

    这是纯内存态：重启后不恢复（对账只是直播效果，不是游戏规则）。
    """

    stock_id: str
    name: str
    price: float
    tick: int
    settled: bool = False


def _alive_stocks(market: Any) -> list[Any]:
    """市场里还活着的标的（按 id 排序，保证确定性）。"""
    stocks = list(getattr(market, "stocks", {}).values())
    alive = [stock for stock in stocks if bool(getattr(stock, "alive", True))]
    alive.sort(key=lambda stock: str(getattr(stock, "id", "")))
    return alive


def pick_danger_signal(
    market: Any, positions: Any, prices: Any
) -> DangerSignal | None:
    """挑出**当前盘面最极端**的那只票（插件层算，引擎不动）。

    判定口径（两条，命中任意一条就算"危险"）：

    * **波动率维度** —— 该票的 sigma ≥ ``DANGER_SIGMA``；
    * **集中度维度** —— 玩家在这只票上的持仓占全部持仓的 ≥
      ``DANGER_CONCENTRATION``。

    排序：**玩家持有**的票优先（集中度对玩家才有意义），
    其次按命中条数、sigma 排名、id 依次定序 —— 全程确定性，
    不会每拍换一个答案（那样 Yui 说话会自相矛盾）。

    没有危险标的时返回 ``None``（那就什么也不说）。
    """
    alive = _alive_stocks(market)
    if not alive:
        return None

    total_symbols = len(alive)
    # sigma 排名：1 = 最高。降序排（sigma 大在前），同值按 id 定序。
    ordered = sorted(
        alive,
        key=lambda stock: (-float(getattr(stock, "sigma", 0.0) or 0.0), str(stock.id)),
    )
    rank_of = {str(stock.id): index + 1 for index, stock in enumerate(ordered)}

    # 持仓市值（用来算集中度）。
    values: dict[str, float] = {}
    total_value = 0.0
    for stock_id, position in dict(positions or {}).items():
        amount = float(getattr(position, "amount", 0.0) or 0.0)
        price = float(dict(prices or {}).get(stock_id, 0.0) or 0.0)
        value = amount * price
        values[str(stock_id)] = value
        total_value += value

    best: DangerSignal | None = None
    for stock in alive:
        stock_id = str(getattr(stock, "id", ""))
        sigma = float(getattr(stock, "sigma", 0.0) or 0.0)
        value = values.get(stock_id, 0.0)
        held = value > 0.0
        concentration = (value / total_value) if total_value > 0.0 else 0.0
        rank = rank_of.get(stock_id, total_symbols)
        sigma_hot = sigma >= DANGER_SIGMA
        conc_hot = held and concentration >= DANGER_CONCENTRATION
        if not sigma_hot and not conc_hot:
            continue
        if held:
            score = (3 if sigma_hot else 0) + (3 if conc_hot else 0)
        else:
            # 没持有的票只有"波动率"这一条线索 —— 权重低。
            score = 1 if sigma_hot else 0
        candidate = DangerSignal(
            stock_id=stock_id,
            name=str(getattr(stock, "name", "") or stock_id),
            sigma=sigma,
            concentration=concentration,
            sigma_rank=rank,
            total_symbols=total_symbols,
            held=held,
            price=float(getattr(stock, "price", 0.0) or 0.0),
            score=score,
        )
        if best is None:
            best = candidate
            continue
        if candidate.score > best.score:
            best = candidate
            continue
        if candidate.score == best.score:
            if candidate.sigma_rank < best.sigma_rank:
                best = candidate
                continue
            if candidate.sigma_rank == best.sigma_rank and candidate.stock_id < best.stock_id:
                best = candidate
    return best


def danger_signal_text(signal: DangerSignal) -> str:
    """把一次危险观测写成**给 Yui 的事实行**。

    P13 契约：这里只给**客观数据**，不写"用某种语气说"这种写法指令；
    也不出现"请"字（不要额外的祈使句）。

    （本模块的源码级扫描禁止出现 P13 的禁词 —— 所以这里也不写那几个词。）
    """
    text = f"· 盘面警示：{signal.name}（{signal.stock_id}）波动率 {signal.sigma:.2f}"
    if signal.total_symbols > 0 and signal.sigma_rank == 1:
        text += f"（全市场 {signal.total_symbols} 只里最高）"
    elif signal.total_symbols > 0 and 0 < signal.sigma_rank <= 3:
        text += f"（全市场 {signal.total_symbols} 只里排第 {signal.sigma_rank}）"
    if signal.held and signal.concentration > 0.0:
        text += f"，玩家在这只票上的持仓占 {signal.concentration * 100:.0f}%"
    return text + "。"


def settle_text(
    name: str, stock_id: str, price_now: float, price_then: float
) -> str:
    """预警几拍之后的**对账事实行**。

    只摆两个价格与涨跌 —— "跌了"还是"没跌"由数字自己说，
    Yui 拿到之后可以得意，也可以自嘲。
    """
    before = float(price_then)
    now = float(price_now)
    change = ((now - before) / before) if before > 0.0 else 0.0
    return (
        f"· 盘面更新：{name}（{stock_id}）现价 {now:.2f} 元"
        f"（预警时 {before:.2f} 元，{change * 100:+.1f}%）。"
    )


def player_options_text(mode_family: str) -> str:
    """玩家**当下真的能用**的那些操作（按模式族枚举）。

    这是一句**事实**：这些按钮就在面板上。把它摆出来而不是写"让他去借高利贷"，
    是为了守住 P13（不替 Yui 决定说什么、也不指定她怎么演）——
    接下来她会不会催、怎么催，那是她的事。

    * ``infinite`` —— 领工资（低保）、借高利贷、卖出持仓、按兵不动；
    * ``classic`` —— 卖出持仓、拒绝二道贩子的强制卖出、按兵不动
      （经典模式**没有**低保、**没有**高利贷 —— 见 ``modes/classic.py``）。
    """
    if str(mode_family) == "infinite":
        return "领取工资、借高利贷、卖出持仓、按兵不动"
    return "卖出持仓、拒绝二道贩子的强制卖出、按兵不动"


def pick_rally_target(
    positions: Any, prices: Any, market: Any
) -> tuple[str, str, float] | None:
    """挑「庄家洗盘」要拉的那只票。

    口径：**持仓市值最大**且**正在浮亏**的那一只
    （满仓的人亏在最重的那只上，错觉给得才像真的）。

    返回 ``(stock_id, name, price)``；挑不出来时返回 ``None``。
    """
    best: tuple[str, str, float] | None = None
    best_value = -1.0
    price_map = dict(prices or {})
    for stock_id, position in dict(positions or {}).items():
        amount = float(getattr(position, "amount", 0.0) or 0.0)
        if amount <= 0.0:
            continue
        price = float(price_map.get(stock_id, 0.0) or 0.0)
        avg_cost = float(getattr(position, "avg_cost", 0.0) or 0.0)
        if price <= 0.0:
            continue
        if (price - avg_cost) * amount >= 0.0:
            # 没在亏 ⇒ 不给它"翻盘"的错觉。
            continue
        value = amount * price
        stock = dict(getattr(market, "stocks", {}) or {}).get(stock_id)
        if stock is None or not bool(getattr(stock, "alive", True)):
            continue
        key = str(stock_id)
        if value > best_value or (value == best_value and best is not None and key < best[0]):
            best_value = value
            best = (
                key,
                str(getattr(stock, "name", "") or key),
                price,
            )
    return best


def apply_rally(stock: Any, rate: float = GOD_RALLY_RATE) -> float:
    """给一只票强拉一次「庄家洗盘」反弹（**下一拍**）。

    做法：把现价抬 ``rate``，并**同步 K 线最后一点** ——
    否则 K 线与现价会差 10%，观众一眼就能看出破绽。

    ``prev_price`` **不动**：它是上一拍的价格，于是涨跌幅天然显示 +10%。
    """
    old = float(getattr(stock, "price", 0.0) or 0.0)
    if old <= 0.0:
        return old
    new = round(old * (1.0 + float(rate)), 6)
    if new <= 0.0:
        return old
    stock.price = new
    kline = getattr(stock, "kline", None)
    if kline:
        try:
            last_tick = kline[-1][0]
        except (TypeError, IndexError):
            last_tick = 0
        kline[-1] = (last_tick, new)
    return new
