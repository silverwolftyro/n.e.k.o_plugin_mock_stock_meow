"""P27.0：极简做空账本（**插件层**）。

这个模块只做**算术**：开仓、逐拍盯市、爆仓平仓、算 Yui 的对手曲线。
它不认识数据库、不认识宿主、不认识玩家对象 —— 那些都在 ``__init__.py`` 里。

口径（用户口径的直译）：

* 玩家选一个**杠杆倍率**（1x / 10x / 50x）并投入一笔**保证金**；
* 标的下跌 ⇒ 玩家赚「跌幅 × 倍率 × 保证金」；上涨 ⇒ 相应扣除；
* **保证金亏光 ⇒ 强制平仓（爆仓）**，亏损以保证金为上限。

⚠ **红线**：引擎层与模式层一行不改 ⇒ 500 局基线逐字节不变。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: 可选的杠杆倍率（面板上就这三个）。
SHORT_LEVERAGES: tuple[int, ...] = (1, 10, 50)

#: 默认倍率（面板初始选中的那个）。
DEFAULT_SHORT_LEVERAGE: int = 10

#: 同时最多持有几个空头（防止玩法变成"满屏做空"）。
SHORT_MAX_POSITIONS: int = 4

#: 保证金下限（低于这个数没有意义，面板也不会放行）。
SHORT_MIN_MARGIN: float = 1.0

#: Yui 对手曲线：前 N 拍"极差"（几乎不赚）。
YUI_OPPONENT_FLAT_TICKS: int = 10

#: Yui 对手曲线：前 N 拍每一拍往下走的比例（1% 一拍，够难看）。
YUI_OPPONENT_DROP_RATE: float = 0.01

#: Yui 对手曲线：进入上升段后，每拍至少涨这么多（相对起点）。
YUI_OPPONENT_MIN_GAIN_RATE: float = 0.005

#: Yui 对手曲线：最终要比玩家高出这么多（相对玩家末值的比例）。
YUI_OPPONENT_LEAD_RATE: float = 0.02

#: Yui 对手曲线：最终至少要比玩家高出这么多（绝对值）。
YUI_OPPONENT_LEAD_FLOOR: float = 1.0

#: Yui 对手曲线：波动段的**锯齿幅度**（相对起点资产的百分比，一拍的最大偏移）。
YUI_OPPONENT_VOLATILITY: float = 0.018

#: Yui 对手曲线：伪随机游走的**固定种子**。
YUI_OPPONENT_WALK_SEED: int = 20240711


def _yui_walk_jitter(seed: int, index: int) -> float:
    """确定性伪随机数（落在 ``-1 ~ 1``）。

    为什么不用 ``random``：这条曲线**每一拍都会重算一遍**（面板每次刷新
    都要重新取载荷），用真随机会让折线图自己乱抖 —— 玩家看到的就成了
    「Yui 的资产在跳」，而不是「Yui 的资产有波动」。
    同一个 ``(seed, index)`` 永远算出同一个值 ⇒ 曲线**形状稳定**，
    只有「又长了一拍」这种真实变化才会改变它。
    """
    mask = 0xFFFFFFFFFFFFFFFF
    value = (seed * 6364136223846793005 + (index + 1) * 1442695040888963407) & mask
    value ^= value >> 33
    value = (value * 0xFF51AFD7ED558CCD) & mask
    value ^= value >> 33
    value = (value * 0xC4CEB9FE1A85EC53) & mask
    value ^= value >> 33
    return (value / mask) * 2.0 - 1.0


@dataclass(frozen=True)
class ShortPosition:
    """一笔空头仓位。

    ⚠ ``entry_price`` 是**滚动**的：每一拍结算完就把锚点挪到当前价，
    于是下一拍算的是"这一拍"的涨跌，而不是"从开仓算到现在"的涨跌
    （后者会重复计入已经入账的盈亏）。
    """

    stock_id: str
    margin: float
    leverage: int
    entry_price: float
    opened_tick: int
    margin_left: float

    @property
    def notional(self) -> float:
        """名义本金 = 保证金 × 倍率。"""
        return float(self.margin) * float(self.leverage)


@dataclass(frozen=True)
class ShortMark:
    """某一拍盯市之后的结果。"""

    stock_id: str
    price: float
    pnl: float
    margin_left: float
    liquidated: bool


def normalize_leverage(raw: object) -> int | None:
    """把面板传来的倍率规整成 ``SHORT_LEVERAGES`` 里的一个。

    认不得就返回 ``None``（调用方据此报错）—— **不做静默兜底**，
    免得玩家以为按 10x 开的仓其实是别的倍率。
    """
    try:
        value = int(float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value in SHORT_LEVERAGES:
        return value
    return None


def short_pnl(position: ShortPosition, price: float) -> float:
    """这一拍的浮动盈亏。

    ``跌幅 × 倍率 × 保证金`` —— 标的跌了为正（玩家赚），涨了为负。
    """
    entry = float(position.entry_price)
    if entry <= 0.0:
        return 0.0
    try:
        current = float(price)
    except (TypeError, ValueError):
        return 0.0
    return (entry - current) / entry * position.notional


def mark_short(position: ShortPosition, price: float) -> ShortMark:
    """盯市一次：算出这一拍的盈亏，并判断要不要爆仓。

    **亏损以保证金为上限**：保证金归零那一刻强制平仓，
    玩家最多亏掉当初押进去的那笔保证金，不会亏成负资产。
    """
    raw_pnl = short_pnl(position, price)
    margin_left = float(position.margin_left) + raw_pnl
    if margin_left <= 0.0:
        # 只亏到保证金归零为止 —— 剩下的那点溢出直接抹掉。
        return ShortMark(
            stock_id=position.stock_id,
            price=float(price),
            pnl=-float(position.margin_left),
            margin_left=0.0,
            liquidated=True,
        )
    return ShortMark(
        stock_id=position.stock_id,
        price=float(price),
        pnl=raw_pnl,
        margin_left=margin_left,
        liquidated=False,
    )


def open_short(
    stock_id: str,
    margin: float,
    leverage: int,
    price: float,
    tick: int,
) -> ShortPosition:
    """开一笔空头。

    调用方负责先扣玩家的现金 —— 这里只产出仓位本身。
    """
    return ShortPosition(
        stock_id=str(stock_id),
        margin=float(margin),
        leverage=int(leverage),
        entry_price=float(price),
        opened_tick=int(tick),
        margin_left=float(margin),
    )


def roll_short(position: ShortPosition, price: float) -> ShortPosition:
    """把锚点挪到当前价（这一拍的盈亏已经入账了）。"""
    return replace(position, entry_price=float(price))


def liquidate_short(position: ShortPosition, price: float) -> ShortMark:
    """强制平仓（爆仓）。

    返回的 ``pnl`` 是**已经发生**的亏损（负数），调用方把它计入现金。
    """
    return mark_short(position, price)


def short_positions_to_rows(
    positions: list[ShortPosition], prices: dict[str, float]
) -> list[dict[str, object]]:
    """把仓位翻成面板用的行（**白名单字段**，一个多余字段都不给）。"""
    rows: list[dict[str, object]] = []
    for position in positions:
        price = float(prices.get(position.stock_id, position.entry_price))
        mark = mark_short(position, price)
        rows.append(
            {
                "stock_id": position.stock_id,
                "margin": round(float(position.margin), 6),
                "leverage": int(position.leverage),
                "entry_price": round(float(position.entry_price), 6),
                "price": round(price, 6),
                "pnl": round(float(mark.pnl), 6),
                "margin_left": round(float(mark.margin_left), 6),
                "liquidated": bool(mark.liquidated),
            }
        )
    return rows


def yui_opponent_curve(
    player_curve: list[float], start_equity: float
) -> list[float]:
    """Yui（内置系统猫娘）的对手资产曲线。

    口径（用户口径的直译）：

    * **前 10 拍极差** —— 几乎不赚，甚至每一拍往下走一点；
    * **10 拍之后波动上行** —— 有涨有跌（**锯齿**），但**总体**往上走；
    * **最终必定比玩家高** —— 末点一定严格高于玩家的末值。

    **P31.0：这条线现在有「心跳」。**
    以前它是「前 10 拍一条直线往下 + 之后一条斜线爬升」—— 画得太干净，
    一眼看着就假。现在进入上升段之后**每一拍都有随机涨跌幅**（锯齿状），
    像真的行情那样一跳一跳的。

    锯齿怎么做到"既能抖又不跑偏"：先把每一拍的随机偏移算出来，
    再把它们的**均值削掉** —— 抖动还在，整体趋势仍然精确地落在目标线上
    （末点严格高于玩家）。

    这条曲线是**纯表现层**的合成数据（不来自引擎、不影响任何判定），
    它的作用只是给玩家一个"有人在赢我"的对照。
    """
    values = [float(value) for value in (player_curve or [])]
    if not values:
        return []

    base = float(start_equity)
    if base <= 0.0:
        base = values[0]
    if base <= 0.0:
        base = 1.0

    count = len(values)
    flat = min(YUI_OPPONENT_FLAT_TICKS, count)
    curve: list[float] = []
    for index in range(flat):
        curve.append(base * (1.0 - YUI_OPPONENT_DROP_RATE * index))

    player_final = values[-1]
    target = max(
        player_final * (1.0 + YUI_OPPONENT_LEAD_RATE),
        player_final + YUI_OPPONENT_LEAD_FLOOR,
    )

    rise = count - flat
    if rise <= 0:
        # 还没走满 10 拍 —— 把最后一点直接顶到目标值。
        if curve:
            curve[-1] = max(curve[-1], target)
        return curve

    start_value = curve[-1]
    # ① 基准斜率：从 start_value 均匀走到 target。
    base_step = (target - start_value) / rise
    # 玩家崩了也可能出现「target 比 start_value 还低」⇒ 至少保证整体向上
    # （和旧口径一致：玩家崩了，Yui 依然在赢）。
    minimum_step = base * YUI_OPPONENT_MIN_GAIN_RATE
    if base_step < minimum_step:
        base_step = minimum_step
    # ② 锯齿扰动：每一拍一个「涨一点 / 跌一点」的随机偏移。
    amplitude = abs(base) * YUI_OPPONENT_VOLATILITY
    jitter = [
        _yui_walk_jitter(YUI_OPPONENT_WALK_SEED, index) * amplitude
        for index in range(rise)
    ]
    # ③ 把扰动的均值削掉 ⇒ 末点仍然**精确**落在目标线上（不被锯齿带偏）。
    mean = sum(jitter) / rise
    value = start_value
    for index in range(rise):
        value += base_step + (jitter[index] - mean)
        curve.append(value)

    # 保底：末点必须**严格**高于玩家的末值。
    if curve[-1] <= player_final:
        curve[-1] = player_final + max(
            YUI_OPPONENT_LEAD_FLOOR, abs(player_final) * YUI_OPPONENT_LEAD_RATE
        )
    return curve
