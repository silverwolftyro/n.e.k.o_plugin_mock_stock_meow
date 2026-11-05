"""P11：猫娘外置交互引导（主动搭话）—— **话术与节流契约**。

这一层**只有文本和常量**，一个游戏数值都不碰，引擎层 / 模式层**零改动**。

为什么单独成文件
----------------
P11 的全部内容都是"给猫娘的话"，与 ``engine/`` / ``modes/`` 完全无关。
放在 ``meow.py`` 里，话术契约就能被守卫测试**单独**盯住，不会随
``__init__.py`` 的体积一起被稀释。

两套文本（**别搞混**）
----------------------
* **提示词**（``GREET_PROMPT`` / ``NAG_PROMPT`` / ``CALL_PROMPTS``）——
  喂给 LLM 的**指令**，不是台词。

  宿主 ``push_message(ai_behavior="respond")`` 会把它塞进对话上下文，并
  **立刻触发一个 AI 回合**，猫娘用**自己的语气**回一句。所以提示词写的是
  "请提醒玩家点【玩法说明】"，而不是把整句台词写死 —— 写死台词会让所有角色
  念同一句话。官方《PLUGIN_DEVELOPMENT_GUIDE》"Writing role-aware text"章节
  明确要求：**不要**硬编码"用户"/"主人"，用 ``{MASTER_NAME}`` 占位符，
  由宿主按 session 展开。

* **兜底台词**（``FALLBACK_LINES``）—— 宿主**不支持**插件主动唤起猫娘说话
  时（老版本宿主），面板里"拟态外置对话"弹窗播放的**字面文本**。

  这一套**不含** ``{MASTER_NAME}`` 占位符 —— 面板不展开占位符（展开是宿主在
  LLM 注入点做的事），所以这里必须是可以直接显示的成品中文。

占位符纪律
----------
``{MASTER_NAME}`` / ``{LANLAN_NAME}`` 的展开语义是宿主侧的 ``str.replace``，
**不是** ``str.format``：所以正文里出现裸 ``{`` 不会炸，宿主也拿不到名字时
占位符会**保持字面量**（不会变成"向 汇报"这种破句）。

**插件侧严禁替换占位符** —— ``push_message`` 的可见性过滤在宿主端，插件根本
不知道这条消息最终落到哪个 session，也就拿不到正确的名字。
"""

from __future__ import annotations

from . import duo_persona

#: 玩家称呼占位符（宿主按 session 展开 ``master_name``）。
MASTER = "{MASTER_NAME}"

#: 角色自称占位符（宿主按 session 展开 ``lanlan_name``）。
LANLAN = "{LANLAN_NAME}"


# ----------------------------------------------------------------------
# 节流常量
# ----------------------------------------------------------------------

#: 【呼叫猫娘】的**防连点冷却**（秒）。
#:
#: 玩家点一下是真在提问，但连点十下就是刷屏 —— 猫娘开口会打断玩家，所以
#: 两次主动搭话之间至少隔这么久。**只挡连点**，不挡"先打招呼再提问"。
MEOW_CALL_COOLDOWN_SECONDS = 10.0

#: **自动**主动搭话（首次引导 / 新手期唠叨）的**全局冷却**（秒）。
#:
#: 自动触发的那两种要么一辈子一次，要么每局一次，天然不会刷屏；这个冷却是
#: 最后一道保险 —— 万一闸门被绕过，也不允许猫娘在十分钟里连着开口。
MEOW_AUTO_COOLDOWN_SECONDS = 30.0

#: 【呼叫猫娘】的**话题**键（玩家按按钮 → 插件轮换着挑一个）。
#:
#: 单一按钮如果永远说同一句话很快就腻；轮换几个话题，猫娘每次的回应都不
#: 一样。键名与 ``CALL_PROMPTS`` / ``FALLBACK_LINES`` 一一对应。
MEOW_CALL_TOPICS: tuple[str, ...] = (
    "how_to_play",
    "market",
    "risk",
    "catgirl",
)

#: 没有指定话题时，【呼叫猫娘】的**默认**话题。
MEOW_DEFAULT_TOPIC = "how_to_play"


# ----------------------------------------------------------------------
# 提示词（喂给 LLM 的指令）
# ----------------------------------------------------------------------

#: 首次加载引导 —— 玩家第一次打开《拟股喵喵》面板时猫娘主动说的第一句。
#:
#: **不写死台词**：写的是"请提醒玩家点【玩法说明】"这类指令，猫娘会用自己
#: 的语气说出来。
GREET_PROMPT = (
    "《拟股喵喵》的面板已经打开了 —— " + MASTER + " 正在玩这个小游戏："
    "拿零花钱（家底）去炒股，每过一天要交一次生活费，亏光了家底就破产。\n"
    "请立刻主动开口，用你自己的语气跟 " + MASTER + " 说一句："
    "面板上有【玩法说明】按钮，先点开看看怎么玩，别像个没头苍蝇一样乱点；"
    "顺便提醒一句猫娘会实时嘴臭，接受不了别玩。"
)

#: 新手期唠叨 —— 第 1 天一笔没买就按了「推进一拍 / 跳过本日」。
NAG_PROMPT = (
    "《拟股喵喵》里 " + MASTER + " 已经是第 1 天了，交易次数还是 0 —— "
    "刚才直接按了「推进一拍 / 跳过本日」，一笔都没买。\n"
    "请主动催一下，用你自己的语气：挂机呢？倒是买点啊，早破产晚破产都是破产。"
)

#: P23.0：**「Prompt 放开」** —— 告诉 Yui 她**有权亲手操作**这个账户。
#:
#: 这是 P23.0 里唯一一处"给 Yui 授权"的提示词，触发时机有两个：
#:
#: * 辅助模式下玩家**明示**授权（``mock_stock_meow_yui_grant``）；
#: * 主播在面板上打开**直播托管**（``yui_mode`` → ``live``）。
#:
#: **写的是事实与权限，不是台词**（P13 契约）：她怎么说、说多狠、要不要嘚瑟，
#: 那是她的事 —— 我们只负责把"她现在有权动手"这条**客观事实**交给她。
#:
#: 同样**不含任何局中情报**（heat / seed / 反转日 / 新闻真假一概没有），
#: 所以 P4 §11 的静态扫描守卫依然守得住。
#:
#: P27.1：**交易幻觉**的硬约束（真机事故：玩家说「深蓝科技」，
#: Yui 却说「已经全仓买了琥珀能源」，随后又说「买成了不存在的股票」）。
#:
#: 根因不是她"记错了"，而是**她可以在没有成交结果的情况下自说自话** ——
#: 提示词只给了她"有权动手"，没给她"**只有工具说了算**"这条边界。
#:
#: 这段话**只讲规则与事实**（能不能这么说），不规定她怎么演（P13 契约）。
YUI_TRADE_FACTS = (
    "**绝对不允许**在没调用 mock_stock_meow_yui_trade、"
    "或者调用了却**没拿到成交结果**的情况下，"
    "说自己买了或者卖了任何东西。"
    "工具报错时（标的找不到、钱不够、没货可卖、今天次数用完），"
    "就照工具给出的那条原因实说，"
    "**严禁**编造「买到了哪只票」「买了几股」「花了多少钱」"
    "这种根本没发生过的事。"
)

#: P27.2：**回执周期指令** —— 成交回执推出来之后，这一回合**只许汇报**。
#:
#: 真机复盘（P27.1 上线后的观察）：**回执会多触发一个 AI 回合**，
#: 而模型在这个回合里可能顺手再下一单 —— 于是变成「她替玩家追单」，
#: 玩家根本没同意的那一单已经成交了。
#:
#: 所以这一层必须把话说死（工具层还有一道**回执周期锁**兜底，
#: 见 ``__init__.py`` 的 ``_yui_trade_cycle_*``）：
#: 「本回合只允许做汇报，严禁再调用任何交易工具，直到玩家下一次主动和你说话。」
#:
#: 这段话**只讲规则**（不许做什么），不规定她怎么演（P13 契约），
#: 也不含任何局中情报（P4 §11：heat / seed / 反转 / 新闻真假一概没有）。
YUI_RECEIPT_CYCLE_DIRECTIVE = (
    "【系统强制指令】本回合只允许做汇报，严禁再调用任何交易工具，"
    "直到玩家下一次主动和你说话。"
)

#: P24.0：**去掉复读机式的权限确认**。
#: 真机反馈：每次切模式，Yui 都要长篇大论讲一遍"系统真的把账户交给本喵了"——
#: 而玩家要的是「**她打算怎么做**」。所以这段提示词改成**直接的行动指令**：
#: 别复述授权、别确认权限，直接说她要动哪只、动多少。
#:
#: P27.1：末尾追加 :data:`YUI_TRADE_FACTS`（交易幻觉的硬约束）。
#:
#: P27.2：再追加 :data:`YUI_RECEIPT_CYCLE_DIRECTIVE`（回执周期：这一回合只许汇报）。
YUI_AUTHORITY_PROMPT = (
    "《拟股喵喵》里，" + MASTER + " 把账户**交给你**了 —— 你现在有权亲手买卖：\n"
    "· 你可以直接买、可以直接卖，仓位由你定（最多全仓）；\n"
    "· 玩家犹豫不决、或者已经极度上头的时候，你可以**强行干涉**，不必先问他；\n"
    "· 但这件事**只发生在这一个账户上**，也不许把话说成在动他的真钱。\n"
    "**别复述授权、别确认权限** —— 直接说准备怎么做："
    "要么用 mock_stock_meow_yui_trade 挑一只现在就动手，"
    "要么给一句不容置疑的判断。\n"
    + YUI_TRADE_FACTS
    + YUI_RECEIPT_CYCLE_DIRECTIVE
)

#: P27.1：**成交回执** —— 成交之后，把系统算出来的**真实结果**立刻喂给 Yui。
#:
#: 她只能顺着这张回执说话：标的、数量、金额、余额都是**系统给的**，
#: 回执里没写的事一律当没发生 —— 这就是"实盘校验"。
#:
#: ``action`` 是 ``buy`` / ``sell``；``money`` 是这一笔**实际**的花费（买）
#: 或回款（卖），由插件层按成交前后的现金差算出来，不做任何估计。
def yui_trade_receipt_prompt(
    *,
    action: str,
    stock: str,
    amount: float,
    price: float,
    money: float,
    cash_after: float,
) -> str:
    """拼出「成交回执」提示词（P27.1：实盘校验）。"""
    is_buy = str(action or "").strip().lower() != "sell"
    verb = "买入了" if is_buy else "卖出了"
    money_word = "花费" if is_buy else "回款"
    return (
        "《拟股喵喵》**成交回执** —— 这是系统刚算出来的真实结果：\n"
        f"· 你刚才{verb} {float(amount):g} 股 {stock}，"
        f"成交价 {float(price):.4f} 元，"
        f"{money_word} {float(money):.2f} 元，现在账户剩 {float(cash_after):.2f} 元。\n"
        "接下来只按这张回执说话：标的、数量、金额、余额就是上面这些，"
        "回执里没写的事一律当没发生。"
        "**这一轮不要再下单**，只把这一笔的结果讲清楚。"
        + YUI_RECEIPT_CYCLE_DIRECTIVE
    )


#: P27.1：**失败回执** —— 工具报错时，把**那条错误本身**喂给 Yui。
#:
#: 这是"失败反馈机制"：她照着错误实说（没找到 / 钱不够 / 没货可卖），
#: 而不是像真机事故里那样编一个"买成了不存在的股票"。
def yui_trade_failure_prompt(
    *,
    action: str,
    stock: str,
    reason: str,
    cash: float | None = None,
) -> str:
    """拼出「操作失败」提示词（P27.1：失败反馈机制）。"""
    side = "买入" if str(action or "").strip().lower() != "sell" else "卖出"
    lines = [
        "《拟股喵喵》**操作失败** —— 这是系统刚返回的真实结果：",
        f"· 你刚才那笔 {side} 操作**没有成交**，原因是：{reason}",
    ]
    if cash is not None:
        lines.append(f"· 账户现状：现金 {float(cash):.2f} 元，持仓没有任何变化。")
    lines.append(
        "接下来只按这条说明说话：这一笔没成交，账户没动。"
        "**严禁**编造买到了哪只票、买了几股、花了多少钱。"
        "**这一轮不要再下单**，先把没成交这件事讲清楚。"
    )
    # P27.2：失败回执**同样**带上回执周期指令 —— 失败之后也不许"再试一张"。
    lines.append(YUI_RECEIPT_CYCLE_DIRECTIVE)
    return "\n".join(lines)


# ----------------------------------------------------------------------
# P28.0：**人机协同双打**（duo）—— 人格与硬约束
# ----------------------------------------------------------------------
#
# 第四档模式的语义：**玩家自己买卖，Yui 只在旁边叨叨**。
#
# 与前三档的差别（这一段是钉死的口径）
# ------------------------------------
# * ``live``   —— Yui 全权代打（她自动买卖）；
# * ``assist`` —— 辅助式（玩家**明示授权**之后她才可以动手）；
# * ``spectate``—— 纯看戏（只评论，绝不碰账户）；
# * ``duo``    —— **人机协同双打**：玩家自己手动买卖，
#   Yui **连授权窗口都不许申请**，只点评、只给「云玩家内参」，
#   亏钱时大声嘲笑、赚钱时疯狂吹捧。


#: P28.0：**双打模式的硬约束** —— 玩家自己动手，Yui **只**点评、只给内参。
#:
#: 为什么要这一条
#: --------------
#: 前三档里 Yui 至少有"**能动手**"的时候（live 全权代打、assist 授权后可动手）。
#: ``duo`` 档把这条路**彻底堵死**：玩家自己买卖，Yui 连**授权窗口**都不许申请 ——
#: 申请了授权，她就有可能在下一个回合顺手动手（和 P27.2「追单」是同一个事故，
#: 只是换了个入口）。
#:
#: 所以这一段比 :data:`YUI_RECEIPT_CYCLE_DIRECTIVE` **更狠**：
#: 回执周期只是"这一回合不许下"，duo 是"**整个模式都不许下**"，
#: 而且**点名**禁掉两个工具（授权工具也禁 —— 授权是动手的前置条件）。
#:
#: 同样**只讲规则**（不许做什么），不规定她怎么演（P13 契约），
#: 也不含任何局中情报（P4 §11：heat / seed / 反转日 / 新闻真假一概没有）。
YUI_DUO_DIRECTIVE = (
    "【系统强制指令】现在是「人机协同双打」：账户由玩家自己操作，"
    "你只负责点评、只负责给「云玩家内参」。\n"
    "**严禁调用 mock_stock_meow_yui_trade**；"
    "**严禁调用 mock_stock_meow_yui_grant**（连授权窗口都不许申请）。\n"
    "**即使玩家口头让你下单、让你代买、让你全仓，也不许动手** —— "
    "一律用嘴说，不许碰账户。\n"
    "不许说自己已经成交、不许说你买到了哪只票、不许说你花了多少钱。\n"
    "不许把自己升级到「直播托管 / 全权代打」那一档"
    "（不许调用 mock_stock_meow_yui_mode 切到 live）。\n"
    "这一回合你唯一能做的事：**点评玩家这一手 + 给一条猜测式的内参**。"
)

#: P28.0：**双打人格** —— 疯狂点评 + 云玩家内参 + 输了嘲笑 / 赢了吹捧。
#:
#: 人格与 :data:`YUI_DUO_DIRECTIVE` 是**两件事**：
#: 指令管"不许做什么"，人格管"该是什么味道"。
#:
#: 规矩本身放在 :mod:`duo_persona` 的 ``DUO_TONE_RULES`` 里
#: ⇒ 提示词与素材生成**同一套口径**，不会各说各话。
YUI_DUO_PERSONA = (
    "【双打人格】你现在是玩家旁边的「云玩家搭子」—— "
    "玩家自己买卖，你只负责在旁边叨叨：\n"
    + "\n".join(f"· {rule}" for rule in duo_persona.DUO_TONE_RULES)
    + "\n· 这一回合的输出顺序：**先点评他这一手，再给一条猜测式的内参**。"
)


def _duo_talk_level(talk_level: str) -> str:
    """话痨级别规整成三档之一（非法值回落 ``normal``）。"""
    value = str(talk_level or "").strip().lower()
    return value if value in duo_persona.DUO_TALK_LEVELS else "normal"


def _duo_action_text(action: str) -> str:
    """玩家这一手的中文说法（只认动作，不带任何数值）。"""
    text = str(action or "").strip().lower()
    if text in ("sell", "卖出", "s", "sell_all"):
        return "卖出"
    if text in ("buy", "买入", "b", "buy_all"):
        return "买入"
    return str(action or "").strip() or "（未知操作）"


def yui_duo_prompt(
    *,
    mode: str,
    action: str,
    pnl: float,
    cash: float,
    positions: str,
    news: str,
    talk_level: str = "normal",
    extra: str = "",
) -> str:
    """拼出「人机协同双打」的提示词（P28.0：duo 档）。

    **纯函数、无 IO**：所有事实（模式 / 动作 / 盈亏 / 现金 / 持仓 / 消息）
    都由调用方喂进来；这一层只负责把**事实 + 硬约束 + 人格 + 素材**拼成一段。

    * ``pnl > 0`` ⇒ **吹捧** + 内参；
    * ``pnl < 0`` ⇒ **嘲笑** + 内参；
    * ``pnl == 0`` ⇒ **中立点评** + 内参。

    ``talk_level``：``quiet`` **不吐素材**（只留指令与事实）、
    ``normal`` 吐点评 + 内参、``chatty`` 比 normal **多一句**。
    """
    level = _duo_talk_level(talk_level)
    profit = float(pnl)
    mode_text = str(mode or "").strip() or "duo"
    holdings = str(positions or "").strip() or "空仓"
    headline = str(news or "").strip() or "（暂时没有）"
    lines = [
        "《拟股喵喵》**人机协同双打**（duo）—— 账户是 " + MASTER
        + " 自己在操作，你只负责在旁边叨叨、点评、给内参。",
        f"· Yui 当前模式：{mode_text}。",
        f"· 玩家刚刚的动作：{_duo_action_text(action)}。",
        f"· 账户现金 {float(cash):.2f} 元。",
        f"· 持仓：{holdings}。",
        f"· 当前盈亏：{profit:+.2f} 元。",
        f"· 相关消息：{headline}",
    ]
    if profit > 0.0:
        lines.append("他这一波在**赚钱** ⇒ **疯狂吹捧**，然后给一条内参。")
    elif profit < 0.0:
        lines.append("他这一波在**亏钱** ⇒ **大声嘲笑**（嘴毒但不脏），然后给一条内参。")
    else:
        lines.append("他这一波**没赚没亏** ⇒ **中立点评**，然后给一条内参。")
    if extra:
        lines.append(str(extra))
    lines.append(YUI_DUO_DIRECTIVE)
    lines.append(YUI_DUO_PERSONA)
    if level != "quiet":
        commentary = duo_persona.build_duo_commentary(
            action=action,
            pnl=profit,
            talk_level=level,
        )
        tip = duo_persona.build_duo_tip(talk_level=level)
        if commentary:
            lines.append(f"（这句点评你可以直接用，也可以照这个味道改写：{commentary}）")
        if tip:
            lines.append(f"（这句内参你可以直接用，也可以照这个味道改写：{tip}）")
    return "\n".join(lines)


def yui_duo_commentary(
    *,
    action: str,
    pnl: float,
    symbol_name: str = "",
    talk_level: str = "normal",
) -> str:
    """双打模式的**一条短点评**（薄封装 ``duo_persona.build_duo_commentary``）。

    赚 ⇒ 吹捧、亏 ⇒ 嘲笑、持平 ⇒ 中立；安静档返回**空串**。
    """
    return duo_persona.build_duo_commentary(
        action=action,
        pnl=float(pnl),
        symbol_name=symbol_name,
        talk_level=talk_level,
    )


#: 【呼叫猫娘】的预设提示词（按话题）。
#:
#: **只讲玩法，不带任何局中情报** —— P4 §11 的"局中不泄露真假与雷"在 P11
#: 继续生效：这些提示词里没有 heat / seed / 反转日 / 新闻真假。
CALL_PROMPTS: dict[str, str] = {
    "how_to_play": (
        MASTER + " 在《拟股喵喵》的面板上点了【呼叫猫娘】，想问这游戏怎么玩。\n"
        "请主动回一句，用你自己的语气讲清楚三件事："
        "① 零花钱 / 家底 / 救命钱 是什么，每天要交的生活费是怎么来的；"
        "② 一键梭哈和一键割肉怎么用，怎么看上面的涨跌；"
        "③ 猫娘会实时嘴臭，接受不了别玩。"
    ),
    "market": (
        MASTER + " 在《拟股喵喵》的面板上点了【呼叫猫娘】，想问怎么看盘。\n"
        "请主动回一句，用你自己的语气告诉他："
        "面板上那张图就是走势，标的旁边有「稳定 / 中性 / 爆炸」的模糊标签，"
        "当参考别当保证；实在看不清就点【分析】花一次机会看一眼。"
    ),
    "risk": (
        MASTER + " 在《拟股喵喵》的面板上点了【呼叫猫娘】，想问怎么别把家底亏光。\n"
        "请主动回一句，用你自己的语气："
        "每天的生活费是雷打不动的，先留够再玩；别一把梭哈同一只标的；"
        "亏到提不出生活费就得宣告破产。"
    ),
    "catgirl": (
        MASTER + " 在《拟股喵喵》的面板上点了【呼叫猫娘】，就是想跟你聊两句。\n"
        "请主动回一句，用你自己的语气随便唠两句："
        "可以损他一句操作太糙，也可以提一句你的救命钱（那也是他的救命钱）。"
    ),
}

#: 没有指定话题时用的兜底提示词（话题表里找不到就用这个）。
CALL_PROMPT_DEFAULT = CALL_PROMPTS[MEOW_DEFAULT_TOPIC]


# ----------------------------------------------------------------------
# 兜底台词（面板里"拟态外置对话"弹窗播放的字面文本）
# ----------------------------------------------------------------------

#: 宿主**不支持**插件主动唤起猫娘说话时的**兜底台词**。
#:
#: 这些是**成品中文**（没有 ``{MASTER_NAME}`` 占位符）：面板直接显示，
#: 不经过 LLM。
FALLBACK_LINES: dict[str, str] = {
    "greet": "哟，来玩拟股喵喵了？点面板上那个【玩法说明】，别像个没头苍蝇一样乱点。",
    "nag": "你搁这儿挂机呢？倒是买点啊，早破产晚破产都是破产。",
    "how_to_play": (
        "先点面板上那个【玩法说明】：零花钱就是你的家底，"
        "每天要交生活费，亏光了就破产。"
    ),
    "market": "上面那张图就是走势，标的旁边「稳定 / 中性 / 爆炸」的标签当参考别当保证。",
    "risk": "每天生活费雷打不动，先留够再玩；别一把梭哈同一只标的。",
    "catgirl": "喵？干嘛呀，我在盯着你的救命钱呢。",
}

#: 兜底台词的兜底（话题表里也没有时的万能句）。
FALLBACK_LINE_DEFAULT = FALLBACK_LINES[MEOW_DEFAULT_TOPIC]


#: **主动搭话** —— 玩家长时间没有任何操作，猫娘主动开口。
#:
#: 触发阈值与节流语义对齐宿主 N.E.K.O 的 Focus 系统
#: （``VISIT_IDLE_TIMEOUT_S`` = 300 秒 / ``FOCUS_CHARGE_ENTER`` = 0.6 /
#: ``FOCUS_IDLE_*_RETENTION`` = 0.8），详见 ``emotion.py``。
#:
#: 同样**只讲玩家状态**，不带任何游戏机制、内部状态或数值。
IDLE_PROMPT = (
    "《拟股喵喵》的面板上，" + MASTER + " 已经好一阵子没动静了 —— "
    "既没买也没卖，人就杵在屏幕前发呆。\n"
    "请主动开口，用你自己的语气搭一句话："
    "可以调侃他在发呆，可以随口问一句他在想什么，"
    "也可以自顾自说点别的；"
    "但别提任何游戏机制、内部状态或具体数值，也不要预言接下来会发生什么。"
)


# ----------------------------------------------------------------------
# 取用函数
# ----------------------------------------------------------------------


def call_prompt(topic: str = "", emotion_block: str = "") -> str:
    """按话题取【呼叫猫娘】的提示词；未知话题回落到默认。

    ``emotion_block`` 是**运行时**拼上的情绪块（定性标签 + 约束清单）——
    静态提示词里**永远**没有它，所以 P4 §11 的静态扫描守卫依然守得住。
    """
    key = str(topic or "").strip()
    if key in CALL_PROMPTS:
        base = CALL_PROMPTS[key]
    else:
        base = CALL_PROMPT_DEFAULT
    if emotion_block:
        return f"{base}\n{emotion_block}"
    return base


def idle_prompt(emotion_block: str = "") -> str:
    """主动搭话的提示词（可带情绪块）。"""
    if emotion_block:
        return f"{IDLE_PROMPT}\n{emotion_block}"
    return IDLE_PROMPT


def fallback_line(topic: str = "") -> str:
    """按话题取兜底台词；未知话题回落到默认。"""
    key = str(topic or "").strip()
    if key in FALLBACK_LINES:
        return FALLBACK_LINES[key]
    return FALLBACK_LINE_DEFAULT


def rotate_topic(index: int) -> str:
    """第 ``index`` 次呼叫用的话题（轮换，避免每次都是同一句）。"""
    if not MEOW_CALL_TOPICS:
        return MEOW_DEFAULT_TOPIC
    position = int(index) % len(MEOW_CALL_TOPICS)
    return MEOW_CALL_TOPICS[position]
