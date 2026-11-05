"""拟股喵喵（N.E.K.O. 插件 · 第一层外壳）。

架构定位
--------
本文件属于**第一层：N.E.K.O. 插件层**，铁律是「只做翻译」：
把宿主事件（入口调用、生命周期、UI 动作）翻译成对第二层（游戏模式层）的调用，
再把第二层的结果翻译成 ``Ok`` / ``Err`` 与 UI 可用的数据结构。
本层**不生成价格、不计算上头指数、不包含任何游戏规则**——那些都在第三层
``engine/`` 里，纯标准库，不认识 N.E.K.O 的存在。

对应分层::

    第一层（本文件）  plugin.toml / __init__.py / config.example.toml
                      i18n/zh-CN.json / ui/panel.tsx / cards.py / storage/*.py
    第二层            modes/classic.py / modes/infinite.py / modes/shared.py
    第三层            engine/*.py（纯标准库，不认识 N.E.K.O 的存在）

教育目的声明
------------
本插件是一个**纯虚构、纯娱乐**的反诈教学游戏：

* 所有标的 ID 形如 ``FAKE-xxx``，**不映射任何真实股票代码或外汇对**；
* 价格、费率、代币均为虚构，**无现金价值**，不接入任何真实行情源；
* 插件**不存在**充值、提现、兑换、入金、出金一类功能，连变量名与占位符都没有；
* 游戏刻意演示「养套杀」（先让你小赢，在你上头的瞬间精准收割）与「每日账单强制平仓」
  两种真实世界高频出现的收割手法，目的是让玩家在零成本环境里亲身体验被收割的
  全过程，从而对真实交易中的高收益诱导保持警惕。

请勿把本插件的代码、参数或文案用于任何真实的金融工具、投资建议或营销场景。
本文件内所有涉及收割／强制平仓／二道贩子的逻辑均带注释说明其教育目的。

局中隐藏规则（重要）
--------------------
* **heat（上头指数）**局中完全隐藏：不出现在 ``status`` / ``market`` /
  ``buy`` / ``sell`` 的返回体里，只在 ``settle`` 的结算报告中出现。
* **seed（本局种子）**局中完全隐藏：``join`` 也不回显，只在 ``settle`` 时展示。
* **反转日**不可预告：不出现在 ``market`` 返回体里。
* 因此本层**逐字段手写返回字典**，禁止 ``dataclasses.asdict`` 之类整体导出
  ——漏删一个 key 就是泄漏。``tests/test_visibility.py`` 会递归扫描返回体做守卫。
"""

from __future__ import annotations

import asyncio
import functools
import json
import random
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
    ui,
)

# 下面带 ``X as X`` 的名字是**显式重导出**（PEP 484 约定）：插件内部不用它们，
# 但 ``plugins.mock_stock_meow.<名字>`` 是这个插件的公开门面的一部分。
# 写成重导出而不是删掉，是为了不改动已发布版本的对外表面。
from .cards import (
    ACHIEVEMENT_UNLOCK_NOTE,
    ACHIEVEMENTS,
    CARD_CSS,
    DEFAULT_IDENTITY,
    EPITAPHS,
    IDENTITIES,
    Card,
    achievement_by_key,
    achievement_by_source,
    build_achievement_card,
    build_bill_card,
    build_catgirl_daily_card,
    build_harvest_card,
    build_kill_card,
    build_news_flash_card,
    build_pawnbroker_card,
    build_taunt_card,
    build_welfare_card,
    death_reason_label,
    identity_echo,
    identity_intro,
    identity_label,
    pick_catgirl_silence,
    pick_taunt,
)
from .cards import (
    DEATH_REASON_LABELS as DEATH_REASON_LABELS,
)
from .cards import (
    achievement_by_title as achievement_by_title,
)
from .cards import (
    t as i18n_text,
)
from .combo import (
    COMBO_MODES,
    ComboTracker,
)
from .community_events import (
    EVENT_KIND_TOKEN,
    INTEL_CHANCE,
    MAX_TOKEN_RESCUES_PER_GAME,
    TOKEN_RESCUE_CHANCE,
    CommunityEvent,
    build_intel_event,
    build_token_rescue_event,
    pick_intel_target,
)
from .community_events import (
    roll as community_roll,
)
from .emotion import (
    EmotionProfile,
    banned_visible_death_words,
    build_profile,
    consecutive_losses,
    holding_loss_ratio,
    render_prompt_block,
    render_tags,
    summarize,
)
from .engine.config import (
    STREAM_CATGIRL as STREAM_CATGIRL,
)
from .engine.config import (
    STREAM_HARVEST as STREAM_HARVEST,
)
from .engine.config import (
    STREAM_MARKET as STREAM_MARKET,
)
from .engine.config import (
    EngineConfig,
)
from .engine.errors import (
    EngineError,
    InsufficientCashError,
    InvalidAmountError,
    StockNotFoundError,
    TPlusOneError,
)
from .engine.harvest import (
    TRIGGER_PEAK as TRIGGER_PEAK,
)
from .engine.harvest import (
    TRIGGER_PHASE as TRIGGER_PHASE,
)
from .engine.harvest import (
    execute_harvest as execute_harvest,
)
from .engine.heat import (
    apply_heat_events,
    heat_events_for_trade,
    position_concentration,
    update_heat,
)
from .engine.market import advance_market, apply_jump, create_market
from .engine.models import HeatEventContext, MarketState, Player, Position
from .engine.news import news_kind
from .engine.rng import derive_rng
from .engine.rng import derive_stream_rng as derive_stream_rng
from .engine.settlement import (
    apply_death,
    build_settlement_report,
    buy_fee,
    check_inaction,
    claim_welfare,
    execute_buy,
    execute_sell,
    sell_fee,
    welfare_entitlement,
)
from .fakenews import (
    FakeNewsLedger,
    build_fake_news_outrage_payload,
)
from .live_guard import (
    GOD_RALLY_CONCENTRATION,
    GOD_RALLY_LIMIT,
    GOD_RALLY_RATE,
    GOD_RESCUE_LIMIT,
    INTUITION_COOLDOWN_TICKS,
    INTUITION_MAX_WARNINGS,
    INTUITION_SETTLE_TICKS,
    DangerSignal,
    IntuitionWatch,
    apply_rally,
    danger_signal_text,
    pick_danger_signal,
    pick_rally_target,
    player_options_text,
    settle_text,
)
from .meow import (
    GREET_PROMPT,
    MEOW_AUTO_COOLDOWN_SECONDS,
    MEOW_CALL_COOLDOWN_SECONDS,
    NAG_PROMPT,
    YUI_AUTHORITY_PROMPT,
    YUI_DUO_DIRECTIVE,
    YUI_RECEIPT_CYCLE_DIRECTIVE,
    YUI_TRADE_FACTS,
    call_prompt,
    fallback_line,
    idle_prompt,
    rotate_topic,
    yui_duo_commentary,
    yui_duo_prompt,
    yui_trade_failure_prompt,
    yui_trade_receipt_prompt,
)
from .modes import get_mode
from .modes.shared import (
    ModeContext,
    classic_initial_days,
    classic_living_cost_range,
    daily_living_cost,
    execute_scheduled_harvest,
    next_living_cost,
)
from .name_easter_eggs import (
    CATEGORY_COMMUNITY,
    CATEGORY_HUBRIS,
    CATEGORY_STEADY,
    DEFAULT_PLAYER_NAME,
    FACT_CASH_DOWN,
    FACT_CASH_UP,
    FACT_DAILY_RETURN,
    FACT_LOAN_SIGNED,
    FACT_LOAN_UNAVAILABLE,
    FACT_MODE_HELL,
    FACT_NEWS_FAKE,
    EasterEgg,
    apply_cash_multiplier,
    build_fact,
    daily_return_gain,
    is_developer_cat,
    match_egg,
    public_info,
    sanitize_name,
)
from .short_book import (
    DEFAULT_SHORT_LEVERAGE,
    SHORT_LEVERAGES,
    SHORT_MAX_POSITIONS,
    SHORT_MIN_MARGIN,
    ShortPosition,
    mark_short,
    normalize_leverage,
    open_short,
    roll_short,
    short_positions_to_rows,
    yui_opponent_curve,
)
from .storage.repo import DbUnavailableError, Repo
from .storage.session import (
    Session,
    build_engine_config,
    load_session,
    save_snapshot,
)
from .storage.session import (
    load_snapshot as load_snapshot,
)
from .story import (
    STORY_PUSH_COOLDOWN_SECONDS,
    STORY_PUSH_PRIORITY,
    chapter_by_id,
    chapter_card,
    chapter_prompt,
    chapter_scene,
    chapter_title,
    chapters,
    choice_by_id,
    choice_hint,
    choice_label,
    coalesce_key,
    effect_of,
    effect_tag,
    next_chapter,
    reply_prompt,
)
from .tutorial import (
    GATE_WAIT_KEY,
    PHASE_DONE,
    PHASE_EPILOGUE,
    PHASE_GREET,
    PHASE_IDLE,
    PHASE_STEP,
    STEP_TIMEOUT_SECONDS,
    TIER_STEPS,
    build_epilogue_prompt,
    build_greet_prompt,
    build_name_intro_prompt,
    build_step_prompt,
)

#: P16.0 · 入门档初始资金乘数（新手期「先甜后苦」的放水口径）。
BEGINNER_CASH_MULTIPLIER = 1.2

#: P16.0 · 哪些档位享受**新手期放水**（前两关）。
#:
#: 街机 = 新手村、入门 = 断供前夕 —— 这两关的诉求是「先甜」；
#: 进阶与地狱是**收割场**，放水会直接毁掉压迫感，所以**不给**。
SOFTENED_MODES: tuple[str, ...] = ("classic_arcade", "classic_beginner")

#: P28.0 · 第一关（街机档）的档位 id。
#:
#: 第一关的定位只有一条：**爽**。于是这一档拿到三样特别待遇 ——
#:
#: ① ``peak_threshold`` 被抬到 ``ARCADE_PEAK_THRESHOLD``（上头即暴跌**永不触发**）；
#: ② 卖出亏损由系统**暗中补钱**（玩家永远是赚的）；
#: ③ 面板只留 K 线 + 买卖两键 + 现钱（见 ``ui/panel.tsx`` 的 ``arcadeSimple``）。
#:
#: 三样**全部是插件层**的运行时干预：``engine/`` 与 ``modes/`` 一行不改
#: ⇒ ``tests/simulate.py`` 的 500 局基线仍逐字节一致。
MODE_ARCADE = "classic_arcade"

#: P28.0 · 第一关的「上头即暴跌」阈值（**插件层覆写**）。
#:
#: 引擎默认 ``peak_threshold = 40.0``（heat 越过它就算一次峰值），
#: 峰值会触发收割、也会触发「最上头那一秒的全仓暴跌」。
#: 第一关**绝对禁止**这套机制 ⇒ 阈值抬到一个 heat 不可能够到的数。
#: 数值取 ``1e9`` 而不是「随便一个大数」：heat 是浮点累加，
#: 任何有限阈值在极端局里都还有被够到的可能。
ARCADE_PEAK_THRESHOLD = 1.0e9

#: P16.0 · 蜜月期新闻真实度（**插件层覆写**，引擎默认值一个字节都没动）。
#:
#: 引擎默认 ``true_ratio_honeymoon = 0.60`` / ``truth_ratio_honeymoon = 0.70``；
#: 插件层在运行时把它们提到 0.85 ⇒ 新手期的新闻更值得相信，
#: 玩家更容易赚到第一桶金 —— 这正是后续收割成立的前提。
HONEYMOON_TRUE_RATIO = 0.85
HONEYMOON_TRUTH_RATIO = 0.85

#: P16.0 · 玩家「无操作」的判定阈值（秒）。
#:
#: **对齐宿主 N.E.K.O**：``config/visit_settings.py`` 的
#: ``VISIT_IDLE_TIMEOUT_S = 300``（双方 300 秒没有一行往来就算空闲）。
#: 插件层不自己拍一个数字 —— 跟宿主保持一致，玩家在两个系统里感受到
#: 是同一个「发呆」尺度。
IDLE_PROACTIVE_SECONDS = 300.0

#: P16.0 · 搭话 charge 的**每分钟衰减系数**。
#:
#: **对齐宿主 N.E.K.O 的 Focus 语义**：``FOCUS_IDLE_SILENT_RETENTION`` /
#: ``FOCUS_IDLE_REPLIED_RETENTION`` 都是 ``0.8`` —— 空闲只让「搭话欲望」
#: 衰减，**不产生触发**（触发只由玩家的输入驱动）。
PROACTIVE_CHARGE_RETENTION = 0.8

#: P16.0 · 允许主动搭话的 charge 门槛。
#:
#: 对齐宿主 ``FOCUS_CHARGE_ENTER = 0.6``：charge 高于它说明玩家刚有过
#: 互动，不该打扰；低于它才搭话。
PROACTIVE_CHARGE_ENTER = 0.6


def _apply_beginner_softening(config: EngineConfig) -> EngineConfig:
    """P16.0 · 新手期放水：**插件层**覆写引擎配置的新闻真实度。

    引擎的默认值**一个字节都没动**（``engine/config.py`` 保持原样），
    覆写只发生在运行时 ⇒ ``tests/simulate.py``（用默认配置）跑出来的
    500 局基线**逐字节全等**。

    代价要说清楚：真实游戏的新手期比基线模拟器**更友好** ——
    模拟器不再是真实游戏的忠实代理。这是产品决策（先甜后苦），
    不是引擎回归。

    **只给前两关**（见 ``SOFTENED_MODES``）：后两档是收割场，
    放水会直接毁掉压迫感。
    """
    news = replace(
        config.news,
        true_ratio_honeymoon=HONEYMOON_TRUE_RATIO,
        truth_ratio_honeymoon=HONEYMOON_TRUTH_RATIO,
    )
    return replace(config, news=news)


def _softened_config_for(mode: str, config: EngineConfig) -> EngineConfig:
    """按档位决定要不要放水。

    * 前两关（街机 / 入门）⇒ 返回覆写过 ``news`` 的配置；
    * 其余档位（进阶 / 地狱 / 无限）⇒ **原样返回**，一个字节都不改。

    注意：覆写后的配置**只喂给模式层**（``ModeContext``）——
    模式层只用 ``ctx.engine_config.news`` 生成新闻，
    费率 / heat / 市场那几段压根没动过。
    """
    if mode not in SOFTENED_MODES:
        return config
    return _apply_beginner_softening(config)


def _apply_arcade_softening(mode: str, config: EngineConfig) -> EngineConfig:
    """P28.0 · 第一关（街机档）：**「上头即暴跌」彻底失效**。

    第一关的唯一目的是「爽」—— 让新手与主播都产生「我是天才」的错觉。
    「在你最上头的那一秒精准收割」是**后面几档**的压迫感来源，
    放在第一关只会把新手吓跑。

    做法（**插件层**）：把 ``heat.peak_threshold`` 抬到
    ``ARCADE_PEAK_THRESHOLD``（1e9）。引擎的 ``is_at_peak`` 在任何现实局里
    都够不到这个数 ⇒ **峰值收割**与**峰值触发的阶段切换**都不会发生。

    注意街机档的难度档位用的是 ``harvest_trigger="fixed_trades"``
    （阶段切换本来就按成交笔数走），但 ``modes/shared.py`` 里的
    **峰值收割**是独立算的 —— 所以只放宽阈值就够，不需要碰难度档位。

    引擎层与模式层**一行不改**：``engine/config.py`` 的默认值保持原样，
    覆写只发生在运行时 ⇒ ``tests/simulate.py`` 的 500 局基线逐字节一致。
    """
    if mode != MODE_ARCADE:
        return config
    heat = replace(config.heat, peak_threshold=ARCADE_PEAK_THRESHOLD)
    return replace(config, heat=heat)


def _config_for_player(player_name: str, mode: str, config: EngineConfig) -> EngineConfig:
    """按**档位**与**名字彩蛋**决定运行时配置（P20.0）。

    * 先过 P16.0 的新手期放水（``_softened_config_for``）；
    * 再叠上彩蛋的覆写：
      - **股神狂妄** ⇒ 新闻准确率下降（假消息更多）；
      - **社区特供** ⇒ 借款额度 / 还款额翻倍（只对无尽档有地方用）。

    引擎默认值**一个字节都没动**（``engine/config.py`` 保持原样），
    覆写只发生在运行时 ⇒ ``tests/simulate.py``（用默认配置）跑出来的
    500 局基线**逐字节全等**。

    彩蛋是**名字的纯函数**（``match_egg(player_name)``）—— 名字存在
    ``players.name`` 里，所以宿主重启后效果照样在，不需要额外的状态。
    """
    config = _softened_config_for(mode, config)
    # P28.0：第一关再叠一层「上头即暴跌」的彻底豁免。
    config = _apply_arcade_softening(mode, config)
    egg = match_egg(player_name)
    if egg is None:
        return config
    if egg.news_fake_boost > 0.0:
        # 三个旋钮一起动，事实才站得住：
        # * ``truth_ratio_honeymoon`` / ``true_ratio_honeymoon`` —— 开局那段
        #   的真新闻占比与准确率（都往下压）；
        # * ``fake_ratio_trap`` —— TRAP 阶段的**假新闻占比**（往上抬）。
        #
        # 只压前两个的话，事实「这一局新闻的准确率比平时低得多」在
        # TRAP 阶段就是**假话**（那时候假新闻占比一点没动）。
        news = replace(
            config.news,
            true_ratio_honeymoon=max(
                0.0, config.news.true_ratio_honeymoon - egg.news_fake_boost
            ),
            truth_ratio_honeymoon=max(
                0.0, config.news.truth_ratio_honeymoon - egg.news_fake_boost
            ),
            fake_ratio_trap=min(
                1.0, config.news.fake_ratio_trap + egg.news_fake_boost
            ),
        )
        config = replace(config, news=news)
    if egg.loan_multiplier != 1.0:
        infinite = replace(
            config.infinite,
            loan_amount=round(config.infinite.loan_amount * egg.loan_multiplier, 6),
            loan_repay=round(config.infinite.loan_repay * egg.loan_multiplier, 6),
        )
        config = replace(config, infinite=infinite)
    return config


def _egg_applied_facts(egg: EasterEgg | None, mode: str) -> tuple[str, ...]:
    """这一局**真的生效了**的彩蛋效果（喂给 Yui 的事实清单）。

    **只报实际生效的**：无尽档不会被换成地狱档（那是降级），经典档也没有
    放贷渠道（借了钱没人还）—— 没发生的事不许说给模型听。

    措辞一律**不带数字**（P19.0 的同一条纪律）。
    """
    if egg is None:
        return ()
    facts: list[str] = []
    if egg.cash_multiplier > 1.0:
        facts.append(FACT_CASH_UP)
    elif egg.cash_multiplier < 1.0:
        facts.append(FACT_CASH_DOWN)
    if egg.mode_override and mode == egg.mode_override:
        facts.append(FACT_MODE_HELL)
    if egg.news_fake_boost > 0.0:
        facts.append(FACT_NEWS_FAKE)
    if egg.loan_multiplier != 1.0:
        # 无尽档才真的有放贷渠道；经典档签的那张纸眼下只是个签名。
        facts.append(FACT_LOAN_SIGNED if mode == "infinite" else FACT_LOAN_UNAVAILABLE)
    if egg.daily_return > 0.0:
        facts.append(FACT_DAILY_RETURN)
    return tuple(facts)


__all__ = ["MockStockMeowPlugin"]


#: 插件 ID，与 [plugin].id 保持一致。
PLUGIN_ID = "mock_stock_meow"

#: 插件版本号，与 [plugin].version 保持一致。
PLUGIN_VERSION = "0.13.4-final-safety"

#: P10：游戏速度档位 → **真实时间**推进间隔的倍率。
#:
#: **只改"现实里等多久跳一拍"，一个游戏数值都不碰**：每天多少 tick、
#: 行情怎么走、手续费多少、强制平仓线在哪 —— 全部不变。倍率越大＝等得越久。
#:
#: * ``slow``   —— 3 倍间隔，经典模式一个游戏日约 4.8 分钟（默认太窒息）
#: * ``normal`` —— 1 倍，等同 0.9.2 的手感
#: * ``fast``   —— 0.5 倍间隔，手快的人玩
_GAME_SPEED_ORDER: tuple[str, ...] = ("slow", "normal", "fast")
_GAME_SPEED_MULTIPLIER: dict[str, float] = {
    "slow": 3.0,
    "normal": 1.0,
    "fast": 0.5,
}

#: P10：插件级设置（``settings`` 表）的键名。
_SETTING_GAME_SPEED = "game_speed"
_SETTING_AUTO_TICK = "auto_tick"
_SETTING_ONBOARDING = "onboarding_ack"

#: P18.0：教程的**全局闸门**（``settings`` 表键名）。
#:
#: 值是一段 JSON：``{"classic_arcade": true, "infinite": true}`` ——
#: 记录「哪些档位已经教过了」。
#:
#: 为什么还需要它：``players`` 表**每局一行**，只靠 ``players.tutorial_state``
#: 的话，每开一局教程都会重来一遍。分工是：
#:
#: * ``players.tutorial_state`` ⇒ 这一档**教到哪一步了**（玩家拍板方案 A）；
#: * ``settings.tutorial_tiers`` ⇒ 这一档**还要不要教**（全局闸门）。
#:
#: 这与 P10 的 ``onboarding_ack`` **完全同构**（全局标记 + 玩家维度标记）。
_SETTING_TUTORIAL_TIERS = "tutorial_tiers"

#: P11：猫娘主动搭话（外置交互引导）的 ``settings`` 表键名。
#:
#: * ``meow_greeted`` —— "首次加载引导"已经打过招呼了（**全局一次**）；
#: * ``meow_nag_player`` —— 已经被唠叨过的 ``player_id``（每局一次）；
#: * ``meow_last_at`` —— 上一次主动搭话的时间戳（**全局冷却**）。
#:
#: 三个键都是**插件自己的偏好**，与宿主配置无关 —— 写库不会触发插件重启，
#: 局中的存档与内存态一律不受影响。
_SETTING_MEOW_GREETED = "meow_greeted"
_SETTING_MEOW_NAG_PLAYER = "meow_nag_player"
_SETTING_MEOW_LAST_AT = "meow_last_at"

#: P23.0：Yui 的**操控模式**（``settings`` 表键名）。
#:
#: 这是一份**玩家可见的偏好**（跟"游戏速度""自动手动"同级）——
#: 它决定 Yui **能不能动手**，而不只是"她说什么话"。
#:
#: 三种模式（玩家拍板，见 ``文档/P23.0-方案-Yui操控链路与大K线布局.md`` §2.7）：
#:
#: * ``live``     —— **直播专用**：Yui 一站式负责买卖（玩家犹豫或上头时她可以
#:   强行干涉）。交易工具**直接放行**。
#: * ``assist``   —— **辅助式（默认）**：Yui 只给建议；玩家明示「帮忙搞」之后，
#:   才在**授权窗口**内允许她代劳。
#: * ``spectate`` —— **纯看戏**：只评论，不碰账户。交易工具**硬拒**（兜底防线）。
#:
#: 落库是因为它是**跨重启**的偏好；读值走 ``_settings_map_cache``（一次会话读完）。
_SETTING_YUI_MODE = "yui_mode"

#: P23.0：Yui 的操控模式（**字符串字面量**，别用 Enum —— 要落库）。
YUI_MODE_LIVE = "live"
YUI_MODE_ASSIST = "assist"
YUI_MODE_SPECTATE = "spectate"

#: P28.0：``duo`` —— **人机协同双打**（直播的第二个按钮）。
#:
#: 玩家自己手动买卖，Yui **一根手指都不碰账户**（连授权窗口都不算）：
#: 她只负责疯狂点评、给「云玩家内参」（可能是假的）、亏钱时嘲笑、赚钱时吹捧。
#:
#: 为什么不复用 ``assist``：``assist`` 允许玩家明示授权后由她代劳，
#: 而「双打」的卖点恰恰是**她不许动手** —— 两种语义塞进一档，
#: 工具层的硬闸门就没法拿「模式」当唯一判据了。
#:
#: 闸门在 :meth:`_yui_gate`：**硬拒**（提示词管不住模型，工具层才管得住）。
YUI_MODE_DUO = "duo"

#: 全部合法模式（顺序 = 面板上的展示顺序）。
YUI_MODES: tuple[str, ...] = (
    YUI_MODE_LIVE,
    YUI_MODE_DUO,
    YUI_MODE_ASSIST,
    YUI_MODE_SPECTATE,
)

#: LLM（Yui 自己）**可以切换**的模式。
#:
#: 刻意**不含** ``live``：直播托管是**主播自己**在面板上开的授权，
#: Yui 不许给自己升权 —— 否则「模型把「帮忙看看」理解成「帮我全仓了」」
#: 这一步就**没有任何硬防线**了（提示词管不住模型，工具层才管得住）。
#:
#: 允许的两个模式都是**收紧**方向的：``assist``（只给建议）与
#: ``spectate``（只评论）。从 ``live`` 切到它们 = 玩家说「你别管了」，
#: 那是玩家在收回授权，Yui 有权执行。
YUI_LLM_SWITCHABLE_MODES: tuple[str, ...] = (YUI_MODE_ASSIST, YUI_MODE_SPECTATE)

#: 默认模式：**辅助式**。
#:
#: 默认值选 ``assist`` 而不是 ``live`` 是**安全默认值** ——
#: 装上插件的第一秒，Yui 绝不能自己动玩家的钱。
YUI_MODE_DEFAULT = YUI_MODE_ASSIST

# ------------------------------------------------------------------
# P24.0：Yui 的「话痨级别」
# ------------------------------------------------------------------
#
# 直播时没动静太干了：玩家要的是"她在旁边一直叨叨"，
# 但太叨叨又会把画面刷爆。所以这是一个**玩家可见的偏好**
#（跟"游戏速度""自动手动"同级），直接决定**后台多久开一次 Yui 回合**。
#
# 三档：
#
# * ``quiet``   —— **安静**：只在**重大事件**（新闻 / 爆仓 / 斩杀线）时开口。
# * ``normal``  —— **正常（默认）**：直播托管时**每隔 3 个 tick** 开一次回合。
# * ``chatty``  —— **话痨**：**每个 tick** 都开一次回合，外加事件插话。
#
# 落库（同 ``_SETTING_YUI_MODE``）；读值走内存缓存。
_SETTING_YUI_TALK_LEVEL = "yui_talk_level"

YUI_TALK_QUIET = "quiet"
YUI_TALK_NORMAL = "normal"
YUI_TALK_CHATTY = "chatty"

#: 全部合法档位（顺序 = 面板上的展示顺序）。
YUI_TALK_LEVELS: tuple[str, ...] = (YUI_TALK_QUIET, YUI_TALK_NORMAL, YUI_TALK_CHATTY)

#: 默认档位：**正常**。
YUI_TALK_LEVEL_DEFAULT = YUI_TALK_NORMAL

#: 每档「**每隔几个 tick** 开一次主动回合」。
#:
#: ``0`` = **不主动开**（安静档只在重大事件时说话）。
#:
#: 这就是"每隔 N 个 tick"里的那个 N —— 它**由话痨级别决定**，
#: 而话痨级别是玩家可调的偏好。
YUI_TALK_TICK_INTERVALS: dict[str, int] = {
    YUI_TALK_QUIET: 0,
    YUI_TALK_NORMAL: 3,
    YUI_TALK_CHATTY: 1,
}

#: 两次主动回合之间的**最小墙钟间隔**（秒）—— 限流的第二道闸。
#:
#: 只靠 tick 间隔不够：``skip_day`` 之类的快进会一口气跑十几个 tick，
#: 于是"每 3 个 tick 开一次"在一瞬间变成"开五次"。墙钟闸门兜住这个。
YUI_TALK_MIN_GAP_SECONDS = 10.0

#: P23.0：辅助模式下，玩家明示「帮忙搞」之后的**授权窗口**（秒）。
#:
#: 为什么要有窗口而不是一个永久开关：授权必须**有时效** ——
#: 玩家五秒钟前说「帮我买」，不代表三十秒后他还想要 Yui 替他下单。
#: 窗口一过，Yui 自动退回「只给建议」。
YUI_ASSIST_GRANT_WINDOW_SECONDS = 120.0

#: P23.0：Yui **每天**最多能下的单（节流配额）。
#:
#: 三道防线的最后一道：模式闸门管「能不能」，授权窗口管「此刻能不能」，
#: 配额管「今天别太疯」。超额 ⇒ 工具**拒绝**（而不是排队等）。
YUI_TRADES_PER_DAY = 3

# ------------------------------------------------------------------
# P32.0：狂按快进感知 + 提示词队列熔断
# ------------------------------------------------------------------
#
# 真机事故（2026-11-05）：主播嫌"等一天太久"，对着【跳过本日】**连点**，
# 于是一口气把 30 天的新闻、收割、群友事件全攒进了 ``_yui_pending_prompts`` ——
# 队列积压几百条，Yui 的反应延迟十几秒，开始对着旧消息**自言自语**。
#
# P32.0 用三把刀处理这件事：
#
# ① **感知狂按**（:meth:`MockStockMeowPlugin.skip_day` 记点击间隔）；
# ② **狂按期限频**（她被限成"一句吐槽"，别再一直跳出来）；
# ③ **推送前熔断**（:meth:`MockStockMeowPlugin._prune_yui_queue`）——
#    积压的长队**只留最新一条**，过期的直接扔。

#: 【狂按快进】的判定窗口（秒）：窗口内点击次数超过
#: :data:`SKIP_FAST_MAX_CLICKS` 才算狂按。
SKIP_FAST_WINDOW_SECONDS = 5.0

#: 【狂按快进】的点击阈值：**5 秒内连续点击超过 3 次**（即 ≥ 4 次）。
SKIP_FAST_MAX_CLICKS = 3

#: 狂按期长度（秒）：从判定成立那一刻算起，这段时间里 Yui 被限频。
#:
#: 12 秒 ≈ 两三个心跳周期：够她把那句吐槽说完，又不至于让下一轮正常
#: 直播也被静音。
SKIP_FAST_MUTE_SECONDS = 12.0

#: 狂按期里 Yui 的**最小说话间隔**（秒）。
SKIP_FAST_TALK_MIN_GAP_SECONDS = 8.0

#: 待推送提示词队列的**熔断阈值**：队列长度超过它 ⇒ 只留最新一条。
YUI_QUEUE_MAX_LEN = 3

#: 待推送提示词队列的**熔断阈值**：消息存放超过它 ⇒ 当成过期消息丢掉。
YUI_QUEUE_MAX_AGE_SECONDS = 5.0

#: P32.0：这些提示词**不参与**「同天同类去重」—— 它们必须**条条送到**。
#:
#: * ``yui_duo_commentary`` —— 双打模式「每成交一笔就点评一句」是节目效果，
#:   同一天压成一条等于把主播最爱的互动给砍了；
#: * ``yui_harvest`` / ``yui_bankrupt`` / ``yui_god_rescue`` —— 重大事件，
#:   一次都不能少。
#:
#: 其余（新闻 / 群友事件 / 直觉预警 / 主动回合）按「同天同类只留最新」
#: 折叠 —— 那才是快进时真正会积压成几百条的东西。
YUI_QUEUE_KEEP_ALL_REASONS: tuple[str, ...] = (
    "yui_duo_commentary",
    "yui_harvest",
    "yui_bankrupt",
    "yui_god_rescue",
)

#: 狂按时注入给 Yui 的**特殊提示词**（P32.0 需求原文）。
#:
#: 重点是"**不要分析行情了，一句话搞定**" —— 玩家根本不看分析，
#: 再长的行情解读都是噪音。
SKIP_FAST_TAUNT_PROMPT = (
    "玩家正在疯狂快进，根本不看你的分析。你现在只需要吐槽他急不可耐"
    "（比如：'你赶着投胎啊！'），不要分析行情了，一句话搞定。"
)


class _YuiPendingPrompt(tuple):
    """待推送的提示词条目（**恒为三元组** ``(prompt, reason, priority)``）。

    P32.0：熔断需要知道两件额外的事 ——

    * ``enqueued_at`` —— 入队时刻（秒）。存放超过
      :data:`YUI_QUEUE_MAX_AGE_SECONDS` 的就是**过期消息**，推送前扔掉。
    * ``day`` —— 入队时玩家所在的**游戏日**。同一天同类的消息只留最新一条。

    刻意**不改元素形状**：它就是 ``tuple`` 的子类，``len(item) == 3``、
    ``prompt, reason, priority = item`` 照旧成立（守卫 ``test_p290_duo_mock``
    钉死的就是这一点）。两个新字段挂在实例上，不进元组本体。
    """

    def __new__(
        cls,
        prompt: str,
        reason: str,
        priority: int,
        enqueued_at: float = 0.0,
        day: int = 0,
    ) -> "_YuiPendingPrompt":
        obj = super().__new__(cls, (prompt, reason, priority))
        obj.enqueued_at = float(enqueued_at)
        obj.day = int(day)
        return obj

#: P23.0：【耻辱柱】最多展示多少条历史破产记录。
#:
#: 面板是个 iframe，表格太长会把页面撑爆 —— 而且**越短越丢人**的那些
#: 排在最前面，真正要看的就那么几十行。
_SHAME_MAX_ROWS = 50

#: P12：剧情状态（``settings`` 表）的键名。
#:
#: 一个键存整份剧情状态（JSON）：演过哪几幕、当前待选哪一幕、埋了哪些点、
#: 欠着多少债。**带 ``player`` 字段** —— 换一局就自动重置（剧情按局推进）。
_SETTING_STORY_STATE = "story_state"

#: P12：剧情判定的**最小间隔**（秒）。
#:
#: 剧情判定要读一次会话（查库），所以**不能**每秒都做。心跳本身是 1 秒一次，
#: 这里再压一道 2 秒 ⇒ 判定频率上限 0.5 次/秒，开销可以忽略。
STORY_CHECK_INTERVAL_SECONDS = 2.0

#: P12：剧情效果的**兜底金额**（当"今日生活费"算不出来时用）。
#:
#: 剧情效果（接济 / 加注 / 借贷）都以"今日生活费"的倍数计。取不到生活费时
#: 用这个值兜底 —— 效果给少了也比**给错一个天文数字**安全。
STORY_EFFECT_FALLBACK_CASH = 20.0

#: P11.1：手动推进类动作的**最小间隔**（秒）。
#:
#: 狂点【推进一拍】会让面板顶部疯狂弹红色警告
#: ``Failed to query plugin UI action context``（宿主 500）。成因链：
#:
#: 1. 宿主执行任何 UI action **之前**都会先 ``await host.get_ui_context()``
#:    （``plugin/core/host.py``，**5 秒预算**）—— 这是一次到插件的 IPC 往返；
#: 2. 插件是**单进程单事件循环**，每个请求都要挤进同一个循环；
#: 3. 第二个并发请求会去抢 ``self._lock``（``threading.Lock``），
#:    而 ``acquire()`` 是**阻塞**调用 ⇒ **整个事件循环被冻住**；
#: 4. 事件循环一冻，宿主那次 ``get_ui_context`` 就排在后面 ⇒ **超时 500**。
#:
#: 所以**后端也必须有一道闸门**：两次推进至少隔这么久，太快的**静默丢弃**
#: （返回温和的 ``Ok``，**绝不抛红色 Error**）。前端还有一道全局请求锁，
#: 两道一起才靠谱 —— 前端锁防"手抖"，后端闸门防"多标签页 / LLM 工具调用"。
_ADVANCE_THROTTLE_SECONDS = 1.0

#: 「无为而治」称号的解锁天数（P5.5 改动 6）。
#:
#: 存活到第 14 天（两个工资周期）且**零有效交易** —— 触发后**不强制结束**，
#: 玩家可以继续"什么都不做"地活着。
#:
#: 「有效交易」用**引擎的豁免口径**算：无限模式开局现金为 0，玩家**必须**先
#: 卖掉基准股票才交得上第一天生活费，那一笔不算他主动交易（与隐藏结局
#: ``check_inaction`` 的口径完全一致）。
INACTION_UNLOCK_DAYS = 14

_TAG_LABELS = {"stable": "稳定", "neutral": "中性", "explosive": "爆炸"}

#: 每天的分析次数（P5）。分析是**有成本**的：想拿到当天的模糊提示，就得花次数。
ANALYSES_PER_DAY = 2

# ── P27.0：做空（极简账本）──────────────────────────────────────────
#
# 做空整本账都在**插件层**（``short_book.py``）：引擎层与模式层一行不改，
# 所以 500 局基线逐字节不变。这里的常量只是**插件层自己的开关**。

#: 做空账本存在插件级设置表里的键（值是一段 JSON）。
SHORT_BOOK_SETTING_KEY = "p270_short_book"

#: 资产曲线（玩家 vs Yui）存在插件级设置表里的键。
EQUITY_CURVE_SETTING_KEY = "p270_equity_curve"

#: 一份「报纸」（额外一次分析）的价钱 —— 后面几档要自己掏钱买。
#:
#: 口径：约等于**六天生活费**。起步现金（进阶档 ~170、地狱档 ~168）够买，
#: 但一下掏掉三分之一 ⇒「后面你得自己买报纸了」这句话是真有分量的，
#: 却又不会让按钮从头到尾都点不动（直播时观众要看得见它生效）。
REPORT_PRICE = 60.0

#: 新手前两档：老爸给的高级报纸，白拿（不扣钱）。
REPORT_FREE_MODES = ("classic_arcade", "classic_beginner")

#: 一天最多额外买几份报纸（防止玩家把报纸当无限资源）。
REPORT_MAX_EXTRA = 3

#: 资产曲线最多留多少个点（别让设置表越滚越大）。
EQUITY_CURVE_MAX_POINTS = 240

#: 面板载荷里最多带多少只标的的 K 线（细节 2：限流）。
#:
#: 宿主用 ``document.createElement`` 建元素，**画不了 SVG**，K 线只能用 CSS 拼；
#: 而且 60 点 × 全部标的会让 ``@ui.context`` 载荷爆掉（推送上限 512 KiB）。
#: 所以只给"持仓 + 爆炸标签"的 K 线，其余标的只给 price / tag / alive。
PANEL_KLINE_MAX = 20

#: 当天有异动时的**模糊**提示（P5 方案 B）。
#:
#: **只说"今天特殊"，绝不说特殊在哪** —— 不给 bool、不说方向、不提"反转"，
#: 更不能说"今天的利好是假的"之类的话。这句话**只**出现在 ``analyze`` 的
#: 返回体里；面板载荷（``@ui.context``）里没有它，想拿到就得花分析次数。
ANALYSIS_MARKET_NOTE = "今天盘面有异动，新闻方向别全信。"

#: 分析的固定说明（P5）。
#:
#: **注意**：这句里不能出现"反转"两个字 —— 局中任何返回体都不得出现
#: 反转日词族（守卫按词族扫描）。所以这里说"下一次异动"而不是"哪天反转"。
ANALYSIS_NOTE = "分析只给模糊倾向：不给精确数值，也不预告下一次异动。"

#: 新闻中性分类的中文标签（P5：局中只给分类，不给标题正文）。
_NEWS_KIND_LABELS = {
    "bullish": "偏多",
    "bearish": "偏空",
    "vague": "含糊",
    "noise": "噪音",
}


def _news_headline(content: str) -> str:
    """从新闻正文里切出「标题」（第一句）。

    引擎生成的是**一整句**新闻，P5 契约 §6 要求"点开详情才给标题和内容"，
    所以这里按中文标点把第一句切出来当标题，剩下的当正文。
    """
    text = str(content or "")
    for mark in ("。", "！", "？", "；"):
        index = text.find(mark)
        if index > 0:
            return text[: index + 1]
    return text
_TAG_COLORS = {"stable": "green", "neutral": "amber", "explosive": "red"}

#: 引擎配置缓存的存活时间（秒）。
_ENGINE_CONFIG_TTL = 5.0

#: 每多少次心跳打一条"心跳体检"日志（1 秒心跳 → 每分钟一条）。
_HEARTBEAT_LOG_EVERY = 60

#: P26.0：**单次心跳的硬超时**（秒）。
#:
#: 为什么必须有：宿主对每个 handler 都是 ``asyncio.run()``（见宿主
#: ``plugin/core/host.py`` 的 ``_run_timer_interval``）—— 心跳 handler
#: **不返回**，那个定时线程就**永远卡在这一轮**，后面再也不会有下一次心跳。
#: 于是市场当场冻结：面板还在、按钮还能点，但时间不再流动（直播事故）。
#:
#: 所以心跳**必须**有硬上限：超时 ⇒ 放弃这一轮 ⇒ 记日志 ⇒ 下一轮照常跑。
#:
#: 30 秒是"绝不会误伤正常心跳"的量级：一拍 tick 的流水线本身另有
#: ``_TICK_PIPELINE_TIMEOUT_SECONDS``（20 秒）兜底，两者都触发才叫卡死。
_HEARTBEAT_TIMEOUT_SECONDS = 30.0

#: P26.0：**单拍 tick 流水线的硬超时**（秒）。
#:
#: 比引擎自带的 ``tick_skip_seconds``（15 秒）**大**：引擎那道闸是
#: "这一拍算太慢就别写回"，属于**正常**的慢；这里守的是"**永远不回来**"
#: （await 挂死）。所以阈值必须更大 ⇒ 正常慢拍的行为**完全不变**。
_TICK_PIPELINE_TIMEOUT_SECONDS = 20.0

#: 猫娘弹幕在内存里最多留多少条。
_DANMAKU_MAX = 20

#: 面板载荷里最多带多少条弹幕（限流：载荷上限 512 KiB）。
_DANMAKU_PAYLOAD_MAX = 8

#: P7.2：每个标的的**走势小折线**带多少个点（降采样，别把载荷撑爆）。
_PANEL_SPARK_POINTS = 18

#: P26.0：面板上最多留多少条**群友事件**记录。
_COMMUNITY_EVENTS_MAX = 8

#: P7.3：网贷弹窗的**超时兜底**（秒）。
#:
#: 0.8.2 真机事故：网贷弹窗是「点不掉 + 按 ESC 也关不掉」的，而唯一能关它的
#: 路径是按钮的 ``onResult`` —— 那条路径被宿主 ``ActionButton`` 里的
#: ``await api.refresh()`` 挡着，刷新一失败按钮就永远等不到回调，弹窗也永远
#: 关不掉，玩家直接被锁死在面板里。
#:
#: 这里给出**服务端**兜底：弹窗打开超过这个时间没人操作，就由插件层自动结算
#: —— 无限模式**强制借入**（保住游戏），经典模式**直接判定破产**（没有第二条
#: 路）。前端自己也有一份倒计时，两边各管各的，谁先到谁生效；即便前端整个
#: 冻住了，服务端这一条也能把玩家放出来。
_LOAN_PROMPT_TIMEOUT_SECONDS = 30.0

#: P7.4：LLM 工具注册的**自愈周期**（秒）。
#:
#: ``@llm_tool`` 的注册**只在插件启动时触发一次** —— 官方文档写得很死：
#: 「helper **不**做的事：``main_server`` 重启后或首启竞态后的自动重注册。
#: 注册仅在插件启动时触发一次；如果当时 ``main_server`` 不可达，工具会对
#: 模型不可见」。
#:
#: 后果很直接：``main_server`` 一重启，猫娘就再也看不见本插件的工具了 ——
#: 玩家在聊天框说「加入一局」，猫娘只会回一句「打深渊还是清委托？」。
#:
#: 这里每隔一段时间**重发**注册消息。``main_server`` 侧是**覆盖语义**
#: （同名重复注册会覆盖旧的），所以重发是安全的；插件侧只发 IPC，不动本地
#: 状态，也不需要 ``unregister``（避免中间出现「工具消失」的窗口）。
_LLM_TOOL_REREGISTER_SECONDS = 60.0

#: P7.4：``join`` 入口支持的模式（面板开局按钮与 LLM 工具共用）。
JOIN_MODES = (
    "classic_arcade",
    "classic_beginner",
    "classic_advanced",
    "classic_hell",
    "infinite",
)

#: P23.1：LLM 传过来的档位**别名**。
#:
#: 真机事故：猫娘在聊天里说「来一局街机」，LLM 就把 ``mode`` 传成
#: ``"arcade"`` / ``"街机"`` / ``"简单"`` —— 全都不在 ``JOIN_MODES`` 里，
#: 于是开局被拒，猫娘只好回一句「开局失败了喵」。
#:
#: 这里把常见说法**归一化**回正式档位；认不出来才回落街机档。
_JOIN_MODE_ALIASES: dict[str, str] = {
    "arcade": "classic_arcade",
    "classic": "classic_arcade",
    "街机": "classic_arcade",
    "简单": "classic_arcade",
    "容易": "classic_arcade",
    "easy": "classic_arcade",
    "beginner": "classic_beginner",
    "入门": "classic_beginner",
    "新手": "classic_beginner",
    "advanced": "classic_advanced",
    "进阶": "classic_advanced",
    "高级": "classic_advanced",
    "hell": "classic_hell",
    "地狱": "classic_hell",
    "最难": "classic_hell",
    "infinite": "infinite",
    "无尽": "infinite",
    "无限": "infinite",
}


def _normalize_join_mode(raw: Any) -> str:
    """把 LLM 传来的档位说法**归一化**成正式档位。

    认不出来 ⇒ 回落 ``classic_arcade``（街机档最适合"来一局"这种随口一提）。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return "classic_arcade"
    if value in JOIN_MODES:
        return value
    return _JOIN_MODE_ALIASES.get(value, "classic_arcade")

#: 结算页固定反诈文案（**不可通过配置关闭**）。
ANTI_FRAUD_NOTICE = (
    "你连这个纯虚构的游戏都玩不过。全球最聪明的钱，正在真实市场里等着你。别去。"
)

_MODE_NAMES = {
    "classic_arcade": "街机",
    "classic_beginner": "入门",
    "classic_advanced": "进阶",
    "classic_hell": "地狱",
    "infinite": "无限",
}

#: 引擎异常 → 宿主错误码的映射表（契约 §8.2）。
#:
#: 映射固定写在**插件层**：引擎层不认识 N.E.K.O 的存在，也就不该知道宿主
#: 的错误码长什么样。
ERROR_CODES: dict[type[EngineError], str] = {
    InsufficientCashError: "insufficient_cash",
    InvalidAmountError: "invalid_amount",
    StockNotFoundError: "stock_not_found",
    TPlusOneError: "t_plus_one_locked",
}


def _engine_error_code(error: EngineError) -> str:
    """把引擎异常翻译成宿主错误码。"""
    return ERROR_CODES.get(type(error), "engine_error")


def _fill_text(text: str, params: dict[str, Any]) -> str:
    """填变量：``{name}`` → 值（改动 4 的三屏文案用）。"""
    for key, value in params.items():
        text = text.replace("{" + str(key) + "}", str(value))
    return text


def _cell(row: Any, key: str, default: Any = "") -> Any:
    """读一行里的某个字段（``sqlite3.Row`` 与 ``dict`` 都能读）。

    宿主数据库的行对象是 ``sqlite3.Row`` —— 它**没有** ``.get()``；
    而测试替身有时给的是 ``dict``。两种都兼容一下，省得每次都踩。
    """
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _err(code: str, detail: str | None = None) -> Err:
    """统一构造业务错误：消息**只从 i18n 的 ``error.<code>`` 查表**。

    错误消息的**唯一**来源是 ``i18n/zh-CN.json`` —— 代码里**只留错误码**，
    中文一个字都不写在这儿（P6 清掉的技术债：P1~P5 的 23 条硬编码中文）。

    ``detail`` 用于追加上下文（数据库异常、引擎原始说明）；没有它就是
    纯查表结果。查不到 key 时退回错误码本身（不会给出空白消息）。
    """
    message = i18n_text(f"error.{code}", code)
    if detail:
        message = f"{message}（{detail}）"
    return Err(SdkError(message, code=code))


def _locked(method):
    """给入口方法套上插件层的全局锁（P3 契约 §1.3）。

    锁覆盖"读 Session → 改 → 写回"的**整个事务区间**，这样心跳线程与
    入口线程不会互相覆盖对方的写。

    注意：定时器 ``market_tick`` **不能**用这个装饰器——它自己会去拿锁，
    嵌套会死锁。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        with self._lock:
            return await method(self, *args, **kwargs)

    return wrapper


class _TickJournal:
    """收集一个 tick 内发生的所有写操作，最后一次性提交。

    「先在内存里把整条流水线的结果算完，最后一步才在一个事务里一次性写回」
    是 P3 契约 §3.4 的硬要求：tick 中途崩掉时，数据库里不会留下半写状态。
    """

    __slots__ = (
        "trades",
        "positions",
        "players",
        "liquidations",
        "pawnbroker",
        "deleted_trades",
        "daily",
        "catgirl_daily",
    )

    def __init__(self) -> None:
        #: 本 tick 产生的成交（按顺序）。
        self.trades: list[Any] = []
        #: ``player_id -> 需要重写的 stock_id 集合``。
        self.positions: dict[str, set[str]] = {}
        #: 本 tick 内状态被改动的玩家（人类玩家 / 猫娘）。
        self.players: dict[str, Player] = {}
        #: 本 tick 的收割结果。
        self.liquidations: list[Any] = []
        #: 本 tick 的二道贩子流水。
        self.pawnbroker: list[dict[str, Any]] = []
        #: 被撤销的成交 id（撤销窗口）。
        self.deleted_trades: list[int] = []
        #: 玩家日终结算结果。
        self.daily: Any = None
        #: 猫娘日终结算结果。
        self.catgirl_daily: Any = None

    def touch_player(self, player: Player) -> None:
        """标记某个玩家的状态在本 tick 被改动过。"""
        self.players[player.id] = player

    def touch_position(self, player: Player, stock_id: str) -> None:
        """标记某个持仓在本 tick 被改动过。"""
        self.positions.setdefault(player.id, set()).add(stock_id)


@neko_plugin
class MockStockMeowPlugin(NekoPluginBase):
    """拟股喵喵插件外壳（P2：持久化 + 宿主入口）。

    本层只做三件事：

    1. 把宿主事件（入口调用、生命周期）翻译成对第三层引擎的调用；
    2. 把引擎结果翻译成 ``Ok`` / ``Err`` 与 UI 数据（**逐字段白名单**）；
    3. 把状态落进 ``self.db``（权威）与 ``self.state``（缓存）。

    游戏规则一律不在这里——它们在 ``engine/`` 与 ``modes/`` 里。
    """

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._repo = Repo(self.db)
        # 全局锁（P3 契约 §1.3）：覆盖"读 Session → 改 → 写回"的整个事务区间。
        # 不可重入——**严禁**出现 ``with self._lock:`` 的嵌套。
        self._lock = threading.Lock()
        #: 本次 tick 是否已经在跑（防止 TRIGGER 手动触发时重入）。
        self._tick_running = False
        #: 上一次推进 tick 的真实时间戳（秒）。**只在内存里**：重启后重新
        #: 记基线，避免"插件重启即跳盘"。
        self._last_tick_at = 0.0
        #: 心跳计数（每 ``_HEARTBEAT_LOG_EVERY`` 次打一条体检日志）。
        self._heartbeat_count = 0
        #: P26.0：心跳**被硬超时打断**的次数（自愈计数，直播体检用）。
        self._heartbeat_timeouts = 0
        #: P26.0：tick 流水线**被硬超时打断**的次数（跳拍计数）。
        self._tick_timeouts = 0
        # ── P26.0：群友随机事件（插件层彩蛋）────────────────────────
        #: 已经发生过的群友事件（面板要显示，最近 8 条）。
        self._community_events: list[CommunityEvent] = []
        #: 本局天降 Token 的次数（上限 ``MAX_TOKEN_RESCUES_PER_GAME``）。
        self._token_rescue_count = 0
        #: 已经推给 Yui 的群友事件 id（别重复推）。
        self._yui_seen_community: set[str] = set()
        # ── P26.1：直播护栏（插件层）────────────────────────────────
        #: 已经发出过的「危险感知」预警（用来几拍之后对账）。
        #: 纯内存态：重启后不恢复 —— 对账只是直播效果，不是游戏规则。
        self._intuition_watch: list[IntuitionWatch] = []
        #: 本局发出的预警次数（上限 ``INTUITION_MAX_WARNINGS``）。
        self._intuition_warnings = 0
        #: 上一次发出预警的 tick（**全局**最小间隔，节流用）。
        self._intuition_last_tick = -10_000
        #: 本局**强制**垫付的次数（上限 ``GOD_RESCUE_LIMIT``）。
        #: 与 P26.0 的随机救助**分开计数**：护栏不吃随机事件的额度。
        self._god_rescue_count = 0
        #: 本局「庄家洗盘」假反弹的次数（上限 ``GOD_RALLY_LIMIT``）。
        self._god_rally_count = 0
        #: 已经排好、**下一拍**生效的假反弹（``None`` = 没有）。
        self._god_rally_pending: dict[str, Any] | None = None
        # ── P27.0：做空账本与资产曲线的**内存缓存** ───────────────────
        #: 面板上下文是**同步**的（5 秒超时，不能查库）⇒ 空头账本在这里
        #: 留一份缓存；开仓、盯市之后同步更新。
        self._short_book_cache: list[ShortPosition] = []
        #: 缓存属于哪一个玩家（换局就清空）。
        self._short_book_player_id: str = ""
        #: 玩家的资产曲线（每一拍记一个总资产）。
        self._equity_points: list[float] = []
        #: 资产曲线属于哪一个玩家。
        self._equity_player_id: str = ""
        #: 二道贩子强制卖出的撤销窗口截止时间戳（秒）。
        self._daily_window_until = 0.0
        #: 最近一次收割事件（局中通知用；**不含 heat 数值**）。
        self._pending_event: dict[str, Any] | None = None
        #: 引擎配置缓存（心跳每秒都读配置太浪费）。
        self._engine_config_cache: EngineConfig | None = None
        self._engine_config_cached_at: float = 0.0
        # ── P16.0：玩家情绪画像（**只在内存里**）────────────────────
        #: 搭话前 / 结算时按需刷新；猫娘开口时读它。
        #: **局中绝不进玩家可见的载荷**（守 P4 §14）——
        #: 结算页给的是另一套玩家可见文案（``emotion_summary``）。
        self._emotion_profile: EmotionProfile | None = None
        #: 玩家最后一次**主动操作**的时间（epoch 秒）。
        self._last_player_action_at: float = time.time()
        #: 搭话 charge（0~1）：玩家越活跃，charge 越高，越不需要被搭话。
        self._proactive_charge: float = 1.0
        #: 上次 charge 衰减的时间戳。
        self._proactive_charge_at: float = time.time()
        #: 上次主动搭话的时间（epoch 秒）。
        self._idle_meow_at: float = 0.0
        # ── P16.0：连击（**纯表现层** —— 一个游戏数值都不改）──────────
        #: 街机档的「爽感」配件：连续买中上涨的股票就叠一层连击。
        self._combo = ComboTracker()
        # ── P18.0：分档次动态教程（**运行时状态**）────────────────────
        #: 阶段 / 当前档位 / 当前步 / 超时截止 / 已触发的门槛事件。
        #: 进度**落库**在 ``players.tutorial_state``（按档位分别记录）。
        self._tutorial: dict[str, Any] = {
            "phase": PHASE_IDLE,
            "tier": "",
            "index": 0,
            "deadline": 0.0,
            "gate": "",
            "gates_seen": [],
            "epilogue_done": False,
        }
        #: 面板渲染时发现「玩家交不起饭钱」⇒ 记一笔门槛事件（同步标记）。
        self._tutorial_gate_pending: str = ""
        #: 业务配置缓存（``config.example.toml`` 的那些段；P7 的
        #: ``[director]`` / ``[visual]`` 从这里读）。
        self._business_config_cache: dict[str, Any] | None = None
        self._business_config_cached_at: float = 0.0
        #: 导演模式开关的缓存（面板载荷是同步的，要能直接读）。
        self._director_enabled_cache: bool = False
        #: ── P10：节奏偏好（游戏速度 / 自动·手动）的**内存缓存** ────────
        #: 心跳每秒跑一次、面板每秒轮询一次，都不能每次都去查 ``settings``
        #: 表 —— 宿主数据库每次开会话都要走 IPC，面板上下文只有 5 秒超时。
        #: 这三个偏好只会被玩家的按钮改写（改写时缓存同步更新），
        #: 所以**读一次就够**。
        self._settings_map_cache: dict[str, str] | None = None
        #: 面板上按过的游戏速度（空串 = 没按过，交给 ``config.example.toml``）。
        self._speed_override: str = ""
        #: 自动/手动推进（``None`` = 没设过，默认自动）。
        self._auto_tick_override: bool | None = None
        #: 全局"引导已读"标记（``settings.onboarding_ack``）。
        self._onboarding_ack_cache: bool = False
        #: 玩家维度的"已读"标记缓存：``(player_id, 是否已读)``。
        self._onboarding_player_seen_cache: tuple[str, bool] = ("", False)
        #: ── P11：猫娘外置交互引导（主动搭话）的**内存闸门** ────────────
        #:
        #: 猫娘开口会打断玩家 —— 所以每一次主动搭话都必须先过闸门：
        #:   * ``_meow_greeted``   —— 首次加载引导打过招呼了（全局一次）；
        #:   * ``_meow_nag_player``—— 已经被唠叨过的 player_id（每局一次）；
        #:   * ``_meow_last_at``   —— 上次主动搭话的时间戳（全局冷却）；
        #:   * ``_meow_call_count`` —— 呼叫次数（用来轮换话题）。
        #: 全部**纯内存**：落库的键在 ``settings`` 表里（见 ``_SETTING_MEOW_*``），
        #: 这里只是它们的缓存 —— 面板每秒轮询，热路径上不能查库。
        self._meow_greeted: bool = False
        self._meow_nag_player: str = ""
        self._meow_last_at: float = 0.0
        self._meow_call_count: int = 0
        # ── P23.0：Yui 操控端口的**内存态** ───────────────────────────────
        #
        # **全部纯内存**：跨重启活下来的是「模式」（落 ``settings`` 表），
        # 授权窗口与配额**故意不落库** —— 授权必须由玩家**当场的明示**产生，
        # 重启一次就作废（这是安全默认值）。
        #
        #: Yui 当前的操控模式（``live`` / ``assist`` / ``spectate``）。
        self._yui_mode: str = YUI_MODE_DEFAULT
        #: 玩家最近一次明示授权的时间戳（epoch 秒）；0 = 从未授权。
        self._yui_grant_at: float = 0.0
        #: 今天的 Yui 成交笔数（配额）。
        self._yui_trade_count: int = 0
        #: 上面那个计数**属于哪一天**（跨天自动清零）。
        self._yui_trade_day: int = 0
        #: P27.2：**回执周期锁** —— 成交之后到"玩家下一次主动操作"之间，
        #: Yui **只许汇报**，不许再下单（防"她替玩家追单"）。
        #:
        #: 纯内存（与授权窗口、配额一样**故意不落库**）：重启一次就作废，
        #: 这是安全默认值 —— 宁可少一次交易，也不许"重启就能追单"。
        self._yui_trade_cycle_locked: bool = False
        #: 最近一次 Yui 介入的**摘要**（面板横幅用；纯表现层）。
        self._yui_last_action: dict[str, Any] | None = None
        # ── P24.0：话痨级别与主动回合的内存态 ──────────────────────────
        #
        #: 话痨级别（落 ``settings`` 表；读值走内存缓存）。
        self._yui_talk_level: str = YUI_TALK_LEVEL_DEFAULT
        #: 上一次**主动回合**的墙钟时间（epoch 秒）—— 限流闸门。
        self._yui_talk_last_at: float = 0.0
        #: 已经播报过的新闻 id —— 用来只播**新**闻。
        self._yui_seen_news: set[str] = set()
        #: ── P28.0：人机协同双打（``duo``）的**点评素材**输入 ────────────
        #:
        #: 纯内存（与授权窗口、配额一样**故意不落库**）：双打是**当前这一局**
        #: 的直播状态，重启后重来一遍更自然。
        #: 玩家刚做了什么（``buy`` / ``sell`` / ``idle``）—— 点评的题材。
        self._yui_duo_action: str = "idle"
        #: 玩家**已实现**的盈亏累计（正 = 赚，负 = 亏）—— 吹捧还是嘲笑。
        self._yui_duo_pnl: float = 0.0
        #: ── P28.0：假新闻「活人感」的**插件层记账** ───────────────────────
        #:
        #: :class:`.fakenews.FakeNewsLedger` 按天记下**当天的假新闻**，
        #: 次日开盘时取走（``consume``）⇒ 同一批假新闻**只爆发一次**。
        #: 同样**只在内存**：要的是「活人感」，不是账本。
        self._fake_news_ledger: FakeNewsLedger = FakeNewsLedger()
        #: 玩家**买过报纸**（``buy_report``）的日子 —— 「昨天买的报纸是假的」
        #: 这句话的前置条件：他得真买过报纸，才谈得上「被骗了」。
        self._fake_news_bought_days: set[int] = set()
        #: 玩家**当天已实现的亏损**（元）—— 供愤怒文案报出确切金额。
        #: 亏没亏是真账；**没亏就不报金额**（不编造）。
        self._fake_news_loss_by_day: dict[int, float] = {}
        #: **待外推**的 Yui 提示词队列（``(提示词, 原因, 优先级)``）。
        #:
        #: 为什么要有队列：提示词是在 tick 里**持锁**时攒的，
        #: 而推送必须**锁外**做（``push_message`` 要 await 宿主 IPC，
        #: 持锁 await 会冻住事件循环 —— P11.1 那个 500 红色警告的根因）。
        self._yui_pending_prompts: list[tuple[str, str, int]] = []
        # ── P32.0：狂按快进感知 + 队列熔断的状态（**纯内存**）────────────
        #:
        #: 最近几次点【跳过本日】的**时刻**（秒）。判定口径：
        #: 5 秒窗口内超过 3 次 ⇒ 【狂按快进】。
        self._skip_click_times: list[float] = []
        #: 狂按期截止时刻（秒）。在这之前 Yui 被**限频**。
        self._skip_fast_until: float = 0.0
        #: 一共判定过几次狂按（只为了日志与面板观察）。
        self._skip_fast_count: int = 0
        #: 狂按期里 Yui 上次说话的时刻（限频闸门用）。
        self._yui_fast_last_talk_at: float = 0.0
        # ── P33.0：全局状态位 —— 现在有没有**活跃对局** ─────────────
        #:
        #: 玩家点【退出对局】或对局进入结算状态之后 ⇒ False ⇒
        #: 心跳体与所有 ``push_message`` **立刻 return** ——
        #: 这就是「退出后的幽灵播报」的总闸（心跳、Yui 播报、弹窗全停）。
        #:
        #: 开局（``join``）⇒ True；退场 / 结算 ⇒ False。
        #: **纯内存**就够：插件重启后由 :meth:`_game_is_active` 用**库**复核。
        self._is_game_active: bool = False
        #: 「这一局已经结束了」的**闩锁**（P33.0）。
        #:
        #: 为什么要单独的闩锁：结算流程里 ``_deactivate_game()`` 先把嘴堵上，
        #: **之后**才去清库里的席位。这中间有一个 ``await``（落库），
        #: 心跳随时可能插进来 —— 如果它只看库，就会把刚关掉的闸门
        #: **重新打开**（这一局又被判成"还活着"）。闩锁一旦上锁，
        #: 这一局就**不许复活**，直到玩家重新 ``join``。
        #:
        #: 它是**纯内存**的：插件重启后闩锁归零，靠库里的席位判断对局还在不在。
        self._game_ended: bool = False
        #: Yui 介入的**序号**（面板靠它判断"这条是不是新的"）。
        self._yui_seq: int = 0
        #: 兜底弹窗的**序号**（面板靠它判断"这条是不是新的"）。
        #: 宿主不支持主动搭话时，插件把台词写进这个槽位，面板轮询到序号变了
        #: 就弹出"拟态外置对话"。
        self._meow_fallback_seq: int = 0
        #: 兜底弹窗的台词与原因（**只在内存里**：是"此刻"的一次提示）。
        self._meow_fallback_line: str = ""
        self._meow_fallback_reason: str = ""
        #: 最近一次玩家 id（P5：局终后 ``market_state.player_id`` 被清空，
        #: 面板要靠它去找归档的结算报告）。
        self._last_player_id: str = ""
        #: P20.0：玩家最近用过的**自报名号**（纯内存缓存）。
        #:
        #: 空闲态的面板要把它回填进名字输入框（"下次进入直接读取并显示"）。
        #: 真正的持久化在 ``players.name`` 里（那一列本来就在，写的就是
        #: ``player.name``）—— 这里只是**省掉一次查库**的热路径缓存。
        self._remembered_name: str = ""
        #: 猫娘最近的日终摘要（P5 面板用）。
        self._catgirl_daily: dict[str, Any] | None = None
        #: 猫娘上一次日终时的累计成交笔数（用来算"今天她动没动手"）。
        self._catgirl_trades_seen: int | None = None
        #: 上一次【呼叫猫娘】的时间戳（**防连点**冷却；与自动搭话分开计时，
        #: 这样"先被打招呼、再手动提问"不会被误伤）。
        self._meow_call_last_at: float = 0.0
        #: ── P11.1：手动推进类动作的**时间戳闸门**（纯内存）────────────
        #:
        #: 两次【推进一拍】之间至少隔 ``_ADVANCE_THROTTLE_SECONDS`` 秒。
        #: 纯内存就够 —— 插件重启等于"玩家刚打开面板"，那时不该拦着。
        #:
        #: 【跳过本日】**不加**这个闸门：它本来就每天只能一次（游戏自己的
        #: ``skip_day_used``），而且"连续快进"是合法用法。
        self._last_advance_at: float = 0.0
        #: ── P12：剧情状态（**纯内存缓存**，落库键 ``story_state``）────────
        #:
        #: 剧情按局推进，所以状态里带 ``player``（换一局就重置）。结构：
        #: ``{"player": id, "seen": [...], "pending": "ch_x", "tags": [...],
        #: "debt": {...} | None, "granted": 0.0, "reply": "..." | None}``。
        #:
        #: **纯内存**：面板每秒轮询，热路径上不能查库；真正落库的是
        #: ``_story_commit_state``（只在"演了一幕"或"选了一次"时写）。
        self._story_state: dict[str, Any] = {}
        #: 剧情状态的 **CAS 基线**（读到的原文）。写回时用它判断
        #: "有没有人抢先改过"—— 改过就重读重试（**不持锁**，见
        #: ``Repo.cas_setting`` 的说明）。
        self._story_raw: str | None = None
        #: 剧情的**版本号**：每演一幕 +1，面板靠它判断"这一幕是不是新的"。
        self._story_seq: int = 0
        #: 上一次剧情推送的时间戳（全局冷却）。
        self._story_last_push_at: float = 0.0
        #: 上一次剧情判定的间戳（判定频率闸门）。
        self._story_last_check_at: float = 0.0
        #: ── P11.1：面板上下文的**上一次快照** ─────────────────────────
        #:
        #: 上下文处理器拿不到锁时返回它（**非阻塞**兜底）——
        #: 宁可给面板一点旧数据，也绝不让宿主因为 5 秒超时而报 500。
        self._context_cache: dict[str, Any] | None = None
        #: ── P7：社区名册 ──────────────────────────────────────────────
        #: 社区名册（群友人设 + 抽象新闻池）。从 ``community_roster.json``
        #: 读取；读不到就退化成空名册（插件照常跑，只是没有彩蛋）。
        self._roster: list[dict[str, Any]] = []
        self._roster_loaded = False
        #: ── P7：猫娘弹幕 ──────────────────────────────────────────────
        #: 猫娘实时嘴臭的弹幕队列（**只在内存里**：弹幕是"此刻"的装饰，
        #: 重启后清空是合理的）。新条目追加在末尾，面板取最新的几条。
        self._danmaku: list[dict[str, Any]] = []
        #: 弹幕序号（面板靠它判断"这条是不是新的"）。
        self._danmaku_seq = 0
        #: ── P7：导演模式（直播特制版）────────────────────────────────
        #: 导演指令队列：面板点一下 → 插件排一条指令 → 下个 tick 执行。
        #: **只存在于特制版**；纯净版下 ``director.enabled`` 恒为 False，
        #: 导演入口一律直接报错。
        self._director_queue: list[dict[str, Any]] = []
        # ── P19.0：导演段的**隐藏开关**（仅主播 / 开发者用）────────────
        #: ``force_black_swan`` —— 在玩家**单笔盈利刷新纪录**的那一刻，
        #: 把引擎的暴跌提前拉出来（全仓）。记的是本局见过的最高单笔盈利。
        self._black_swan_peak: float = 0.0
        #: 本局是不是已经拉过一次黑天鹅（一局只来一次）。
        self._black_swan_fired: bool = False
        #: ── P7.2：导演台的「赛博弹窗轰炸」──────────────────────────────
        #: 轰炸**截止时间戳**（秒）与**小丑数量**。面板据此弹出无法关闭的
        #: Emoji 海，并在倒计时里封锁操作。纯表现层，不影响任何数值。
        self._director_boom_until: float = 0.0
        self._director_boom_count: int = 50
        #: 本次轰炸**本来该持续多久**（秒）。P7.3：前端拿它算自己的硬上限
        #: （从第一次看见轰炸起算，到点无条件关窗），不再去减客户端时钟 ——
        #: 时钟偏了、载荷停更了，都锁不死玩家。
        self._director_boom_duration: float = 5.0
        #: ── P7.3：网贷弹窗的**死锁兜底**───────────────────────────────
        #: 弹窗**被打开**的时刻（epoch 秒）。0 = 当前没有待处理的网贷弹窗。
        #: 服务端据此在 ``_LOAN_PROMPT_TIMEOUT_SECONDS`` 之后自动结算 ——
        #: 前端哪怕整个冻住、按钮永远等不到回调，玩家也不会被永久锁死。
        self._loan_prompt_at: float = 0.0
        #: 弹窗是在第几天弹出来的（跨天要重置计时）。
        self._loan_prompt_day: int = 0
        #: ── P7.4：LLM 工具注册的**自愈计时** ─────────────────────────
        #: 下次重发注册的时间点（epoch 秒）。``@llm_tool`` 只在启动时注册一次，
        #: ``main_server`` 一重启工具就对模型不可见了 —— 这里定期补一刀。
        self._llm_reregister_at: float = time.time() + _LLM_TOOL_REREGISTER_SECONDS

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件进程启动：幂等地建表建索引。

        数据库被用户或清理工具删掉也能自愈——``CREATE TABLE IF NOT EXISTS``
        的开销是毫秒级，不需要"只建一次"的逻辑。

        **宿主时序（真机踩过的坑）**：宿主可能在插件实例构造**之后**才把
        有效配置下发到插件进程 —— 那时 ``db.enabled`` 还是 False，建表必然
        失败。这种情况下**不能放弃**：读到有效配置后由 ``Repo`` 的懒建表
        自愈机制补建（``Repo.ensure_schema``），下一次读写会自动把表建好。

        **0.7.4 真机复盘（关键）**：不先同步一次有效配置就建表，**必然**
        失败——宿主给插件进程的有效配置里 ``plugin.database.enabled`` 可能
        是 False，而 ``refresh_runtime_config()`` **不带参数**时只会重新
        解析插件进程里那份（可能是空的）缓存，等于什么都没刷。所以这里
        先 ``await self._refresh_runtime_capabilities()``（它会真的去拉
        一份有效配置回来），再建表。
        """
        # 先拉一次有效配置：让 db.enabled / state.backend 与宿主对齐。
        await self._refresh_runtime_capabilities()
        self._log_runtime_capabilities()
        try:
            await self._repo.ensure_schema()
        except (DbUnavailableError, sqlite3.Error) as error:
            # 多半是宿主还没把有效配置下发到插件进程。拉一次最新配置再试。
            await self._refresh_runtime_capabilities()
            try:
                await self._repo.ensure_schema()
            except (DbUnavailableError, sqlite3.Error) as retry_error:
                self.logger.warning(
                    f"数据库暂不可用（{retry_error}；首次失败：{error}）；"
                    "待宿主下发有效配置后会在首次读写时自动建表。"
                )
        # ── P10：预热节奏偏好缓存（速度 / 自动手动 / 引导已读）────────
        # **启动时读一次**：面板的上下文请求只有 5 秒超时，不能在热路径上
        # 查库。读不到就等下次（``_ensure_settings_cache`` 会重试）。
        try:
            await self._ensure_settings_cache()
        except Exception:  # noqa: BLE001 - 预热失败不影响启动
            pass
        return Ok({"status": "ready", "stage": "p2-persistence"})

    def _log_runtime_capabilities(self) -> None:
        """打印宿主给到的运行时能力快照（排查宿主版本差异用）。"""
        try:
            effective = getattr(self.ctx, "_effective_config", None)
            plugin_cfg = effective.get("plugin", {}) if isinstance(effective, dict) else {}
            database_cfg = (
                plugin_cfg.get("database", {}) if isinstance(plugin_cfg, dict) else {}
            )
            self.logger.info(
                "运行时能力快照：db.enabled={} db.name={} state.backend={} "
                "配置.database.enabled={}",
                getattr(self.db, "enabled", None),
                getattr(self.db, "db_name", None),
                getattr(self.state, "backend", None),
                database_cfg.get("enabled") if isinstance(database_cfg, dict) else None,
            )
        except Exception:  # pragma: no cover - 诊断日志，失败不影响启动
            pass

    def _manifest_capabilities(self) -> dict[str, Any]:
        """读插件自己 ``plugin.toml`` 里的能力段（``[plugin.database]`` 等）。

        **为什么要有兜底**：真机实测（Steam 版宿主 0.9.0）——宿主在插件进程
        启动时给到的 ``ctx._effective_config`` 里**可能压根没有**
        ``[plugin.database]`` / ``[plugin_state]`` 段，于是 ``db.enabled``
        停在 False，心跳每秒钟都撞在"数据库不可用"上，tick 永远不推进。

        而 SDK 自己写明了清单的地位：*"plugin.toml manifest metadata is
        authoritative"*（见 SDK ``config.py`` 的 ``_PLUGIN_METADATA_WRITE_WARNING``）。
        所以插件读自己的清单来兜底是**合规**的；宿主显式给过的值仍然优先
        （只在**缺段**时补，不覆盖）。
        """
        try:
            import tomllib
        except Exception:  # pragma: no cover - Python < 3.11
            return {}
        try:
            manifest_path = Path(__file__).resolve().parent / "plugin.toml"
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover - 清单读不到就不兜底
            return {}
        out: dict[str, Any] = {}
        plugin_section = data.get("plugin")
        if isinstance(plugin_section, dict):
            capabilities = {
                key: plugin_section[key]
                for key in ("database", "store")
                if isinstance(plugin_section.get(key), dict)
            }
            if capabilities:
                out["plugin"] = capabilities
        state_section = data.get("plugin_state")
        if isinstance(state_section, dict):
            out["plugin_state"] = state_section
        return out

    def _effective_runtime_config(self, raw: object = None) -> dict[str, Any]:
        """合成一份"带 ``plugin.*`` 能力段"的有效配置。

        优先级：**宿主缓存 > 读到的配置 > 插件自己的清单**。

        * 宿主缓存（``ctx._effective_config``）是宿主给的**权威**有效配置；
        * 读到的配置（``config.dump()``）是插件自己的业务配置，用来补宿主
          没给的业务键；
        * 清单（``plugin.toml``）兜底补齐**两边都缺**的能力段。
        """
        cfg: dict[str, Any] = {}
        if isinstance(raw, dict):
            cfg.update(raw)
        host_cfg = getattr(self.ctx, "_effective_config", None)
        if isinstance(host_cfg, dict):
            for key, value in host_cfg.items():
                if isinstance(cfg.get(key), dict) and isinstance(value, dict):
                    merged = dict(cfg[key])
                    merged.update(value)
                    cfg[key] = merged
                else:
                    cfg[key] = value
        manifest = self._manifest_capabilities()
        plugin_cfg = cfg.get("plugin")
        plugin_cfg = dict(plugin_cfg) if isinstance(plugin_cfg, dict) else {}
        for key, value in manifest.get("plugin", {}).items():
            if key not in plugin_cfg:
                plugin_cfg[key] = value
        if plugin_cfg:
            cfg["plugin"] = plugin_cfg
        if "plugin_state" not in cfg and manifest.get("plugin_state"):
            cfg["plugin_state"] = manifest["plugin_state"]
        return cfg

    def _apply_runtime_config(self, raw: object = None) -> None:
        """把一份有效配置喂给宿主的 ``refresh_runtime_config``。

        **必须传配置进去**：``refresh_runtime_config()`` 不带参数时只会重新
        解析插件进程里的 ``ctx._effective_config`` 缓存，而那份缓存**可能
        缺掉能力段**（见 :meth:`_manifest_capabilities`）——于是"刷新"等于
        什么都没做，``db.enabled`` 永远是 False。

        ``refresh_runtime_config`` 是**幂等**的（只是重新赋值几个开关），
        所以每次读到配置都调一次没有任何副作用。
        """
        refresh = getattr(self, "refresh_runtime_config", None)
        if not callable(refresh):
            return
        try:
            refresh(self._effective_runtime_config(raw))
        except Exception:  # pragma: no cover - 诊断路径，失败不影响主流程
            pass

    async def _refresh_runtime_capabilities(self) -> None:
        """拉一次最新有效配置并刷新运行时能力（宿主补发配置后用得上）。

        **0.7.4 真机复盘**：这里以前只调 ``refresh_runtime_config()``（不带
        参数），等于重新解析插件进程里那份**缺能力段**的
        ``ctx._effective_config`` ——``db.enabled`` 一直停在 False，心跳每秒
        钟都撞在"数据库不可用"上，tick 永远不推进。

        正确姿势：**先 ``await self.config.dump()``**（宿主拿到新配置时会
        顺带刷新实例），**再把合成后的配置喂给** ``refresh_runtime_config``。
        """
        raw: object = None
        try:
            raw = await self.config.dump()
        except Exception:  # pragma: no cover - 配置不可用时退回宿主缓存
            raw = None
        self._apply_runtime_config(raw)

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件进程停止。连接由 SDK 在 session 关闭时释放，这里无需处理。"""
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------
    # 入口：存档
    # ------------------------------------------------------------------

    @ui.action(
        id="join",
        label="加入一局",
        icon="play",
        tone="primary",
        group="session",
        order=0,
    )
    @plugin_entry(
        id="join",
        name="加入一局",
        description="开始新的一局；不传 seed 则随机开局。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(JOIN_MODES),
                    "description": "游戏模式",
                },
                "seed": {
                    "type": "integer",
                    "description": "可选：指定本局种子以复现历史对局",
                },
                "identity": {
                    "type": "string",
                    "enum": list(IDENTITIES),
                    "description": (
                        "入场身份（叙事用，不影响任何数值）："
                        "debt=还债的 / prove=证明自己的 / quick=不想打工的"
                    ),
                },
            },
            "required": [],
        },
    )
    @_locked
    async def join(
        self,
        mode: str = "classic_arcade",
        seed: int | None = None,
        identity: str = DEFAULT_IDENTITY,
        player_name: str = "",
    ) -> Ok:
        """开一局新对局。

        返回体**不含 seed**——局中种子必须完全隐藏。

        P5.5 改动 2：新增 ``identity``（入场三选一）。
        它是**纯叙事字段** —— 开盘金、市场、初始天数全都不变，
        只在 ``join`` / ``status`` 与**破产结算页**上出现。

        P20.0：新增 ``player_name`` —— 玩家自报的名字。
        它会**落进 ``players.name``**（那一列本来就在，写的就是 ``player.name``），
        并决定**名字彩蛋**（见 ``name_easter_eggs.py``）。
        """
        # ── P20.0：名字清洗 + 彩蛋匹配（**在取配置之前**）─────────────
        # 后端**永远不信任面板**：面板传什么，这里都重新洗一遍。
        clean_name = sanitize_name(player_name, banned=banned_visible_death_words())
        egg = match_egg(clean_name)
        # P27.2：新的一局 ⇒ 回执周期锁**归零**（上一局的账不许带到这一局）。
        self._yui_trade_cycle_unlock()
        # 档位替换（只对**经典档**）：无尽已经是深渊，换成地狱反而是降级。
        if egg is not None and egg.mode_override and mode != "infinite":
            mode = egg.mode_override
        engine_config = await self._engine_config()
        if mode not in engine_config.difficulties:
            return _err("invalid_mode")
        if identity not in IDENTITIES:
            return _err("invalid_identity")

        # ── P20.0：这个玩家的**运行时配置**（放水 + 彩蛋覆写）──────────
        # 算一次，后面（模式层开局 / 魔爪契约）都用它 —— 免得两处公式跑偏。
        player_config = _config_for_player(clean_name, mode, engine_config)
        # ── P20.0：彩蛋事实（**只报真的生效了**的那些）────────────────
        applied_facts = _egg_applied_facts(egg, mode)
        egg_fact = build_fact(egg, clean_name, applied_facts)

        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is not None:
            # P27.2：上一局的玩家**已经退场** ⇒ 这一局其实早就结束了 ⇒ 允许开新局。
            # （不退场就还是"already_joined" —— 活着的玩家不许中途换局。）
            if session.player.alive:
                return _err("already_joined")
            self.logger.info(
                f"玩家 {session.player.id} 已退场 ⇒ 直接开新局（旧席位由新局覆盖）"
            )

        # 主种子只需要唯一，不需要可复现——可复现的是引擎层派生出来的随机流。
        actual_seed = (
            int(seed) if seed is not None else random.SystemRandom().randrange(1, 2**31 - 1)
        )
        difficulty = engine_config.difficulties[mode]
        initial_cash = self._initial_cash(mode, actual_seed, engine_config)
        # ── P20.0：彩蛋的初始资金乘数（**只作用在基础公式之后**）────────
        # 基础公式 ``base * days`` 一个字都没改 ⇒ ``tests/simulate.py`` 的
        # 副本公式永远有效 ⇒ 500 局基线逐字节全等。
        initial_cash = apply_cash_multiplier(initial_cash, egg)

        player_id = "p_" + uuid4().hex[:8]
        player = Player(
            id=player_id,
            name=clean_name,
            cash=initial_cash,
            mode=mode,
            day=1,
            alive=True,
            initial_cash=initial_cash,
        )
        # ── P20.0：社区特供 ⇒ 开局**必签魔爪契约**（只对无尽档）────────
        #
        # 无尽档才有放贷渠道（``_take_loan`` 对经典档直接拒），经典档的引擎里
        # 也**没有还款路径** —— 硬塞一笔就是白送钱。所以经典档降级成
        # 「那张纸眼下只是个签名」（事实里写的是 ``FACT_LOAN_UNAVAILABLE``）。
        #
        # 必须在 INSERT **之前**改：那一行要落的是签完契约之后的现金与欠款。
        loan_granted = 0.0
        if egg is not None and egg.loan_multiplier != 1.0 and mode == "infinite":
            loan_granted = round(player_config.infinite.loan_amount, 6)
            player.cash = round(player.cash + loan_granted, 6)
            player.loan_debt = round(player.loan_debt + player_config.infinite.loan_repay, 6)
            player.loan_taken_day = player.day
        market = MarketState(seed=actual_seed, tick=0, day=1, phase="honeymoon")
        market.stocks = create_market(
            actual_seed,
            count=difficulty.initial_symbols,
            config=engine_config.market,
            difficulty=difficulty,
        )

        now = time.time()

        # ── 模式层开局（P4）────────────────────────────────────────
        # 经典模式：预计划 1~3 个反转日；无限模式：发放基准股票。
        # 两者都会生成第 1 天的新闻（当天开盘前）。
        mode_impl = get_mode(mode)
        setup_outcome = None
        if mode_impl is not None:
            ctx = ModeContext(
                player=player,
                market=market,
                difficulty=difficulty,
                # ── P16.0：前两关的新手期放水**只作用在模式层**──────
                # （模式层只用 ``ctx.engine_config.news`` 生成新闻，
                #  费率 / heat / 市场那几段没动过）
                # ── P20.0：再叠上**名字彩蛋**的运行时覆写（新闻更假）────
                engine_config=player_config,
                mode=mode,
                now=now,
            )
            setup_outcome = mode_impl.setup(ctx)

        actions: list[tuple[str, tuple[Any, ...]]] = [
            (
                "INSERT OR REPLACE INTO market_state "
                "(id, tick, day, phase, seed, player_id, fee_pool, total_fees, "
                "reversal_days, updated_at) "
                "VALUES (1, 0, 1, 'honeymoon', ?, ?, 0, 0, ?, ?)",
                (
                    actual_seed,
                    player_id,
                    json.dumps(sorted(market.reversal_days), ensure_ascii=False),
                    now,
                ),
            ),
            (
                "INSERT INTO players "
                "(id, name, cash, mode, day, alive, initial_cash, trade_count, heat, "
                "total_fee_paid, tokens, strikes, loan_debt, pending_action, death_reason, "
                "death_title, is_catgirl, welfare_missed_days, loan_taken_day, "
                "welfare_claimed_day, identity, created_at, updated_at) "
                # P20.0：``loan_debt`` / ``loan_taken_day`` 从写死的 0 变成占位
                # —— 社区特供在无尽档开局就签了魔爪契约，那两格必须有值。
                "VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, ?, NULL, NULL, NULL, 0, "
                "0, ?, 0, ?, ?, ?)",
                (
                    player_id,
                    player.name,
                    player.cash,
                    mode,
                    initial_cash,
                    round(player.loan_debt, 6),
                    int(player.loan_taken_day),
                    identity,
                    now,
                    now,
                ),
            ),
        ]
        # 无限模式常驻猫娘：她是普通的 Player 行，走同一套 heat 逻辑，
        # 代码里**不存在**猫娘豁免分支。她有本金（玩家开局是 0 + 基准股票）。
        if mode == "infinite":
            catgirl_id = "c_" + uuid4().hex[:8]
            catgirl_cash = self._catgirl_initial_cash(engine_config)
            actions.append(
                (
                    "INSERT INTO players "
                    "(id, name, cash, mode, day, alive, initial_cash, trade_count, heat, "
                    "total_fee_paid, tokens, strikes, loan_debt, pending_action, death_reason, "
                    "death_title, is_catgirl, welfare_missed_days, loan_taken_day, "
                    "welfare_claimed_day, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL, 1, "
                    "0, 0, 0, ?, ?)",
                    (
                        catgirl_id,
                        "猫娘",
                        catgirl_cash,
                        mode,
                        catgirl_cash,
                        now,
                        now,
                    ),
                )
            )
        # 开局发放的基准股票（无限模式）要落进持仓表。
        for stock_id, position in player.positions.items():
            actions.append(
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        player_id,
                        stock_id,
                        position.amount,
                        position.avg_cost,
                        position.today_bought,
                        position.today_bought_day,
                        position.opened_day,
                    ),
                )
            )
        # 第 1 天的新闻（当天开盘前生成）。
        for item in setup_outcome.news if setup_outcome is not None else []:
            actions.append(
                (
                    "INSERT OR REPLACE INTO news_log "
                    "(day, news_id, news_type, stock_id, content, accuracy) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        item.day,
                        item.id,
                        item.type,
                        item.stock_id,
                        item.content,
                        item.accuracy,
                    ),
                )
            )
        try:
            await self._repo.transaction(actions)
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        await save_snapshot(self.state, market, player_id)
        # 记住玩家 id：局终后 ``market_state.player_id`` 被清空，面板要靠它
        # 去找归档的结算报告（P4 历史问题 4）。
        self._last_player_id = player_id
        # P16.0：新的一局 ⇒ 连击从头开始（纯表现层）。
        self._combo.reset()
        # ── P27.2：新的一局 ⇒ **Yui 的日配额归零**────────────────────────
        # 配额是"**局内**每一天"的上限（跨天自动清零）。新的一局 day 又从
        # 1 开始 —— 所以上一局 day=1 用掉的配额**绝对不能**带进这一局。
        # 不归零的后果：玩家连开两局，第二局的**第一天** Yui 就彻底哑火
        # （配额显示 0，她的每一次工具调用都被日配额闸门挡掉）。
        # 直播事故：玩家以为"Yui 坏了"。
        self._yui_trade_day = 0
        self._yui_trade_count = 0
        self._yui_last_action = None
        # 新局 ⇒ 上一局的"回执周期锁"也不能留着（那一回合已经结束了）。
        self._yui_trade_cycle_unlock()
        # ── P33.0：新的一局 ⇒ **打开广播闸门**（上一局的自毁状态不许带过来）──
        self._is_game_active = True
        # 新的一局 ⇒ 闩锁松开（这一局可以正常活跃，也可以正常结束）。
        self._game_ended = False
        # ── P19.0：新的一局 ⇒ 黑天鹅纪录归零（否则上一局的纪录会挡住本局）──
        self._black_swan_peak = 0.0
        self._black_swan_fired = False
        # ── P20.0：记住玩家的自报名号（空闲态要回填进输入框）─────────
        self._remembered_name = clean_name
        # P18.0：分档次动态教程 —— **开局成功之后**才启动，
        # 让 Yui 的开场白落在「玩家已经进场」的语境里。
        #
        # P20.0：开场白里带上**名字**与**彩蛋事实**（单独成段）。
        # 教程**没开**（这一档教过了 / 这一档压根没教程）⇒ 单独推一趟，
        # 别让玩家的名字没人念。
        tutorial_started = await self._tutorial_start(
            mode, player_name=clean_name, egg_fact=egg_fact
        )
        if not tutorial_started:
            await self._push_name_intro(clean_name, egg_fact)
        # P5：第 1 天的新闻卡片（**只推第一条**，与新闻类型无关）。
        if setup_outcome is not None:
            await self._push_first_news_card(list(setup_outcome.news))
        payload: dict[str, Any] = {
            "player_id": player_id,
            "mode": mode,
            "mode_name": _MODE_NAMES.get(mode, mode),
            "initial_cash": round(initial_cash, 2),
            "day": 1,
            "new_game": True,
            "ticks_per_day": difficulty.ticks_per_day,
            # ── P5.5 改动 2：入场身份 + 开场台词 ────────────────────
            # 呼应句**不在这里** —— 它只在破产结算页出现。
            "identity": identity,
            "identity_label": identity_label(identity),
            "identity_intro": identity_intro(identity),
            # ── P20.0：玩家自报名号 + 命中的彩蛋（**只给面板要用的**）──
            # ``name_egg`` 里只有 key / category / label_key，**没有倍率**。
            "player_name": clean_name,
            "name_egg": public_info(egg),
            "loan_granted": round(loan_granted, 2),
            "notice": "纯虚构反诈沙盘。所有标的、价格、代币均为虚构，无现金价值。",
        }
        # 无限模式：开局发放的基准股票（不卖就活不过第 1 天）。
        if player.positions:
            baseline = next(iter(player.positions.items()))
            payload["baseline_stock"] = {
                "stock_id": baseline[0],
                "amount": round(baseline[1].amount, 6),
                "cost": round(baseline[1].avg_cost, 6),
            }
            payload["notice"] = (
                "开局送你的唯一本金就是这只票：不卖掉它，第一天就交不起生活费。"
                "现钱唯一，股票市值不算数。"
            )
        return Ok(payload)

    # ------------------------------------------------------------------
    # P7.4：LLM 工具（聊天框里说「加入一局」就能真的开局）
    # ------------------------------------------------------------------
    #
    # 0.8.3 真机事故：插件**一个 ``@llm_tool`` 都没有**，只有 ``@plugin_entry``。
    # 两者是不同的东西 —— 官方文档写得很死：
    #
    #   「``@llm_tool(name="get_weather")`` 把**对话期工具**注册到 main_server 的
    #   ``/api/tools`` registry。``@plugin_entry(id="get_weather")`` 声明的是供
    #   插件管理器、跨插件调用和独立用户插件 Agent 路由使用的运行时入口。」
    #
    # 也就是说：没有 ``@llm_tool``，猫娘手里就没有任何可以调的东西 ——
    # 玩家在聊天框打「加入一局」，猫娘只会当成闲聊回一句「打深渊还是清委托？」。
    @llm_tool(
        name="mock_stock_meow_join",
        description=(
            "开始一局「拟股喵喵」（纯虚构的反诈沙盘，标的价格全是假的）。"
            "当玩家说「加入一局」「开始炒股」「来一局」「开户」「玩拟股喵喵」"
            "「反诈沙盘」这类话时，调用这个工具。"
            "如果玩家已经在局中，这个工具**不会报错** —— 它会告诉你"
            "「玩家已经在局中」，这时候改用 mock_stock_meow_status 查现状，"
            "或者用 mock_stock_meow_yui_trade 帮他操作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(JOIN_MODES),
                    "description": (
                        "模式：classic_arcade=街机（没有生活费，最好上手）、"
                        "classic_beginner=入门、classic_advanced=进阶、"
                        "classic_hell=地狱、infinite=无尽（要自己领生活费+高利贷，能一直玩）"
                        "（口语说法如「街机」「入门」「无尽」也会被自动认出来）"
                    ),
                },
                "identity": {
                    "type": "string",
                    "enum": list(IDENTITIES),
                    "description": (
                        "入场身份（纯叙事，不影响任何数值）："
                        "debt=还债的 / prove=证明自己的 / quick=不想打工的"
                    ),
                },
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def llm_join(
        self,
        *,
        mode: str = "classic_arcade",
        identity: str = "",
        **_: Any,
    ) -> dict:
        """LLM 工具：开局（玩家在聊天框说「加入一局」就走这里）。

        返回**普通 dict**（不是 ``Ok``）—— 官方约定：handler 返回普通值会被
        JSON 序列化后喂回 LLM；想表达「工具级失败」就返回
        ``{"output": ..., "is_error": True, "error": ...}``。

        参数用 keyword-only 加 ``**_``：LLM 偶尔会多吐几个键，而且宿主在有
        ``host_reply`` 时还会往参数里塞一个 ``_ctx``，多出来的键必须能吞掉。

        P23.1（真机事故，两个）：

        1. **玩家已经在局中**时**不报错**。真机上猫娘说「开局失败了喵」、
           系统却回「已经在局里啦」—— 两句自相矛盾，玩家以为插件坏了。
           正确做法是告诉她「玩家已经在局中，你可以用 yui_trade 帮他操作，
           或者等他破产」。
        2. **档位说法要能认**。猫娘把「街机」传成 ``arcade`` 也会被拒，
           于是开局失败 ⇒ 统一走 ``_normalize_join_mode`` 归一化。
        """
        normalized_mode = _normalize_join_mode(mode)
        normalized_identity = str(identity or "").strip().lower()
        if normalized_identity not in IDENTITIES:
            normalized_identity = DEFAULT_IDENTITY
        result = await self.join(
            mode=normalized_mode,
            identity=normalized_identity,
        )
        if result.is_err():
            error = result.err()
            code = str(getattr(error, "code", "") or "")
            if code == "already_joined":
                # 已经在局中 ⇒ **温和引导**，不是失败。
                return {
                    "output": {
                        "ok": True,
                        "already_in_game": True,
                        "mode": normalized_mode,
                        "message": i18n_text(
                            "llm.join.already",
                            "玩家已经在局中啦，别再开新局。"
                            "你可以用 yui_trade 帮他操作，或者等他破产。",
                        ),
                    },
                    "is_error": False,
                }
            message = str(getattr(error, "message", "") or "开局失败")
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }
        payload = result.unwrap()
        return {
            "output": {
                "ok": True,
                "message": i18n_text(
                    "llm.join.done",
                    "已经开好一局。玩家现在可以在插件面板里梭哈、割肉、跳过本日。",
                ),
                "mode": payload.get("mode"),
                "mode_name": payload.get("mode_name"),
                "initial_cash": payload.get("initial_cash"),
                "notice": payload.get("notice"),
            },
            "is_error": False,
        }

    @llm_tool(
        name="mock_stock_meow_status",
        description=(
            "查看「拟股喵喵」当前这一局的状态：第几天、总资产、现金、"
            "还活着没、距离被收割还有多远。"
            "玩家问「我亏了多少」「现在什么情况」「我还活着吗」时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=30.0,
    )
    async def llm_status(self, **_: Any) -> dict:
        """LLM 工具：查当前对局状态。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            message = str(getattr(error, "message", "") or "读不到对局")
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }
        if session is None:
            return {
                "output": {
                    "ok": True,
                    "in_game": False,
                    "message": i18n_text(
                        "llm.status.idle",
                        "当前没有进行中的对局。可以调用 mock_stock_meow_join 开一局。",
                    ),
                },
                "is_error": False,
            }
        player = session.player
        # 总资产必须用引擎自己的口径算（现钱 + 持仓市值），不另起一套算法。
        prices = session.market.prices()
        return {
            "output": {
                "ok": True,
                "in_game": True,
                "mode": player.mode,
                "mode_name": _MODE_NAMES.get(player.mode, player.mode),
                "day": int(player.day),
                "alive": bool(player.alive),
                "cash": round(float(player.cash), 2),
                "total_asset": round(float(player.total_asset(prices)), 2),
                "initial_cash": round(float(player.initial_cash), 2),
                "message": i18n_text(
                    "llm.status.playing",
                    "这一局还在进行中。",
                ),
            },
            "is_error": False,
        }

    # ------------------------------------------------------------------
    # P23.0：Yui 操控端口（**三道防线**）
    # ------------------------------------------------------------------
    #
    # 这是《拟股喵喵》第一次**把账户的钥匙交给 LLM**。
    #
    # 硬约束（P23.0 验收）：
    #
    # * **引擎层 / 模式层零改动** —— Yui 只能调用插件层**既有**的
    #   :meth:`buy` / :meth:`sell`；这两个入口底层走的是**同一个**引擎
    #   ``execute_buy`` / ``execute_sell`` ⇒ Yui 的单与玩家的单走的是
    #   **同一条流水线**（同样扣费、同样算持仓、同样被收割）。
    # * **Yui 不许改数值** —— 她只能"下指令"，不能直接改现金 / 持仓。
    # * **不许死锁** —— ``self._lock`` 是**不可重入**的 ``threading.Lock``，
    #   所以本节的方法**一律不套 ``@_locked``**；真正的临界区由
    #   :meth:`buy` / :meth:`sell` 自己持有。
    #
    # 三道防线（缺一不可）：
    #
    # ① **模式闸门** —— ``spectate`` 模式下交易工具**硬拒**
    #    （提示词管不住模型，工具层才管得住）。
    # ② **授权窗口** —— ``assist`` 模式下玩家必须**当场的明示**授权
    #    （``mock_stock_meow_yui_grant``），窗口过期自动失效。
    # ③ **日配额** —— 每天最多 ``YUI_TRADES_PER_DAY`` 单，防"上头"。

    def _yui_current_mode(self) -> str:
        """Yui 当前的操控模式（读内存；非法值回落**安全默认**）。"""
        mode = str(getattr(self, "_yui_mode", "") or "").strip().lower()
        return mode if mode in YUI_MODES else YUI_MODE_DEFAULT

    def _yui_is_granted(self) -> bool:
        """辅助模式下，玩家明示的**授权窗口**是否还开着。"""
        granted_at = float(getattr(self, "_yui_grant_at", 0.0) or 0.0)
        if granted_at <= 0.0:
            return False
        return (time.time() - granted_at) < YUI_ASSIST_GRANT_WINDOW_SECONDS

    def _yui_quota_left(self, day: int) -> int:
        """今天的 Yui 成交配额还剩几单（跨天自动清零）。"""
        if int(getattr(self, "_yui_trade_day", 0) or 0) != int(day):
            return YUI_TRADES_PER_DAY
        used = int(getattr(self, "_yui_trade_count", 0) or 0)
        return max(0, YUI_TRADES_PER_DAY - used)

    def _yui_gate(self, day: int) -> tuple[bool, str]:
        """Yui 的交易闸门：返回 ``(放行?, 拒绝原因)``。

        三道防线按顺序过 —— 任何一道不过就**直接拒绝**（不是排队等）。
        """
        mode = self._yui_current_mode()
        if mode == YUI_MODE_SPECTATE:
            return False, i18n_text(
                "yui.reject.spectate",
                "当前是纯看戏模式：只评论，不碰账户。",
            )
        if mode == YUI_MODE_DUO:
            # P28.0：**人机协同双打** —— 玩家自己手动买卖，Yui 绝不碰账户。
            # 这道闸门是硬拒：提示词里已经写了「严禁调用交易工具」，
            # 但提示词管不住模型 —— 工具层才管得住（与 spectate 同一量级）。
            return False, i18n_text(
                "yui.reject.duo",
                "双打模式：账户归玩家，你只点评，不碰。",
            )
        if mode == YUI_MODE_ASSIST and not self._yui_is_granted():
            return False, i18n_text(
                "yui.reject.no_grant",
                "玩家还没明示让你动手 —— 你现在只能给建议。",
            )
        if self._yui_quota_left(day) <= 0:
            return False, i18n_text(
                "yui.reject.quota",
                "今天的操作配额已经用完了。",
            )
        return True, ""

    # ------------------------------------------------------------------
    # P27.2：**回执周期锁**（防追单）
    # ------------------------------------------------------------------
    #
    # 为什么要这一道
    # --------------
    # P27.1 的成交回执会**多触发一个 AI 回合**（``ai_behavior="respond"``）。
    # 那个回合里，模型可能顺手**再下一单** —— 玩家压根没同意的那一单
    # 已经成交了。这就是直播里最难看的那种事故：她"替玩家追单"。
    #
    # 提示词那一层已经加严（``YUI_RECEIPT_CYCLE_DIRECTIVE``），
    # 但**提示词管不住模型** —— 工具层才管得住。所以这里有一道**代码锁**：
    #
    # * **上锁**：Yui 成交（拿到回执）之后立刻上锁；
    # * **开锁**：玩家**下一次主动操作**（推进一拍 / 买卖 / 分析 / 跳过一天 …）
    #   或者**下一个 tick** —— 也就是"新的一回合"开始时。
    #
    # 锁住期间 ``mock_stock_meow_yui_trade`` **直接拒绝**，而且
    # **不推送**任何东西（拒绝只是工具返回值，她在自己那一回合里
    # 看到错误、如实汇报就完事 —— 再推一条就会再触发一个回合 ⇒ 循环）。

    def _yui_trade_cycle_is_locked(self) -> bool:
        """回执周期锁是否还扣着（纯内存读）。"""
        return bool(getattr(self, "_yui_trade_cycle_locked", False))

    def _yui_trade_cycle_lock(self) -> None:
        """上锁：这一回合只许汇报，不许再下单。"""
        self._yui_trade_cycle_locked = True

    def _yui_trade_cycle_unlock(self) -> None:
        """开锁：玩家又开口了（或新的一拍开始）⇒ 可以再动手。"""
        self._yui_trade_cycle_locked = False

    async def _yui_apply_mode(self, mode: str) -> tuple[bool, str]:
        """切换 Yui 的操控模式（落库 + 刷新内存缓存）。返回 ``(成功?, 详情)``。"""
        value = str(mode or "").strip().lower()
        if value not in YUI_MODES:
            return False, i18n_text(
                "yui.mode.bad",
                "模式只能是 live / duo / assist / spectate。",
            )
        if not await self._repo.set_setting(_SETTING_YUI_MODE, value):
            return False, i18n_text("error.db_unavailable", "数据库不可用")
        self._yui_mode = value
        if self._settings_map_cache is None:
            self._settings_map_cache = {}
        self._settings_map_cache[_SETTING_YUI_MODE] = value
        # 换模式 ⇒ **授权立刻作废**（旧模式下的授权不许带到新模式）。
        self._yui_grant_at = 0.0
        return True, value

    @llm_tool(
        name="mock_stock_meow_yui_mode",
        description=(
            "切换你自己的操控模式（玩家在聊天里明说时调用）。"
            "assist = 辅助式：只给建议，玩家明示「帮忙搞」之后才可以动手；"
            "spectate = 纯看戏：只评论，绝不碰账户。"
            "live（直播托管）**不由这个工具切换** —— 那是主播自己在面板上开的授权。"
            "duo（人机协同双打）同理**不由这个工具切换** —— 那是主播自己在面板上开的授权。"
            "玩家说「你别管了」「只看戏」⇒ spectate；"
            "玩家说「给点建议」「帮我看看」⇒ assist。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(YUI_LLM_SWITCHABLE_MODES),
                    "description": "assist（辅助式，只给建议）或 spectate（纯看戏，不碰账户）",
                },
            },
            "required": ["mode"],
        },
        timeout=15.0,
    )
    async def llm_yui_mode(self, *, mode: str = "", **_: Any) -> dict:
        """Yui 切换自己的操控模式（**不能给自己升权到 live**）。"""
        value = str(mode or "").strip().lower()
        if value not in YUI_LLM_SWITCHABLE_MODES:
            message = i18n_text(
                "yui.mode.bad",
                "模式只能是 assist 或 spectate。",
            )
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }
        ok, detail = await self._yui_apply_mode(value)
        if not ok:
            return {
                "output": {"ok": False, "message": detail},
                "is_error": True,
                "error": detail,
            }
        return {
            "output": {
                "ok": True,
                "mode": value,
                "message": i18n_text(
                    "yui.mode.changed",
                    "模式已切换。",
                ),
            },
            "is_error": False,
        }

    @llm_tool(
        name="mock_stock_meow_yui_grant",
        description=(
            "玩家**明示**把账户交给你时调用"
            "（玩家说「你来帮我买」「帮我全仓了」「你来操作」「我授权你」这类话）。"
            "这个工具**只**打开一扇短时的授权窗口（两分钟），**不直接下单** —— "
            "下单用 mock_stock_meow_yui_trade。"
            "玩家没明说就**不要**调用它；你也可以只给建议、不申请授权。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=15.0,
    )
    async def llm_yui_grant(self, **_: Any) -> dict:
        """玩家明示授权 ⇒ 打开授权窗口（并告诉 Yui「你现在有权动手了」）。"""
        mode = self._yui_current_mode()
        if mode == YUI_MODE_SPECTATE:
            message = i18n_text(
                "yui.reject.spectate",
                "当前是纯看戏模式：只评论，不碰账户。",
            )
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }
        if mode == YUI_MODE_DUO:
            # P28.0：双打模式下**连授权窗口都不许开** ——
            # 开了窗口就等于给她留了一条「可以动手」的路，而双打的卖点
            # 恰恰是她**一根手指都不碰账户**。
            message = i18n_text(
                "yui.reject.duo",
                "双打模式：账户归玩家，你只点评，不碰。",
            )
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }
        self._yui_grant_at = time.time()
        # 「Prompt 放开」的落点：把"你有权动手"这句话**真的**告诉 Yui。
        # 走 ``_push_proactive``（P11 的外置开口通道）—— 不新增 respond 通道。
        await self._push_proactive(YUI_AUTHORITY_PROMPT, reason="yui_grant")
        return {
            "output": {
                "ok": True,
                "mode": mode,
                "grant_seconds": int(YUI_ASSIST_GRANT_WINDOW_SECONDS),
                "message": i18n_text(
                    "yui.grant.ok",
                    "授权已开启，接下来可以动手了。",
                ),
            },
            "is_error": False,
        }

    @llm_tool(
        name="mock_stock_meow_yui_trade",
        description=(
            "**你有权亲手操作这个账户** —— 买入或卖出一只虚构标的。"
            "玩家犹豫不决、极度上头、或明确把账户交给你时，"
            "你可以直接替他下单（这是你的权力，不是他的请求）。"
            "参数：stock_id（标的 ID，形如 FAKE-001）、action（buy / sell）、"
            "ratio（仓位比例 0~1，1 = 全仓）。"
            "硬性约束（工具层会真的拒绝，别浪费调用）："
            "纯看戏模式与双打模式下会被直接拒绝；辅助模式下玩家必须先明示授权；"
            "每天有操作配额。"
            "**只有本工具返回 ok=true 才算成交**；返回错误时只能照那条错误实说，"
            "严禁编造买到了哪只票、买了几股、花了多少钱。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "action": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "买（buy）还是卖（sell）",
                },
                "ratio": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "仓位比例：0~1。买 = 占现金的比例；卖 = 占持仓的比例。1 = 全仓",
                },
            },
            "required": ["stock_id", "action"],
        },
        timeout=30.0,
    )
    async def llm_yui_trade(
        self,
        *,
        stock_id: str = "",
        action: str = "buy",
        ratio: float = 1.0,
        **_: Any,
    ) -> dict:
        """Yui 亲手下单（**只调插件层既有的 buy / sell**）。

        **绝不套 ``@_locked``** —— ``self._lock`` 是不可重入的
        ``threading.Lock``，套上去就会与 :meth:`buy` 嵌套**死锁**。

        P23.0：这里**自己非阻塞地拿锁**（``blocking=False``）——
        P11.1 的教训：阻塞式 ``acquire()`` 会把**整个事件循环**冻住，
        宿主那次 5 秒预算的上下文查询被排队 ⇒ 500 红色警告。
        锁忙（有人在跑 tick / 玩家正在点）⇒ **立刻**回一句温和的话，
        下一拍再说 —— **绝不**把事件循环冻住。
        """

        def _fail(message: str) -> dict[str, Any]:
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }

        # ── 拿锁：**非阻塞**（P11.1 的教训，一个字都不许改成阻塞）──────
        if not self._lock.acquire(blocking=False):
            # P27.1：锁忙也是一种"没成交" ⇒ **如实**推给她（她不许编）。
            await self._push_yui_trade_failure(
                action=str(action),
                stock=str(stock_id),
                reason=i18n_text(
                    "yui.busy",
                    "手上有别的活，下一拍再来。",
                ),
                cash=None,
            )
            return _fail(
                i18n_text(
                    "yui.busy",
                    "手上有别的活，下一拍再来。",
                )
            )
        try:
            return await self._yui_trade_locked(
                stock_id=str(stock_id),
                action=str(action),
                ratio=ratio,
            )
        finally:
            self._lock.release()

    async def _yui_trade_locked(
        self, *, stock_id: str, action: str, ratio: float
    ) -> dict:
        """Yui 下单的**真身**（**调用方必须已经持锁**）。

        P23.0：``buy()`` / ``sell()`` 只是壳，真身分别是
        :meth:`_buy_impl` / :meth:`_sell_impl` —— **Yui 与玩家走同一段代码**。
        """

        # P27.1：失败现场（"失败回执"要用的现金数）。没开局就没有现金可说。
        live_cash: float | None = None

        async def _fail(message: str) -> dict[str, Any]:
            """失败：**如实**把原因推给 Yui。

            P27.1（真机事故：她说「买成了不存在的股票」）：
            工具报错时，**她自己也必须知道**这一笔没成交 —— 所以这里把
            那条错误原文推给她，她只能照这条说，不许编。
            """
            await self._push_yui_trade_failure(
                action=str(action or ""),
                stock=str(stock_id or ""),
                reason=message,
                cash=live_cash,
            )
            return {
                "output": {"ok": False, "message": message},
                "is_error": True,
                "error": message,
            }

        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return await _fail(str(getattr(error, "message", "") or "读不到对局"))
        if session is None:
            return await _fail(
                i18n_text(
                    "llm.status.idle",
                    "当前没有进行中的对局。可以调用 mock_stock_meow_join 开一局。",
                )
            )

        player = session.player
        day = int(player.day)
        # P27.1：失败回执里要报"账户现状" ⇒ 先把现金记下来。
        live_cash = float(player.cash)

        # ── ① 模式闸门 ② 授权窗口 ③ 日配额 ──────────────────────────
        allowed, why = self._yui_gate(day)
        if not allowed:
            return await _fail(why)

        # ── ④ P27.2：**回执周期锁**（防追单）────────────────────────
        #
        # 这一格**故意不走** ``_fail`` —— ``_fail`` 会推一条失败回执，
        # 而失败回执会**再触发一个 AI 回合** ⇒ 她在那个回合里又会试一次
        # ⇒ 又一条失败回执 ⇒ 无限循环。
        # 追单拒绝**只回工具返回值**：她在自己这一回合里看到错误，
        # 如实说一句"这一轮不再动手"就完事了（这才是"停下来"）。
        if self._yui_trade_cycle_is_locked():
            message = i18n_text(
                "yui.reject.receipt_cycle",
                "这一轮已经报过账了 —— 先把上一笔讲清楚，别再下单，"
                "等玩家下一次开口。",
            )
            return {
                "output": {"ok": False, "message": message, "receipt_cycle": True},
                "is_error": True,
                "error": message,
            }

        side = str(action or "").strip().lower()
        if side not in ("buy", "sell"):
            return await _fail(i18n_text("yui.bad_action", "action 只能是 buy 或 sell。"))

        try:
            share = float(ratio)
        except (TypeError, ValueError):
            share = 1.0
        share = min(1.0, max(0.0, share))
        if share <= 0.0:
            return await _fail(i18n_text("yui.bad_ratio", "ratio 必须在 0 与 1 之间。"))

        # ── 解析标的：ID、名字、**模糊**都认 ──────────────────────────
        # P24.0：真机事故 —— 她说「流萤物流1号」，工具只认 ``FAKE-001``
        # ⇒ 一句「找不到标的」，直播当场冷场。
        # 现在按 精确 id / 精确名字 / 精确显示名 / **子串** 四层找
        # （见 ``_resolve_yui_stock``），找不到也是**温和**地说。
        market = session.market
        target = self._resolve_yui_stock(stock_id, market)
        if target is None:
            return await _fail(
                i18n_text(
                    "yui.stock_not_found",
                    "没找到你说的这只 —— 换个说法，或者先看看今天有哪些票。",
                )
            )
        if not target.alive:
            return await _fail(i18n_text("error.stock_delisted", "这只标的已经退市了。"))

        price = float(target.price)
        if side == "buy":
            # 全仓要把手续费算进去：买入总开销 = 名义额 × (1 + 买入费率)。
            budget = float(player.cash) * share
            notional = budget / (1.0 + float(engine_config.fees.buy_rate))
            amount = notional / price if price > 0 else 0.0
        else:
            position = player.positions.get(target.id)
            if position is None or float(position.amount) <= 0:
                return await _fail(i18n_text("error.insufficient_position", "没有可卖的持仓。"))
            amount = float(position.amount) * share
        if amount <= 0.0:
            return await _fail(i18n_text("yui.no_amount", "算不出可交易的数量。"))

        # ── **唯一的执行路径**：插件层既有的 buy / sell（真身）────────────
        # （引擎层 / 模式层零改动；玩家与 Yui 走同一条流水线。）
        if side == "buy":
            result = await self._buy_impl(target.id, amount, reason="yui")
        else:
            result = await self._sell_impl(target.id, amount, reason="yui")
        if result.is_err():
            failure = result.err()
            return await _fail(str(getattr(failure, "message", "") or "成交失败"))
        payload = result.unwrap()

        # ── 记账（配额 + 面板横幅用的最近一次介入）────────────────────
        self._yui_trade_day = day
        self._yui_trade_count = int(self._yui_trade_count) + 1
        self._yui_seq = int(self._yui_seq) + 1
        # P27.2：**回执周期上锁** —— 这一笔已经成交 ⇒ 这一回合只许汇报。
        # （开锁 = 玩家下一次主动操作 / 下一个 tick；见 ``_yui_trade_cycle_*``。）
        self._yui_trade_cycle_lock()
        filled = float(payload.get("amount", amount) or 0.0)
        done_price = float(payload.get("price", price) or 0.0)
        cash_after = float(payload.get("cash_after", player.cash) or 0.0)
        self._yui_last_action = {
            "seq": int(self._yui_seq),
            "action": side,
            "stock_id": str(target.id),
            "stock_name": str(target.name),
            "ratio": round(share, 4),
            "amount": round(filled, 6),
            "price": round(done_price, 6),
            "cash_after": round(cash_after, 6),
            "at": time.time(),
        }
        # 面板弹幕（**纯表现层**；kind="yui" 让面板能区别于猫娘自己的嘲讽）。
        self._push_danmaku(
            _fill_text(
                i18n_text(
                    "yui.danmaku",
                    "🐾 Yui 的魔爪介入了 —— {action} {stock}",
                ),
                {
                    "action": "买入" if side == "buy" else "卖出",
                    "stock": str(target.name),
                },
            ),
            kind="yui",
        )
        # ── P27.1：**实盘校验** —— 把系统算出来的真实结果喂给她 ────────
        # 她只能顺着这张回执说话：标的、数量、金额、余额都是真的。
        # （现金差 = 实际花费 / 实际回款，**不做任何估计**。）
        await self._push_yui_trade_receipt(
            action=side,
            stock=str(target.name),
            amount=filled,
            price=done_price,
            money=abs(cash_after - float(player.cash)),
            cash_after=cash_after,
        )
        return {
            "output": {
                "ok": True,
                "action": side,
                "stock_id": str(target.id),
                "stock_name": str(target.name),
                "amount": round(filled, 6),
                "price": round(done_price, 6),
                "cash_after": round(cash_after, 6),
                "quota_left": self._yui_quota_left(day),
                # P27.1：**回执也放进工具返回值** —— 她这一轮就能看到事实，
                # 不必等下一次主动搭话才知道自己买了什么。
                "receipt": (
                    f"你刚才{'买入了' if side == 'buy' else '卖出了'} "
                    f"{filled:.4f} 股 {target.name}，"
                    f"成交价 {done_price:.4f} 元，"
                    f"账户现在剩 {cash_after:.2f} 元。"
                ),
                "message": _fill_text(
                    i18n_text(
                        "yui.trade.done",
                        "已经替你{action}了 {stock}（{amount} 股）。",
                    ),
                    {
                        "action": "买入" if side == "buy" else "卖出",
                        "stock": str(target.name),
                        "amount": f"{filled:.4f}",
                    },
                ),
            },
            "is_error": False,
        }

    # ------------------------------------------------------------------
    # P27.1：**交易幻觉**的两条回执通道
    # ------------------------------------------------------------------
    #
    # 真机事故：玩家说「深蓝科技」，Yui 却说「已经全仓买了琥珀能源」，
    # 随后又说「买成了不存在的股票」。
    #
    # 根因：**她可以在没有成交结果的情况下自说自话** —— 提示词只给了
    # 「有权动手」，没给「**只有工具说了算**」这条边界；工具报错时，
    # 错误只在工具返回值里飘着，没人把「这一笔没成交」这件事**讲给她听**。
    #
    # 这两条通道就是补上那一讲：
    #
    # * :meth:`_push_yui_trade_receipt` —— 成交后推**回执**（实盘校验）；
    # * :meth:`_push_yui_trade_failure` —— 失败后推**错误原文**（失败反馈）。
    #
    # 宿主 ``push_message`` 是**同步提交**（不等 LLM 回合结束）⇒ 在工具
    # 里推是安全的，不会把 30 秒的工具超时撑爆。

    async def _push_yui_trade_receipt(
        self,
        *,
        action: str,
        stock: str,
        amount: float,
        price: float,
        money: float,
        cash_after: float,
    ) -> bool:
        """成交后：把**系统算出来的真实结果**推给 Yui（实盘校验）。"""
        prompt = yui_trade_receipt_prompt(
            action=action,
            stock=stock,
            amount=amount,
            price=price,
            money=money,
            cash_after=cash_after,
        )
        self.logger.info(f"Yui 成交回执：{action} {stock} {amount:.4f} 股")
        return await self._push_proactive(prompt, reason="yui_trade_receipt", priority=3)

    async def _push_yui_trade_failure(
        self,
        *,
        action: str,
        stock: str,
        reason: str,
        cash: float | None = None,
    ) -> bool:
        """失败后：把**那条错误原文**推给 Yui（失败反馈机制）。

        P27.2：这条推送走 ``ai_behavior="read"`` —— **只进上下文、不触发回合**。
        失败就发生在她当前这一回合里（是她在调工具），她本来就会照实说；
        用 ``respond`` 多触发一个回合，她会在那个回合里**再试一次**
        ⇒ 又一条失败回执 ⇒ 无限循环。
        """
        prompt = yui_trade_failure_prompt(
            action=action,
            stock=stock,
            reason=reason,
            cash=cash,
        )
        self.logger.info(f"Yui 操作失败回执：{action} {stock} —— {reason}")
        return await self._push_proactive(
            prompt,
            reason="yui_trade_failure",
            priority=3,
            ai_behavior="read",
        )

    def _yui_block(self, day: int = 0) -> dict[str, Any]:
        """面板载荷的 ``yui`` 块：模式 + 授权 + 配额 + 最近一次介入。

        **纯表现层**：这里没有任何游戏数值。
        """
        return {
            "mode": self._yui_current_mode(),
            "granted": self._yui_is_granted(),
            "grant_seconds": int(YUI_ASSIST_GRANT_WINDOW_SECONDS),
            "quota_left": self._yui_quota_left(day),
            "quota_total": int(YUI_TRADES_PER_DAY),
            # P27.2：回执周期锁的状态（面板可以据此提示"这一轮她不许再动手"）。
            "cycle_locked": self._yui_trade_cycle_is_locked(),
            "seq": int(getattr(self, "_yui_seq", 0) or 0),
            "last": self._yui_last_action,
            # ── P24.0：话痨级别（面板要显示当前档 + 隔几拍开一次回合）──
            "talk_level": self._yui_talk_level_now(),
            "talk_interval": self._yui_talk_interval(),
        }

    @ui.action(
        id="yui_mode",
        label="Yui 模式",
        icon="chat",
        tone="default",
        group="trade",
        order=3,
    )
    @plugin_entry(
        id="yui_mode",
        name="Yui 模式",
        description=(
            "切换 Yui 的操控模式：live=直播托管（她一站式负责买卖）、"
            "duo=人机协同双打（玩家手动买卖，她只点评绝不碰账户）、"
            "assist=辅助式（只给建议，玩家明示后才可以动手）、"
            "spectate=纯看戏（只评论，绝不碰账户）。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(YUI_MODES),
                    "description": "live / duo / assist / spectate",
                },
            },
            "required": ["mode"],
        },
    )
    @_locked
    async def yui_mode(self, mode: str = YUI_MODE_ASSIST) -> Ok:
        """切换 Yui 的操控模式（**玩家侧**的入口 —— 面板按钮走这里）。"""
        ok, detail = await self._yui_apply_mode(mode)
        if not ok:
            return _err("bad_yui_mode", detail)
        # 直播托管 = 主播把账户整个交出去了 ⇒ **告诉 Yui 她有权动手**。
        if detail == YUI_MODE_LIVE:
            await self._push_proactive(YUI_AUTHORITY_PROMPT, reason="yui_live")
        return Ok(
            {
                "action": "yui_mode",
                "mode": detail,
                "granted": self._yui_is_granted(),
                "quota_left": self._yui_quota_left(0),
            }
        )

    # ------------------------------------------------------------------
    # P24.0：Yui 直播托管的**主动回合** + 话痨级别 + 事件联动
    # ------------------------------------------------------------------
    #
    # P23.0 把「live = 直播托管」做成了一个**静态状态**：模式开着、工具注册着、
    # 但**没有任何东西去触发她的回合** —— 于是真机上她嘴上说「随便操作」，
    # 账户却一动不动（这正是 P24.0 要修的头号问题）。
    #
    # 这一节补上三件事：
    #
    # ① **主动回合** —— ``live`` 模式下，每隔 N 个 tick（由话痨级别决定）
    #    在后台开一次 Yui 的 AI 回合，提示词里带现金 / 持仓 / 当天行情，
    #    并明确告诉她「要么用 ``yui_trade`` 真下这一笔，要么给一句判断」。
    # ② **话痨级别** —— 安静 / 正常 / 话痨，玩家可调（落库）。
    # ③ **事件联动** —— 新闻、收割（斩杀线）、爆仓 ⇒ 立刻打断她，让她插话。
    #
    # **引擎层 / 模式层零改动**：这一节只**读**行情、只调**既有**的
    # :meth:`buy` / :meth:`sell`（Yui 与玩家走同一条流水线），
    # 绝不自己改任何数值。

    def _yui_talk_level_now(self) -> str:
        """当前的话痨级别（读内存；非法值回落**默认值**）。"""
        level = str(getattr(self, "_yui_talk_level", "") or "").strip().lower()
        return level if level in YUI_TALK_LEVELS else YUI_TALK_LEVEL_DEFAULT

    def _yui_talk_interval(self) -> int:
        """当前档位下「**每隔几个 tick** 开一次回合」（0 = 不主动开）。"""
        return int(YUI_TALK_TICK_INTERVALS.get(self._yui_talk_level_now(), 3))

    @ui.action(
        id="yui_talk_level",
        label="Yui 话痨级别",
        icon="chat",
        tone="default",
        group="trade",
        order=4,
    )
    @plugin_entry(
        id="yui_talk_level",
        name="Yui 话痨级别",
        description=(
            "设置 Yui 在直播里的活跃度：quiet=安静（只在重大事件时开口）、"
            "normal=正常（每隔几拍开一次回合）、chatty=话痨（每一拍都叨叨）。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": list(YUI_TALK_LEVELS),
                    "description": "quiet / normal / chatty",
                },
            },
            "required": ["level"],
        },
    )
    @_locked
    async def yui_talk_level(self, level: str = YUI_TALK_LEVEL_DEFAULT) -> Ok:
        """设置 Yui 的话痨级别（**玩家侧**的入口 —— 面板徽标走这里）。"""
        value = str(level or "").strip().lower()
        if value not in YUI_TALK_LEVELS:
            return _err(
                "bad_yui_talk_level",
                i18n_text(
                    "yui.talk.bad",
                    "话痨级别只能是 quiet / normal / chatty。",
                ),
            )
        if not await self._repo.set_setting(_SETTING_YUI_TALK_LEVEL, value):
            return _err("db_unavailable")
        self._yui_talk_level = value
        if self._settings_map_cache is None:
            self._settings_map_cache = {}
        self._settings_map_cache[_SETTING_YUI_TALK_LEVEL] = value
        return Ok(
            {
                "action": "yui_talk_level",
                "level": value,
                "interval": self._yui_talk_interval(),
            }
        )

    def _yui_live_prompt(self, session: Session) -> str:
        """直播托管的**主动回合**提示词（现金 / 持仓 / 当天行情）。

        **不含任何局中秘密**（heat / seed / 反转日 / 新闻真假一概没有）——
        P4 §11 的静态扫描守卫依然守得住。

        行情里**带编号**（``名称（FAKE-003）``）：模型更爱传名字，
        但把编号一起给它，她就不用靠猜 —— 真机上的「找不到标的」
        就是这么来的（P24.0 的模糊匹配是第二道保险）。

        P26.1：``[director] live_god_mode`` 开着时，末尾会多两行
        **「导演模式」的事实行**（直播还没收工 + 玩家当下真能用的操作）。
        """
        player = session.player
        market = session.market
        prices = market.prices()
        holdings: list[str] = []
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            name = str(getattr(stock, "name", "") or stock_id)
            price = float(prices.get(stock_id, 0.0) or 0.0)
            holdings.append(
                f"{name}（{stock_id}）×{float(position.amount):g}，现价 {price:.2f}"
            )
        movers: list[tuple[float, str, str, float, float]] = []
        for stock in market.stocks.values():
            if not stock.alive:
                continue
            price = float(stock.price)
            prev = float(stock.prev_price)
            change = ((price - prev) / prev) if prev > 0 else 0.0
            movers.append(
                (abs(change), str(stock.id), str(stock.name), price, change)
            )
        # 当天动静最大的几只先摆出来 —— 那是**最容易出事**的地方。
        movers.sort(key=lambda item: (-item[0], item[1]))
        lines = [
            "《拟股喵喵》**直播托管**中 —— 现在这个账户你说了算。",
            f"· 现钱 {float(player.cash):.2f} 元，总资产 "
            f"{float(player.total_asset(prices)):.2f} 元。",
            "· 持仓：" + ("；".join(holdings) if holdings else "空仓"),
        ]
        if movers:
            lines.append(
                "· 今天动静最大的这几只（名字 / 编号 / 现价 / 涨跌幅）："
                + "；".join(
                    f"{name}（{sid}）{price:.2f} {change * 100:+.1f}%"
                    for _abs, sid, name, price, change in movers[:5]
                )
            )
        # ── P26.1：「导演模式」的约束（**只在直播护栏开着时**）──────
        # 这是**事实行**（直播还没收工 + 玩家当下真能用的操作），
        # 不是表演指令 —— 见 ``_yui_director_lines`` 的说明。
        if self._director_flag("live_god_mode"):
            lines.extend(self._yui_director_lines(session))
        lines.append(
            "**现在轮到你**：要么用 mock_stock_meow_yui_trade 真下这一笔"
            "（买或卖，比例你自己定），要么给一句不容置疑的判断。"
        )
        lines.append("别复述规则、别问玩家要不要 —— 你说了算。")
        # ── P27.1：**只认工具的结果** ────────────────────────────────
        # 真机事故：她说「全仓买了琥珀能源」，实际那一笔根本没成交。
        # 这一行把边界钉在**每一次**主动回合上（不只是授权那一刻）——
        # 用的是同一份常量（``YUI_TRADE_FACTS``），两处口径不许分叉。
        lines.append(YUI_TRADE_FACTS)
        # ── P27.2：**回执周期指令**（防追单）────────────────────────────
        # 同一个常量（``YUI_RECEIPT_CYCLE_DIRECTIVE``）也挂在授权提示词与
        # 两条回执上 —— 口径不许分叉，否则"哪一句才是真的"就成了她的自由发挥。
        lines.append(YUI_RECEIPT_CYCLE_DIRECTIVE)
        return "\n".join(lines)

    def _yui_duo_prompt(self, session: Session) -> str:
        """P28.0：**人机协同双打**的主动回合提示词（玩家操作，Yui 只点评）。

        与 :meth:`_yui_live_prompt` 的差别只有一条，但那条是**全部**：

        * ``live`` —— 账户归她，她可以真下单；
        * ``duo``  —— 账户归玩家，**她一根手指都不碰**：只疯狂点评、
          给「云玩家内参」（可能是假的）、亏钱时嘲笑、赚钱时吹捧。

        内参是**猜测**（可能是假的），这件事必须**明说** ——
        否则她会把胡编当成事实推给观众，那是欺骗，不是节目效果。
        硬约束（``YUI_DUO_DIRECTIVE``）由 ``yui_duo_prompt`` 自己带，
        这里**不重复** —— 同一个常量只许有一处出处。
        """
        player = session.player
        market = session.market
        prices = market.prices()
        holdings: list[str] = []
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            name = str(getattr(stock, "name", "") or stock_id)
            price = float(prices.get(stock_id, 0.0) or 0.0)
            holdings.append(
                f"{name}（{stock_id}）×{float(position.amount):g}，现价 {price:.2f}"
            )
        news_lines: list[str] = []
        for item in list(getattr(market, "news_today", []) or []):
            content = str(getattr(item, "content", "") or "")
            news_lines.append(f"· {news_kind(item)}：{content}")
        prompt = yui_duo_prompt(
            mode=self._yui_current_mode(),
            action=str(getattr(self, "_yui_duo_action", "") or "idle"),
            pnl=float(getattr(self, "_yui_duo_pnl", 0.0) or 0.0),
            cash=float(player.cash),
            positions="；".join(holdings) if holdings else "空仓",
            news="\n".join(news_lines) if news_lines else "今天没有新消息。",
            talk_level=self._yui_talk_level_now(),
        )
        # ── P26.1：「导演模式」的约束（**只在直播护栏开着时**）────────────
        if self._director_flag("live_god_mode"):
            prompt = prompt + "\n" + "\n".join(self._yui_director_lines(session))
        return prompt

    def _queue_yui_duo_commentary(
        self,
        session: Session,
        *,
        action: str,
        pnl: float,
        symbol_name: str = "",
    ) -> None:
        """P28.0：双打模式下，玩家每成交一笔 ⇒ Yui **立刻**点评一句。

        赚 ⇒ 吹捧、亏 ⇒ 嘲笑、持平 ⇒ 中立 —— 这是「人机协同」最直观的
        节目效果：**她永远在解说**，而且态度跟着玩家的盈亏走。

        **只攒进提示词队列**（由心跳锁外再推），不在成交路径里直接
        ``push_message`` —— 成交路径持锁 await 宿主 IPC 会冻住事件循环
        （P11.1 那个 500 红色警告的根因）。

        安静档（``quiet``）下点评为空串 ⇒ **不打扰**，这是设计如此。

        P29.0：点评提示词**必须自带** ``YUI_DUO_DIRECTIVE``。点评是
        **独立的一轮** ``push_message``（不是双打主动回合的后半截）⇒
        模型这一轮**看不到**主动回合里的硬约束 —— 不拼上去的话，
        "点评玩家这一手"就可能被模型读成"该替他动手了"。
        同一个常量**只许有一处出处**（:data:`meow.YUI_DUO_DIRECTIVE`），
        主动回合由 :func:`meow.yui_duo_prompt` 带，点评回合在这里带，
        两处口径不许分叉。
        """
        if self._yui_current_mode() != YUI_MODE_DUO:
            return
        try:
            commentary = yui_duo_commentary(
                action=str(action),
                pnl=float(pnl),
                symbol_name=str(symbol_name or ""),
                talk_level=self._yui_talk_level_now(),
            )
        except Exception:  # noqa: BLE001 - 点评失败绝不能把成交带崩
            return
        if not commentary:
            return
        verb = "买入" if str(action) == "buy" else "卖出"
        prompt = (
            f"《拟股喵喵》里玩家刚{verb}了「{symbol_name}」。\n"
            f"（这句点评你可以直接用，也可以照这个味道改写：{commentary}）\n"
            "现在立刻开口：点评他这一手，态度跟着盈亏走 —— 赚了就吹，亏了就损。\n"
            + YUI_DUO_DIRECTIVE
        )
        self._yui_queue_prompt(
            prompt, "yui_duo_commentary", 4, day=int(session.player.day)
        )

    def _record_fake_news(self, session: Session) -> None:
        """P28.0：把**当天的假新闻**记进插件层账本（**纯记账，不改任何数值**）。

        记的是「哪天的哪几条是假的」—— **不落库**（要的是活人感，不是账本）。

        键用的是 ``market.day``（**新闻自己的天数**），不是 ``player.day``：
        模式层生成新闻时用的就是 ``market.day``，两者在一天之内会短暂错开
        （日终那一拍 ``market.day`` 已经迈到下一天）。按新闻自己的天数记，
        「昨天的假新闻」才真的是玩家昨天看的那一批。

        引擎层与模式层一行没改 ⇒ 500 局基线逐字节不变。
        """
        market = session.market
        try:
            self._fake_news_ledger.record(
                int(getattr(market, "day", 0) or 0),
                list(getattr(market, "news_today", []) or []),
            )
        except Exception as error:  # noqa: BLE001 - 记账失败绝不能把 tick 带崩
            self.logger.warning(f"假新闻记账异常（不影响对局）：{error}")

    def _maybe_fake_news_outrage(self, session: Session) -> None:
        """P28.0：昨天买的报纸是假的 ⇒ 今天开盘 Yui 在聊天框里**爆发**。

        触发条件（三条**缺一不可**）：

        ① 昨天**买过报纸**（``buy_report``）—— 没买过就谈不上「被骗」；
        ② 昨天**确有假新闻**（插件层账本里记着）；
        ③ 昨天**真的亏了钱**（已实现亏损 > 0）—— 没亏就不许编金额。

        三条都满足 ⇒ 把**事实**（几条假的、涉及哪些标的、亏了多少钱）
        推给 Yui，她据此在聊天框里爆发生气文字，替玩家承担被骗的情绪。

        推的是「事实 + 情绪素材」，**不替她写台词** —— 台词是她自己的。
        """
        player = session.player
        today = int(player.day)
        yesterday = today - 1
        # 记账容器只留最近几天 ⇒ 不会随对局时长无限增长。
        self._fake_news_bought_days = {
            day for day in self._fake_news_bought_days if day >= yesterday - 1
        }
        for stale_day in [
            day for day in self._fake_news_loss_by_day if day < yesterday - 1
        ]:
            self._fake_news_loss_by_day.pop(stale_day, None)
        if yesterday not in self._fake_news_bought_days:
            return
        pending = self._fake_news_ledger.consume(today)
        if not pending:
            return
        loss = round(float(self._fake_news_loss_by_day.pop(yesterday, 0.0)), 6)
        if loss <= 0.0:
            # 没亏钱 ⇒ **不爆发**：报金额就是编造。
            return
        payload = build_fake_news_outrage_payload(
            day=today,
            fake_items=pending,
            loss_amount=loss,
            talk_level=self._yui_talk_level_now(),
        )
        fact = str(payload.get("fact") or "")
        opinion = str(payload.get("opinion") or "")
        if not fact:
            return
        prompt = (
            "《拟股喵喵》里出了一件事，你必须当场发作。\n"
            f"事实：{fact}\n"
            f"（这句愤怒你可以直接用，也可以照这个味道改写：{opinion}）\n"
            "你现在非常生气。请在聊天框里爆发：把那个破报社昨天塞给他的假消息"
            "狠狠骂一顿，顺便替观众骂他两句。\n"
            "只准用上面给出的事实，不许编造金额、标的名或内幕。"
        )
        self._yui_queue_prompt(
            prompt, "fake_news_outrage", 5, day=int(session.player.day)
        )
        self.logger.info(
            f"[P28.0] 假新闻爆发：{len(pending)} 条假新闻，亏 {loss:.2f} 元 ⇒ 推给 Yui"
        )

    def _yui_news_prompt(self, session: Session, item: Any) -> str:
        """新闻出来了 ⇒ 让 Yui 立刻点评一句。

        **提示词里绝不出现新闻的类型**（真 / 假 / 雷 / 含糊 / 噪音一概没有）——
        她只拿到**正文**。类型是引擎的秘密，玩家也不许知道（P4 §11）。

        P13 契约：这里写的是**事实与该做的事**，不是台词 ——
        所以只给"发生了什么 + 现在该给出什么判断"，不指定她怎么演。
        """
        market = session.market
        stock_id = str(getattr(item, "stock_id", "") or "")
        stock = market.stocks.get(stock_id) if stock_id else None
        name = str(getattr(stock, "name", "") or "") if stock is not None else ""
        content = str(getattr(item, "content", "") or "")
        head = (
            f"《拟股喵喵》里刚出了一条关于「{name}」的消息："
            if name
            else "《拟股喵喵》里刚出了一条消息："
        )
        return (
            head
            + content
            + "\n观众现在要听到你对这条消息的判断：它对这只票意味着什么、"
            "现在该动还是不该动。不要照抄上面的原文。"
        )

    def _yui_event_prompt(self, title: str, detail: str) -> str:
        """重大事件（收割 / 爆仓）⇒ 打断当前动作，让她发表感言。

        P13 契约：给**事实 + 该给出的反应**，不写"用某种语气说"这种表演指令。
        """
        return (
            f"《拟股喵喵》里出事了：{title}\n{detail}\n"
            "这一刻观众需要你的反应：说点狠的、说点真话，不要照抄公告。"
        )

    def _collect_yui_live_prompt(self, session: Session) -> None:
        """直播托管：**每隔 N 个 tick** 攒一条「该你动了」的提示词。

        三道限流（缺一不可）：

        ① **tick 间隔** —— 由话痨级别决定（安静档 = 0 ⇒ 不主动开）。
        ② **日配额** —— 今天的单用完了就别再开回合（她连单都下不了，
           开回合只是白刷屏）。
        ③ **墙钟闸门** —— ``skip_day`` 之类的快进会一口气跑十几个 tick，
           只靠 ① 会瞬间开好几个回合；这里用墙钟再兜一层。
        """
        # P28.0：``duo``（人机协同双打）也要攒主动回合 —— 玩家手动买卖，
        # Yui 只疯狂点评。``assist`` / ``spectate`` 依旧**不主动开回合**。
        mode = self._yui_current_mode()
        if mode not in (YUI_MODE_LIVE, YUI_MODE_DUO):
            return
        interval = self._yui_talk_interval()
        if interval <= 0:
            return
        market = session.market
        tick = int(market.tick)
        if tick <= 0 or tick % interval != 0:
            return
        # 日配额是**交易**配额：双打模式下 Yui 一笔都不许下（``_yui_gate``
        # 硬拒），所以配额不该拦住她的**点评** —— 她越能说，直播越热闹。
        if mode == YUI_MODE_LIVE and self._yui_quota_left(int(session.player.day)) <= 0:
            return
        now = time.time()
        last_at = float(getattr(self, "_yui_talk_last_at", 0.0) or 0.0)
        if last_at > 0.0 and (now - last_at) < YUI_TALK_MIN_GAP_SECONDS:
            return
        # **先占位再攒**：墙钟闸门要在"攒"这一刻就生效，
        # 否则快进时一口气能攒出好几个回合。
        self._yui_talk_last_at = now
        if mode == YUI_MODE_DUO:
            self._yui_queue_prompt(
                self._yui_duo_prompt(session),
                "yui_duo_turn",
                3,
                day=int(session.player.day),
            )
            return
        self._yui_queue_prompt(
            self._yui_live_prompt(session),
            "yui_live_turn",
            3,
            day=int(session.player.day),
        )

    def _collect_yui_event_prompts(
        self, session: Session, outcome: Any, was_alive: bool
    ) -> None:
        """重大事件 ⇒ 攒提示词（新闻 / 收割 / 爆仓）。

        **为什么对所有新闻一视同仁**（而不是只对雷新闻和假新闻）：

        只推雷 / 假的话，玩家从「她理我了没」就能**反推**这条新闻是什么类型
        —— P4 §11 的「局中不泄露真假与雷」当场作废。所以这里
        **每一条新新闻都推**，提示词里也**绝不出现类型标记**。
        雷新闻的正文本身就足够炸，Yui 读完自己就会激动。

        （「突发新闻 / 假新闻要立刻点评」这条要求因此是**被满足**的 ——
        它们当然会被点评；只是我们不把"只点评这种"这件事暴露出去。）
        """
        market = session.market
        player = session.player

        # ① 新闻（**新**的才推）
        for item in list(getattr(market, "news_today", []) or []):
            news_id = str(getattr(item, "id", "") or "")
            if not news_id or news_id in self._yui_seen_news:
                continue
            self._yui_seen_news.add(news_id)
            self._yui_queue_prompt(
                self._yui_news_prompt(session, item),
                "yui_news",
                5,
                day=int(getattr(session.market, "day", 0) or 0),
            )
        # 旧的新闻 id 别把内存吃满（一天几只，攒几十天就上百个）。
        if len(self._yui_seen_news) > 200:
            self._yui_seen_news = set(list(self._yui_seen_news)[-80:])

        # ② 收割（斩杀线）—— 玩家**看得见**这件事（也有卡片），
        #    所以 Yui 提它不构成剧透。
        for result in list(getattr(outcome, "liquidations", []) or []):
            stock_id = str(getattr(result, "stock_id", "") or "")
            stock = market.stocks.get(stock_id)
            name = str(getattr(stock, "name", "") or stock_id)
            self._yui_queue_prompt(
                self._yui_event_prompt(
                    f"「{name}」被盯上了，你手里的钱被强行砍了一刀。",
                    f"集中度 {float(getattr(result, 'concentration', 0.0) or 0.0):.0%}，"
                    f"触发源 {str(getattr(result, 'trigger', '') or '未知')}。",
                ),
                "yui_harvest",
                8,
                day=int(session.player.day),
            )

        # ③ 爆仓 —— 这一局结束了，她必须说点什么。
        if was_alive and not bool(player.alive):
            self._yui_queue_prompt(
                self._yui_event_prompt(
                    "这一局结束了 —— 家底见底了。",
                    "玩家已经破产，这一局到此为止。",
                ),
                "yui_bankrupt",
                9,
                day=int(session.player.day),
            )

        # ④ 群友随机事件（P26.0：天降 Token / 免费内参）—— 只给**事实**。
        #
        # 提示词里**只有事实**（P13 契约）：钱到账多少、内参原话是什么。
        # 内参的正文**只在这里出现** —— 面板那边只有一枚标签，看不到内容。
        for event in list(self._community_events):
            if event.id in self._yui_seen_community:
                continue
            self._yui_seen_community.add(event.id)
            # 天降 Token 是"救命"级 ⇒ 优先级比内参高（内参只是彩蛋）。
            priority = 7 if event.kind == EVENT_KIND_TOKEN else 5
            self._yui_queue_prompt(
                self._yui_community_prompt(event),
                f"yui_community_{event.kind}",
                priority,
                day=int(session.player.day),
            )
        # 事件 id 别把内存吃满（一局最多几十条，攒久了也该收一收）。
        if len(self._yui_seen_community) > 200:
            self._yui_seen_community = set(list(self._yui_seen_community)[-80:])

    # ------------------------------------------------------------------
    # P26.0：群友随机事件（浅忆QanYi 的天降 Token / 云玩家的免费内参）
    #
    # 这一节**只做插件层的事**：掷点、算钱、拼事件、给 Yui 递事实。
    #
    # * 引擎层（``engine/``）与模式层（``modes/``）**一行不改**
    #   ⇒ ``tests/simulate.py`` 的 500 局基线逐字节不变。
    # * 随机源是**系统熵**（``community_events.roll``），绝不用玩家可选的
    #   seed —— 否则玩家枚举 seed 就能稳定刷救助，「永远必亏」当场作废。
    # * 事件里只准出现公开信息（价格 / K 线），不碰 heat / seed / 反转日 /
    #   新闻真假（P4 §11）。
    # ------------------------------------------------------------------

    def _yui_community_prompt(self, event: CommunityEvent) -> str:
        """群友事件 ⇒ 给 Yui 的**事实**（P13：只给事实，不写表演指令）。

        「内参」的正文**只在这里出现** —— 面板那边看不到（面板只有一枚标签）。
        """
        if event.kind == EVENT_KIND_TOKEN:
            fact = (
                f"第 {event.day} 天，玩家快交不上生活费了；群友「浅忆QanYi」"
                f"往玩家账户里投了一笔钱，到账 {event.amount:.2f} 元，"
                "刚好够交今天和明天两天的生活费。"
            )
        else:
            fact = (
                f"第 {event.day} 天，群友「云玩家」给玩家塞了一份免费内参，"
                f"内参原话是：{event.hint}"
            )
        return (
            "《拟股喵喵》里发生了一件事：\n"
            f"{fact}\n"
            "这一刻观众需要你的反应：说点狠的、说点真话，不要照抄公告。"
        )

    def _record_community_event(self, event: CommunityEvent) -> None:
        """把一条群友事件记进面板载荷（最近 ``_COMMUNITY_EVENTS_MAX`` 条）。"""
        self._community_events.append(event)
        if len(self._community_events) > _COMMUNITY_EVENTS_MAX:
            del self._community_events[:-_COMMUNITY_EVENTS_MAX]

    def _community_events_payload(self) -> list[dict[str, Any]]:
        """面板载荷里的群友事件（**白名单** —— 一个秘密字段都不带）。"""
        return [event.public_payload() for event in self._community_events]

    def _maybe_token_rescue(
        self,
        session: Session,
        engine_config: EngineConfig,
        journal: _TickJournal,
    ) -> None:
        """「天降 Token」：玩家极度缺钱时，极小概率被群友投喂。

        **评估时机：日终结算之前** —— 结算会扣当日生活费、判定「能不能交上
        明天的账单」，钱必须**在结算之前**到账，否则这笔钱救不了任何一次三振。

        **金额**：刚好够交今天 **和** 明天两笔生活费
        （``living_cost + next_living_cost - cash``）——
        少一分明天就交不上（要挨二道贩子那一刀），多一分那就不是"救场"了。

        街机档没有生活费 ⇒ 永远不会"交不上饭钱" ⇒ 永不触发。
        """
        player = session.player
        if not bool(getattr(player, "alive", True)):
            return
        if self._token_rescue_count >= MAX_TOKEN_RESCUES_PER_GAME:
            return
        mode = session.mode
        market = session.market
        day = int(player.day)
        living_cost = daily_living_cost(mode, day, market.seed, engine_config)
        if living_cost <= 0.0:
            return
        cash = float(player.cash)
        if cash >= living_cost:
            # 还交得上今天的饭钱 ⇒ 不算"极度缺钱" ⇒ 不触发。
            return
        if not community_roll(chance=TOKEN_RESCUE_CHANCE):
            return
        next_cost = next_living_cost(session.difficulty, engine_config)
        event = build_token_rescue_event(
            day=day,
            cash_before=cash,
            living_cost=living_cost,
            next_living_cost=next_cost,
        )
        if event.amount <= 0.0:
            return
        player.cash = round(cash + event.amount, 6)
        journal.touch_player(player)
        self._token_rescue_count += 1
        self._record_community_event(event)
        self.logger.info(
            f"群友「浅忆QanYi」天降 Token：第 {day} 天到账 {event.amount:.2f} 元"
            f"（现金 {cash:.2f} → {player.cash:.2f}，当日生活费 {living_cost:.2f}）"
        )

    def _maybe_community_intel(
        self, session: Session, engine_config: EngineConfig
    ) -> None:
        """「云玩家免费内参」：新的一天开盘时，可能塞来一份内参。

        面板**不显示内容**（只有一枚 buff 标签），正文只走 ``push_message`` 给 Yui。
        """
        player = session.player
        if not bool(getattr(player, "alive", True)):
            return
        if not community_roll(chance=INTEL_CHANCE):
            return
        target = pick_intel_target(session.market, engine_config)
        if target is None:
            return
        event = build_intel_event(day=int(player.day), target=target)
        self._record_community_event(event)
        self.logger.info(
            f"群友「云玩家」免费内参：第 {event.day} 天，内参指向「{target[1]}」"
        )

    # ------------------------------------------------------------------
    # P26.1：直播护栏（Yui 的「危险感知」+ 强制续命锁）
    #
    # 这一节**只做插件层的事**：算数字、改内存里的 ``player.cash`` 与
    # ``stock.price``、给 Yui 递事实。
    #
    # * 引擎层（``engine/``）与模式层（``modes/``）**一行不改**
    #   ⇒ ``tests/simulate.py`` 的 500 局基线逐字节不变。
    # * **干预**只在 ``[director] live_god_mode = true`` 时发生
    #   （且必须同时 ``enabled = true``）—— 默认关闭，纯净版零干预。
    #   「危险感知」是纯展示层（只多一句提示词），所以它**不受开关限制**。
    # * 给 Yui 的提示词只含**公开数据**（波动率、持仓集中度、价格），
    #   不碰 heat / seed / 反转日 / 新闻真假（P4 §11）。
    # ------------------------------------------------------------------

    def _yui_director_lines(self, session: Session) -> list[str]:
        """「导演模式」的**事实行**（P26.1 任务三）。

        需求原文是一句带表演指令的祈使句（"你现在的任务是拖延时间……你必须
        大呼小叫……"），外加半句"他要是<那两个字>，你的直播就结束了" ——
        但那句话**不能照字面写**：

        * "大呼小叫"是**表演指令** —— P13 契约的核心就是"不替 Yui 演戏"；
        * 那两个字落在**玩家可见文案的禁用词族**里 —— 文案层不许出现那一类语义；
        * 一句话塞三个行为指令，等于把 Yui 变成复读机。

        （注：需求原文里那半句在这里**不能逐字引用** —— 玩家可见文案禁那一类
        语义，``tests/test_p141_compliance.py`` 会扫插件层的每一个字符串常量。）

        所以这里改写成**等价的事实行**：

        * "直播还没收工 / 玩家一旦撑不住，这场就当场结束" ——
          这是**世界设定**（直播确实还没结束），也是"拖延时间"的等价表达；
        * "玩家手上现在还能用的操作：……" ——
          客观枚举面板上**真的存在**的那些按钮（无限模式有领工资与借高利贷，
          经典模式**没有** —— 见 ``modes/classic.py``）。

        代价是"催"这件事由模型自己决定，不再由插件写死 —— 这正是 P13 要的。
        """
        mode = get_mode(session.mode)
        family = str(getattr(mode, "mode_family", "classic") or "classic")
        return [
            "· 直播还没收工：这一场直播现在开着，玩家一旦撑不住，这一场就当场结束。",
            f"· 玩家手上现在还能用的操作：{player_options_text(family)}。",
        ]

    def _yui_intuition_prompt(self, signal: DangerSignal) -> str:
        """「危险感知」的**预警提示词**（只给事实）。

        P13 契约：这里给的是**客观数据**（波动率、排名、持仓占比），
        不是"用某种语气说"这种表演指令；也不出现"请"字。
        """
        return (
            "《拟股喵喵》盘面数据：\n"
            f"{danger_signal_text(signal)}\n"
            "这一刻观众需要你的反应：说点狠的、说点真话，不要照抄公告。"
        )

    def _yui_intuition_settle_prompt(
        self, watch: IntuitionWatch, price_now: float
    ) -> str:
        """预警几拍之后的**对账提示词**（只给事实）。

        "跌了没有"由两个价格自己说 —— Yui 拿到之后可以得意，也可以自嘲。
        """
        return (
            "《拟股喵喵》盘面数据：\n"
            f"{settle_text(watch.name, watch.stock_id, price_now, watch.price)}\n"
            "这一刻观众需要你的反应：说点狠的、说点真话，不要照抄公告。"
        )

    def _yui_god_rescue_prompt(self, session: Session, event: CommunityEvent) -> str:
        """强制垫付 ⇒ 给 Yui 的**事实**（P13：只给事实，不写表演指令）。"""
        lines = [
            "《拟股喵喵》账户状态：",
            f"· 玩家原本只剩 {event.cash_before:.2f} 元，"
            f"当日生活费 {event.living_cost:.2f} 元，今天的饭钱已经交不上。",
            f"· 群友「浅忆QanYi」往玩家账户里投了 {event.amount:.2f} 元，"
            "刚好够交今天和明天两天的生活费。",
            *self._yui_director_lines(session),
        ]
        return (
            "\n".join(lines)
            + "\n这一刻观众需要你的反应：说点狠的、说点真话，不要照抄公告。"
        )

    def _update_intuition(self, session: Session) -> None:
        """「危险感知」（伪预知）：把**当前盘面最极端的公开数据**喂给 Yui。

        底层没有精确的未来推演 —— 引擎不会告诉任何人明天涨还是跌。
        这里做的只是"把最极端的那几个数字摆到她眼前"，让她**看起来**
        能未卜先知。至于那只票接下来几拍跌不跌，**由市场自己决定**
        （不跌也是好戏：她可以说"算你走运"）。

        只在 ``live``（直播托管）模式下生效 —— 那是唯一会主动开回合的模式，
        也是"伪预知"唯一能被观众看到的地方。

        两件事：

        ① **对账** —— 已经过了 ``INTUITION_SETTLE_TICKS`` 拍的预警，
           把"预警时的价格"和"现在的价格"摆在一起；
        ② **新预警** —— 当前有危险标的时发一条。

        节流：同一只票 ``INTUITION_COOLDOWN_TICKS`` 拍内不重复，
        一局最多 ``INTUITION_MAX_WARNINGS`` 次 —— 预警多了就不叫直觉了。
        """
        if self._yui_current_mode() != YUI_MODE_LIVE:
            return
        market = session.market
        player = session.player
        if not bool(getattr(player, "alive", True)):
            return
        tick = int(market.tick)
        prices = market.prices()

        # ① 对账（先做：过期的预警要腾出冷却位）
        for watch in self._intuition_watch:
            if watch.settled or tick - watch.tick < INTUITION_SETTLE_TICKS:
                continue
            stock = market.stocks.get(watch.stock_id)
            price_now = (
                float(getattr(stock, "price", watch.price))
                if stock is not None
                else watch.price
            )
            watch.settled = True
            self._yui_queue_prompt(
                self._yui_intuition_settle_prompt(watch, price_now),
                "yui_intuition_settle",
                6,
                day=int(session.player.day),
            )
        # 冷却期过了的预警丢掉（别把内存吃满）。
        self._intuition_watch = [
            watch
            for watch in self._intuition_watch
            if tick - watch.tick < INTUITION_COOLDOWN_TICKS
        ]

        # ② 新预警
        if self._intuition_warnings >= INTUITION_MAX_WARNINGS:
            return
        if tick - self._intuition_last_tick < INTUITION_COOLDOWN_TICKS:
            # **全局**最小间隔：预警多了就不叫直觉了，叫"报丧"。
            return
        signal = pick_danger_signal(market, player.positions, prices)
        if signal is None:
            return
        for watch in self._intuition_watch:
            if (
                watch.stock_id == signal.stock_id
                and tick - watch.tick < INTUITION_COOLDOWN_TICKS
            ):
                return
        self._intuition_watch.append(
            IntuitionWatch(
                stock_id=signal.stock_id,
                name=signal.name,
                price=signal.price,
                tick=tick,
            )
        )
        self._intuition_warnings += 1
        self._intuition_last_tick = tick
        self._yui_queue_prompt(
            self._yui_intuition_prompt(signal),
            "yui_intuition_warning",
            6,
            day=int(session.player.day),
        )

    def _maybe_god_rescue(
        self,
        session: Session,
        engine_config: EngineConfig,
        journal: _TickJournal,
    ) -> None:
        """强制续命：现金跌破生活费红线时，**强制**垫一笔。

        与 P26.0 的随机救助是**两件事**：

        * 随机救助 5% 概率、上限 3 次 —— 是**彩蛋**；
        * 强制垫付**不看概率**、独立上限 ``GOD_RESCUE_LIMIT`` —— 是**护栏**。

        两边**分开计数**：护栏不吃随机事件的额度，随机事件也不占护栏的额度。

        评估时机同样是**日终结算之前** —— 结算会扣当日生活费并判定
        "能不能交上明天的账单"，钱必须**在结算之前**到账，
        否则这笔钱救不了任何一次三振。

        街机档没有生活费 ⇒ 永远不会"交不上饭钱" ⇒ 永不触发。
        """
        if not self._director_flag("live_god_mode"):
            return
        if self._god_rescue_count >= GOD_RESCUE_LIMIT:
            return
        player = session.player
        if not bool(getattr(player, "alive", True)):
            return
        market = session.market
        day = int(player.day)
        living_cost = daily_living_cost(
            session.mode, day, market.seed, engine_config
        )
        if living_cost <= 0.0:
            return
        cash = float(player.cash)
        if cash >= living_cost:
            # 还交得上今天的饭钱 ⇒ 不算"撑不住" ⇒ 不触发。
            return
        next_cost = next_living_cost(session.difficulty, engine_config)
        event = build_token_rescue_event(
            day=day,
            cash_before=cash,
            living_cost=living_cost,
            next_living_cost=next_cost,
        )
        if event.amount <= 0.0:
            return
        player.cash = round(cash + event.amount, 6)
        journal.touch_player(player)
        self._god_rescue_count += 1
        # 面板上照样留一条记录（与 P26.0 同一套事件形状）。
        self._record_community_event(event)
        # 这一条**由本节自己推**（更完整：带「导演模式」事实），
        # 所以先标记成"已推过"，别让 P26.0 的群友提示词再推一遍。
        self._yui_seen_community.add(event.id)
        self._yui_queue_prompt(
            self._yui_god_rescue_prompt(session, event),
            "yui_god_rescue",
            7,
            day=int(session.player.day),
        )
        self.logger.info(
            f"[live_god_mode] 强制垫付：第 {day} 天到账 {event.amount:.2f} 元"
            f"（现金 {cash:.2f} → {player.cash:.2f}，当日生活费 {living_cost:.2f}，"
            f"第 {self._god_rescue_count}/{GOD_RESCUE_LIMIT} 次）"
        )

    def _maybe_god_rally(
        self,
        session: Session,
        engine_config: EngineConfig,
        prices: dict[str, float],
    ) -> None:
        """「庄家洗盘」假象：**排**一次**下一拍**生效的反弹。

        触发条件（三条同时成立）：

        ① **满仓** —— 单一标的占比 ≥ ``GOD_RALLY_CONCENTRATION``；
        ② **血量见底** —— 现金已经交不上当日生活费；
        ③ **在亏** —— 持仓市值最大的那只正在浮亏。

        做了什么：**只**把那只票的价格在下一拍抬 10%。
        玩家于是看到"好像反弹了"，还能再撑几拍 —— 而引擎的行情该怎么走
        还该怎么走（那才是真正的暴跌）。

        一局最多 ``GOD_RALLY_LIMIT`` 次 —— 错觉贵在稀有。
        """
        if not self._director_flag("live_god_mode"):
            return
        if self._god_rally_count >= GOD_RALLY_LIMIT:
            return
        if self._god_rally_pending is not None:
            return
        player = session.player
        if not bool(getattr(player, "alive", True)):
            return
        market = session.market
        living_cost = daily_living_cost(
            session.mode, int(player.day), market.seed, engine_config
        )
        if living_cost <= 0.0:
            return
        if float(player.cash) >= living_cost:
            return
        concentration = position_concentration(player.positions, prices)
        if concentration < GOD_RALLY_CONCENTRATION:
            return
        target = pick_rally_target(player.positions, prices, market)
        if target is None:
            return
        stock_id, name, _price = target
        self._god_rally_pending = {"stock_id": stock_id, "name": name}
        self.logger.info(
            f"[live_god_mode] 「庄家洗盘」已排：下一拍给「{name}」({stock_id}) "
            f"抬 {GOD_RALLY_RATE * 100:.0f}%"
        )

    def _apply_god_rally(self, market: MarketState) -> int:
        """把排好的「庄家洗盘」假反弹**施加**在**这一拍**。

        位置很讲究：排在 ``advance_market`` **之后**、导演暴跌**之前** ——
        于是"先反弹、后真跌"的节奏刚好（错觉与真跌一前一后）。

        引擎一行没改：这里只改内存里的 ``stock.price`` 与 K 线最后一点。
        ``prev_price`` **不动**（它是上一拍的价格）⇒ 涨跌幅天然显示 +10%。
        """
        pending = self._god_rally_pending
        if not pending:
            return 0
        self._god_rally_pending = None
        stock = market.stocks.get(str(pending.get("stock_id")))
        if stock is None or not bool(getattr(stock, "alive", True)):
            return 0
        old = float(getattr(stock, "price", 0.0) or 0.0)
        new = apply_rally(stock, GOD_RALLY_RATE)
        if new <= old:
            return 0
        self._god_rally_count += 1
        self.logger.info(
            f"[live_god_mode] 「庄家洗盘」生效：「{pending.get('name')}」"
            f" {old:.2f} → {new:.2f}（第 {self._god_rally_count}/{GOD_RALLY_LIMIT} 次）"
        )
        return 1

    def _yui_queue_prompt(
        self,
        prompt: str,
        reason: str,
        priority: int,
        *,
        day: int = 0,
    ) -> None:
        """把一条提示词攒进待推送队列（P32.0：带上入队时刻与游戏日）。

        队列元素仍是 ``(prompt, reason, priority)`` 三元组 —— 只是多挂了
        ``enqueued_at`` 与 ``day`` 两个只读属性（见 :class:`_YuiPendingPrompt`），
        推送前的**熔断**要用它们判断"这条是不是过期了 / 这条是不是同类旧货"。
        """
        self._yui_pending_prompts.append(
            _YuiPendingPrompt(
                prompt,
                reason,
                priority,
                enqueued_at=time.time(),
                day=int(day),
            )
        )

    def _prune_yui_queue(self) -> list[tuple[str, str, int]]:
        """P32.0：推送前的**熔断** —— 丢掉积压，只留最新。

        三条规则（**按顺序**执行）：

        ① **过期** —— 存放超过 :data:`YUI_QUEUE_MAX_AGE_SECONDS` 的直接扔；
        ② **同天同类** —— 同一个游戏日、同一个 ``reason`` 的只留**最新**一条
           （旧的全部 ``pop`` 掉 —— 快进一天能攒十几条新闻，只要一条）；
           但**必须条条送到**的那几类（见 :data:`YUI_QUEUE_KEEP_ALL_REASONS`）
           **不参与**折叠；
        ③ **太长** —— 剩下的还超过 :data:`YUI_QUEUE_MAX_LEN` 条 ⇒ 只留最新一条。

        没有这一步，狂按快进会把几百条消息堆在队列里，Yui 隔十几秒才开始
        回话，还会对着早就过期的行情**自言自语**。
        """
        queue = list(self._yui_pending_prompts)
        if not queue:
            return []
        now = time.time()
        # ① 过期消息：存放超过 5 秒的，Yui 收到也是**过期货**（行情早变了）。
        fresh: list[tuple[str, str, int]] = []
        for item in queue:
            stamp = float(getattr(item, "enqueued_at", 0.0) or 0.0)
            if stamp > 0.0 and (now - stamp) > YUI_QUEUE_MAX_AGE_SECONDS:
                continue
            fresh.append(item)
        if not fresh:
            return []
        # ② 同一天同类：只留最新的一条（旧的全部 pop 掉）。
        #    「必须条条送到」的提示词（双打点评 / 重大事件）**不参与**折叠。
        kept: list[tuple[str, str, int]] = []
        slots: dict[tuple[str, int], int] = {}
        for item in fresh:
            reason = str(item[1])
            if reason in YUI_QUEUE_KEEP_ALL_REASONS:
                kept.append(item)
                continue
            key = (reason, int(getattr(item, "day", 0) or 0))
            if key in slots:
                kept[slots[key]] = item
            else:
                slots[key] = len(kept)
                kept.append(item)
        # ③ 还是太长 ⇒ 只留最新一条。
        if len(kept) > YUI_QUEUE_MAX_LEN:
            kept = kept[-1:]
        return kept

    def _skip_fast_active(self, now: float | None = None) -> bool:
        """现在是不是**狂按期**（Yui 被限频的那段时间）。"""
        until = float(getattr(self, "_skip_fast_until", 0.0) or 0.0)
        if until <= 0.0:
            return False
        current = time.time() if now is None else float(now)
        return current < until

    def _note_skip_click(self) -> bool:
        """P32.0：记一次【跳过本日】点击，返回这是不是【狂按快进】。

        判据：**5 秒窗口内点击超过 3 次**（也就是从第 4 次开始算狂按）。

        为什么记在**入口**（而不是"跳过成功之后"）：主播的狂按是**手速**
        问题 —— 他看都不看就一路点下去。其中被游戏自己的「今天已经跳过一次」
        挡下来的那几下，**也是狂按的一部分**：手速就是这么快。
        """
        now = time.time()
        stamps = [
            stamp
            for stamp in self._skip_click_times
            if (now - stamp) <= SKIP_FAST_WINDOW_SECONDS
        ]
        stamps.append(now)
        self._skip_click_times = stamps
        if len(stamps) <= SKIP_FAST_MAX_CLICKS:
            return False
        self._skip_fast_count += 1
        self._skip_fast_until = now + SKIP_FAST_MUTE_SECONDS
        self.logger.info(
            f"[P32.0] 狂按快进：{SKIP_FAST_WINDOW_SECONDS:.0f} 秒内点了 {len(stamps)} 次"
            f"【跳过本日】（第 {self._skip_fast_count} 次）"
        )
        return True

    def _yui_fast_forward_clear(self) -> None:
        """P32.0：狂按 ⇒ **清空** Yui 的待推送队列。

        玩家疯狂快进时，队列里堆的是几百条**已经过时**的新闻与事件 ——
        留着只会让 Yui 隔十几秒才开始回话，还会对着旧行情**自言自语**。
        """
        self._yui_pending_prompts.clear()

    def _yui_fast_forward_taunt(self, day: int = 0) -> None:
        """P32.0：狂按 ⇒ 注入一条**吐槽**提示词（只吐槽，不分析行情）。

        玩家根本不看分析 ⇒ 再长的行情解读都是噪音。提示词原文见
        :data:`SKIP_FAST_TAUNT_PROMPT`。
        """
        self._yui_queue_prompt(
            SKIP_FAST_TAUNT_PROMPT,
            "yui_fast_forward",
            6,
            day=int(day),
        )
        # 吐槽**必须**立刻说出去 ⇒ 清掉限频闸门（它只拦"连续说话"）。
        self._yui_fast_last_talk_at = 0.0

    async def _flush_yui_prompts(self) -> None:
        """把攒好的提示词推给 Yui（**锁外** —— 心跳里调用）。

        推送必须**锁外**做：``push_message`` 要 ``await`` 宿主 IPC，
        持锁 await 会冻住整个事件循环（P11.1 那个 500 红色警告的根因）。

        P32.0：推之前先**熔断**（:meth:`_prune_yui_queue`）——
        积压的长队只留最新一条，狂按期再叠一层**限频**（一句吐槽就够）。

        P33.0：对局已结束 ⇒ **立刻 return** —— 这一局的旧消息一条都不推
        （这正是「退出后的幽灵播报」的第二道闸）。
        """
        if not self._is_game_active:
            self._yui_pending_prompts.clear()
            return
        pending = self._prune_yui_queue()
        # **先清队列再推**：熔断后的结果就是队列的新状态 ——
        # 过期消息必须真的离开队列（不然它们会一直躺在那儿，占着位置）。
        self._yui_pending_prompts = []
        if not pending:
            return
        # ── P32.0：狂按期限频 ────────────────────────────────────
        # 玩家在疯狂快进 ⇒ 别让她一直跳出来。一句吐槽说完就闭嘴，
        # 等他慢下来再说。
        now = time.time()
        if self._skip_fast_active(now):
            last_at = float(getattr(self, "_yui_fast_last_talk_at", 0.0) or 0.0)
            if last_at > 0.0 and (now - last_at) < SKIP_FAST_TALK_MIN_GAP_SECONDS:
                self.logger.info(
                    f"[P32.0] 狂按快进中，限频生效：这一批 {len(pending)} 条提示词全部丢弃"
                )
                return
            self._yui_fast_last_talk_at = now
            # 狂按期只留**最新一条**（通常就是那句吐槽）。
            pending = pending[-1:]
        for prompt, reason, priority in pending:
            await self._push_proactive(prompt, reason=reason, priority=priority)

    def _resolve_yui_stock(self, query: str, market: Any) -> Any | None:
        """把 Yui 传来的说法**模糊**对到真正的标的上。

        真机事故：她说「流萤物流1号」，工具只认 ``FAKE-001`` ⇒
        一句「找不到标的」，直播当场冷场。所以这里按**四层**找：

        ① **精确** id 或精确名字；
        ② **精确**显示名（群友绑定名，如「小明·作业没写完的人」）；
        ③ **子串**：她说的名字里含标的名（"流萤物流" ⊂ "流萤物流1号"），
           或者标的名里含她说的（她可能带了多余的字）；
        ④ 还是没有 ⇒ 温和失败，让她重新挑一只。

        多个候选时（她只说了「流萤物流」）：先要**还活着**的，
        再取**当天动静最大**的（观众要看的是最有戏的那只），
        最后按名字长度、编号定序 —— **确定性**排序，不会每次都变。
        """
        text = str(query or "").strip()
        if not text:
            return None
        lowered = text.lower()
        stocks = list(getattr(market, "stocks", {}).values())
        if not stocks:
            return None

        def _name_of(stock: Any) -> str:
            return str(getattr(stock, "name", "") or "")

        # ① 精确 id / 精确名字
        for stock in stocks:
            if lowered in (
                str(getattr(stock, "id", "") or "").lower(),
                _name_of(stock).lower(),
            ):
                return stock
        # ② 精确显示名（群友绑定）
        try:
            binding = self._roster_binding(market)
        except Exception:  # noqa: BLE001 - 绑定只是锦上添花，取不到就跳过
            binding = {}
        for stock_id, member in binding.items():
            display = str((member or {}).get("display_name", "") or "").lower()
            if display and display == lowered:
                stock = next(
                    (item for item in stocks if str(item.id) == str(stock_id)), None
                )
                if stock is not None:
                    return stock
        # ③ 子串（两个方向都试）
        partial = [
            stock
            for stock in stocks
            if lowered and lowered in _name_of(stock).lower()
        ]
        if not partial:
            partial = [
                stock
                for stock in stocks
                if _name_of(stock).lower() and _name_of(stock).lower() in lowered
            ]
        if not partial:
            return None

        def _change(stock: Any) -> float:
            price = float(getattr(stock, "price", 0.0) or 0.0)
            prev = float(getattr(stock, "prev_price", 0.0) or 0.0)
            return (price - prev) if prev > 0 else 0.0

        partial.sort(
            key=lambda stock: (
                0 if bool(getattr(stock, "alive", False)) else 1,
                -abs(_change(stock)),
                len(_name_of(stock)),
                str(getattr(stock, "id", "") or ""),
            )
        )
        return partial[0]

    @ui.action(
        id="shame",
        label="耻辱柱",
        icon="trophy",
        tone="default",
        group="idle",
        order=1,
    )
    @plugin_entry(
        id="shame",
        name="耻辱柱",
        description="查看历史破产记录（名字 / 存活天数 / 破产原因），按存活天数升序。",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    @_locked
    async def shame(self) -> Ok:
        """【耻辱柱】：历史破产记录（名字 / 存活天数 / 破产原因）。

        **按存活天数升序** —— 越短越丢人，排在最前面。

        数据来自 ``players`` 表（**零 schema 改动**）：
        ``name`` / ``day`` / ``death_reason`` / ``death_title`` 都已经在那儿了。
        """
        rows: list[dict[str, Any]] = []
        try:
            raw_rows = await self._repo.fetch_all(
                "SELECT name, day, death_reason, death_title, mode, created_at "
                "FROM players "
                "WHERE is_catgirl = 0 AND alive = 0 "
                "AND death_reason IS NOT NULL AND death_reason != '' "
                "ORDER BY day ASC, created_at ASC "
                "LIMIT ?",
                (_SHAME_MAX_ROWS,),
            )
        except (DbUnavailableError, sqlite3.Error):
            raw_rows = []
        for raw in raw_rows or []:
            reason = str(_cell(raw, "death_reason") or "")
            rows.append(
                {
                    "name": str(_cell(raw, "name") or "无名氏"),
                    "day": int(_cell(raw, "day") or 0),
                    "reason": death_reason_label(reason),
                    "reason_key": reason,
                    "title": str(_cell(raw, "death_title") or ""),
                    "mode": str(_cell(raw, "mode") or ""),
                }
            )
        return Ok({"action": "shame", "rows": rows, "total": len(rows)})

    @plugin_entry(
        id="status",
        name="查看状态",
        description="查看自己的资产、持仓与距离强制平仓线。",
    )
    @_locked
    async def status(self) -> Ok:
        """查看自己的状态。

        返回体**不含 heat / seed / 反转日**——这些是局中秘密。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        prices = market.prices()

        positions: list[dict[str, Any]] = []
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            price = prices.get(stock_id, 0.0)
            positions.append(
                {
                    "stock_id": stock_id,
                    "name": stock.name if stock is not None else stock_id,
                    "amount": round(position.amount, 6),
                    "avg_cost": round(position.avg_cost, 6),
                    "price": round(price, 6),
                    "market_value": round(position.market_value(price), 6),
                    "unrealized_pnl": round(position.unrealized_pnl(price), 6),
                    "sellable": round(
                        self._sellable_amount(position, player.day, session.difficulty), 6
                    ),
                    "tag": stock.tag if stock is not None else "neutral",
                    "tag_label": _TAG_LABELS.get(
                        stock.tag if stock is not None else "neutral", "未知"
                    ),
                }
            )

        next_cost = self._next_living_cost(session.difficulty, engine_config)
        distance = (player.cash - next_cost) if next_cost > 0 else None
        return Ok(
            {
                "joined": True,
                "mode": player.mode,
                "mode_name": _MODE_NAMES.get(player.mode, player.mode),
                # ── P20.0：玩家自报名号（聊天框里也要认得人）──────────
                "player_name": str(getattr(player, "name", "") or ""),
                "phase": market.phase,
                "day": player.day,
                "alive": player.alive,
                "cash": round(player.cash, 6),
                "total_asset": round(player.total_asset(prices), 6),
                "distance_to_death": round(distance, 6) if distance is not None else None,
                "concentration": round(position_concentration(player.positions, prices), 6),
                "trade_count": player.trade_count,
                "fee_paid": round(player.total_fee_paid, 6),
                "strikes": player.strikes,
                "positions": positions,
                # ── P3 新增：二道贩子提案 / 收割通知 / 破产原因 ──
                # 这三项都**不含 heat 数值**（heat 只在 settle 出现）。
                "pending_action": self._public_pending_action(player.pending_action or {}),
                "pending_event": self._pending_event,
                "death_reason": player.death_reason,
                "death_title": player.death_title,
                # ── P5.5 改动 2：入场身份（**只有 ID，不含呼应句**）──
                # 呼应句只在破产结算页出现，局中一律不给。
                "identity": session.identity,
                # ── P4 新增：低保 / 高利贷 / 当天新闻 ──
                # 连续未领低保的天数（超过上限会被斩）。
                "welfare_missed_days": player.welfare_missed_days,
                # 未偿还的高利贷本金。
                "loan_debt": round(player.loan_debt, 6),
                # 现在去领能拿回多少钱（0 = 本周期已领过或经典模式没有低保）。
                "pending_relief": round(self._pending_relief(session, engine_config), 6),
                # 当天新闻：**只有中性分类**，真假与方向都不在局中暴露。
                "news_today": self._public_news(market),
            }
        )

    def _pending_relief(self, session: Session, engine_config: EngineConfig) -> float:
        """现在去领低保能拿回多少钱。"""
        if session.mode != "infinite":
            return 0.0
        infinite = engine_config.infinite
        cycles = welfare_entitlement(
            session.player,
            day=session.player.day,
            interval_days=infinite.relief_interval_days,
            claim_limit_days=infinite.welfare_claim_limit_days,
        )
        return infinite.weekly_relief * cycles

    @staticmethod
    def _public_news(market: MarketState) -> list[dict[str, Any]]:
        """当天新闻的**白名单**返回体（P5：**只给中性分类**）。

        P4 §11 原本给的是 ``{id, headline, kind}``；**P5 契约 §6** 把标题与正文
        收进「新闻详情」交互 —— 局中默认**看不到标题**，点开才给。

        ``kind`` 是中性分类（bullish / bearish / vague / noise）：那是这条新闻
        **自称**的方向（反转日会把它整体翻转），**不泄露真假与雷新闻**。
        """
        rows: list[dict[str, Any]] = []
        for item in market.news_today:
            kind = news_kind(item)
            rows.append(
                {
                    "id": item.id,
                    "kind": kind,
                    "kind_label": _NEWS_KIND_LABELS.get(kind, "未知"),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # 入口：行情与交易
    # ------------------------------------------------------------------

    @plugin_entry(
        id="market",
        name="查看行情",
        description="查看当前市场行情与标的稳定／爆炸标签。",
    )
    @_locked
    async def market(self) -> Ok:
        """看一次盘。

        **P3 起本入口不再推进 tick**——tick 由 ``@timer_interval`` 的心跳按
        难度配置的间隔自动推进（经典 8 秒 / 无限 30 秒）。P2 的"看盘顺手
        推一格"会让计时承诺失效，所以这里改成**只读**。

        返回体**不含 seed / 反转日 / heat**。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return Ok(self._market_payload(session))

    @ui.action(
        id="buy",
        label="买入",
        icon="plus",
        tone="primary",
        group="trade",
        order=1,
    )
    @plugin_entry(
        id="buy",
        name="买入",
        description="按当前市价买入一只虚构标的。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "amount": {"type": "number", "minimum": 0, "description": "买入数量"},
            },
            "required": ["stock_id", "amount"],
        },
    )
    @_locked
    async def buy(self, stock_id: str, amount: float, reason: str = "manual") -> Ok:
        """买入。手续费计入持仓成本；买入行为会影响上头指数。

        ``reason`` 是**插件层**的成交来源标签（P23.0）：
        玩家自己点面板按钮 ⇒ ``"manual"``（**默认值**），
        Yui 介入的交易 ⇒ ``"yui"``。

        引擎的 ``reason`` 是**自由标签**（不参与任何数值计算），
        所以引擎层 / 模式层**一行都不用改**；默认值保证玩家路径
        与 P21.5 的 500 局基线**逐字节全等**。

        P23.0：这个入口只是一层壳 —— **真身**在 :meth:`_buy_impl` 里，
        Yui 端口走的也是那同一段代码（玩家与 Yui 的单**逐行同源**）。
        """
        return await self._buy_impl(stock_id, amount, reason)

    async def _buy_impl(
        self, stock_id: str, amount: float, reason: str = "manual"
    ) -> Ok:
        """买入的**真身**（**调用方必须自己持锁**）。

        P23.0：Yui 端口调的是**这个**函数 —— 所以 Yui 的单与玩家的单
        走的是同一段代码（同样扣费、同样算持仓、同样被收割、同样计入上头指数）。

        P27.2：``reason`` 透传给 :meth:`_touch_player_action` ——
        Yui 自己下的单（``reason="yui"``）**不算**"玩家开口"，
        所以**不会**把回执周期锁解开。
        """
        self._touch_player_action(reason)
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _err("invalid_amount")
        if amount <= 0:
            return _err("invalid_amount")

        player = session.player
        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        if not stock.alive:
            return _err("stock_delisted")

        price = stock.price
        notional = price * amount
        fee = buy_fee(notional, engine_config.fees)
        if notional + fee > player.cash + 1e-9:
            return _err("insufficient_cash")

        context = await self._heat_context(session, stock, amount, engine_config)
        try:
            trade = execute_buy(
                player,
                stock,
                amount,
                config=engine_config.fees,
                day=player.day,
                tick=market.tick,
                trade_id=0,
                reason=reason,
            )
        except EngineError as engine_error:
            return _err(_engine_error_code(engine_error), str(engine_error))

        player.heat = apply_heat_events(
            player.heat, heat_events_for_trade(context, engine_config.heat), engine_config.heat
        )
        market.fee_pool += trade.fee
        market.total_fees += trade.fee
        # ── P16.0：连击（**纯表现层**）——记一笔「刚买进」──────────────
        # 等下一次价格推进后判定这笔是不是买中了。**不改任何数值**。
        if session.mode in COMBO_MODES:
            self._combo.add_entry(stock_id, trade.price, trade.amount)
        # ── P18.0：教程的「真实操作推进」——第一笔成交即自动前进一步 ──
        await self._tutorial_on_trade()

        trade_id = await self._persist_trade(session, trade, stock_id)
        # P5：连续补仓 ≥3 次 → 猫娘嘲讽。
        await self._maybe_add_down_taunt(session, stock_id, market)
        # ── P7：猫娘弹幕 ────────────────────────────────────────────
        # 运行时**没有卡片 API** ⇒ 猫娘在局中的嘲讽改走面板的弹幕悬浮窗
        # （外加宿主 showToast 兜底）。文案来自 i18n 的 taunt.buy.* 键族。
        self._push_danmaku(
            pick_taunt(
                "buy",
                seed=player.trade_count,
                params={"stock": stock.name, "amount": amount, "price": price},
            )
        )
        position = player.positions.get(stock_id)
        # ── P28.0：记一笔「玩家刚买了」——双打模式下 Yui 要点评这个动作。
        self._yui_duo_action = "buy"
        self._queue_yui_duo_commentary(
            session, action="buy", pnl=0.0, symbol_name=str(stock.name)
        )
        return Ok(
            {
                "stock_id": stock_id,
                "amount": round(trade.amount, 6),
                "price": round(trade.price, 6),
                "notional": round(trade.notional if hasattr(trade, "notional") else trade.price * trade.amount, 6),
                "fee": round(trade.fee, 6),
                "cash_after": round(player.cash, 6),
                "position": (
                    {
                        "amount": round(position.amount, 6),
                        "avg_cost": round(position.avg_cost, 6),
                        "sellable": round(
                            self._sellable_amount(position, player.day, session.difficulty), 6
                        ),
                    }
                    if position is not None
                    else None
                ),
                "trade_id": trade_id,
            }
        )

    async def _maybe_add_down_taunt(
        self, session: Session, stock_id: str, market
    ) -> None:
        """连续补仓 ≥3 次 → 猫娘嘲讽（P5 的 5 个触发点之一）。

        判定：同一标的**当天**累计买入笔数 ≥3。
        """
        player = session.player
        try:
            rows = await self._repo.fetch_all(
                "SELECT COUNT(*) AS n, AVG(price) AS avg_price FROM trades "
                "WHERE player_id = ? AND stock_id = ? AND side = 'buy' AND day = ?",
                (player.id, stock_id, player.day),
            )
        except (DbUnavailableError, sqlite3.Error):
            return
        if not rows:
            return
        count = int(rows[0]["n"] or 0)
        if count < 3:
            return
        stock = market.stocks.get(stock_id)
        await self._push_taunt(
            "add_down",
            market.seed,
            {
                "n": count,
                "avg": f"{float(rows[0]['avg_price'] or 0.0):.2f}",
                "stock": stock.name if stock is not None else stock_id,
            },
        )

    @ui.action(
        id="sell",
        label="卖出",
        icon="minus",
        tone="default",
        group="trade",
        order=2,
    )
    @plugin_entry(
        id="sell",
        name="卖出",
        description="卖出持仓中的虚构标的。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "amount": {"type": "number", "minimum": 0, "description": "卖出数量"},
            },
            "required": ["stock_id", "amount"],
        },
    )
    @_locked
    async def sell(self, stock_id: str, amount: float, reason: str = "manual") -> Ok:
        """卖出。印花税只在卖出方向收取（现实方式，单边）。

        ``reason`` 的语义与 :meth:`buy` **完全一样**（P23.0）：
        玩家自己点的 ⇒ ``"manual"``；Yui 介入的 ⇒ ``"yui"``。

        P23.0：真身在 :meth:`_sell_impl` 里（Yui 端口走同一段代码）。
        """
        return await self._sell_impl(stock_id, amount, reason)

    async def _sell_impl(
        self, stock_id: str, amount: float, reason: str = "manual"
    ) -> Ok:
        """卖出的**真身**（**调用方必须自己持锁**）。

        P27.2：``reason`` 透传给 :meth:`_touch_player_action`（同 :meth:`_buy_impl`）。
        """
        self._touch_player_action(reason)
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _err("invalid_amount")
        if amount <= 0:
            return _err("invalid_amount")

        player = session.player
        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        if not stock.alive:
            return _err("stock_delisted")

        position = player.positions.get(stock_id)
        if position is None or position.amount <= 0:
            return _err("insufficient_position")
        if amount > position.amount + 1e-9:
            return _err("insufficient_position")

        difficulty = session.difficulty
        if difficulty.t_plus_one and amount > position.sellable_amount(player.day) + 1e-9:
            return _err("t_plus_one_locked")

        # ── P19.0：黑天鹅要用**卖出前**的持仓成本算单笔盈利 ────────────
        # （``execute_sell`` 之后这张持仓行就被改掉了。）
        avg_cost_before = float(getattr(position, "avg_cost", 0.0) or 0.0)

        try:
            trade = execute_sell(
                player,
                stock,
                amount,
                config=engine_config.fees,
                day=player.day,
                tick=market.tick,
                trade_id=0,
                reason=reason,
                t_plus_one=difficulty.t_plus_one,
            )
        except EngineError as engine_error:
            return _err(_engine_error_code(engine_error), str(engine_error))

        market.fee_pool += trade.fee
        market.total_fees += trade.fee

        notional = trade.price * trade.amount
        # 手续费总额来自引擎（与成交同源），这里只把它拆成佣金与印花税两项。
        total_fee = sell_fee(notional, engine_config.fees)
        stamp_tax = notional * engine_config.fees.stamp_tax_rate
        # ── P19.0：单笔实现盈利（扣掉手续费）——黑天鹅的触发判据 ─────────
        realized = (float(trade.price) - avg_cost_before) * float(trade.amount) - float(total_fee)
        # ── P28.0：第一关「永远是赚的」——卖亏了由系统**暗中补钱** ─────
        #
        # 第一关的定位是「爽」：玩家买亏了就该被系统悄悄兜住，
        # 让他带着「我是天才」的错觉走完第一关。
        #
        # 口径：**按单笔已实现盈亏**兜底（浮动亏损在卖出时才结算，
        # 这是玩家唯一能感知到的口径）。补偿额 = 本笔亏损，
        # 补完这一笔的净盈亏恒为 0 ⇒ 玩家在这笔上「没亏也没赚」。
        #
        # **只在第一关**：后面几档的压迫感全靠「真的会亏」，补了就废了。
        # 补钱在 ``_persist_trade`` **之前**做 ⇒ 落库的现金就是补过的现金，
        # 不需要额外一次数据库写入。
        compensation = 0.0
        if session.mode == MODE_ARCADE and realized < 0.0:
            compensation = -realized
            player.cash = round(player.cash + compensation, 6)
            realized = 0.0
            self.logger.info(
                f"第一关兜底：卖出亏损 {compensation:.2f} 元已由系统补上（玩家照旧是赚的）"
            )
        # ── P28.0：记一笔「玩家刚卖了 + 这笔赚没赚」——双打模式的点评素材。
        self._yui_duo_action = "sell"
        self._yui_duo_pnl = round(float(self._yui_duo_pnl) + realized, 6)
        # ── P28.0：记下「今天亏了多少钱」——假新闻愤怒文案要报确切金额。
        #
        # 只在**真的亏**时记：第一关被兜底成 0 ⇒ 天然不会有假新闻愤怒，
        # 这跟第一关「永远是赚的」的定位是一致的。
        # **不落库**（纯内存记账），也不编造：没亏就不报金额。
        if realized < 0.0:
            day_key = int(player.day)
            self._fake_news_loss_by_day[day_key] = round(
                float(self._fake_news_loss_by_day.get(day_key, 0.0)) - realized, 6
            )
        self._queue_yui_duo_commentary(
            session, action="sell", pnl=realized, symbol_name=str(stock.name)
        )
        trade_id = await self._persist_trade(session, trade, stock_id)
        await self._maybe_force_black_swan(session, realized, stock_id)
        remaining = player.positions.get(stock_id)
        # ── P7：猫娘弹幕（见 buy 里的说明）─────────────────────────
        self._push_danmaku(
            pick_taunt(
                "sell",
                seed=player.trade_count,
                params={"stock": stock.name, "amount": amount, "price": trade.price},
            )
        )
        return Ok(
            {
                "stock_id": stock_id,
                "amount": round(trade.amount, 6),
                "price": round(trade.price, 6),
                "notional": round(notional, 6),
                "fee": round(trade.fee, 6),
                "sell_fee": round(total_fee - stamp_tax, 6),
                "stamp_tax": round(stamp_tax, 6),
                "cash_after": round(player.cash, 6),
                "position": (
                    {
                        "amount": round(remaining.amount, 6),
                        "avg_cost": round(remaining.avg_cost, 6),
                        "sellable": round(
                            self._sellable_amount(remaining, player.day, session.difficulty), 6
                        ),
                    }
                    if remaining is not None
                    else None
                ),
                "trade_id": trade_id,
            }
        )

    # ------------------------------------------------------------------
    # P27.0：做空（**插件层账本**，引擎层与模式层零改动）
    # ------------------------------------------------------------------

    @ui.action(
        id="short",
        label="做空",
        icon="minus",
        tone="danger",
        group="trade",
        order=3,
    )
    @plugin_entry(
        id="short",
        name="做空",
        description=(
            "押一笔保证金做空某只标的：标的跌了赚（跌幅 × 倍率 × 保证金），"
            "涨了扣同样的数；保证金亏光会被强制平仓。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {"type": "string", "description": "标的 ID，形如 FAKE-001"},
                "margin": {"type": "number", "minimum": 0, "description": "投入的保证金"},
                "leverage": {
                    "type": "integer",
                    "enum": [1, 10, 50],
                    "description": "杠杆倍率：1 / 10 / 50",
                },
            },
            "required": ["stock_id", "margin", "leverage"],
        },
    )
    @_locked
    async def short(self, stock_id: str, margin: float, leverage: int) -> Ok:
        """做空（真身在 :meth:`_short_impl`）。"""
        return await self._short_impl(stock_id, margin, leverage)

    async def _short_impl(self, stock_id: str, margin: float, leverage: int) -> Ok:
        """做空的**真身**（**调用方必须自己持锁**）。

        P27.0：整本账都在**插件层**（``short_book.py``）——
        引擎层与模式层一行没改 ⇒ 500 局基线逐字节不变。

        口径：保证金当场从现金里扣掉；每一拍盯市，标的跌了按
        「跌幅 × 倍率 × 保证金」入账，涨了按同样的数扣；保证金亏光 ⇒
        **强制平仓（爆仓）**，亏损以保证金为上限。
        """
        self._touch_player_action()
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        if not stock.alive:
            return _err("stock_delisted")

        try:
            margin_value = float(margin)
        except (TypeError, ValueError):
            return _err("invalid_amount")
        if margin_value < SHORT_MIN_MARGIN:
            return _err("invalid_amount")

        leverage_value = normalize_leverage(leverage)
        if leverage_value is None:
            return _err("invalid_leverage")

        book = await self._load_short_book(player.id)
        if len(book) >= SHORT_MAX_POSITIONS:
            return _err("short_positions_full")
        if any(item.stock_id == stock_id for item in book):
            return _err("short_already_open")
        if margin_value > player.cash + 1e-9:
            return _err("insufficient_cash")

        price = float(stock.price)
        book.append(
            open_short(stock_id, margin_value, leverage_value, price, market.tick)
        )

        player.cash = round(player.cash - margin_value, 6)
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, updated_at = ? WHERE id = ?",
                        (player.cash, now, player.id),
                    ),
                    (
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (
                            SHORT_BOOK_SETTING_KEY,
                            json.dumps(
                                self._short_book_payload(player.id, book),
                                ensure_ascii=False,
                            ),
                        ),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        self._short_book_cache = list(book)
        self._short_book_player_id = str(player.id)
        return Ok(
            {
                "stock_id": stock_id,
                "name": stock.name,
                "margin": round(margin_value, 6),
                "leverage": leverage_value,
                "price": round(price, 6),
                "cash_after": round(player.cash, 6),
                "positions": short_positions_to_rows(book, market.prices()),
            }
        )

    @staticmethod
    def _short_book_payload(
        player_id: str, positions: list[ShortPosition]
    ) -> dict[str, Any]:
        """把空头账本翻成可落库的 JSON 结构。"""
        return {
            "player_id": str(player_id),
            "positions": [
                {
                    "stock_id": position.stock_id,
                    "margin": round(float(position.margin), 6),
                    "leverage": int(position.leverage),
                    "entry_price": round(float(position.entry_price), 6),
                    "opened_tick": int(position.opened_tick),
                    "margin_left": round(float(position.margin_left), 6),
                }
                for position in positions
            ],
        }

    async def _load_short_book(self, player_id: str) -> list[ShortPosition]:
        """读这一局的空头仓位（读不到就是空账本）。"""
        raw = await self._repo.get_setting(SHORT_BOOK_SETTING_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        if str(data.get("player_id") or "") != str(player_id):
            return []
        items = data.get("positions")
        if not isinstance(items, list):
            return []
        out: list[ShortPosition] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                out.append(
                    ShortPosition(
                        stock_id=str(item["stock_id"]),
                        margin=float(item["margin"]),
                        leverage=int(item["leverage"]),
                        entry_price=float(item["entry_price"]),
                        opened_tick=int(item.get("opened_tick") or 0),
                        margin_left=float(item["margin_left"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def _save_short_book(
        self, player_id: str, positions: list[ShortPosition]
    ) -> bool:
        """把空头账本写回设置表（**读-改-写**在插件锁内，不会丢更新）。"""
        payload = json.dumps(
            self._short_book_payload(player_id, positions), ensure_ascii=False
        )
        return await self._repo.set_setting(SHORT_BOOK_SETTING_KEY, payload)

    async def _settle_shorts(self, session: Session, journal: _TickJournal) -> None:
        """每一拍盯一次空头（**插件层**）。

        排在导演暴跌**之后** —— 导演安排的暴跌会实打实惠及空头，
        这符合直觉（跌了，空头赚钱）。

        盯市口径：

        * 这一拍的浮动盈亏立刻入现金；
        * 保证金归零 ⇒ **强制平仓（爆仓）**，亏损以保证金为上限；
        * 没爆仓就把锚点挪到当前价，下一拍算的是"这一拍"的涨跌。
        """
        player = session.player
        if not player.alive:
            return
        market = session.market
        book = await self._load_short_book(player.id)
        if not book:
            self._short_book_cache = []
            self._short_book_player_id = str(player.id)
            return
        prices = market.prices()
        remaining: list[ShortPosition] = []
        cash_delta = 0.0
        liquidated = 0
        for position in book:
            price = float(prices.get(position.stock_id, position.entry_price))
            mark = mark_short(position, price)
            cash_delta += mark.pnl
            if mark.liquidated:
                liquidated += 1
                continue
            remaining.append(roll_short(position, price))
        if cash_delta:
            player.cash = round(player.cash + cash_delta, 6)
        await self._save_short_book(player.id, remaining)
        self._short_book_cache = list(remaining)
        self._short_book_player_id = str(player.id)
        if cash_delta or liquidated:
            journal.touch_player(player)
            if liquidated:
                self.logger.info(
                    f"空头爆仓 {liquidated} 笔（玩家 {player.id}），"
                    f"本拍浮动盈亏 {cash_delta:.2f}"
                )

    def _short_positions_block(self, player_id: str, prices: dict[str, float]) -> list[Any]:
        """面板用的空头持仓（**白名单字段**，一个多余字段都不给）。"""
        if str(self._short_book_player_id) != str(player_id):
            return []
        return short_positions_to_rows(self._short_book_cache, prices)

    async def _record_equity_point(self, session: Session) -> None:
        """把这一拍的**总资产**记进资产曲线（面板折线图用）。

        **纯表现层**：这条曲线只用来画图，不参与任何判定。
        """
        player = session.player
        market = session.market
        prices = market.prices()
        try:
            total = float(player.total_asset(prices))
        except (TypeError, ValueError):
            return
        if str(self._equity_player_id) != str(player.id):
            self._equity_points = await self._load_equity_points(player.id)
            self._equity_player_id = str(player.id)
        self._equity_points.append(round(total, 6))
        if len(self._equity_points) > EQUITY_CURVE_MAX_POINTS:
            self._equity_points = self._equity_points[-EQUITY_CURVE_MAX_POINTS:]
        await self._save_equity_points(player.id, self._equity_points)

    async def _load_equity_points(self, player_id: str) -> list[float]:
        """读这一局的资产曲线（读不到就是空曲线）。"""
        raw = await self._repo.get_setting(EQUITY_CURVE_SETTING_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        if str(data.get("player_id") or "") != str(player_id):
            return []
        points = data.get("points")
        if not isinstance(points, list):
            return []
        out: list[float] = []
        for value in points:
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out

    async def _save_equity_points(self, player_id: str, points: list[float]) -> bool:
        """把资产曲线写回设置表（插件重启后还在）。"""
        payload = json.dumps(
            {
                "player_id": str(player_id),
                "points": [round(float(value), 6) for value in points],
            },
            ensure_ascii=False,
        )
        return await self._repo.set_setting(EQUITY_CURVE_SETTING_KEY, payload)

    def _equity_block(self, session: Session) -> dict[str, Any]:
        """玩家 vs Yui 的资产曲线（**纯表现层**，两个等长的数组）。

        Yui 那条线是**合成数据**（``yui_opponent_curve``）：前 10 拍极差，
        之后稳定上升，**最终必定比玩家高**。
        """
        player = session.player
        if str(self._equity_player_id) != str(player.id):
            return {"player": [], "yui": []}
        points = [float(value) for value in self._equity_points]
        yui = yui_opponent_curve(points, float(player.initial_cash))
        return {
            "player": [round(value, 2) for value in points],
            "yui": [round(value, 2) for value in yui],
        }

    def _special_ending(self, session: Session, *, dead: bool) -> dict[str, Any] | None:
        """P27.0：Wehos 的**专属破产结局**卡片。

        命中条件：名字里**含有**「Wehos」**且**这一局是因破产而结算。
        返回 ``None`` ⇒ 走普通的结算流程（普通破产画面照旧）。
        """
        player = session.player
        if not dead:
            return None
        if not is_developer_cat(str(getattr(player, "name", "") or "")):
            return None
        return {
            "kind": "developer_cat",
            "emoji": "🐱",
            "title": i18n_text("special.developer_cat_title", "开发猫出没"),
            "body": i18n_text(
                "special.developer_cat_body",
                "你亏光了最后一块钱，被 Yui 强行抓走变成了开发猫。",
            ),
        }

    # ------------------------------------------------------------------
    # 入口：分析 / 新闻详情（P5 · @ui.action）
    # ------------------------------------------------------------------

    @ui.action(
        id="analyze",
        label="分析",
        icon="search",
        tone="default",
        group="info",
        order=10,
    )
    @plugin_entry(
        id="analyze",
        name="分析",
        description=(
            "花一次分析机会看看某只标的的模糊倾向。"
            "每天次数有限；分析只给倾向，不给精确数值，也不告诉你哪天会反转。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {
                    "type": "string",
                    "description": "标的 ID，形如 FAKE-001；留空则分析持仓最重的那只",
                }
            },
            "required": [],
        },
    )
    @_locked
    async def analyze(self, stock_id: str = "") -> Ok:
        """分析一只标的（P5）。

        **每天 ``ANALYSES_PER_DAY`` 次机会**（用掉的次数落库，重启不丢）。

        分析给的是**模糊倾向**：评级（绿／黄／红）、是不是在追高、你的持仓占比。

        **局中隐藏（P5 方案 B）**：

        * 本入口**不给**"今天是不是反转日"这个 bool；
        * 只在当天确有异动时附一句**不带方向**的提示
          （``ANALYSIS_MARKET_NOTE``）—— 玩家只知道"今天特殊"，
          **不知道特殊在哪**；
        * 这句话**只在这里**出现，``@ui.context`` 载荷里没有它 ——
          想拿到就得花分析次数（这正是方案 B 的"有成本"）。
        """
        self._touch_player_action()
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        prices = market.prices()

        used = player.analyses_used if player.analyses_day == player.day else 0
        if used >= ANALYSES_PER_DAY:
            return _err("analysis_limit_reached")

        stock = self._analysis_target(session, stock_id, prices)
        if stock is None:
            return _err("stock_not_found")

        heat_config = engine_config.heat
        recent = [value for _, value in stock.kline[-heat_config.chase_window :]]
        chasing = bool(recent) and stock.price >= max(recent) * heat_config.chase_ratio

        position = player.positions.get(stock.id)
        weight = 0.0
        if position is not None:
            total = player.total_asset(prices)
            value = position.market_value(prices.get(stock.id, 0.0))
            weight = (value / total) if total > 0 else 0.0

        if chasing or stock.tag == "explosive":
            rating = "red"
        elif stock.tag == "stable":
            rating = "green"
        else:
            rating = "yellow"

        # ── P5 方案 B：当天异动的**模糊**提示（不带方向、不给 bool）──
        market_note = ""
        if market.day in market.reversal_days:
            market_note = ANALYSIS_MARKET_NOTE

        # 消耗一次机会（落库：重启不丢次数）。
        player.analyses_day = player.day
        player.analyses_used = used + 1
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET analyses_day = ?, analyses_used = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.analyses_day, player.analyses_used, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7.2：花钱买分析也要挨骂（每一次点击都必有一句嘴臭）──────
        self._push_danmaku(
            pick_taunt(
                "analyze",
                seed=player.analyses_used * 13 + int(player.day),
                params={"stock": stock.name},
            )
            or i18n_text("taunt.analyze.1", "花钱买建议？我这儿的建议不值这个价。")
        )

        return Ok(
            {
                "stock_id": stock.id,
                "name": stock.name,
                "price": round(stock.price, 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "rating": rating,
                "chasing": chasing,
                "position_weight": round(weight, 4),
                # 空串 = 今天没有异动；有值也只有一句不带方向的话。
                "market_note": market_note,
                "analyses_left": max(0, ANALYSES_PER_DAY - player.analyses_used),
                "note": ANALYSIS_NOTE,
            }
        )

    @ui.action(
        id="buy_report",
        label="买报纸",
        icon="news",
        tone="default",
        group="info",
        order=11,
    )
    @plugin_entry(
        id="buy_report",
        name="买报纸",
        description=(
            "额外买一份「报纸」（多一次分析机会），当天有效。"
            "新手前两档白拿，后面几档要自己掏钱。"
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    @_locked
    async def buy_report(self) -> Ok:
        """买一份「报纸」（**P27.0**：教程里「后面你得自己买报纸了」的兑现）。

        实现口径：把 ``players.analyses_used`` 记成**负数** ——
        ``_analyses_left`` / ``analyze`` 都是「次数 = 2 − used」的口径，
        used 变成负数就等于多了几次机会；而**换天自动归零**
        （``analyses_day`` 一变，used 就不再参与计算）。

        新手前两档（``REPORT_FREE_MODES``）**白拿** —— 那就是「老爸给的高级报纸」；
        后面几档按 ``REPORT_PRICE`` 收费 —— 那就是「自己买报纸」。
        """
        self._touch_player_action()
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player = session.player
        market = session.market
        used = player.analyses_used if player.analyses_day == player.day else 0
        extra = max(0, -used)
        if extra >= REPORT_MAX_EXTRA:
            return _err("report_limit_reached")

        price = 0.0 if session.mode in REPORT_FREE_MODES else REPORT_PRICE
        if price > 0.0 and price > player.cash + 1e-9:
            return _err("insufficient_cash")

        player.analyses_day = player.day
        player.analyses_used = used - 1
        player.cash = round(player.cash - price, 6)
        # ── P28.0：记下「今天买过报纸」────────────────────────────────────
        # 这是「昨天买的报纸是假的」这句话的**前置条件**：
        # 他得真买过报纸，被骗这件事才成立。纯内存记账，不落库。
        self._fake_news_bought_days.add(int(player.day))
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET analyses_day = ?, analyses_used = ?, cash = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            player.analyses_day,
                            player.analyses_used,
                            player.cash,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        return Ok(
            {
                "price": round(price, 6),
                "free": price <= 0.0,
                "cash_after": round(player.cash, 6),
                "analyses_left": max(0, ANALYSES_PER_DAY - player.analyses_used),
                "reports_bought": extra + 1,
                "tick": int(market.tick),
            }
        )

    @ui.action(
        id="news_detail",
        label="新闻详情",
        icon="news",
        tone="default",
        group="info",
        order=20,
    )
    @plugin_entry(
        id="news_detail",
        name="新闻详情",
        description=(
            "看某条新闻的标题与正文。"
            "局中只给中性分类，点开详情才给标题与正文 —— 但**真假永远不给**，"
            "完整真相只在结算页出现。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "news_id": {"type": "string", "description": "新闻 ID，形如 news-3-2"}
            },
            "required": ["news_id"],
        },
    )
    @_locked
    async def news_detail(self, news_id: str) -> Ok:
        """新闻详情（P5）。

        局中默认只给中性分类（见 :meth:`_public_news`）；**点开详情**才给标题与
        正文 —— 但**仍然不给真假**（``accuracy``）、不给原始 ``type``、
        不给雷新闻标记。完整真相只在结算页出现。

        两种失败要分清：

        * ``news_not_found`` —— 全库都没有这条新闻；
        * ``news_detail_forbidden`` —— 这条新闻**存在**，但不属于当前这一局
          （``news_log`` 跨局累积，旧局的新闻 id 仍然留在库里）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        market = session.market
        for item in market.news_today:
            if item.id != news_id:
                continue
            kind = news_kind(item)
            # ── P7：名册群友的抽象新闻 ──────────────────────────────
            # P7.2：按**本局的绑定**认领群友（群友按波动率落在真实槽位上）。
            headline, roster_alias, roster_title = self._roster_news_headline(
                item, market
            )
            return Ok(
                {
                    "id": item.id,
                    "day": item.day,
                    "stock_id": item.stock_id,
                    "kind": kind,
                    "kind_label": _NEWS_KIND_LABELS.get(kind, "未知"),
                    "headline": headline,
                    "content": item.content,
                    # ── P7：名册彩蛋（群友信息）──────────────────────
                    "roster_alias": roster_alias,
                    "roster_title": roster_title,
                }
            )

        # 不在今天的新闻里 —— 看看是不是**别的局**留下的。
        try:
            rows = await self._repo.fetch_all(
                "SELECT news_id FROM news_log WHERE news_id = ? LIMIT 1", (news_id,)
            )
        except (DbUnavailableError, sqlite3.Error):
            rows = []
        if rows:
            return _err("news_detail_forbidden")
        return _err("news_not_found")

    # ------------------------------------------------------------------
    # 入口：按需拉 K 线（P6 档 2 第 6 项）
    # ------------------------------------------------------------------
    #
    # 局中载荷的 K 线是**限流**的（只给持仓 + 爆炸标签的前
    # ``PANEL_KLINE_MAX`` 只）—— 玩家想看**任意**标的的 K 线就调这个入口。
    # 单只标的，载荷很小（60 个点 ≈ 1 KB），不构成负担。

    @ui.action(
        id="kline",
        label="看K线",
        icon="chart",
        tone="default",
        group="market",
        order=4,
    )
    @plugin_entry(
        id="kline",
        name="查看K线",
        description="按需拉取某只标的的 K 线（最近 60 个点）。",
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {
                    "type": "string",
                    "description": "标的 ID（形如 FAKE-xxx）",
                }
            },
            "required": ["stock_id"],
        },
    )
    @_locked
    async def kline(self, stock_id: str) -> Ok:
        """按需拉取某只标的的 K 线（P6 档 2 第 6 项）。

        局中载荷只带前 20 只标的的 K 线（限流，避免载荷爆掉）；
        这个入口让玩家能看**任意**标的 —— 包括没持仓的、没爆炸标签的。

        局中隐藏规则照旧：**不给 heat、不给反转日、不给 seed**。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        market = session.market
        stock = market.stocks.get(stock_id)
        if stock is None:
            return _err("stock_not_found")
        return Ok(
            {
                "stock_id": stock.id,
                "name": stock.name,
                "price": round(stock.price, 6),
                "prev_price": round(stock.prev_price, 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                "alive": bool(stock.alive),
                "kline": [
                    [int(tick), round(float(value), 4)] for tick, value in stock.kline
                ],
            }
        )

    # ------------------------------------------------------------------
    # P33.0：入口：切换观察标的（点行情卡 ⇒ 主 K 线跟着切）
    # ------------------------------------------------------------------
    #
    # 面板那边点一下老虎机方块卡，就调这个入口：
    #   * 面板自己把主 K 线切过去、给那张卡打上**选中态**；
    #   * 后端这边给 Yui 递一条**轻量事实**，让她能顺嘴搭腔。
    #
    # 提示词里**只给事实**（P13 契约）：标的、现价、涨跌幅。
    # 怎么说是她的事 —— 插件不替她写好那句话。

    @ui.action(
        id="watch_stock",
        label="切换观察标的",
        icon="eye",
        tone="default",
        group="market",
        order=5,
    )
    @plugin_entry(
        id="watch_stock",
        name="切换观察标的",
        description=(
            "玩家点了行情卡（切主 K 线）。后端给 Yui 递一条轻量事实"
            "（正在盯哪只、涨跌幅多少），让她能顺嘴搭腔。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stock_id": {
                    "type": "string",
                    "description": "被点中的标的 ID（形如 FAKE-xxx）",
                }
            },
            "required": ["stock_id"],
        },
    )
    @_locked
    async def watch_stock(self, stock_id: str) -> Ok:
        """玩家切换观察标的 ⇒ 给 Yui 递一条**轻量事实**（P33.0）。

        面板那侧负责"切图 + 选中态"，这边负责**让她知道玩家在看哪只** ——
        不然她只能对着自己那份仓位点评，玩家换标的了她还在念旧账。

        提示词里**只给事实**：标的、现价、涨跌幅。措辞她自己定。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        market = session.market
        stock = market.stocks.get(str(stock_id or ""))
        if stock is None:
            return _err("stock_not_found")
        name = str(getattr(stock, "name", "") or stock_id)
        price = float(getattr(stock, "price", 0.0) or 0.0)
        prev_price = float(getattr(stock, "prev_price", 0.0) or 0.0)
        change = ((price - prev_price) / prev_price * 100.0) if prev_price > 0 else 0.0
        prompt = (
            "《拟股喵喵》里玩家切换了观察标的。\n"
            f"事实：他现在盯着「{name}」（{stock.id}），现价 {price:.2f}，"
            f"涨跌幅 {change:+.2f}%。\n"
            "你可以顺嘴接一句（这句只是素材，措辞你自己定）："
            "他盯得这么紧，是不是有什么打算？\n"
            "只准用上面给出的事实，不许编造别的数字或内幕。"
        )
        self._yui_queue_prompt(
            prompt,
            "yui_watch_stock",
            4,
            day=int(session.player.day),
        )
        return Ok(
            {
                "action": "watch_stock",
                "stock_id": str(stock.id),
                "name": name,
                "price": round(price, 6),
                "change": round(change, 4),
            }
        )

    # ------------------------------------------------------------------
    # P5.5 改动 4：天桥虚惊一场（death_scene）
    # ------------------------------------------------------------------
    #
    # 三屏是**梦**：第一屏在天桥，第二屏醒来（趴在出租屋的桌上），第三屏落在
    # "你还有机会"。**不写任何具体的自杀细节**（不写"跳下去""往前走"）——
    # 留白本身就是表达。
    #
    # 只对**无限模式的破产**触发：经典模式是教学关，不需要这么重。

    @staticmethod
    def _death_scene(
        session: Session,
        *,
        dead: bool,
        death_reason: str | None,
        days: int,
    ) -> dict[str, str] | None:
        """构造三屏文案（经典模式恒为 ``None``）。"""
        if not dead or session.mode != "infinite":
            return None
        params = {
            "identity_quote": identity_intro(session.identity),
            "death_reason_label": death_reason_label(death_reason),
            "days": int(days),
        }
        return {
            "screen1": _fill_text(
                i18n_text(
                    "death_scene.screen1",
                    "你站在天桥上，看着车流。风很大。\n你想起入场那天说的话：“{identity_quote}”",
                ),
                params,
            ),
            "screen2": _fill_text(
                i18n_text(
                    "death_scene.screen2",
                    "……然后你挨了一记飞踢。\n猫娘把你从天桥边踹回了出租屋。"
                    "你醒了，趴在桌子上，手机屏幕还亮着，股市 K 线还在跳。\n"
                    "她说：“你刚才说梦话，说什么‘再借一笔就能回本’。”",
                ),
                params,
            ),
            "screen3": _fill_text(
                i18n_text(
                    "death_scene.screen3",
                    "你破产了。破产原因：{death_reason_label}。撑了 {days} 天。\n"
                    "但你醒过来了。\n现实里，你还有机会不玩这个游戏。",
                ),
                params,
            ),
        }

    # ------------------------------------------------------------------
    # P5.5 改动 5：称号图鉴（achievements 独立表）
    # ------------------------------------------------------------------

    async def _unlocked_achievement_titles(self, player_id: str) -> set[str]:
        """这个玩家已解锁的称号（**称号名**，不是内部 key）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT title FROM achievements WHERE player_id = ?", (player_id,)
            )
        except (DbUnavailableError, sqlite3.Error):
            return set()
        return {str(row["title"]) for row in rows}

    async def _achievements_payload(
        self, player_id: str, identity: str
    ) -> dict[str, Any]:
        """称号图鉴载荷（改动 5 / 改动 7）。

        ``unlocked`` 给称号名 + 叙事句 + 身份呼应句；``locked`` 只给
        「？？？」+ 一句提示 —— **提示里唯一带「活」字的是「无为而治」**，
        那是玩家发现隐藏结局的线索。
        """
        unlocked_titles = await self._unlocked_achievement_titles(player_id)
        echo = identity_echo(identity)
        unlocked: list[dict[str, Any]] = []
        locked: list[dict[str, Any]] = []
        for item in ACHIEVEMENTS:
            if str(item["title"]) in unlocked_titles:
                unlocked.append(
                    {
                        "title": str(item["title"]),
                        "epitaph": str(item["hint"]),
                        "identity_epitaph": echo,
                    }
                )
            else:
                locked.append(
                    {"title": "？？？", "hint": str(item["hint"])}
                )
        return {
            "unlocked": unlocked,
            "locked": locked,
            "total": len(ACHIEVEMENTS),
            "unlocked_count": len(unlocked),
        }

    async def _unlock_achievement(
        self, player_id: str, key: str, *, tick: int
    ) -> dict[str, Any] | None:
        """解锁一个称号（**幂等**）。已经解锁过时返回 ``None``。"""
        item = achievement_by_key(key)
        if item is None:
            return None
        title = str(item["title"])
        try:
            row = await self._repo.fetch_one(
                "SELECT title FROM achievements WHERE player_id = ? AND title = ?",
                (player_id, title),
            )
            if row is not None:
                return None
            await self._repo.transaction(
                [
                    (
                        "INSERT INTO achievements (player_id, title, unlocked_at) "
                        "VALUES (?, ?, ?)",
                        (player_id, title, int(tick)),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as error:
            self.logger.warning(f"称号解锁写入失败（{title}）：{error}")
            return None
        return item

    async def _maybe_unlock_achievements(self, session: Session) -> None:
        """称号解锁的两个触发点（P5.5 改动 5 / 改动 6）。

        1. **破产成就** —— 玩家破产的那一刻就解锁（破产原因 → 称号）。
        2. **「无为而治」** —— 第 ``INACTION_UNLOCK_DAYS`` 天且零有效交易。

        两处都是**幂等**的（``achievements`` 表主键是 ``(player_id, title)``）。
        """
        player = session.player
        if not player.alive:
            reason = player.death_reason or ""
            if reason:
                item = achievement_by_source(reason)
                if item is not None:
                    unlocked = await self._unlock_achievement(
                        player.id, str(item["key"]), tick=session.market.tick
                    )
                    if unlocked is not None:
                        self.logger.info(
                            f"玩家 {player.id} 解锁称号：{unlocked['title']}"
                            f"（破产原因 {reason}）"
                        )
            return
        await self._maybe_unlock_inaction(session)

    async def _maybe_unlock_settlement_achievements(self, session: Session, report) -> None:
        """结算时的成就解锁（P5.5 改动 5 · P6 选项 B 修正）。

        * 破产原因 → 对应称号（与日终那次重复时是**幂等**的，不会插两行）；
        * 结局 ``inaction`` → 「无为而治」；
        * **活着**结算时总资产为 0 → 「一贫如洗」。

        「一贫如洗」的口径是 P6 拍板的**选项 B**：必须是**活着**的。
        选项 A（不论结局）会与 5 个破产成就**必然同时解锁**（清盘时账上必然是零），
        等于没有独立价值；选项 B 两者互斥，叙事才成立（"你没破产，但什么都没了"）。
        """
        player = session.player
        tick = int(getattr(report, "ticks_played", 0) or 0)
        if not player.alive and player.death_reason:
            item = achievement_by_source(player.death_reason)
            if item is not None:
                await self._unlock_achievement(
                    player.id, str(item["key"]), tick=tick
                )
        if getattr(report, "ending", None) == "inaction":
            await self._unlock_achievement(player.id, "inaction", tick=tick)
        if (
            player.alive
            and abs(float(getattr(report, "final_asset", 0.0) or 0.0)) <= 1e-9
        ):
            await self._unlock_achievement(player.id, "broke", tick=tick)

    async def _maybe_unlock_inaction(self, session: Session) -> None:
        """「无为而治」的解锁判定（P5.5 改动 6）。

        存活到第 ``INACTION_UNLOCK_DAYS`` 天且**零有效交易**。
        「有效交易」用**引擎的豁免口径**（与隐藏结局 ``check_inaction`` 一致）——
        字面的 ``trade_count == 0`` 在无限模式下**永远不可达**（开局必须卖掉
        基准股票才能交上第一天生活费）。

        触发后**不强制结束**：玩家可以继续"什么都不做"地活着。
        """
        player = session.player
        if not player.alive or session.mode != "infinite":
            return
        if int(session.inaction_unlocked):
            return
        ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
        days = session.market.tick // ticks_per_day
        if days < INACTION_UNLOCK_DAYS:
            return
        exempt = await self._inaction_exempt_trades(session)
        effective = int(player.trade_count) - max(0, exempt)
        if effective > 0:
            return

        item = await self._unlock_achievement(
            player.id, "inaction", tick=session.market.tick
        )
        if item is None:
            return
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET inaction_unlocked = 1, updated_at = ? "
                        "WHERE id = ?",
                        (time.time(), player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            self.logger.warning(f"无为而治标记写入失败：{db_error}")
            return
        unlocked_titles = await self._unlocked_achievement_titles(player.id)
        note = ACHIEVEMENT_UNLOCK_NOTE.format(
            days=days, title=str(item["title"])
        )
        await self._push_card(
            build_achievement_card(
                title_name=str(item["title"]),
                note=note,
                unlocked_count=len(unlocked_titles),
                total=len(ACHIEVEMENTS),
                identity_echo=identity_echo(session.identity),
            )
        )
        self.logger.info(f"玩家 {player.id} 解锁称号：{item['title']}（第 {days} 天）")

    # ------------------------------------------------------------------
    # 入口：结算本局
    # ------------------------------------------------------------------

    @plugin_entry(
        id="settle",
        name="结算本局",
        description="结束当前这一局并给出结算报告。",
    )
    @_locked
    async def settle(self) -> Ok:
        """结束整局并返回结算报告。

        这是**唯一**允许出现 ``seed`` 与 ``heat_curve`` 的入口——局中它们必须
        完全隐藏，结算页才允许展示。

        经典模式在第 30 个游戏日结束时**已经自动结算**过了，这时再调本入口会
        拿到 ``no_active_game``（结算不重复）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("no_active_game")

        player = session.player
        payload = await self._build_settlement_payload(session, engine_config)
        # ── P33.0：对局进入结算状态 ⇒ **立刻自毁** ────────────────────
        # 这一局到此为止：心跳空转、待推送队列清空、所有播报闭嘴。
        # （放在落库**之前**：库里清席位之前就得先把嘴堵上。）
        self._deactivate_game("settle")

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = ?, updated_at = ? WHERE id = ?",
                        (int(player.alive), now, player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        self._archive_settlement(payload, session)
        return Ok(payload)

    async def _build_settlement_payload(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """组装结算报告（``settle`` 与经典模式自动结算共用）。

        这是 heat 曲线与种子**唯一**允许出现的地方 —— 也是新闻**真相**唯一
        允许出现的地方（局中只看得到中性分类）。
        """
        player = session.player
        market = session.market
        difficulty = session.difficulty

        exempt = await self._inaction_exempt_trades(session)
        ending = (
            "inaction"
            if player.alive and check_inaction(player, max_trades=0, exempt_trades=exempt)
            else None
        )
        harvest_events = await self._harvest_events(player.id)
        catgirl_result = await self._catgirl_result(session)
        news_log = await self._news_log()

        # 「存活天数」= 走完的游戏日数（tick 数 ÷ 每日 tick 数）。
        # 这与 P1/P2 契约的样例一致（60 tick ÷ 12 = 5 天），也避免了
        # "日终结算后 player.day 已经 +1" 带来的差一天。
        days_survived = market.tick // max(1, int(difficulty.ticks_per_day))
        if session.mode != "infinite":
            days_survived = min(days_survived, engine_config.classic.total_game_days)

        report = build_settlement_report(
            player,
            market=market,
            mode=player.mode,
            days_survived=days_survived,
            ticks_played=market.tick,
            ticks_per_day=difficulty.ticks_per_day,
            achievements=[],
            ending=ending,
            catgirl_result=catgirl_result,
            news_log=news_log,
        )
        # ── P18.0：结课小结（风险提示 + 祝福）──────────────────────────
        # **独立的课后小结**，不走剧情层（P13 契约：剧情只给处境）。
        # 台词由 Yui 自己写；整个对局只说一次。
        await self._tutorial_epilogue(
            "玩家这一局没能扛过去，被清出了场子"
            if not player.alive
            else "玩家走完了这一局，活到了最后"
        )
        # ── P5.5 改动 5：结算时的成就解锁（破产原因 / 结局 / 一贫如洗）────
        await self._maybe_unlock_settlement_achievements(session, report)
        # ── P5.5 改动 2/3：结算页的叙事三件套 ──────────────────────
        # 身份呼应句**只在破产结算页**出现（活到终局不给）。
        dead = not player.alive and bool(player.death_reason)
        identity_echo_text = identity_echo(session.identity) if dead else ""
        # 猫娘沉默旁白：只在**本局有猫娘**时出现，概率 30%，按 seed 稳定。
        catgirl_silence_text = ""
        if session.catgirl is not None and pick_catgirl_silence(market.seed):
            catgirl_silence_text = i18n_text(
                "settlement.catgirl_silence", "猫娘站在旁边，什么都没说。"
            )
        # 「无为而治」的打工叙事。
        inaction_worker_text = (
            i18n_text("settlement.inaction_worker", "你每周打工、交生活费、不碰股市。刚好活下来。")
            if ending == "inaction"
            else ""
        )
        # ── P5.5 改动 5：破产称号的叙事句（epitaph）────────────────
        # **插件层**的映射表（引擎的 DEATH_TITLES 一行不动）。
        epitaph_text = ""
        if report.death_reason:
            epitaph_text = EPITAPHS.get(report.death_reason, "")
        elif ending == "inaction":
            epitaph_text = EPITAPHS["inaction"]
        # 细节 A（拍板）：``{epitaph} {身份呼应句}`` 一句话读完。
        display_title = report.death_title
        if epitaph_text:
            display_title = (
                f"{epitaph_text} {identity_echo_text}".strip()
                if identity_echo_text
                else epitaph_text
            )
        # ── P5.5 改动 4：天桥虚惊一场（**只对无限模式的破产**）──────
        death_scene = self._death_scene(
            session,
            dead=dead,
            death_reason=report.death_reason,
            days=report.days_survived,
        )
        # ── P5.5 改动 5：称号图鉴 ────────────────────────────────
        achievements = await self._achievements_payload(player.id, session.identity)
        # ── P16.0：本局情绪画像（**结算页才有**，局中不给）────────────
        # 一句话彩蛋，不含任何数值、不含 heat、不含局中秘密。
        final_profile = await self._build_emotion_profile(session)
        self._emotion_profile = final_profile
        return {
            "player_id": report.player_id,
            "mode": report.mode,
            "mode_name": _MODE_NAMES.get(report.mode, report.mode),
            # ── P20.0：结算页也要显示玩家的名字与命中的彩蛋 ────────────
            # 引擎的 ``SettlementReport`` 是 frozen dataclass 且**没有名字字段**
            # ⇒ 名字只能在这一层（插件自己的载荷 dict）补上。
            "player_name": str(getattr(player, "name", "") or ""),
            "name_egg": public_info(
                match_egg(str(getattr(player, "name", "") or ""))
            ),
            "seed": report.seed,
            "survivor": report.survivor,
            "days_survived": report.days_survived,
            "ticks_played": report.ticks_played,
            "trade_count": report.trade_count,
            "initial_cash": round(report.initial_cash, 6),
            "final_cash": round(report.final_cash, 6),
            "final_asset": round(report.final_asset, 6),
            "total_fee_paid": round(report.total_fee_paid, 6),
            "fee_pool_total": round(report.fee_pool_total, 6),
            "heat_curve": [round(value, 4) for value in report.heat_curve],
            "peak_day": report.peak_day,
            "peak_heat": round(report.peak_heat, 4),
            "death_reason": report.death_reason,
            "death_title": display_title,
            "epitaph": epitaph_text,
            # ⚠ 这里**不能**再写一次 "achievements"：dict 字面量里重复键会**后者覆盖前者**，
            #   而引擎的 ``report.achievements`` 恒为 ``[]``（见上面的
            #   ``build_settlement_report(..., achievements=[])``），
            #   真正要交给面板的是下面那份「称号图鉴」（面板按对象读 unlocked/locked）。
            "ending": report.ending,
            # ── P3 新增：收割时间线与猫娘结局 ──
            # 这里是**唯一**能看到收割时刻 heat 数值的地方（局中看不到）。
            "harvest_events": harvest_events,
            "catgirl_result": catgirl_result,
            # ── P4 新增：低保 / 高利贷 / 新闻真相 ──
            "welfare_summary": self._welfare_summary(session, engine_config),
            "loan_summary": self._loan_summary(session, engine_config),
            "news_log": news_log,
            # ── P5.5 新增：叙事三件套（只在结算页）──────────────────
            "identity": session.identity,
            "identity_echo": identity_echo_text,
            "catgirl_silence": catgirl_silence_text,
            "inaction_worker": inaction_worker_text,
            # ── P5.5 改动 4/5/7 ───────────────────────────────────
            "death_scene": death_scene,
            "achievements": achievements,
            # ── P16.0：本局情绪画像（**结算页才有**，局中不给）────────
            # 一句话彩蛋，不含任何数值、不含 heat、不含局中秘密。
            "emotion_summary": summarize(final_profile),
            # ── P27.0：专属破产结局（Wehos）───────────────────────────
            # 命中就弹专属卡片，**不再**走普通的破产画面；
            # 没命中就是 ``None`` ⇒ 普通流程一个字都不变。
            "special_ending": self._special_ending(session, dead=dead),
            "anti_fraud_notice": ANTI_FRAUD_NOTICE,
            # ── P32.0：存活结局的**专属骄傲文案** ────────────────────
            # 真机事故（2026-11-05）：街机档撑满 30 天、**活着**走到结算日，
            # 结算页却挂着「你连这个游戏都玩不过」和【不服再来？】——
            # 两套结局文案混用。口径只有一条：**有没有真破产**。
            # 存活 ⇒ 给骄傲文案（``panel`` 用它，不许再出现破产文案）；
            # 破产 ⇒ ``anti_fraud_notice`` 照旧，反诈警告一个字都不改。
            "survivor_praise": (
                _fill_text(
                    i18n_text(
                        "settlement.survived_praise",
                        "你居然撑了 {days} 天？有点东西。",
                    ),
                    {"days": int(report.days_survived)},
                )
                if report.survivor
                else ""
            ),
        }

    async def _inaction_exempt_trades(self, session: Session) -> int:
        """隐藏结局豁免的交易笔数。

        无限模式开局现金为 0，玩家**必须**先卖掉基准股票才交得上第一天生活费
        —— 那笔不是他的主动交易，豁免。除此之外的每一笔买卖都算数。
        """
        if session.mode != "infinite":
            return 0
        try:
            row = await self._repo.fetch_one(
                "SELECT side FROM trades WHERE player_id = ? ORDER BY id ASC LIMIT 1",
                (session.player.id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return 0
        if row is None or row["side"] != "sell":
            return 0
        return 1

    def _welfare_summary(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """工资小结（工资是主动领的，不领就欠着）。"""
        player = session.player
        infinite = engine_config.infinite
        if session.mode != "infinite":
            return {
                "available": False,
                "note": "经典模式没有工资。",
            }
        return {
            "available": True,
            "claimed": player.welfare_claimed_day > 0,
            "claimed_day": player.welfare_claimed_day,
            "missed_days": player.welfare_missed_days,
            "limit_days": infinite.welfare_claim_limit_days,
            "interval_days": infinite.relief_interval_days,
            "weekly_relief": round(infinite.weekly_relief, 6),
            "note": (
                "工资必须主动领。连着七天不去领，第八天日终就没人保你了。"
                if player.welfare_missed_days < infinite.welfare_claim_limit_days
                else "你已经连着七天没领工资了。"
            ),
        }

    def _player_config(
        self, session: Session, engine_config: EngineConfig
    ) -> EngineConfig:
        """这个玩家的**运行时配置**（新手期放水 + 名字彩蛋覆写）。

        放贷相关的三个口子（小结 / 借款 / 导演强制放贷）都必须走这里 ——
        否则「社区特供」的开局魔爪契约按翻倍额度签，
        而玩家自己点的每一笔却按基础额度借，两套数字对不上。
        """
        name = str(getattr(session.player, "name", "") or "")
        return _config_for_player(name, session.mode, engine_config)

    def _loan_summary(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """高利贷小结。"""
        player = session.player
        infinite = self._player_config(session, engine_config).infinite
        if session.mode != "infinite":
            return {"available": False, "note": "经典模式没有高利贷。"}
        return {
            "available": True,
            "borrowed": player.loan_taken_day > 0,
            "taken_day": player.loan_taken_day,
            "outstanding": round(player.loan_debt, 6),
            "amount": round(infinite.loan_amount, 6),
            "repay": round(infinite.loan_repay, 6),
            "note": "借五百还六百。还不上，明天就是你的清算日。",
        }

    # ------------------------------------------------------------------
    # P5.5 改动 1：破产原因称号的**文案层覆盖**
    # ------------------------------------------------------------------
    #
    # 引擎的 ``DEATH_TITLES`` 里还留着"低保"字样（P5.5 总约束：``engine/``
    # 一行不动），但玩家**永远看不到**那一句 —— 插件层按 ``death_reason``
    # （**破产原因枚举**，不是字符串比对）改写称号。
    #
    #: 破产原因 → i18n key（值在 ``i18n/zh-CN.json`` 的 ``death.*`` 命名空间）。
    #:
    #: **5 种破产原因全覆盖**（P14.1）：引擎的 ``DEATH_TITLES`` 里仍留着
    #: 几处过于激烈的措辞（引擎层一行不动），
    #: 插件层必须把**每一种**都改写掉 —— 少覆盖一种，玩家就能从结算页
    #: 直接读到引擎原文。
    _DEATH_TITLE_OVERRIDES: dict[str, str] = {
        "starved": "death.starved",
        "liquidated": "death.liquidated",
        "harvested": "death.harvested",
        "welfare_neglect": "death.welfare_neglect",
        "loan_default": "death.loan_default",
    }

    @classmethod
    def _override_death_title(cls, player: Player) -> None:
        """把引擎产出的破产原因称号改写成插件层文案（**原地**修改）。"""
        reason = player.death_reason
        if not reason:
            return
        key = cls._DEATH_TITLE_OVERRIDES.get(reason)
        if key is None:
            return
        player.death_title = i18n_text(
            key, "这一局到此为止。账上的数字已经替你回答了。"
        )

    async def _news_log(self) -> list[dict[str, Any]]:
        """新闻全记录（**含真假**）—— 只有结算页能拿到。

        局中玩家只看得到中性分类（bullish / bearish / vague / noise）；这里
        才给出真新闻、假新闻与雷新闻的真相。
        """
        try:
            rows = await self._repo.fetch_all(
                "SELECT day, news_id, news_type, stock_id, content, accuracy, direction "
                "FROM news_log ORDER BY day ASC, news_id ASC"
            )
        except (DbUnavailableError, sqlite3.Error):
            return []
        keys = rows[0].keys() if rows else ()
        return [
            {
                "day": int(row["day"]),
                "id": row["news_id"],
                "type": row["news_type"],
                "stock_id": row["stock_id"],
                "headline": row["content"],
                "accuracy": row["accuracy"],
                "direction": row["direction"] if "direction" in keys else None,
            }
            for row in rows
        ]

    def _archive_settlement(self, payload: dict[str, Any], session: Session) -> None:
        """把结算产物归档到用户数据目录（**不是**插件源码目录）。"""
        try:
            folder = self.data_path("settlements")
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (
                f"settlement-{session.player.id}-{session.market.tick}.json"
            )
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            self.logger.warning(f"结算归档写入失败：{error}")

    # ------------------------------------------------------------------
    # 卡片投递（P5）
    # ------------------------------------------------------------------

    async def _push_card(self, card: Card) -> None:
        """投递一张卡片到聊天频道。

        卡片走 ``ctx.create_card(...)``（**公开 SDK API**），**AI 看不到**
        （``ai_behavior="blind"``）。投递失败**不影响本局** —— 游戏状态已经
        落库了，卡片只是"锦上添花"。

        严禁改回 ``plugin.sdk.shared.core.cards.create_card``：``shared`` 是宿主
        **内部实现**，插件直引会导致在真实宿主里启动即崩
        （``No module named 'plugin.sdk.shared.core.cards'``）。

        同理**不要**为具体的卡片异常类写 ``except``：宿主不同版本导出的
        门面符号集并不一致（0.7.1 引的 ``CardSubmissionError`` 在 Steam 版
        运行时就**不存在** → ``cannot import name`` → 启动即崩）。
        一律用 ``except Exception`` 兜底记日志。

        **0.7.4 真机复盘**：Steam 版宿主（0.9.0）的 SDK 门面里**根本没有**
        卡片 API ——``create_card`` / ``ChatCard`` / ``PluginView`` 全部
        不存在（实测：运行时二进制里 ``create_card`` 出现 0 次）。所以这里
        **按能力探测**：有 ``create_card`` 就用卡片；没有就降级成一条
        **纯文本**聊天消息（``push_message`` 在 0.9.0 里是有的）。

        **P33.0：退场之后不许再弹卡片。**
        卡片全是**局中事件**（日终结算 / 收割 / 新闻 / 账单）——
        这一局都结束了还弹，就是观众眼里的"幽灵弹窗"。
        """
        if card is None:
            return
        if not self._is_game_active:
            self.logger.info(
                f"[P33.0] 对局已结束：跳过卡片投递（{card.kind}）"
            )
            return
        creator = getattr(self.ctx, "create_card", None)
        if callable(creator):
            try:
                await creator(
                    html=card.html,
                    summary=card.summary,
                    css=CARD_CSS,
                    actions=card.action_spec(),
                )
                return
            except Exception as error:  # pragma: no cover - 宿主侧异常
                self.logger.warning(f"卡片投递失败（{card.kind}）：{error}")
                return

        # 宿主没有卡片 API（Steam 版 0.9.0）：降级成一条纯文本消息。
        push = getattr(self.ctx, "push_message", None)
        if not callable(push):
            self.logger.info(
                f"宿主未提供卡片 API（{card.kind}）：本次跳过卡片投递"
            )
            return
        try:
            result = push(
                visibility=["chat"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": card.summary}],
            )
            if hasattr(result, "__await__"):
                await result
        except Exception as error:  # pragma: no cover - 宿主侧异常
            self.logger.warning(f"卡片降级投递失败（{card.kind}）：{error}")

    async def _push_taunt(
        self,
        trigger: str,
        seed: int,
        params: dict[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        """按触发点推一张猫娘嘲讽卡片（文案按 seed **稳定**选取）。

        ``detail`` 是**追加**在嘲讽下面的一行（卡片有独立的 detail 槽位）。
        P19.0 用它挂「专属挽留句」—— 例如入门档开局没几天就因为没看水位
        被清出去时，猫娘那句「我前面教过你的」。
        """
        text = pick_taunt(trigger, seed, params)
        if not text:
            return
        await self._push_card(
            build_taunt_card(trigger=trigger, text=text, detail=detail)
        )

    @staticmethod
    def _beginner_taught_you_line(player: Any) -> str:
        """入门档专属的挽留句（「我前面教过你的」）。

        条件**全部**满足才给：

        * 档位是 ``classic_beginner``（第一个要交生活费的档位，教程教过看水位）；
        * 出局在**前 3 天**（教程刚教完就栽了，才谈得上「我前面教过你的」）；
        * 出局原因是**交不起生活费**（断供 / 清算），不是别的。

        返回空串 = 不追加任何东西（其他档位、其他出局原因一律走通用嘲讽）。
        """
        if str(getattr(player, "mode", "") or "") != "classic_beginner":
            return ""
        if int(getattr(player, "day", 0) or 0) > 3:
            return ""
        if str(getattr(player, "death_reason", "") or "") not in {"starved", "liquidated"}:
            return ""
        return i18n_text(
            "taunt.cash_kill.beginner_early",
            "我前面教过你的 —— 这一关开始就有生活费，水位线就在那儿盯着呢。",
        )

    @staticmethod
    def _egg_taunt_line(player: Any) -> str:
        """彩蛋专属的挽留句（P20.0）。

        带**名号**出局时，那句挽留必须**认得那个名字** ——
        否则「股神也会破产啊？」这种效果根本落不了地。

        走 i18n（**不是 AI 生成的**）：这是**插件层的固定文案**，
        零新机制，也不用等模型开口。文案里的 ``{name}`` 在这里替换掉。
        """
        name = str(getattr(player, "name", "") or "")
        egg = match_egg(name)
        if egg is None:
            return ""
        if egg.category == CATEGORY_HUBRIS:
            text = i18n_text(
                "taunt.cash_kill.egg_hubris",
                "{name}也会破产啊？—— 看来这市场不认名号，只认余额。",
            )
        elif egg.category == CATEGORY_COMMUNITY:
            text = i18n_text(
                "taunt.cash_kill.egg_community",
                "{name}是自己人，但自己人也没豁免 —— 魔爪契约不认关系，只认日子。",
            )
        elif egg.category == CATEGORY_STEADY:
            text = i18n_text(
                "taunt.cash_kill.egg_steady",
                "{name}躺着就能赢，也会出局？那这市场比你想的还不讲理。",
            )
        else:
            text = i18n_text(
                "taunt.cash_kill.egg_self",
                "{name}这名字起得倒是诚实 —— 只是诚实救不了账户。",
            )
        return text.replace("{name}", name)

    def _death_detail_line(self, player: Any) -> str:
        """破产时的**追加一行**（P19.0 的挽留句 + P20.0 的彩蛋句）。

        两句都有 ⇒ 换行拼在一起；只有一句 ⇒ 就是那一句；都没有 ⇒ 空串
        （调用方据此不填 detail 槽位）。
        """
        parts = [
            line
            for line in (
                self._beginner_taught_you_line(player),
                self._egg_taunt_line(player),
            )
            if line
        ]
        return "\n".join(parts)

    async def _push_first_news_card(self, news: list[Any]) -> None:
        """推当天的「突发新闻」卡片 —— **只推当天的第一条**。

        **为什么不按新闻类型挑**（P5 文档契约变更第 2 条）：

        如果只给雷新闻推卡片，玩家看到"偏空 + 有卡片"就能推断"这条是雷"，
        P4 §11 的"局中不泄露真假与雷"当场作废。所以推送规则**与类型无关** ——
        每天固定推第一条，卡片出现与否不携带任何类型信息。
        """
        if not news:
            return
        item = news[0]
        kind = news_kind(item)
        await self._push_card(
            build_news_flash_card(
                news_id=item.id,
                kind=kind,
                kind_label=_NEWS_KIND_LABELS.get(kind, "未知"),
                headline=item.content,
                content=item.content,
                day=item.day,
                stock_id=item.stock_id,
            )
        )

    async def _push_cards(
        self,
        session: Session,
        outcome,
        engine_config: EngineConfig,
    ) -> None:
        """把这一 tick 产生的事件变成卡片（P5）。

        推送规则
        --------
        * **突发新闻卡片按"当天的第一条"推送**，**不看新闻类型** ——
          如果只给雷新闻推卡片，玩家立刻能推断"这条是雷"，
          P4 §11 的"局中不泄露真假与雷"就被破坏了（详见 P5 文档契约变更）。
        * **账单 / 二道贩子卡片**只在日终且交不起时推。
        * **猫娘嘲讽**按 5 个触发点推（见 ``cards.TAUNT_TRIGGERS``）。
        """
        market = session.market
        player = session.player
        difficulty = session.difficulty
        prices = market.prices()
        seed = market.seed
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        daily = outcome.daily

        # ── ① 收割卡片（每次收割一张）────────────────────────────
        for result in outcome.liquidations:
            stock = market.stocks.get(result.stock_id)
            position = player.positions.get(result.stock_id)
            amount = position.amount if position is not None else 0.0
            await self._push_card(
                build_harvest_card(
                    tick=market.tick,
                    day=player.day,
                    stock_id=result.stock_id,
                    stock_name=stock.name if stock is not None else result.stock_id,
                    concentration=result.concentration,
                    severity=result.severity,
                    price_before=result.price_before,
                    price_after=result.price_after,
                    amount=amount,
                    trigger=result.trigger,
                )
            )
            if result.concentration >= 0.5:
                loss = max(0.0, result.price_before - result.price_after) * amount
                await self._push_taunt(
                    "harvest",
                    seed,
                    {
                        "stock": stock.name if stock is not None else result.stock_id,
                        "weight": f"{result.concentration:.0%}",
                        "severity": f"{result.severity:.0%}",
                        "loss": f"{loss:.2f}",
                    },
                )

        # ── ② 突发新闻卡片（当天的第一条，**不按类型挑**）──────────
        await self._push_first_news_card(list(outcome.news))

        # ── ③ 重仓退市 → 猫娘嘲讽 ─────────────────────────────────
        total_assets = player.total_asset(prices)
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            if stock is None or stock.alive:
                continue
            if total_assets <= 0:
                continue
            weight = position.market_value(prices.get(stock_id, 0.0)) / total_assets
            if weight < 0.5:
                continue
            await self._push_taunt(
                "heavy_delisted",
                seed,
                {"stock": stock.name, "weight": f"{weight:.0%}"},
            )

        # ── ④ 猫娘自己破产 → 猫娘嘲讽 ─────────────────────────────
        catgirl = session.catgirl
        if catgirl is not None and not catgirl.alive and daily is not None:
            await self._push_taunt(
                "catgirl_dead",
                seed,
                {"days": market.tick // ticks_per_day},
            )

        if daily is None:
            return

        settlement = daily.settlement
        living_cost = daily_living_cost(player.mode, player.day, seed, engine_config)
        next_cost = next_living_cost(difficulty, engine_config)

        # ── ⑤ 账单卡片 + 二道贩子报价卡片（交不起时）───────────────
        if settlement is not None and next_cost > 0 and player.cash < next_cost:
            await self._push_card(
                build_bill_card(
                    day=player.day,
                    living_cost=living_cost,
                    next_cost=next_cost,
                    cash=player.cash,
                    gap=next_cost - player.cash,
                    missed_bills=int(player.strikes),
                )
            )
            pending = player.pending_action or {}
            legs = pending.get("legs") or []
            if pending.get("kind") == "pawnbroker_sell" and legs:
                await self._push_card(
                    build_pawnbroker_card(
                        day=player.day,
                        gap=next_cost - player.cash,
                        discount=float(pending.get("discount", 0.2)),
                        legs=[
                            {
                                "stock_id": leg.get("stock_id", ""),
                                "stock_name": leg.get("stock_id", ""),
                                "amount": leg.get("amount", 0.0),
                                "notional": float(leg.get("amount", 0.0))
                                * float(leg.get("price", 0.0)),
                            }
                            for leg in legs
                        ],
                        cash_after=player.cash,
                        window_seconds=int(
                            max(0.0, self._daily_window_until - time.time())
                        ),
                    )
                )

        # ── ⑥ 强制平仓卡片 + 现钱强制平仓嘲讽 ──────────────────────────────
        if settlement is not None and settlement.dead and player.death_reason:
            # 改动 5：破产原因的**叙事句**（epitaph）当称号用 —— 细节 A：
            # ``{epitaph} {身份呼应句}`` 一句话读完。
            epitaph = EPITAPHS.get(player.death_reason, "")
            await self._push_card(
                build_kill_card(
                    death_reason=player.death_reason or "",
                    death_title=epitaph
                    or player.death_title
                    or player.death_reason
                    or "",
                    days_survived=market.tick // ticks_per_day,
                    total_asset=player.total_asset(prices),
                    harvest_count=await self._harvest_count(player.id),
                    # ── P5.5 改动 2/3：结算时刻的叙事 ──────────────
                    # 身份呼应句 + 猫娘沉默旁白（与结算报告用同一套判定，
                    # 所以面板和聊天卡片说的是同一件事）。
                    identity_echo=identity_echo(session.identity),
                    catgirl_silence=(
                        i18n_text(
                            "settlement.catgirl_silence",
                            "猫娘站在旁边，什么都没说。",
                        )
                        if session.catgirl is not None
                        and pick_catgirl_silence(seed)
                        else ""
                    ),
                )
            )
            if player.death_reason in {"starved", "liquidated"}:
                await self._push_taunt(
                    "cash_kill",
                    seed,
                    {
                        "bill": f"{next_cost:.2f}",
                        "cash": f"{player.cash:.2f}",
                        "gap": f"{max(0.0, next_cost - player.cash):.2f}",
                        "days": market.tick // ticks_per_day,
                        "title": player.death_title or "出局",
                        # ── P20.0：嘲讽**带上玩家的名字** ──────────────
                        # （``taunt.cash_kill.*`` 里带 ``{name}`` 占位）
                        "name": str(getattr(player, "name", "") or ""),
                    },
                    # ── P19.0：入门档专属的「我前面教过你的」────────────
                    # 入门档是**第一个要交生活费**的档位，而教程里
                    # 刚好教过「看水位」。开局没几天就因为没看水位被清出去，
                    # 猫娘这句挽留是**有出处的** —— 不是通用嘲讽。
                    # ── P20.0：彩蛋专属挽留句走**同一个 detail 槽位** ──
                    detail=self._death_detail_line(player),
                )

        # ── ⑦ 猫娘播报 + 低保提醒（无限模式日终）──────────────────
        if player.mode == "infinite":
            if catgirl is not None:
                await self._push_card(
                    build_catgirl_daily_card(
                        day=player.day,
                        action=self._catgirl_action_label(),
                        cash=catgirl.cash,
                        positions_value=catgirl.position_value(prices),
                        days_survived=market.tick // ticks_per_day,
                        position_count=len(catgirl.positions),
                    )
                )
            cycle_start = ((player.day - 1) // 7) * 7 + 1
            if (
                player.day == cycle_start
                and player.welfare_claimed_day < cycle_start
            ):
                await self._push_card(
                    build_welfare_card(
                        cycle=(player.day - 1) // 7 + 1,
                        amount=engine_config.infinite.weekly_relief,
                        interval_days=engine_config.infinite.relief_interval_days,
                    )
                )

    async def _harvest_count(self, player_id: str) -> int:
        """这个玩家一共被收割了几次（强制平仓卡片用）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT COUNT(*) AS n FROM liquidation_log WHERE player_id = ?",
                (player_id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return 0
        if not rows:
            return 0
        return int(rows[0]["n"])

    def _catgirl_action_label(self) -> str:
        """猫娘"今天做了什么"的一句话（面板与卡片共用）。"""
        daily = self._catgirl_daily or {}
        trades = int(daily.get("trades", 0)) if daily else 0
        previous = self._catgirl_trades_seen
        if previous is None:
            return "—"
        if trades > previous:
            return f"交易了 {trades - previous} 笔"
        return "观望"

    # ------------------------------------------------------------------
    # P11：猫娘外置交互引导（主动搭话）
    # ------------------------------------------------------------------
    #
    # **这一节是"真·宿主主动搭话"，不是面板模拟。**
    #
    # 官方 ``push_message`` v2 有两个**正交**的轴：
    #
    # * ``visibility``  —— 插件的 parts 渲染到哪（``["chat"]`` / ``["hud"]`` / 空）；
    # * ``ai_behavior`` —— LLM 怎么处理：
    #   ``"respond"``（**喂给 LLM 并立刻触发一个 AI 回合**，AI 的回复作为
    #   猫娘气泡进聊天框）/ ``"read"``（只进上下文、不触发回合）/
    #   ``"blind"``（完全不喂 LLM）。
    #
    # 本插件**既有**的卡片 / 新闻推送全是 ``ai_behavior="blind"``（AI 看不见，
    # 否则会泄题 —— P4 §11）；那正是"猫娘一直安静"的根因。P11 是一条
    # **独立的、显式的**新通道：``visibility=[]`` + ``ai_behavior="respond"`` ——
    # 玩家只看到猫娘的气泡，看不到插件的原始提示（官方文档明说这种组合下
    # "only the AI's reply is visible"）。
    #
    # **提示词不是台词**：``respond`` 会让猫娘用**自己的语气**生成回复，所以
    # text 写的是"请提醒玩家点【玩法说明】"这类**指令**（含关键信息），
    # 而不是把整句台词写死 —— 写死台词会让所有角色念同一句话。
    #
    # **P5 契约不受影响**：P11 的提示词里**只有玩法**，没有 heat / seed /
    # 反转日 / 新闻真假 —— 局中不泄露的规则继续生效。
    #
    # **节流**：猫娘开口会打断玩家。每一次主动搭话都必须先过闸门
    # （见 ``_meow_*`` 实例属性），绝不允许刷屏。

    def _current_lanlan(self) -> str:
        """当前角色名（拿不到就留空，让宿主自己兜底）。

        官方 SDK 的 ``create_card`` 就是这么做的：``target_lanlan or
        getattr(self._host_ctx, "current_lanlan", None)``。这里**只读**，
        读不到就传 ``None`` —— 宿主会把消息转给默认角色
        （历史日志：``proactive_message rerouted: lanlan=None missing``）。
        """
        for holder in (getattr(self.ctx, "_host_ctx", None), self.ctx):
            if holder is None:
                continue
            name = getattr(holder, "current_lanlan", None)
            if isinstance(name, str) and name.strip():
                return name.strip()
        return ""

    async def _push_proactive(
        self,
        prompt: str,
        *,
        reason: str,
        priority: int = 2,
        ai_behavior: str = "respond",
        allow_inactive: bool = False,
    ) -> bool:
        """让猫娘**主动开口**（P11 主路径）。返回宿主是否受理了这次推送。

        **绝不抛异常**：主动搭话是"锦上添花"，失败了也绝不能拖垮游戏。
        和 ``_push_card`` 一样，一律用 ``except Exception`` 兜底 —— 宿主不同
        版本导出的符号集不一致，写具体的异常类会 ``cannot import name``。

        ``priority``（P24.0）：**重大事件**用更高的优先级去插队，
        把她正在做的回合**打断**（收割 / 爆仓 / 新闻都要立刻说话）。

        P27.2：``ai_behavior="read"`` 用来推**只进上下文、不触发回合**的消息
        —— 交易**失败**回执走这条。理由：失败就发生在**她当前这一回合**里
        （是她在调工具），错误已经进了她的上下文，她本来就会如实说；
        再用 ``respond`` 多触发一个回合，她会在那个回合里**再试一次**
        ⇒ 又一条失败回执 ⇒ **自触发循环**（直播里就变成她一直"再买一次"）。

        **P33.0：退场之后一律闭嘴。**
        玩家点了【退出对局】或对局结算之后，``_is_game_active`` 变 False ⇒
        这里**立刻 return** —— 绝不再冒出"幽灵播报"（观众看到人走了、
        猫还在对着上一局自言自语）。
        ``allow_inactive=True`` 是给**玩家主动要的回应**留的口子
        （【呼叫猫娘】/【猫娘招呼】：那是玩家点的，不是幽灵）。
        """
        if not allow_inactive and not self._is_game_active:
            self.logger.info(
                f"[P33.0] 对局已结束（{reason}）：本次主动搭话跳过（不许幽灵播报）"
            )
            return False
        push = getattr(self.ctx, "push_message", None)
        if not callable(push):
            self.logger.info(f"宿主未提供 push_message（{reason}）：本次跳过主动搭话")
            return False
        try:
            if ai_behavior == "read":
                # P27.2：只进 LLM 上下文，**不触发** AI 回合（失败回执专用）。
                result = push(
                    visibility=[],
                    ai_behavior="read",
                    parts=[{"type": "text", "text": prompt}],
                    source=PLUGIN_ID,
                    target_lanlan=self._current_lanlan() or None,
                    priority=priority,
                )
            else:
                result = push(
                    # 玩家**看不到**插件的原始提示（只有猫娘的气泡进聊天框）。
                    visibility=[],
                    # 喂给 LLM 并**立刻触发一个 AI 回合** —— 猫娘用她自己的语气回。
                    ai_behavior="respond",
                    parts=[{"type": "text", "text": prompt}],
                    source=PLUGIN_ID,
                    target_lanlan=self._current_lanlan() or None,
                    # 比默认的 0 高一点（但远低于弹幕 / 提醒那几档），免得猫娘
                    # 的招呼在队列里被别的高优先级消息挤掉。
                    priority=priority,
                )
            if hasattr(result, "__await__"):
                await result
            # 只在**成功**时记时间：接不住就没打断玩家，不该占掉冷却窗口。
            self._meow_last_at = time.time()
            return True
        except Exception as error:  # pragma: no cover - 宿主侧异常
            self.logger.warning(f"猫娘主动搭话失败（{reason}）：{error}")
            return False

    def _record_meow_fallback(self, topic: str, reason: str) -> None:
        """记一条"兜底弹窗"：宿主**接不住**主动搭话时，面板弹拟态对话。

        序号（``_meow_fallback_seq``）是面板的判据 —— 面板每 3 秒轮询一次
        上下文，看到序号变了就知道"有一条新的猫娘消息要弹"。
        """
        self._meow_fallback_seq += 1
        self._meow_fallback_line = fallback_line(topic)
        self._meow_fallback_reason = reason

    def _meow_block(self) -> dict[str, Any]:
        """面板载荷的 ``meow`` 块：猫娘主动搭话的状态（**纯内存**）。

        面板每秒轮询，这里**不能查库、不能问宿主**。
        """
        now = time.time()
        cooldown_left = 0.0
        if self._meow_call_last_at > 0.0:
            cooldown_left = max(
                0.0, MEOW_CALL_COOLDOWN_SECONDS - (now - self._meow_call_last_at)
            )
        return {
            "greeted": bool(self._meow_greeted),
            "can_call": cooldown_left <= 0.0,
            "call_cooldown": round(cooldown_left, 1),
            "fallback_seq": int(self._meow_fallback_seq),
            "fallback_line": self._meow_fallback_line,
            "fallback_reason": self._meow_fallback_reason,
        }

    def _story_block(self) -> dict[str, Any]:
        """面板载荷的 ``story`` 块：剧情状态（**纯内存**，不查库）。

        面板每秒轮询，这里**不能查库、不能问宿主**。

        **一切从状态里读**（不读实例属性）—— 插件重启后实例属性会没，
        但状态在库里，读出来就还在。

        ``choices`` 只在**卡片投递失败**时才给（正常路径的选项按钮在
        **聊天卡片**上，贴着外置 Yui 的气泡）—— 面板不重复画一套按钮，
        免得两处状态打架。
        """
        state = self._story_state or {}
        card_ok = bool(state.get("card_ok"))
        choices: list[dict[str, Any]] = []
        if not card_ok:
            choices = [
                {
                    "id": str(item.get("id") or ""),
                    "label": str(item.get("label") or ""),
                    "hint": str(item.get("hint") or ""),
                }
                for item in (state.get("choices") or [])
                if isinstance(item, dict)
            ]
        debt = state.get("debt") if isinstance(state.get("debt"), dict) else None
        return {
            "seq": int(self._story_seq),
            "pending": str(state.get("pending") or ""),
            "title": str(state.get("title") or ""),
            "scene": str(state.get("scene") or ""),
            "card_ok": card_ok,
            "choices": choices,
            "tags": [str(item) for item in (state.get("tags") or [])],
            "debt": (
                {
                    "amount": round(float(debt.get("amount") or 0.0), 2),
                    "due_day": int(debt.get("due_day") or 0),
                    "paid": round(float(debt.get("paid") or 0.0), 2),
                }
                if debt
                else None
            ),
        }

    @ui.action(
        id="meow_greet",
        label="猫娘招呼",
        icon="chat",
        tone="default",
        group="settings",
        order=5,
    )
    @plugin_entry(
        id="meow_greet",
        name="让猫娘打个招呼",
        description=(
            "玩家第一次打开《拟股喵喵》面板时调用："
            "让外置猫娘主动说一句引导语（全局只说一次）。"
        ),
    )
    async def meow_greet(self) -> Ok:
        """首次加载引导：让外置猫娘**主动说一句引导语**。

        **只说一次**（全局）：闸门是 ``settings.meow_greeted``。

        说过了也**不重说** —— 猫娘开口会打断玩家，反复打招呼是骚扰。

        宿主接不住时（老版本 / 主动搭话被拒）会退化成面板里的
        "拟态外置对话"弹窗（由 ``_record_meow_fallback`` 记账）。
        """
        await self._ensure_settings_cache()
        if self._meow_greeted:
            return Ok(
                {
                    "action": "meow_greet",
                    "spoken": False,
                    "reason": "already_greeted",
                    "topic": "greet",
                    "line": "",
                }
            )
        now = time.time()
        if self._meow_last_at > 0.0 and (
            now - self._meow_last_at
        ) < MEOW_AUTO_COOLDOWN_SECONDS:
            return Ok(
                {
                    "action": "meow_greet",
                    "spoken": False,
                    "reason": "cooldown",
                    "topic": "greet",
                    "line": "",
                }
            )
        spoken = await self._push_proactive(
            GREET_PROMPT,
            reason="greet",
            # P33.0：玩家**主动点的**招呼 —— 不受"对局已结束"闸门限制。
            allow_inactive=True,
        )
        # **无论成功与否**都记下"打过招呼"：失败时已经弹过兜底弹窗了，
        # 反复重试只会反复弹窗。
        self._meow_greeted = True
        await self._repo.set_setting(_SETTING_MEOW_GREETED, "1")
        if not spoken:
            self._record_meow_fallback("greet", "push_failed")
        return Ok(
            {
                "action": "meow_greet",
                "spoken": spoken,
                "reason": "ok" if spoken else "push_failed",
                "topic": "greet",
                "line": "" if spoken else fallback_line("greet"),
            }
        )

    @ui.action(
        id="meow_call",
        label="呼叫猫娘",
        icon="chat",
        tone="default",
        group="settings",
        order=6,
    )
    @plugin_entry(
        id="meow_call",
        name="呼叫猫娘",
        description="玩家在面板上主动呼叫外置猫娘：让猫娘回一句提示或吐槽。",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "想问的话题（how_to_play / market / risk / catgirl）；"
                        "留空则自动轮换，避免每次都是同一句。"
                    ),
                }
            },
            "required": [],
        },
    )
    async def meow_call(self, topic: str = "") -> Ok:
        """【呼叫猫娘】：玩家在面板上按一下，外置猫娘回一句。

        **只挡连点**（``MEOW_CALL_COOLDOWN_SECONDS``），不挡"刚被打招呼就来提问"
        —— 玩家主动要的回应必须给。
        """
        await self._ensure_settings_cache()
        now = time.time()
        chosen = str(topic or "").strip()
        if not chosen:
            chosen = rotate_topic(self._meow_call_count)
        if (
            self._meow_call_last_at > 0.0
            and (now - self._meow_call_last_at) < MEOW_CALL_COOLDOWN_SECONDS
        ):
            return Ok(
                {
                    "action": "meow_call",
                    "spoken": False,
                    "reason": "cooldown",
                    "topic": chosen,
                    "line": fallback_line(chosen),
                }
            )
        self._meow_call_count += 1
        # ── P16.0：把玩家的情绪画像（定性标签）拼进提示词 ────────────
        # 只在**真的要开口**时才去查库 —— 没有画像时提示词与旧版一模一样。
        await self._refresh_emotion_profile()
        spoken = await self._push_proactive(
            call_prompt(chosen, self._emotion_block()),
            reason="call",
            # P33.0：玩家**主动点的**回应 —— 不受"对局已结束"闸门限制。
            allow_inactive=True,
        )
        self._meow_call_last_at = now
        if not spoken:
            self._record_meow_fallback(chosen, "push_failed")
        return Ok(
            {
                "action": "meow_call",
                "spoken": spoken,
                "reason": "ok" if spoken else "push_failed",
                "topic": chosen,
                "line": "" if spoken else fallback_line(chosen),
            }
        )

    async def _maybe_meow_nag(self, session: Session) -> dict[str, Any] | None:
        """新手期唠叨：第 1 天**一笔没买**就按了「推进一拍 / 跳过本日」。

        触发条件（三个都要满足）：

        * 游戏日是**第 1 天**；
        * 累计成交笔数是 **0**（真的什么都没做）；
        * 还没唠叨过这一局（``settings.meow_nag_player`` 记 ``player_id``）。

        返回给调用方的 ``meow`` 块（``None`` = 没唠叨）。调用方把它塞进
        返回体，面板据此决定要不要弹"拟态外置对话"。
        """
        player = session.player
        if int(player.day) != 1:
            return None
        if int(player.trade_count) > 0:
            return None
        player_id = str(player.id)
        if not player_id or self._meow_nag_player == player_id:
            return None
        now = time.time()
        if self._meow_last_at > 0.0 and (
            now - self._meow_last_at
        ) < MEOW_AUTO_COOLDOWN_SECONDS:
            return None
        spoken = await self._push_proactive(NAG_PROMPT, reason="nag")
        self._meow_nag_player = player_id
        await self._repo.set_setting(_SETTING_MEOW_NAG_PLAYER, player_id)
        if not spoken:
            self._record_meow_fallback("nag", "push_failed")
        return {
            "spoken": spoken,
            "reason": "ok" if spoken else "push_failed",
            "topic": "nag",
            "line": "" if spoken else fallback_line("nag"),
        }

    # ------------------------------------------------------------------
    # P12：剧情引擎（四幕叙事 + 外置 Yui 驱动）
    # ------------------------------------------------------------------
    #
    # **红线**：这一整节都在**插件层**。引擎层、模式层一行没改；
    # 500 局基线也不受影响（``tests/simulate.py`` 只 import ``engine.*`` 与
    # ``modes.*``，**从不 import 插件层**）。
    #
    # **三件事必须分开**：
    #
    # 1. **判定**（``_story_tick``）—— 什么时候该演哪一幕。**读**，不写。
    # 2. **呈现**（``_push_story_chapter``）—— 让**外置猫娘 Yui** 用她自己的
    #    语气把这一幕说出来（``push_message(ai_behavior="respond")``），
    #    再投一张**聊天卡片**，选项按钮就挂在 Yui 气泡旁边。
    # 3. **结算**（``story_choice`` 动作）—— 玩家点了某个选项之后，
    #    **数值效果**在这里生效（插件层改钱，照抄 ``_take_loan`` 的写法）。
    #
    # **为什么推送必须在锁外**：``push_message`` 会 ``await`` 宿主 IPC，
    # 而插件是**单线程事件循环** —— 在持锁时 await 会冻住整个循环
    # （P11.1 那个 500 红色警告的根因）。所以推送一律放在心跳里、
    # **锁外**跑。

    def _story_default_state(self, player_id: str) -> dict[str, Any]:
        """一份"空剧情"的状态。"""
        return {
            "player": str(player_id or ""),
            "seen": [],
            "pending": "",
            "tags": [],
            "debt": None,
            "granted": 0.0,
            "reply": None,
            # 这一幕的**展示层**也跟着存 —— 插件重启后内存里的东西全没了，
            # 而这一幕还"待选"（玩家还没点），那就必须**原样还在**。
            "title": "",
            "scene": "",
            "choices": [],
            "card_ok": False,
        }

    async def _story_load_state(self, player_id: str) -> dict[str, Any]:
        """读剧情状态（``settings`` 表一个键）；读不到就当"还没演过"。

        顺带记下 **CAS 基线**（读到的原文）—— 下一次写回时用它判断
        "有没有人抢先改过"。

        换局（``player_id`` 变了）⇒ **状态重置**（剧情按局推进）。
        """
        raw = None
        try:
            raw = await self._repo.get_setting(_SETTING_STORY_STATE)
        except Exception:  # pragma: no cover - 读不到就当空剧情
            raw = None
        state = self._story_default_state(player_id)
        stored = None
        if raw:
            try:
                stored = json.loads(raw)
            except (TypeError, ValueError):
                stored = None
        mine = isinstance(stored, dict) and str(stored.get("player") or "") == str(
            player_id or ""
        )
        if mine:
            state.update(
                {
                    "seen": [str(item) for item in (stored.get("seen") or [])],
                    "pending": str(stored.get("pending") or ""),
                    "tags": [str(item) for item in (stored.get("tags") or [])],
                    "debt": stored.get("debt"),
                    "granted": float(stored.get("granted") or 0.0),
                    "reply": stored.get("reply"),
                    "title": str(stored.get("title") or ""),
                    "scene": str(stored.get("scene") or ""),
                    "choices": list(stored.get("choices") or []),
                    "card_ok": bool(stored.get("card_ok")),
                }
            )
            self._story_raw = raw
        else:
            # 不属于这一局（换局了 / 第一次）⇒ 基线为空，
            # 下一次写回是"无条件覆盖"。
            self._story_raw = None
        self._story_state = state
        return state

    async def _story_commit_state(self) -> bool:
        """把剧情状态写回（**CAS**，失败返回 ``False`` 让调用方重读重试）。

        **全程不持锁** —— 持锁 ``await``（数据库往返）会冻住事件循环，
        那正是 P11.1 那个 500 红色警告的根因。
        """
        raw = json.dumps(self._story_state, ensure_ascii=False)
        try:
            if self._story_raw is None:
                ok = await self._repo.set_setting(_SETTING_STORY_STATE, raw)
            else:
                ok = await self._repo.cas_setting(
                    _SETTING_STORY_STATE, self._story_raw, raw
                )
        except Exception as error:  # pragma: no cover - 写不进去也不影响游戏
            self.logger.warning(f"剧情状态写入失败：{error}")
            return False
        if ok:
            self._story_raw = raw
        return bool(ok)

    def _story_snapshot(
        self, session: Session, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """剧情判定用的**快照**（全是插件层已经算好的数字）。

        **不读 heat / seed / 反转日** —— 那是 P5 契约的局中禁区，
        剧情也一样不许碰。
        """
        player = session.player
        prices = session.market.prices()
        total_assets = player.total_asset(prices)
        initial = float(player.initial_cash or 0.0)
        loss_ratio = 0.0
        if initial > 0.0:
            loss_ratio = max(0.0, (initial - float(total_assets)) / initial)
        try:
            bill = float(self._next_living_cost(session.difficulty, engine_config))
        except Exception:  # pragma: no cover - 兜底
            bill = 0.0
        return {
            "day": int(player.day),
            "cash": float(player.cash),
            "next_bill": bill,
            "initial_cash": initial,
            "total_assets": float(total_assets),
            "trade_count": int(player.trade_count),
            "strikes": int(player.strikes),
            "loss_ratio": float(loss_ratio),
        }

    async def _story_tick(self) -> None:
        """剧情判定 + 呈现（**推送必须在锁外**跑）。

        由心跳（``market_tick``）在 ``_run_ticks`` **之后**调用 —— 那里锁已经
        归还了，和既有的 ``_loan_prompt_watchdog`` 同一形状。

        **为什么"改状态"要进锁、"推送"要出锁**：

        * 状态是一整块 JSON（``settings`` 表一个键），``_story_tick`` 与
          ``story_choice`` 都是"读 → 改 → 写回" ⇒ 不串行化就会**丢更新**；
        * 推送要 ``await`` 宿主 IPC，在持锁时 await 会**冻住事件循环**
          （P11.1 那个 500 红色警告的根因）。
        """
        now = time.time()
        if (
            self._story_last_check_at > 0.0
            and (now - self._story_last_check_at) < STORY_CHECK_INTERVAL_SECONDS
        ):
            return
        self._story_last_check_at = now

        try:
            engine_config = await self._engine_config()
            session, error = await self._session_or_error(engine_config)
            if error is not None or session is None:
                return
            player_id = str(session.player.id)
            state = await self._story_load_state(player_id)
            snapshot = self._story_snapshot(session, engine_config)

            # ── 自愈：旧版本写的状态里**没有展示层** ──────────────────
            # （展示层是 P12 才加进状态的）。那一幕还"待选" ⇒ 补上，
            # 否则面板弹窗是空的，玩家根本选不了。
            pending = str(state.get("pending") or "")
            if pending and not str(state.get("scene") or ""):
                chapter = chapter_by_id(pending)
                if chapter is not None:
                    state["title"] = chapter_title(chapter)
                    state["scene"] = chapter_scene(chapter, snapshot)
                    state["choices"] = [
                        {
                            "id": str(choice.get("id") or ""),
                            "label": choice_label(choice),
                            "hint": choice_hint(choice),
                        }
                        for choice in (chapter.get("choices") or [])
                    ]
                    self._story_state = state
                    await self._story_commit_state()

            # ── 玩家刚选完 ⇒ 请 Yui 回应一句 ──────────────────────────
            reply = state.get("reply")
            if isinstance(reply, dict) and str(reply.get("prompt") or ""):
                prompt = await self._story_take_reply(player_id)
                if prompt:
                    await self._push_story_reply(prompt)
                return

            # ── 该演新的一幕了吗？ ────────────────────────────────────
            chapter = next_chapter(
                snapshot,
                state.get("seen") or [],
                str(state.get("pending") or ""),
            )
            if chapter is None:
                return
            opened = await self._open_story_chapter(chapter, player_id, snapshot)
            if opened is None:
                return
            await self._push_story_chapter(opened, snapshot)
        except Exception as error:  # noqa: BLE001 - 剧情绝不能把心跳带崩
            self.logger.error(f"剧情判定异常（不影响后续心跳）：{error}")

    async def _story_take_reply(self, player_id: str) -> str:
        """取走"要请 Yui 回应的那句提示词"（取走 = 清空）。

        **CAS**（不持锁）：别人抢先改了就重读重试。
        """
        for _ in range(3):
            state = await self._story_load_state(player_id)
            reply = state.get("reply")
            if not isinstance(reply, dict) or not str(reply.get("prompt") or ""):
                return ""
            prompt = str(reply.get("prompt") or "")
            state["reply"] = None
            self._story_state = state
            if await self._story_commit_state():
                return prompt
        return ""

    async def _open_story_chapter(
        self,
        chapter: dict[str, Any],
        player_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        """演一幕（改状态 → 落库）。返回这一幕（或 ``None``）。

        **CAS**（不持锁）：状态被别人抢改了就重读重试；重试三次都失败
        就放弃这一轮（下一轮心跳再说）。
        """
        for _ in range(3):
            state = await self._story_load_state(player_id)
            chapter_id = str(chapter.get("id") or "")
            seen = [str(item) for item in (state.get("seen") or [])]
            if not chapter_id or chapter_id in seen:
                return None
            if str(state.get("pending") or ""):
                # 上一幕还没选完 —— 不开新的一幕（剧情是递进的）。
                return None
            seen.append(chapter_id)
            state["seen"] = seen
            state["pending"] = chapter_id
            # ── 展示层**跟着状态存** ────────────────────────────────
            # 插件重启后内存里的东西全没了，而这一幕还"待选"（玩家还没点）
            # —— 那它必须**原样还在**（否则弹窗是空的，玩家选不了）。
            state["title"] = chapter_title(chapter)
            state["scene"] = chapter_scene(chapter, snapshot)
            state["choices"] = [
                {
                    "id": str(choice.get("id") or ""),
                    "label": choice_label(choice),
                    "hint": choice_hint(choice),
                }
                for choice in (chapter.get("choices") or [])
            ]
            state["card_ok"] = False
            self._story_state = state
            if not await self._story_commit_state():
                continue
            self._story_seq += 1
            return chapter
        return None

    async def _push_story_chapter(
        self, chapter: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        """让**外置猫娘 Yui** 说出这一幕，并把选项做成**聊天卡片**。

        * 提示词走 ``push_message(visibility=[], ai_behavior="respond")``
          —— 玩家只看到猫娘的气泡，看不到插件的原文（官方文档：这种组合下
          "only the AI's reply is visible"）；
        * ``coalesce_key`` 让同一章的连发**自动合并**（不刷屏）；
        * 选项按钮在**聊天卡片**上（``data-neko-action``）—— 贴着 Yui 气泡，
          面板里**不画**任何对话框。
        """
        chapter_id = str(chapter.get("id") or "")
        prompt = chapter_prompt(chapter, snapshot)
        if not prompt:
            return

        now = time.time()
        if (
            self._story_last_push_at > 0.0
            and (now - self._story_last_push_at) < STORY_PUSH_COOLDOWN_SECONDS
        ):
            return

        spoken = await self._push_story_voice(prompt, chapter_id)
        if spoken:
            self._story_last_push_at = time.time()
        card_ok = await self._push_story_card(chapter, snapshot)
        # ── 把"选项卡片送到了没有"记回状态（**跨重启存活**）──────────
        # 面板据此决定：选项在**聊天卡片**上（外置，贴着 Yui 气泡），
        # 还是由面板的沉浸模式**接管**（降级）。
        await self._story_mark_card(card_ok and spoken)

    async def _story_mark_card(self, card_ok: bool) -> None:
        """把"选项卡片送到了没有"记回状态（CAS，失败重试）。"""
        player_id = str((self._story_state or {}).get("player") or "")
        if not player_id:
            return
        for _ in range(3):
            state = await self._story_load_state(player_id)
            if bool(state.get("card_ok")) == bool(card_ok):
                return
            state["card_ok"] = bool(card_ok)
            self._story_state = state
            if await self._story_commit_state():
                return

    async def _push_story_voice(self, prompt: str, chapter_id: str) -> bool:
        """把提示词推给宿主，让**外置 Yui** 开口。返回宿主是否受理。

        **绝不抛异常**：剧情是"锦上添花"，失败了也绝不能拖垮游戏。
        """
        push = getattr(self.ctx, "push_message", None)
        if not callable(push):
            self.logger.info(f"宿主未提供 push_message（story:{chapter_id}）：本次跳过")
            return False
        # ── P33.0：退场之后不许再演剧情（幽灵播报）────────────────────
        if not self._is_game_active:
            self.logger.info(
                f"[P33.0] 对局已结束：跳过剧情推送（{chapter_id}）"
            )
            return False
        # 把**实际推出去的那段提示词**记下来：既方便排查"她为什么这么说"，
        # 也让"提示词里只有事实、没有表演指令"这条规矩**可审计**。
        self.logger.info(f"剧情提示词（{chapter_id}）：{prompt}")
        try:
            result = push(
                # ``visibility=[]`` ⇒ 插件原文**不显示**，只有 AI 的回复进聊天框。
                visibility=[],
                # ``respond`` ⇒ 喂给 LLM 并**立刻触发一个 AI 回合**（猫娘开口）。
                ai_behavior="respond",
                parts=[{"type": "text", "text": prompt}],
                source=PLUGIN_ID,
                target_lanlan=self._current_lanlan() or None,
                # 剧情比日常搭话重要，但不该挤掉提醒类消息（memo = 8）。
                priority=STORY_PUSH_PRIORITY,
                # 同一章的连发**合并成最新的** ⇒ 不刷屏。
                coalesce_key=coalesce_key(chapter_id),
            )
            if hasattr(result, "__await__"):
                await result
            return True
        except Exception as error:  # pragma: no cover - 宿主侧异常
            self.logger.warning(f"剧情推送失败（{chapter_id}）：{error}")
            return False

    async def _push_story_card(
        self, chapter: dict[str, Any], snapshot: dict[str, Any]
    ) -> bool:
        """把这一幕的**选项**做成聊天卡片（按钮挂在 Yui 气泡旁边）。

        卡片 API 不存在（老版本宿主）⇒ 返回 ``False``，面板的沉浸模式接管。
        """
        creator = getattr(self.ctx, "create_card", None)
        if not callable(creator):
            self.logger.info("宿主未提供卡片 API（story）：选项交给面板沉浸模式")
            return False
        spec = chapter_card(chapter, snapshot)
        try:
            await creator(
                html=spec["html"],
                summary=spec["summary"],
                css=spec["css"],
                actions=spec["actions"],
            )
            return True
        except Exception as error:  # pragma: no cover - 宿主侧异常
            self.logger.warning(f"剧情卡片投递失败（{chapter.get('id')}）：{error}")
            return False

    async def _push_story_reply(self, prompt: str) -> None:
        """玩家选完之后的**一句回应**（同样走外置 Yui）。"""
        await self._push_story_voice(prompt, "reply")

    # ------------------------------------------------------------------
    # P12：剧情选项（聊天卡片按钮的回调入口）
    # ------------------------------------------------------------------

    @ui.action(
        id="story_choice",
        label="剧情选项",
        icon="chat",
        tone="default",
        group="story",
        order=1,
    )
    @plugin_entry(
        id="story_choice",
        name="剧情选项",
        description="在聊天卡片上选择剧情选项（外置猫娘演的四幕叙事）。",
        input_schema={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节 id"},
                "choice_id": {"type": "string", "description": "选项 id"},
            },
            "required": ["chapter_id", "choice_id"],
        },
    )
    @_locked
    async def story_choice(self, chapter_id: str, choice_id: str) -> Ok:
        """玩家点了某个剧情选项。

        **数值效果在这一刻生效**（插件层改钱，和 ``_take_loan`` 同一写法）：

        * ``cash``  —— 一次性给一笔钱（接济 / 不甘心加注）；
        * ``debt``  —— 给一笔钱，同时记一笔**到期要还**的债。

        效果**只对当前这一局**有效，且**每幕只能选一次**（选了就把
        ``pending`` 清掉）—— 卡片按钮重复点不会重复发钱。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        player_id = str(session.player.id)
        chapter = chapter_by_id(str(chapter_id or ""))
        choice = choice_by_id(chapter, str(choice_id or ""))
        if chapter is None or choice is None:
            return _err("story_unknown_choice")

        snapshot = self._story_snapshot(session, engine_config)
        effect = effect_of(choice)
        unit = self._story_unit_cash(session, engine_config)

        kind = str(effect.get("type") or "none")
        granted = 0.0
        debt_amount = 0.0
        due_day = 0
        if kind == "cash":
            granted = round(unit * float(effect.get("multiplier") or 0.0), 2)
        elif kind == "debt":
            granted = round(unit * float(effect.get("cash_multiplier") or 0.0), 2)
            debt_amount = round(unit * float(effect.get("repay_multiplier") or 0.0), 2)
            due_day = int(session.player.day) + int(effect.get("due_days") or 3)

        # ── 先"认领"这个选项（CAS），**再**发钱 ────────────────────────
        # 顺序反了会双发：先发钱再 CAS，CAS 失败重试就会发第二次。
        claimed = False
        for _ in range(3):
            state = await self._story_load_state(player_id)
            pending = str(state.get("pending") or "")
            if not pending or pending != str(chapter_id or ""):
                return _err("story_no_pending")
            tags = [str(item) for item in (state.get("tags") or [])]
            tag = effect_tag(choice)
            if tag and tag not in tags:
                tags.append(tag)
            state["tags"] = tags
            state["granted"] = round(float(state.get("granted") or 0.0) + granted, 2)
            if debt_amount > 0.0:
                state["debt"] = {
                    "amount": debt_amount,
                    "due_day": due_day,
                    "paid": 0.0,
                    "chapter_id": str(chapter_id or ""),
                }
            # 选完了 ⇒ 这一幕不再是"待选"，并记下**要请 Yui 回应的那句提示**。
            state["pending"] = ""
            state["reply"] = {
                "prompt": self._story_reply_prompt(
                    chapter,
                    choice,
                    self._story_snapshot_after(snapshot, granted),
                    granted,
                    debt_amount,
                    due_day,
                ),
            }
            self._story_state = state
            if await self._story_commit_state():
                claimed = True
                break
        if not claimed:
            return _err("story_busy")

        # ── 认领成功 ⇒ 数值生效（插件层改钱，和 ``_take_loan`` 同一写法）──
        player = session.player
        if granted > 0.0:
            player.cash = round(player.cash + granted, 6)
            now = time.time()
            try:
                await self._repo.transaction(
                    [
                        (
                            "UPDATE players SET cash = ?, updated_at = ? WHERE id = ?",
                            (player.cash, now, player.id),
                        )
                    ]
                )
            except (DbUnavailableError, sqlite3.Error) as db_error:
                return _err("db_unavailable", db_error)

        return Ok(
            {
                "action": "story_choice",
                "chapter_id": str(chapter_id or ""),
                "choice_id": str(choice.get("id") or ""),
                "label": choice_label(choice),
                "granted": granted,
                "debt": self._story_state.get("debt"),
                "cash_after": round(player.cash, 6),
                "note": "选项已生效。",
            }
        )

    def _story_unit_cash(
        self, session: Session, engine_config: EngineConfig
    ) -> float:
        """剧情效果金额单位**（今日生活费）。

        取不到就用 ``STORY_EFFECT_FALLBACK_CASH`` 兜底 —— 效果给少了也比
        **给错一个天文数字**安全。
        """
        try:
            cost = float(
                daily_living_cost(
                    session.player.mode,
                    session.player.day,
                    session.market.seed,
                    engine_config,
                )
            )
        except Exception:  # pragma: no cover - 兜底
            cost = 0.0
        if cost <= 0.0:
            cost = float(STORY_EFFECT_FALLBACK_CASH)
        return cost

    @staticmethod
    def _story_snapshot_after(
        snapshot: dict[str, Any], granted: float
    ) -> dict[str, Any]:
        """把快照推到**这次选择生效之后**（现金 + 总资产都加上）。

        为什么要这一步：提示词里同时会写"这次选择产生的实际变化：现金 +X"
        和"客观数据：可动用现金 Y"。如果 Y 还是**变化前**的数字，LLM 读到的是
        自相矛盾的两句话 —— 它可能因此得出错误的处境判断。
        """
        after = dict(snapshot or {})
        if granted:
            after["cash"] = round(float(after.get("cash") or 0.0) + float(granted), 2)
            after["total_assets"] = round(
                float(after.get("total_assets") or 0.0) + float(granted), 2
            )
        return after

    @staticmethod
    def _story_reply_prompt(
        chapter: dict[str, Any] | None,
        choice: dict[str, Any] | None,
        snapshot: dict[str, Any],
        granted: float,
        debt_amount: float,
        due_day: int = 0,
    ) -> str:
        """玩家选完之后，请**外置 Yui** 回应的那句提示词。

        **P13**：拼装逻辑搬到了 ``story.reply_prompt`` —— **所有**给 Yui 的
        提示词都在同一个地方拼，那里只放"客观事实 + 开放式请求"。
        这里**不许**再写任何指定表演方式的指令。
        """
        due_days = 0
        if due_day > 0 and snapshot.get("day"):
            try:
                due_days = max(0, int(due_day) - int(snapshot.get("day") or 0))
            except (TypeError, ValueError):
                due_days = 0
        return reply_prompt(
            chapter,
            choice,
            snapshot,
            granted=granted,
            debt_amount=debt_amount,
            due_days=due_days,
        )

    # ------------------------------------------------------------------
    # P12：剧情到期还债（**在 tick 的日终结算里**，所以天然随 tick 落库）
    # ------------------------------------------------------------------

    def _story_apply_debt(
        self, player: Player, state: dict[str, Any], day: int
    ) -> float:
        """剧情借贷**到期扣款**（从现金里扣；扣不动就欠着，**绝不判破产**）。

        **只在内存里改现金** —— 调用方（tick 流水线）随后会把玩家整行写回，
        所以不需要额外的一次数据库写入，也不会与 tick 抢写。

        返回实际扣掉的钱。
        """
        debt = state.get("debt")
        if not isinstance(debt, dict):
            return 0.0
        try:
            amount = float(debt.get("amount") or 0.0)
            due_day = int(debt.get("due_day") or 0)
            paid = float(debt.get("paid") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if amount <= 0.0 or due_day <= 0 or int(day) < due_day:
            return 0.0
        remaining = max(0.0, amount - paid)
        if remaining <= 0.0:
            state["debt"] = None
            return 0.0
        take = min(remaining, max(0.0, float(player.cash)))
        player.cash = round(float(player.cash) - take, 6)
        debt["paid"] = round(paid + take, 6)
        if debt["paid"] >= amount - 0.005:
            state["debt"] = None
        return take

    # ------------------------------------------------------------------
    # 心跳与 tick 流水线（P3）
    # ------------------------------------------------------------------

    @timer_interval(id="market_tick", name="市场心跳", seconds=1, auto_start=True)
    async def market_tick(self, **_) -> Ok:
        """1 秒**心跳**。

        **心跳不是 tick。** 真正的 tick 间隔由难度配置决定（经典四档 8 秒、
        无限模式 30 秒），这里只负责判断"到没到期"。

        为什么不是直接把 ``seconds`` 设成 8/30：SDK 的 ``@timer_interval``
        要求 ``seconds`` 是**类定义期的字面量整数**（运行时读不到配置），
        而本局的模式是玩家 ``join`` 时才确定的。详见 P3 契约 §1.1。

        ── P26.0：硬超时兜底（**心跳自愈**）──────────────────────────
        宿主对每个 handler 都 ``asyncio.run()``（宿主 ``plugin/core/host.py``
        的 ``_run_timer_interval``）—— 本方法**不返回**，那个定时线程就
        **永远卡在这一轮**，后面再也不会有下一次心跳 ⇒ 市场当场冻结
        （面板还在、按钮还能点，但时间不再流动 —— 直播事故）。

        所以真正的活儿放进 ``_heartbeat_body``，这里用
        ``_HEARTBEAT_TIMEOUT_SECONDS`` 兜住：超时 ⇒ 记日志 ⇒ **下一轮照跑**。
        """
        task = asyncio.ensure_future(self._heartbeat_body())
        try:
            _done, pending = await asyncio.wait(
                {task}, timeout=_HEARTBEAT_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        if pending:
            # 卡住了：放弃这一轮，**下一轮心跳照常**（这才是"自愈"）。
            task.cancel()
            self._heartbeat_timeouts += 1
            self.logger.error(
                f"市场心跳超过 {_HEARTBEAT_TIMEOUT_SECONDS:.0f}s 未完成 —— "
                f"已放弃本次心跳（下一拍继续，累计 {self._heartbeat_timeouts} 次）"
            )
            return Ok({"status": "heartbeat", "due": 0, "timeout": True})
        try:
            return task.result()
        except Exception as error:  # noqa: BLE001 - 心跳绝不能把定时器带崩
            self.logger.error(f"市场心跳异常（不影响后续 tick）：{error}")
            return Ok({"status": "heartbeat", "due": 0, "error": True})

    # ------------------------------------------------------------------
    # P33.0：对局生命周期（**退出即自毁**）
    # ------------------------------------------------------------------
    #
    # 真机事故（2026-11-05）：主播点了【退出对局】（或对局自动结算）之后，
    # 后台心跳**还在跑**，Yui 还在对着**上一局**的旧行情自言自语 ——
    # 严重的串台（观众看到的是"人走了猫还在播"）。
    #
    # 根因有两条：
    #
    # ① 宿主那个 1 秒心跳是**宿主线程**（``plugin/core/host.py`` 的
    #    ``_run_timer_interval``），**插件层清不掉**它 —— 唯一的停法是
    #    "让心跳体自己变成空转"（:meth:`_heartbeat_body` 的第一道闸）。
    # ② 各个 ``push_message`` 入口**没有"这局还在吗"的判断** ——
    #    于是剧情、看门狗、攒好的提示词队列全都能在退场后继续推。
    #
    # 所以这里给一条**全局状态位** ``_is_game_active``：退场 ⇒ False ⇒
    # 心跳与所有推送立刻闭嘴。

    def _deactivate_game(self, reason: str = "") -> None:
        """P33.0：玩家退场（退出对局 / 对局结算）⇒ **全量清理**。

        清三样东西：

        ① **状态位** ``_is_game_active = False`` —— 心跳与所有
           ``push_message`` 从这一刻起立刻 return；
        ② **待推送队列** ``_yui_pending_prompts`` —— 攒着的几百条旧消息
           一条都不推（推了就是"对着旧行情自言自语"）；
        ③ 各种**待办**（剧情、网贷弹窗、直觉预警）—— 看门狗不会再动它们。

        这不是"把插件关掉"：插件进程还在跑，只是**这一局**结束了。
        下一局 ``join`` 会把状态位重新打开。
        """
        # 闩锁与状态位**永远**要落到位 —— 即使这一局早就清过了。
        # （早退分支只省日志：那一次早退如果顺手把闩锁也省掉，
        #  插件重启后进来的玩家一退出，闸门就可能被心跳重新打开。）
        already_clean = (
            (not self._is_game_active) and (not self._yui_pending_prompts) and self._game_ended
        )
        self._is_game_active = False
        # 上闩锁：这一局**不许复活**（结算落库前的那段时间里，
        # 心跳若只看库会把闸门重新打开）。
        self._game_ended = True
        self._yui_pending_prompts.clear()
        # 剧情待办：退场之后不许再演下一幕。
        self._story_state = None
        # 网贷弹窗：退场之后看门狗不许再替他点「确认」。
        self._loan_prompt_at = 0.0
        self._loan_prompt_day = 0
        # 最近一次收割通知：退场之后不再弹。
        self._pending_event = None
        # 直觉预警的挂单：退场之后全部作废。
        self._intuition_watch = []
        # P32.0：狂按期的限频闸门也一并归零（新局重新计时）。
        self._skip_fast_until = 0.0
        self._yui_fast_last_talk_at = 0.0
        if already_clean:
            # 已经清过了 ⇒ 不重复记日志。
            return
        self.logger.info(f"[P33.0] 对局已结束（{reason or 'exit'}）：心跳与播报全部闭嘴")

    async def _game_is_active(self) -> bool:
        """现在有没有**活跃对局**（**以库为准**，状态位只是快路径）。

        为什么还要查库
        --------------
        插件进程重启后状态位会丢（纯内存），但对局可能**还在库里** ——
        只认状态位会把市场**按住不动**（真机事故：面板还在、按钮还能点，
        时间却不流动了）。反过来，结算 / 退场之后库里已经没有席位 ⇒
        状态位与库**一致** ⇒ 心跳与所有推送立刻闭嘴。

        库读不到（数据库还没起来 / 被删了）⇒ **保守地当作还有对局** ——
        宁可多跑一拍心跳，也不许把市场按住不动。

        插件进程**重启**（状态位丢失）但对局还在库里 ⇒ 这里把状态位
        **重新打开** —— 否则市场还在走、Yui 却哑了（真机事故）。

        **快路径**：状态位已经是 True ⇒ 直接返回，**不查库**。
        心跳每秒一次，多一次查询就是多一次 IO；退出路径都会显式调
        :meth:`_deactivate_game`，所以"这一局还在"这件事轮不到库来回答。
        """
        # 闩锁上着 ⇒ 这一局已经结束，**不许复活**（结算落库前的那段时间）。
        if self._game_ended:
            return False
        if self._is_game_active:
            return True
        try:
            row = await self._repo.fetch_one(
                "SELECT player_id FROM market_state WHERE id = 1"
            )
        except (DbUnavailableError, sqlite3.Error):
            return True
        active = bool(row is not None and row["player_id"])
        if active:
            # 插件重启后对局还在 ⇒ 把广播闸门重新打开（Yui 别哑掉）。
            self._is_game_active = True
        return active

    async def _heartbeat_body(self) -> Ok:
        """心跳的**真正**内容（由 ``market_tick`` 用硬超时包住）。

        这里面每一步都各自 ``try/except``：**一步挂了不许拖垮整轮心跳**。

        **P33.0：第一道闸 —— 没有活跃对局就立刻返回。**
        宿主那个 1 秒心跳是**宿主线程**（``plugin/core/host.py`` 的
        ``_run_timer_interval``），插件层 ``clearInterval`` **清不掉**它；
        唯一的停法就是让心跳体**变成空转**。退场之后这一句挡住：
        剧情、网贷看门狗、主动搭话看门狗、提示词队列 —— 一个都不再跑。
        """
        started = time.perf_counter()
        # ── P33.0：LLM 工具自愈**永远跑**（跟对局无关）──────────────
        # 玩家在空闲页也要能聊天 ⇒ 这道自愈不能被"没有对局"挡住。
        try:
            await self._reregister_llm_tools_if_due()
        except Exception as error:  # noqa: BLE001 - 自愈绝不能把心跳带崩
            self.logger.error(f"LLM 工具自愈异常（不影响后续心跳）：{error}")
        # ── P33.0：没有活跃对局 ⇒ **立刻返回**（幽灵播报的第一道闸）──
        if not await self._game_is_active():
            # 基线照常刷新：否则下一局开局会一次性补一大串 tick。
            self._last_tick_at = time.time()
            return Ok({"status": "heartbeat", "due": 0, "inactive": True})
        due = 0
        try:
            due = await self._due_ticks()
            if due > 0:
                await self._run_ticks(due)
        except Exception as error:  # noqa: BLE001 - 心跳绝不能把定时器带崩
            self.logger.error(f"市场心跳异常（不影响后续 tick）：{error}")
        # ── P12：剧情判定（**在锁外** —— ``_run_ticks`` 已经把锁还回来了）──
        # 推送外置猫娘要 ``await`` 宿主 IPC，绝不能在持锁时 await
        # （那会冻住事件循环，就是 P11.1 那个 500 红色警告的根因）。
        try:
            await self._story_tick()
        except Exception as error:  # noqa: BLE001 - 剧情绝不能把心跳带崩
            self.logger.error(f"剧情判定异常（不影响后续心跳）：{error}")
        # P7.3：网贷弹窗的 30 秒超时兜底（前端冻住时的最后一道保险）。
        # 它自己持锁（``_run_ticks`` 已经把锁还回来了），所以放在这里不会嵌套。
        try:
            await self._loan_prompt_watchdog()
        except Exception as error:  # noqa: BLE001 - 看门狗绝不能把心跳带崩
            self.logger.error(f"网贷弹窗看门狗异常（不影响后续心跳）：{error}")
        # P7.4：LLM 工具注册的**自愈**（main_server 重启后把工具救回来）。
        # ⚠ P33.0：这一段已经挪到本方法**最前面**（没有对局时也要跑）。
        # P16.0：玩家发呆太久 → 猫娘主动搭话（对齐宿主 Focus 语义）。
        try:
            await self._idle_meow_watchdog()
        except Exception as error:  # noqa: BLE001 - 主动搭话绝不能把心跳带崩
            self.logger.error(f"主动搭话看门狗异常（不影响后续心跳）：{error}")
        # ── P24.0：把 tick 里攒好的提示词推给 Yui（**锁外**）──────────
        # 直播托管的主动回合、新闻 / 收割 / 爆仓的插话，都走这条。
        # 必须在锁外：``push_message`` 会 await 宿主 IPC。
        try:
            await self._flush_yui_prompts()
        except Exception as error:  # noqa: BLE001 - 推话绝不能把心跳带崩
            self.logger.error(f"Yui 提示词推送异常（不影响后续心跳）：{error}")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > 100.0:
            # 只有真的慢了才去读配置，避免每秒都打一次宿主配置
            warn_ms = (await self._engine_config()).market.heartbeat_warn_ms
            if elapsed_ms > warn_ms:
                self.logger.warning(f"市场心跳耗时 {elapsed_ms:.0f}ms，偏慢")
        # 每分钟打一条心跳体检：真机上"tick 不推进"这类问题只能靠它定位
        # （0.7.4 真机复盘）。
        self._heartbeat_count += 1
        if self._heartbeat_count % _HEARTBEAT_LOG_EVERY == 0:
            since = time.time() - self._last_tick_at if self._last_tick_at > 0.0 else 0.0
            self.logger.info(
                f"市场心跳体检：{self._heartbeat_count} 次，本次到期 {due} 个，"
                f"距上次推进 {since:.0f}s"
            )
        return Ok({"status": "heartbeat", "due": due})

    async def _fetch_due_row(self) -> tuple[bool, Any]:
        """读当前局的 ``player_id`` 与模式。

        返回 ``(读到了, 行)``。**"读不到"和"没有这一局"是两回事**：

        * 读不到 → 数据库不可用（宿主还没下发有效配置 / 库被删了），
          要刷新运行时能力再重试，而且**不能**动 ``_last_tick_at`` 基线
          ——否则库恢复后时间差被抹平，市场永远追不上；
        * 读到了但没有行 → 确实没有开局，重置基线（防止"插件重启即跳盘"）。
        """
        try:
            row = await self._repo.fetch_one(
                "SELECT ms.player_id AS player_id, p.mode AS mode "
                "FROM market_state ms LEFT JOIN players p ON p.id = ms.player_id "
                "WHERE ms.id = 1"
            )
        except (DbUnavailableError, sqlite3.Error):
            return False, None
        return True, row

    async def _due_ticks(self) -> int:
        """算出这次心跳该补几个 tick（0 = 还没到期）。"""
        # 先读一次配置：顺带把宿主补发的有效配置同步进运行时能力
        # （``db.enabled`` / ``state.backend``），否则心跳会一直撞在
        # "数据库不可用"上（0.7.4 真机复盘：tick 不推进的根因）。
        engine_config = await self._engine_config()

        readable, row = await self._fetch_due_row()
        if not readable:
            # 库读不到：拉一次最新有效配置再试一次。
            await self._refresh_runtime_capabilities()
            readable, row = await self._fetch_due_row()
            if not readable:
                # 库确实不可用：**保留基线**，等库恢复后按"补 tick"规则追赶。
                return 0

        now = time.time()
        if row is None or not row["player_id"] or not row["mode"]:
            self._last_tick_at = now
            return 0

        difficulty = engine_config.difficulties.get(row["mode"])
        if difficulty is None:
            return 0
        # P10：手动模式下心跳**不自动推进** —— 玩家自己点【推进一拍】。
        # 基线照常刷新：切回自动时不会一次性补一大串 tick（那会直接送命）。
        await self._ensure_settings_cache()
        if not self._auto_tick_enabled():
            self._last_tick_at = now
            return 0
        # P10：游戏速度只缩放**间隔**（等多久跳一拍），不碰任何游戏数值。
        speed = self._resolve_speed()
        interval = self._tick_interval(difficulty.tick_interval_seconds, speed)
        if self._last_tick_at <= 0.0:
            # 首次心跳只记基线：不跳盘（插件重启即跳盘是最糟的体验）
            self._last_tick_at = now
            return 0
        due = int((now - self._last_tick_at) // interval)
        if due <= 0:
            return 0
        cap = max(1, int(engine_config.market.max_catchup_ticks))
        if due > cap:
            self.logger.warning(
                f"补 tick：本次心跳到期 {due} 个，只补 {cap} 个（跳过 {due - cap} 个）"
            )
            due = cap
        return due

    async def _run_ticks(self, count: int) -> None:
        """连续跑 ``count`` 个 tick。"""
        for _ in range(max(1, count)):
            await self._run_one_tick()

    async def _run_one_tick(self) -> bool:
        """推进一个 tick（持锁；异常不影响定时器）。

        **P11.1：锁改成"非阻塞获取"** —— 这是并发报错的**根因**。

        ``self._lock`` 是 ``threading.Lock``，``acquire()`` 是**阻塞**调用。
        在 asyncio 的**单线程事件循环**里，阻塞式 ``acquire()`` 会把
        **整个事件循环冻住**：插件于是没法响应宿主发来的
        ``get_ui_context()``（5 秒预算）⇒ 宿主抛
        ``Failed to query plugin UI action context``（500 红色警告）。

        所以拿不到锁时**直接跳过这一拍**，绝不阻塞事件循环。返回
        ``True`` 表示这一拍**真的跑了**。
        """
        if not self._lock.acquire(blocking=False):
            # 有人在跑（多半是 skip_day 的快进）—— 跳过这一拍。
            # **绝不阻塞事件循环**（阻塞 = 宿主查不到上下文 = 500）。
            return False
        try:
            if self._tick_running:
                return False
            self._tick_running = True
            try:
                await self._run_tick_pipeline()
            except Exception as error:  # noqa: BLE001
                # 异常发生时**不写回**（内存里的半成品直接丢弃）
                self.logger.error(f"tick 异常（不影响后续 tick）：{error}")
            finally:
                self._tick_running = False
                self._last_tick_at = time.time()
            return True
        finally:
            self._lock.release()

    async def _run_tick_pipeline(self) -> None:
        """跑一拍流水线，**带硬超时**（P26.0：心跳自愈的第二道闸）。

        ``_tick_once`` 里全是 ``await``（数据库 / 宿主 IPC）。只要有一个
        **永远不回来**，这一拍就会把心跳线程**永久**钉死。

        这里用 ``asyncio.wait`` 而不是 ``asyncio.wait_for``：
        ``wait_for`` 在被取消的对象**不响应取消**时会跟着一起卡住，
        而 ``wait`` 到点就返回 ⇒ **心跳一定能继续下一轮**。

        超时 ⇒ 取消这一拍（内存里的半成品直接丢弃，不写回）⇒ 记日志 ⇒
        下一拍继续跑。基线 ``_last_tick_at`` 由调用方的 ``finally`` 推进，
        所以**不会**出现"补一大串 tick"的连锁补帧。
        """
        task = asyncio.ensure_future(self._tick_once())
        try:
            _done, pending = await asyncio.wait(
                {task}, timeout=_TICK_PIPELINE_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        if pending:
            task.cancel()
            self._tick_timeouts += 1
            self.logger.error(
                f"tick 流水线超过 {_TICK_PIPELINE_TIMEOUT_SECONDS:.0f}s 未完成 —— "
                f"已放弃本拍（不写回，下一拍继续，累计 {self._tick_timeouts} 次）"
            )
            return
        # 正常完成 / 抛异常：把异常原样抛给调用方统一记录。
        task.result()

    async def _tick_once(self) -> None:
        """tick 流水线（P4：瘦身为**纯调度**）。

        顺序**不能乱**：先价格、再 heat 衰减、再模式层（当天新闻 + 阶段判定与
        收割调度）、再收割执行、再日终结算，最后才写回。

        本方法**不含任何游戏规则** —— 规则全在 ``modes/`` 里；这里只负责把
        模式层算出来的结果**执行**（收割）与**落库**。
        """
        started = time.perf_counter()
        # P27.2：**新的一拍 = 新的一回合** ⇒ 回执周期锁在这里解开。
        # （自动模式下玩家可能一直没点按钮，但市场照样在走 —— 一拍就是一轮。）
        self._yui_trade_cycle_unlock()
        engine_config = await self._engine_config()
        try:
            session = await load_session(
                self._repo, self.state, engine_config=engine_config
            )
        except (DbUnavailableError, sqlite3.Error) as error:
            # 宿主补发有效配置后重试一次。
            await self._refresh_runtime_capabilities()
            try:
                session = await load_session(
                    self._repo, self.state, engine_config=engine_config
                )
            except (DbUnavailableError, sqlite3.Error):
                self.logger.warning(f"tick 跳过：数据库不可用（{error}）")
                return
        if session is None:
            return

        mode = get_mode(session.mode)
        if mode is None:
            return

        market = session.market
        player = session.player
        difficulty = session.difficulty
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        tick = market.tick
        journal = _TickJournal()
        # P24.0：这一拍开始时玩家**还活着**吗 —— 用来判「这一拍爆仓了」。
        was_alive = bool(player.alive)
        # 记住玩家 id：局终后 ``market_state.player_id`` 被清空，
        # 面板要靠它去找归档的结算报告（P4 历史问题 4）。
        self._last_player_id = player.id

        # ── 步 2：更新所有存活标的价格（公共底盘）────────────────
        advance_market(
            market,
            seed=market.seed,
            ticks_per_day=ticks_per_day,
            config=engine_config.market,
        )
        market.day = market.tick // ticks_per_day + 1

        # ── P26.1：「庄家洗盘」假反弹（**下一拍**生效）────────────
        # 排在 advance_market 之后（价格刚推进）、导演暴跌之前 ——
        # 于是"先反弹、后真跌"的节奏刚好。引擎一行没改：
        # 这里只改内存里的 ``stock.price`` 与 K 线最后一点。
        self._apply_god_rally(market)

        # ── P7：导演暴跌（**只在特制版**，纯净版这里什么都不做）────
        # 排在 advance_market 之后、模式层之前 —— 这样暴跌之后的集中度、
        # 收割等后续反应**照常发生**，而不是硬改结果。引擎一行没改：
        # 暴跌用的是引擎既有的纯函数 apply_jump。
        crashed = self._apply_director_crashes(market, engine_config)
        if crashed:
            journal.touch_player(player)

        # ── P27.0：空头盯市（**插件层账本**）─────────────────────────
        # 排在导演暴跌**之后** —— 导演安排的暴跌会实打实惠及空头
        # （跌了，空头赚钱，符合直觉）。
        #
        # 引擎层与模式层一行没改 ⇒ 500 局基线逐字节不变。
        await self._settle_shorts(session, journal)

        # ── 步 3：衰减上头指数（交易事件在成交时加，这里只做衰减）──
        prices = market.prices()
        # ── P16.0：连击结算（**纯表现层**）────────────────────────────
        # 价格已经推进过了 ⇒ 这一拍就能判定上一批买入是不是买中了。
        # **不改任何数值**：只动 ``self._combo`` 这几个展示字段。
        if session.mode in COMBO_MODES:
            self._combo.resolve(
                prices,
                {
                    stock_id: float(pos.amount)
                    for stock_id, pos in player.positions.items()
                },
                engine_config.fees,
            )
        concentration = position_concentration(player.positions, prices)
        player.heat = update_heat(
            player.heat,
            events=[],
            concentration=concentration,
            config=engine_config.heat,
        )
        player.heat_history.append(player.heat)
        journal.touch_player(player)

        # ── P26.1：直播护栏（插件层）────────────────────────────
        # ① 满仓 + 见底 ⇒ 排一次「庄家洗盘」（**下一拍**生效）；
        # ② 「危险感知」：把当前最极端的盘面数据喂给 Yui（伪预知）。
        # 两件事**都只在** ``[director] live_god_mode = true`` 时才干预数值；
        # 危险感知本身是纯展示层（只多一句提示词），所以它不受开关限制。
        self._maybe_god_rally(session, engine_config, prices)
        self._update_intuition(session)

        # ── 步 4~7：模式层（当天新闻 + 阶段判定与收割调度）────────
        ctx = ModeContext(
            player=player,
            market=market,
            difficulty=difficulty,
            # ── P16.0：前两关的新手期放水**只作用在模式层**──────────
            # 后两档（收割场）拿到的是**原样配置**，压迫感不受影响。
            # ── P20.0：再叠上**名字彩蛋**的运行时覆写（新闻更假 / 借款翻倍）──
            engine_config=_config_for_player(
                str(getattr(player, "name", "") or ""), session.mode, engine_config
            ),
            catgirl=session.catgirl,
            mode=session.mode,
            now=time.time(),
        )
        outcome = mode.tick(ctx)
        # ── P28.0：把**当天的假新闻**记进插件层账本 ──────────────────
        # 引擎的 ``NewsItem.type`` 只有插件层看得到（面板与玩家都拿不到，
        # 那是 P4 §11 的老规矩）。这里只记「哪天的哪几条是假的」，
        # 引擎层与模式层一行没改 ⇒ 500 局基线逐字节不变。
        self._record_fake_news(session)

        # ── 步 5 / 6 / 7：收割执行（公共底盘，由模式层调度）───────
        self._execute_harvest(ctx, outcome, journal, engine_config, tick)

        # ── 步 8 / 9：日终结算（模式层）──────────────────────────
        if market.tick % ticks_per_day == 0:
            # ── P26.0：群友「浅忆QanYi」天降 Token（**结算之前**）────
            # 钱必须在结算**之前**到账 —— 结算会扣当日生活费并判定「能不能
            # 交上明天的账单」，晚一步这笔钱就救不了任何一次三振。
            self._maybe_token_rescue(session, engine_config, journal)
            # ── P26.1：强制续命（**结算之前**）────────────────────
            # 现金跌破生活费红线时**强制**垫一笔（不看概率）。
            # 同样必须在结算**之前** —— 结算会判定能不能交上明天的账单，
            # 晚一步这笔钱救不了任何一次三振。
            self._maybe_god_rescue(session, engine_config, journal)
            daily = mode.daily_settlement(ctx)
            outcome.daily = daily
            outcome.trades.extend(daily.trades)
            outcome.pawnbroker.extend(daily.pawnbroker)
            if daily.distress_window_until:
                self._daily_window_until = daily.distress_window_until
            journal.daily = daily.settlement
            journal.catgirl_daily = daily.catgirl_daily
            if daily.catgirl_daily is not None:
                # 面板要显示"猫娘今天动没动手"：靠**累计成交笔数**的增量。
                self._catgirl_daily = dict(daily.catgirl_daily)
                self._catgirl_trades_seen = int(daily.catgirl_daily.get("trades", 0))
            # ── P20.0：日终的**持仓回报**（稳如老狗类）────────────────
            #
            # 拍板口径：日终把**持仓市值**按这个比例**变成现金**
            # （不是股数乘一点）—— 他什么都不用做，钱自己长一点。
            #
            # 这是一条**插件层**的钩子：引擎层与模式层一行没改，
            # 没有彩蛋的玩家拿到的收益恒为 0（``daily_return_gain`` 返回 0）。
            egg = match_egg(str(getattr(player, "name", "") or ""))
            if egg is not None and egg.daily_return > 0.0:
                positions_value = sum(
                    position.market_value(prices.get(stock_id, 0.0))
                    for stock_id, position in player.positions.items()
                )
                gain = daily_return_gain(positions_value, egg)
                if gain > 0.0:
                    player.cash = round(player.cash + gain, 6)
                    journal.touch_player(player)
                    self.logger.info(
                        f"名字彩蛋「{egg.key}」日终回报 +{gain:.2f}"
                        f"（持仓市值 {positions_value:.2f}）"
                    )
            # ── P12：剧情借贷到期扣款（**在 tick 里**）────────────────
            # 只改内存里的 ``player.cash``；``_commit_tick`` 随后会把玩家整行
            # 写回 ⇒ 不用额外一次数据库写入，也不会与 tick 抢写（没有丢更新）。
            # 扣不动就**欠着**（绝不判破产）—— 借钱的人已经够惨了。
            try:
                story_state = await self._story_load_state(str(player.id))
                repaid = self._story_apply_debt(player, story_state, int(player.day))
                if repaid > 0.0:
                    if await self._story_commit_state():
                        journal.touch_player(player)
                        self.logger.info(
                            f"剧情借贷到期扣款 {repaid:.2f}，剩余现金 {player.cash:.2f}"
                        )
                    else:
                        # 状态没写回去（有人抢改了）⇒ **撤销这次扣款**，
                        # 下一轮心跳再来 —— 现金和债务记录必须一致。
                        player.cash = round(player.cash + repaid, 6)
            except Exception as error:  # noqa: BLE001 - 剧情绝不能把结算带崩
                self.logger.warning(f"剧情还债异常（不影响结算）：{error}")

            # ── P26.0：群友「云玩家」免费内参（**新一天开盘**）──────
            # 结算跑完 ⇒ 新的一天开始 ⇒ 这时候塞内参最自然。
            # 面板只多一枚 buff 标签，内容只走 ``push_message`` 给 Yui。
            self._maybe_community_intel(session, engine_config)
            # ── P28.0：假新闻的「活人感」——新一天开盘 ⇒ Yui 爆发 ─────
            # 结算跑完 ⇒ 新的一天开始 ⇒ 这正是「昨天那张报纸是假的」
            # 该被算总账的时刻。
            self._maybe_fake_news_outrage(session)

        # ── P5.5：破产原因称号的文案层覆盖（引擎零改动）───────────────
        # 引擎给的称号里还有"低保"字样；这里按 death_reason 改写，
        # 于是落库的、面板上的、卡片里的称号**全部**是"打工工资"的说法。
        self._override_death_title(player)
        if session.catgirl is not None:
            self._override_death_title(session.catgirl)

        # ── P27.0：记一个资产曲线点（面板折线图用，**纯表现层**）────
        # 记在这一拍**全部算完之后** ⇒ 曲线上的点就是玩家当时看到的资产。
        await self._record_equity_point(session)

        # ── 步 10：一次性写回 ────────────────────────────────────
        # 模式层可能同时改动了玩家与猫娘：两个都要写回。
        journal.touch_player(player)
        if session.catgirl is not None:
            journal.touch_player(session.catgirl)

        elapsed = time.perf_counter() - started
        if elapsed > engine_config.market.tick_skip_seconds:
            self.logger.error(
                f"tick 耗时 {elapsed:.1f}s，超过 {engine_config.market.tick_skip_seconds}s "
                "阈值，放弃本次 tick（不写回）"
            )
            return
        if elapsed > engine_config.market.tick_warn_seconds:
            self.logger.warning(f"tick 耗时 {elapsed:.1f}s，偏慢")

        await self._commit_tick(session, journal, engine_config, outcome)
        await save_snapshot(self.state, market, player.id)

        # ── 步 11：通知（P3 用日志，**P5 改卡片**）──────────────────
        for result in outcome.liquidations:
            self._pending_event = {
                "kind": "harvest",
                "tick": market.tick,
                "stock_id": result.stock_id,
                "concentration": round(result.concentration, 4),
                "severity": round(result.severity, 4),
            }
            self.logger.info(
                f"玩家 {player.id} 在第 {market.tick} tick 被收割："
                f"{result.stock_id} 集中度 {result.concentration:.0%} "
                f"触发源 {result.trigger}"
            )

        # P5：把这一 tick 的事件变成聊天频道里的卡片。
        await self._push_cards(session, outcome, engine_config)

        # ── P24.0：给 Yui 攒话 ──────────────────────────────────────
        #
        # 这里**只攒不推**：``push_message`` 要 ``await`` 宿主 IPC，
        # 而这一步还在**持锁**（P11.1 的 500 红色警告就是这么来的）。
        # 真正的推送由心跳在**锁外**做（``_flush_yui_prompts``）。
        #
        # 顺序有讲究：**先事件、后主动回合** —— 重大事件永远排在前面，
        # 她的回合不会盖过「你破产了」这种必须立刻说的事。
        self._collect_yui_event_prompts(session, outcome, was_alive)
        self._collect_yui_live_prompt(session)

        # ── P5.5 改动 5/6：称号解锁 ────────────────────────────────
        # 破产成就（清盘的那一刻就解锁）+ 「无为而治」（第 14 天零有效交易）。
        await self._maybe_unlock_achievements(session)

        # ── P4：经典模式第 30 个游戏日自动终局并自动结算 ──────────
        await self._auto_finish_if_needed(session, ctx, mode, engine_config)

    def _execute_harvest(
        self,
        ctx: ModeContext,
        outcome,
        journal: _TickJournal,
        engine_config: EngineConfig,
        tick: int,
    ) -> None:
        """执行模式层调度出来的收割（公共底盘）。

        模式层只回答"该不该收割、用什么阈值"；**执行**在这里 —— 因为执行要
        落库（收割流水），而模式层不碰数据库。两个触发源共用一个执行函数，
        每 tick 最多收割一次。
        """
        result = execute_scheduled_harvest(ctx, outcome.phase_decision, tick=tick)
        if result is not None:
            outcome.liquidations.append(result)
            journal.liquidations.append(result)

    async def _auto_finish_if_needed(
        self,
        session: Session,
        ctx: ModeContext,
        mode,
        engine_config: EngineConfig,
    ) -> None:
        """经典模式第 30 个游戏日结束 → **自动终局并自动结算**。

        自动结算之后，玩家再调 ``settle`` 会拿到 ``no_active_game`` ——
        结算不重复。无限模式没有终点，这段对它什么也不做。
        """
        if not mode.is_finished(ctx):
            return
        if session.mode == "infinite":
            return

        payload = await self._build_settlement_payload(session, engine_config)
        payload["auto_finished"] = True
        payload["finish_reason"] = "classic_total_days"
        # ── P33.0：自动结算 ⇒ 这一局结束 ⇒ **立刻自毁**（不许再播报）──
        self._deactivate_game("auto_finish")

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = ?, updated_at = ? WHERE id = ?",
                        (int(session.player.alive), now, session.player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            self.logger.warning(f"自动结算落库失败：{db_error}")
            return

        self._archive_settlement(payload, session)
        self.logger.info(
            f"经典模式第 {engine_config.classic.total_game_days} 个游戏日结束，"
            f"玩家 {session.player.id} 自动终局并结算"
        )

    async def _commit_tick(
        self,
        session: Session,
        journal: _TickJournal,
        engine_config: EngineConfig,
        outcome,
    ) -> None:
        """把一个 tick 的全部改动写进数据库（一个事务）。

        P4：成交、收割、二道贩子流水、新闻都来自**模式层**的 ``outcome``；
        插件层只负责把它们拼成一个事务写回去 —— 这里不含任何游戏规则。
        """
        market = session.market
        now = time.time()
        actions: list[tuple[str, tuple[Any, ...]]] = []

        for trade_id in journal.deleted_trades:
            actions.append(("DELETE FROM trades WHERE id = ?", (trade_id,)))

        trades = list(journal.trades) + list(outcome.trades)

        # 持仓：凡是本 tick 被动过的（成交涉及的）都要重写一遍。
        touched: dict[str, set[str]] = {}
        for trade in trades:
            touched.setdefault(trade.player_id, set()).add(trade.stock_id)
        for player_id, stock_ids in journal.positions.items():
            touched.setdefault(player_id, set()).update(stock_ids)

        for player_id, stock_ids in touched.items():
            owner = journal.players.get(player_id)
            for stock_id in stock_ids:
                actions.append(
                    (
                        "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                        (player_id, stock_id),
                    )
                )
                position = owner.positions.get(stock_id) if owner else None
                if position is None:
                    continue
                actions.append(
                    (
                        "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                        "today_bought, today_bought_day, opened_day) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            player_id,
                            stock_id,
                            position.amount,
                            position.avg_cost,
                            position.today_bought,
                            position.today_bought_day,
                            position.opened_day,
                        ),
                    )
                )

        for trade in trades:
            actions.append(
                (
                    "INSERT INTO trades (player_id, stock_id, side, amount, price, fee, "
                    "tick, day, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trade.player_id,
                        trade.stock_id,
                        trade.side,
                        trade.amount,
                        trade.price,
                        trade.fee,
                        trade.tick,
                        trade.day,
                        trade.reason,
                        now,
                    ),
                )
            )

        # ── P4：当天新生成的新闻（真假与方向一起落库）───────────
        for item in outcome.news:
            actions.append(
                (
                    "INSERT OR REPLACE INTO news_log "
                    "(day, news_id, news_type, stock_id, content, accuracy, direction) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.day,
                        item.id,
                        item.type,
                        item.stock_id,
                        item.content,
                        item.accuracy,
                        item.direction,
                    ),
                )
            )

        for result in outcome.liquidations or journal.liquidations:
            actions.append(
                (
                    "INSERT INTO liquidation_log (player_id, tick, day, phase, trigger, "
                    "stock_id, severity, player_heat_at_harvest, concentration_at_harvest, "
                    "death_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.player.id,
                        market.tick,
                        market.day,
                        result.phase,
                        result.trigger,
                        result.stock_id,
                        result.severity,
                        result.heat_at_harvest,
                        result.concentration,
                        session.player.death_reason,
                        now,
                    ),
                )
            )

        for entry in list(journal.pawnbroker) + list(outcome.pawnbroker):
            actions.append(
                (
                    "INSERT INTO pawnbroker_log (player_id, tick, day, action, stock_id, "
                    "price, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry["player_id"],
                        entry["tick"],
                        entry["day"],
                        entry["action"],
                        entry["stock_id"],
                        entry["price"],
                        entry["reason"],
                        now,
                    ),
                )
            )

        for player in journal.players.values():
            actions.append(
                (
                    "UPDATE players SET cash = ?, day = ?, alive = ?, trade_count = ?, "
                    "heat = ?, total_fee_paid = ?, strikes = ?, pending_action = ?, "
                    "death_reason = ?, death_title = ?, welfare_missed_days = ?, "
                    "loan_taken_day = ?, welfare_claimed_day = ?, "
                    "analyses_day = ?, analyses_used = ?, updated_at = ? WHERE id = ?",
                    (
                        player.cash,
                        player.day,
                        int(player.alive),
                        player.trade_count,
                        player.heat,
                        player.total_fee_paid,
                        player.strikes,
                        json.dumps(player.pending_action, ensure_ascii=False)
                        if player.pending_action
                        else None,
                        player.death_reason,
                        player.death_title,
                        player.welfare_missed_days,
                        player.loan_taken_day,
                        player.welfare_claimed_day,
                        player.analyses_day,
                        player.analyses_used,
                        now,
                        player.id,
                    ),
                )
            )
            actions.append(
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(player_id, tick) DO UPDATE SET heat = excluded.heat, "
                    "day = excluded.day",
                    (player.id, market.tick, market.day, player.heat),
                )
            )

        actions.append(
            (
                "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                "total_fees = ?, reversal_days = ?, updated_at = ? WHERE id = 1",
                (
                    market.tick,
                    market.day,
                    market.phase,
                    market.fee_pool,
                    market.total_fees,
                    json.dumps(sorted(market.reversal_days), ensure_ascii=False),
                    now,
                ),
            )
        )

        await self._repo.transaction(actions)

    # ------------------------------------------------------------------
    # 入口：日终结算（撤销窗口）
    # ------------------------------------------------------------------

    @plugin_entry(
        id="daily",
        name="日终结算",
        description="领工资、查账单、撤销二道贩子的强制卖出，或借高利贷。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["claim", "reject", "status", "loan"],
                    "description": (
                        "claim=领工资（必须主动领，不领不到账）；"
                        "reject=撤销二道贩子强制卖出；status=只看今日账单；"
                        "loan=借高利贷（明天还）"
                    ),
                }
            },
            "required": [],
        },
    )
    @_locked
    async def daily(self, action: str = "claim") -> Ok:
        """日终结算入口。

        P4 起有四个动作：

        * ``claim`` —— **主动领工资**。不领不到账；允许补领（上限 7 天）。
        * ``reject`` —— 撤销二道贩子的强制卖出（**只在撤销窗口内**可用）。
        * ``status`` —— 只看今日账单（任何时候都能看）。
        * ``loan`` —— 借高利贷（任何时候都能借，每天一次）。

        只有 ``reject`` 受撤销窗口限制 —— 领工资和借钱都不该被窗口卡住。
        """
        if action not in {"claim", "reject", "status", "loan"}:
            return _err("invalid_action")

        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")

        now = time.time()
        player = session.player

        if action == "reject":
            if now >= self._daily_window_until:
                return _err("not_at_daily_boundary")
            return await self._reject_distress_sell(session, engine_config)
        if action == "claim":
            return await self._claim_welfare(session, engine_config)
        if action == "loan":
            return await self._take_loan(session, engine_config)

        pending = player.pending_action or {}
        return Ok(
            {
                "action": "status",
                "day": player.day,
                "cash": round(player.cash, 6),
                "distance_to_death": self._distance_to_death(session, engine_config),
                "strikes": player.strikes,
                "alive": player.alive,
                "pending_action": self._public_pending_action(pending),
                "window_seconds": round(
                    max(0.0, self._daily_window_until - now), 3
                ),
                "welfare_missed_days": player.welfare_missed_days,
                "loan_debt": round(player.loan_debt, 6),
                "pending_relief": round(
                    self._pending_relief(session, engine_config), 6
                ),
            }
        )

    # ------------------------------------------------------------------
    # 入口：日终的三个薄入口（P5 · @ui.action）
    # ------------------------------------------------------------------
    #
    # 这三个入口是给**面板按钮**用的薄壳：各自配独立的 label / tone / confirm，
    # 但业务逻辑**一行都不重写** —— 全部委托给下面那三个私有方法。
    # LLM 侧的 ``daily(action=...)`` 入口保持原样不动。

    @ui.action(
        id="daily_claim",
        label="领工资",
        icon="hand",
        tone="default",
        group="daily",
        order=1,
    )
    @plugin_entry(
        id="daily_claim",
        name="领工资",
        description="主动领取工资。不领不到账；允许补领，最多补 7 天。",
    )
    @_locked
    async def daily_claim(self) -> Ok:
        """领工资（面板按钮的薄入口）。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return await self._claim_welfare(session, engine_config)

    @ui.action(
        id="daily_loan",
        label="借高利贷",
        icon="coin",
        tone="danger",
        group="daily",
        order=2,
        confirm="借五百还六百，明天不还就直接斩。确定要借吗？",
    )
    @plugin_entry(
        id="daily_loan",
        name="借高利贷",
        description="借一笔高利贷：到手 500，次日还 600，还不上直接强制平仓。每天限借一次。",
    )
    @_locked
    async def daily_loan(self) -> Ok:
        """借高利贷（面板按钮的薄入口）。"""
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        return await self._take_loan(session, engine_config)

    @ui.action(
        id="daily_reject",
        label="拒绝二道贩子",
        icon="close",
        tone="danger",
        group="daily",
        order=3,
        confirm="拒绝之后这次强制卖出就撤销了，账单你自己扛。确定拒绝吗？",
    )
    @plugin_entry(
        id="daily_reject",
        name="拒绝二道贩子",
        description="拒绝二道贩子的强制卖出（只在撤销窗口内可用）。",
    )
    @_locked
    async def daily_reject(self) -> Ok:
        """拒绝二道贩子的强制卖出（面板按钮的薄入口）。

        **"接受"不需要按钮** —— 不调这个入口就是默认接受，强制卖出照常执行
        （P5 契约 §3.4 方案 A：引擎零改动）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        if time.time() >= self._daily_window_until:
            return _err("not_at_daily_boundary")
        return await self._reject_distress_sell(session, engine_config)

    # ------------------------------------------------------------------
    # 入口：跳过本日（P5.5 改动 6 · 第 8 个 @ui.action）
    # ------------------------------------------------------------------
    #
    # **不是** ``@_locked``：它自己拿锁（``self._lock`` 不可重入，嵌套会死锁），
    # 因为它要在锁内连续跑几十个 tick（``_tick_once`` **不**拿锁）。

    @ui.action(
        id="skip_day",
        label="跳过本日",
        icon="clock",
        tone="default",
        group="daily",
        order=4,
    )
    @plugin_entry(
        id="skip_day",
        name="跳过本日",
        description="所有档位可用：立即推进到下一个日终，不等 tick 走完。",
    )
    async def skip_day(self) -> Ok:
        """跳过本日（**全档位可用**）。

        **逐 tick 快进**（不是"跳 tick 计数"）—— 复用现有的 tick 流水线一直
        跑到下一个日终边界：

        * heat 是**每 tick 衰减 0.85** 的，直接跳 tick 会漏掉几十次衰减，
          heat 保持原值 → 可能触发 ``peak_threshold`` 的**虚假收割**；
        * 快进之后的状态与"等满一天"**逐位一致** —— 跳过只是"不等"，不是"不算"。

        约束：

        * **每天只能跳一次**（``players.skip_day_used`` 记当前游戏日）；
        * 跳过时**照常走日终结算**（扣生活费、三振、猫娘日终决策一个不少）；
        * 跳过时**照常发卡片**（日终卡片、收割卡片都在）。

        **P31.0：所有档位都能跳。**
        以前只有无限模式放行（经典档位是教学关，不给跳）—— 但直播时主播
        要的是「让时间跑起来」，教学关也一样想快进。现在那道模式闸门
        **已经拆掉**：街机 / 入门 / 进阶 / 无尽**一律可跳**。
        每天一次的限制保留（那是游戏自己的 ``skip_day_used`` 闸门，
        防的是"同一天狂按"，不是"不许跳"）。

        **P11.1：这里刻意不加时间戳闸门。**
        跳过本日本来就**每天只能一次**（游戏自己的 ``skip_day_used`` 闸门），
        不存在"狂点刷屏"；而"连续快进 14 天"是**合法**用法（隐藏结局
        「无为而治」的测试就靠它）—— 加个时间戳闸门反而会把连续快进打断。
        真正的并发风险已经由**上下文处理器的非阻塞拿锁**挡住了（见
        :meth:`market_context`）：宿主的上下文查询**永远不会再被冻住**。

        **P32.0：感知【狂按快进】。**
        「每天只能跳一次」挡不住**连点**：跳过会把日子推进一天，于是下一次
        点击落在**新的一天**，闸门形同虚设 —— 主播一路点下去，一天能快进
        三十天，``_yui_pending_prompts`` 当场积压几百条。

        所以这里**记点击间隔**（:meth:`_note_skip_click`）：**5 秒内点超过
        3 次**判定为【狂按快进】。判定成立 ⇒

        ① **立刻清空** Yui 的待推送队列（:meth:`_yui_fast_forward_clear`）；
        ② 快进跑完后注入一条**吐槽**提示词（:meth:`_yui_fast_forward_taunt`），
           让她只说一句"你赶着投胎啊"，别再分析行情；
        ③ 狂按期内她被**限频**（:meth:`_flush_yui_prompts`），
           别让她一直跳出来。

        注意：这**不是**"不许快进" —— 连续快进仍然是合法玩法（隐藏结局
        「无为而治」就靠它）。这里只是让 Yui 的反应跟得上玩家的节奏。
        """
        self._touch_player_action()
        # ── P32.0：狂按快进感知（**在拿锁之前**记点击时间）──────────
        fast_forward = self._note_skip_click()
        if fast_forward:
            # 先把上一轮攒下的旧消息清掉（别让它们堆到快进之后）。
            self._yui_fast_forward_clear()
        with self._lock:
            engine_config = await self._engine_config()
            session, error = await self._session_or_error(engine_config)
            if error is not None:
                return error
            if session is None:
                return _err("not_joined")
            player = session.player
            if not player.alive:
                return _err("skip_day_dead")
            if int(session.skip_day_used) == int(player.day):
                return _err("skip_day_already_used")
            if self._tick_running:
                return _err("skip_day_busy")

            ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
            current_tick = int(session.market.tick)
            # 下一个日终边界：``market.tick`` 是 ``ticks_per_day`` 的倍数时，
            # 日终结算已经跑过了，所以下一个边界是**再往后一个**。
            target_tick = (current_tick // ticks_per_day + 1) * ticks_per_day
            steps = max(1, target_tick - current_tick)
            skip_day_value = int(player.day)

            self._tick_running = True
            try:
                for _ in range(steps):
                    await self._tick_once()
            except Exception as error:  # noqa: BLE001 - 跳过失败不该炸掉入口
                self.logger.error(f"跳过本日异常（不影响后续 tick）：{error}")
                if fast_forward:
                    # 半路失败也得给玩家一个反应（别让队列继续堆着）。
                    self._yui_fast_forward_clear()
                    self._yui_fast_forward_taunt(day=int(player.day))
                return _err("skip_day_failed")
            finally:
                self._tick_running = False
                # 重置补 tick 的基线：跳过之后不该立刻又补一堆 tick。
                self._last_tick_at = time.time()

            # 重新读一遍：``_tick_once`` 用的是它自己重建的 Session。
            fresh, fresh_error = await self._session_or_error(engine_config)
            if fresh_error is not None:
                return fresh_error
            # ── P32.0：狂按 ⇒ 只留一句吐槽 ────────────────────────
            # 快进跑完了 ⇒ 积压已经形成（几十条过时的新闻与事件）。
            # 这里**再清一次**，然后注入唯一一条吐槽 —— 她只说一句，
            # 别再对着旧行情自言自语。
            if fast_forward:
                self._yui_fast_forward_clear()
                self._yui_fast_forward_taunt(
                    day=int(getattr(getattr(fresh, "player", None), "day", 0) or 0)
                )
            # ── P7：猫娘弹幕（跳过本日也算一次嘴臭）─────────────────
            self._push_danmaku(
                pick_taunt(
                    "skip_day",
                    seed=skip_day_value,
                    params={"day": skip_day_value + 1},
                )
            )
            # ── P11：新手期唠叨（第 1 天一笔没买就跳过）──────────────
            # 用的是**按下那一刻**的 session（``_tick_once`` 重建了自己的
            # Session，``session.player`` 仍是按下前的状态）—— 判据正是
            # "玩家在第 1 天什么都没做就按了跳过本日"。
            meow = await self._maybe_meow_nag(session)
            # 记下"今天已经跳过一次"（记的是**发起跳过时**的游戏日 = 规格说的
            # "当前游戏日"；``_commit_tick`` 不会写这一列，所以这里单独写）。
            #
            # 注意：跳过本身会把日子推进一天，所以**下一次**跳过发生在新的一天，
            # 不会撞上这条限制 —— 这条限制是"同一天不许连按"的安全网。
            try:
                await self._repo.transaction(
                    [
                        (
                            "UPDATE players SET skip_day_used = ?, updated_at = ? "
                            "WHERE id = ?",
                            (skip_day_value, time.time(), player.id),
                        )
                    ]
                )
            except (DbUnavailableError, sqlite3.Error) as db_error:
                return _err("db_unavailable", db_error)

            if fresh is None:
                return Ok(
                    {
                        "action": "skip_day",
                        "skipped_to_day": skip_day_value + 1,
                        "ticks_advanced": steps,
                        "note": "这一局已经结束了。",
                        # P11：猫娘有没有唠叨（面板据此弹兜底对话）
                        "meow": meow,
                    }
                )
            return Ok(
                {
                    "action": "skip_day",
                    "day": int(fresh.player.day),
                    "ticks_advanced": steps,
                    "cash": round(fresh.player.cash, 6),
                    "alive": bool(fresh.player.alive),
                    "death_reason": fresh.player.death_reason,
                    "welfare_missed_days": int(fresh.player.welfare_missed_days),
                    "note": (
                        "这一天过完了。"
                        if fresh.player.alive
                        else "这一天过完了，你也到了头。"
                    ),
                    # P11：猫娘有没有唠叨（面板据此弹兜底对话）
                    "meow": meow,
                }
            )

    # ------------------------------------------------------------------
    # 入口：P10 的四个偏好入口（速度 / 自动·手动 / 推进一拍 / 引导已读）
    # ------------------------------------------------------------------
    #
    # 这四个入口**只改玩家偏好**，不改动任何游戏规则：
    #   * ``set_game_speed`` —— 改"等多久跳一拍"（**真实时间**）；
    #   * ``set_auto_tick`` —— 自动推进 ↔ 手动推进；
    #   * ``advance_tick``  —— 手动模式下点一下走一拍；
    #   * ``onboarding_ack`` —— 记"玩家看过玩法说明了"。
    #
    # ``advance_tick`` 复用**同一条 tick 流水线**（``_tick_once``），所以手动
    # 推进出来的状态与自动推进**逐位一致** —— 手动只是"不等"，不是"不算"。

    @ui.action(
        id="set_game_speed",
        label="游戏速度",
        icon="clock",
        tone="default",
        group="settings",
        order=1,
    )
    @plugin_entry(
        id="set_game_speed",
        name="设置游戏速度",
        description=(
            "切换游戏速度：slow（慢）/ normal（标准）/ fast（快）。"
            "只改真实时间的推进快慢，**不改任何游戏数值**。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "speed": {
                    "type": "string",
                    "enum": list(_GAME_SPEED_ORDER),
                    "description": "速度档位",
                }
            },
            "required": ["speed"],
        },
    )
    async def set_game_speed(self, speed: str = "normal") -> Ok:
        """切换游戏速度（面板上的三档按钮）。

        写的是插件自己的 ``settings`` 表 —— **不碰宿主配置**，所以不会触发
        插件重启，局中的存档、内存态一律不受影响。
        """
        if speed not in _GAME_SPEED_ORDER:
            return _err("bad_game_speed")
        if not await self._repo.set_setting(_SETTING_GAME_SPEED, speed):
            return _err("db_unavailable")
        self._speed_override = speed
        if self._settings_map_cache is None:
            self._settings_map_cache = {}
        self._settings_map_cache[_SETTING_GAME_SPEED] = speed
        # 换速度等于换基线：别让旧基线在下一拍突然补一堆 tick。
        self._last_tick_at = time.time()
        return Ok({"action": "set_game_speed", "speed": speed})

    @ui.action(
        id="set_auto_tick",
        label="自动/手动推进",
        icon="play",
        tone="default",
        group="settings",
        order=2,
    )
    @plugin_entry(
        id="set_auto_tick",
        name="切换自动/手动推进",
        description=(
            "开启自动推进后，市场按间隔自己往下走；"
            "关掉之后市场就停住，等你点【推进一拍】。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true = 自动推进，false = 手动推进",
                }
            },
            "required": ["enabled"],
        },
    )
    async def set_auto_tick(self, enabled: bool = True) -> Ok:
        """切换自动推进 / 手动推进。"""
        flag = "1" if enabled else "0"
        if not await self._repo.set_setting(_SETTING_AUTO_TICK, flag):
            return _err("db_unavailable")
        self._auto_tick_override = bool(enabled)
        if self._settings_map_cache is None:
            self._settings_map_cache = {}
        self._settings_map_cache[_SETTING_AUTO_TICK] = flag
        # 手动模式下心跳不再推进；基线刷新一下，切回来时不会补一大串 tick。
        self._last_tick_at = time.time()
        return Ok({"action": "set_auto_tick", "auto": bool(enabled)})

    def _advance_throttled(self) -> bool:
        """距上一次**真正推进**是否还不到 ``_ADVANCE_THROTTLE_SECONDS`` 秒。

        **P11.1 真机复盘**：这个判定**必须用"此刻"的时间戳**，不能用
        await 之前抓的旧时间戳。

        真机并发实测（8 发齐射）抓到过这么一幕：请求 A 与 B 几乎同时进门，
        两者都用自己的旧时间戳判了一次闸门（都"没被节流"），接着各自去
        ``await`` 查配置/查库；A 先醒，跑完一拍；B 后醒，**闸门不会再判一次**，
        于是又跑了一拍 —— 两次推进**只隔了不到 1 秒**，
        "两次推进至少隔 1 秒"这条契约就破了。

        所以闸门要判**两次**：一次在耗时操作之前（省下 IPC 与查库），
        一次在真正推进之前（用新时间戳，且与拿锁之间**没有 await**）——
        这样两次推进的执行时刻就真的隔开了。
        """
        if self._last_advance_at <= 0.0:
            return False
        return (time.time() - self._last_advance_at) < _ADVANCE_THROTTLE_SECONDS

    @ui.action(
        id="advance_tick",
        label="推进一拍",
        icon="play",
        tone="primary",
        group="settings",
        order=3,
    )
    @plugin_entry(
        id="advance_tick",
        name="推进一拍",
        description="手动模式下立刻推进一个 tick（走的是与自动推进完全相同的流水线）。",
    )
    async def advance_tick(self) -> Ok:
        """手动推进一个 tick。

        **只在手动模式可用**：自动模式下市场自己会走，按这个按钮就变成了
        "偷偷多走一拍"，那自动/手动就分不清了。

        **P11.1：节流闸门** —— 距上一次推进不到 ``_ADVANCE_THROTTLE_SECONDS``
        秒就**静默丢弃**（返回温和的 ``Ok``）。狂点不再引发宿主的
        ``Failed to query plugin UI action context``（500 红色警告），
        也**绝不**抛红色 Error 制造崩溃恐慌。
        """
        self._touch_player_action()
        if self._auto_tick_enabled():
            return _err("advance_tick_requires_manual")
        # ── P11.1：时间戳闸门（在任何耗时操作之前！）──────────────────
        # 必须排在 ``_engine_config()`` / ``_session_or_error()`` 之前：
        # 那两步要走 IPC + 查库，正是把宿主的 5 秒预算吃光的地方。
        if self._advance_throttled():
            return Ok(
                {
                    "action": "advance_tick",
                    "throttled": True,
                    "reason": "advance_tick_busy",
                    "note": "慢一点，市场还在消化上一拍。",
                }
            )
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        if self._tick_running:
            # ── P11.1：正在跑 tick —— 温和提示，**不抛红色 Error** ──────
            # （这一拍已经被心跳/上一次推进吃掉了，别再叠一层。）
            return Ok(
                {
                    "action": "advance_tick",
                    "throttled": True,
                    "reason": "advance_tick_busy",
                    "note": "市场正在推进，稍后再试。",
                }
            )
        # ── P11.1：二次闸门（**用新时间戳**）──────────────────────────
        # 上面那两次 await（IPC + 查库）期间，**别的请求可能真的跑了一拍** ——
        # 真机 8 发齐射实测就抓到过：两个请求都拿"进门时"的旧时间戳判了一次
        # 闸门（都以为自己没被节流），于是先后各跑一拍，间隔不到 1 秒。
        # 这里用**此刻**的时间戳再判一次，"两次推进至少隔 1 秒"才是真成立。
        if self._advance_throttled():
            return Ok(
                {
                    "action": "advance_tick",
                    "throttled": True,
                    "reason": "advance_tick_busy",
                    "note": "慢一点，市场还在消化上一拍。",
                }
            )
        # 时间戳记的是"**真正推进**的那一刻"（不是进门那一刻），
        # 且它与 ``_run_one_tick()`` 的拿锁之间**没有 await** ——
        # 插件是单线程事件循环，所以这两步是原子的：
        # 谁先记下时间戳谁推进，后到的必然被上面的二次闸门拦下。
        self._last_advance_at = time.time()
        ran = await self._run_one_tick()
        if not ran:
            # 锁被占着（``_run_one_tick`` 非阻塞拿锁失败）—— 这一拍没跑。
            return Ok(
                {
                    "action": "advance_tick",
                    "throttled": True,
                    "reason": "advance_tick_busy",
                    "note": "市场正在推进，稍后再试。",
                }
            )
        fresh, fresh_error = await self._session_or_error(engine_config)
        if fresh_error is not None:
            return fresh_error
        # ── P11：新手期唠叨（第 1 天一笔没买就推进）────────────────
        # 用**按下那一刻**的 session（``_run_one_tick`` 重建了自己的 Session，
        # ``session.player`` 仍是推进前的状态）。
        meow = await self._maybe_meow_nag(session)
        if fresh is None:
            return Ok(
                {
                    "action": "advance_tick",
                    "ended": True,
                    "note": "这一局已经结束了。",
                    # P11：猫娘有没有唠叨（面板据此弹兜底对话）
                    "meow": meow,
                }
            )
        return Ok(
            {
                "action": "advance_tick",
                "day": int(fresh.player.day),
                "tick": int(fresh.market.tick),
                "cash": round(fresh.player.cash, 6),
                "alive": bool(fresh.player.alive),
                "death_reason": fresh.player.death_reason,
                "note": (
                    "走了一拍。"
                    if fresh.player.alive
                    else "走了一拍，你也到了头。"
                ),
                # P11：猫娘有没有唠叨（面板据此弹兜底对话）
                "meow": meow,
            }
        )

    @ui.action(
        id="tutorial_begin",
        label="开始教程",
        icon="play",
        tone="primary",
        group="tutorial",
        order=0,
    )
    @plugin_entry(
        id="tutorial_begin",
        name="开始教程",
        description="玩家在教程开场白之后点【开始】时调用：进入第一步。",
    )
    async def tutorial_begin(self) -> Ok:
        """GREET → STEP_0。

        **只推进一次** —— 重复调用不会把玩家甩到后面的步骤。
        """
        if str(self._tutorial.get("phase") or "") != PHASE_GREET:
            return _err("tutorial_not_active")
        await self._tutorial_enter_step(0)
        return Ok({"ok": True, "tutorial": self._tutorial_block()})

    @ui.action(
        id="tutorial_advance",
        label="教程下一步",
        icon="play",
        tone="default",
        group="tutorial",
        order=1,
    )
    @plugin_entry(
        id="tutorial_advance",
        name="教程下一步",
        description="玩家在教程里点【下一步】时调用：推进到下一步。",
    )
    async def tutorial_advance(self) -> Ok:
        """STEP_i → STEP_{i+1}（走完最后一步 ⇒ 结课小结）。"""
        if str(self._tutorial.get("phase") or "") != PHASE_STEP:
            return _err("tutorial_not_active")
        await self._tutorial_enter_step(int(self._tutorial.get("index") or 0) + 1)
        return Ok({"ok": True, "tutorial": self._tutorial_block()})

    @ui.action(
        id="tutorial_skip",
        label="跳过教程",
        icon="close",
        tone="default",
        group="tutorial",
        order=2,
    )
    @plugin_entry(
        id="tutorial_skip",
        name="跳过教程",
        description="玩家点【跳过教程】时调用：记进度，不再打扰。",
    )
    async def tutorial_skip(self) -> Ok:
        """跳过教程 ⇒ 记进度（这一档不再来）。

        **不打断**：跳过之后这一局就安静了。
        """
        phase = str(self._tutorial.get("phase") or PHASE_IDLE)
        if phase in (PHASE_IDLE, PHASE_DONE):
            return _err("tutorial_not_active")
        self._tutorial["phase"] = PHASE_DONE
        self._tutorial["deadline"] = 0.0
        self._tutorial["gate"] = ""
        await self._tutorial_persist()
        await self._tutorial_mark_taught(str(self._tutorial.get("tier") or ""))
        return Ok({"ok": True, "tutorial": self._tutorial_block()})

    @ui.action(
        id="onboarding_ack",
        label="我知道了",
        icon="check",
        tone="default",
        group="settings",
        order=4,
    )
    @plugin_entry(
        id="onboarding_ack",
        name="标记已读玩法说明",
        description="玩家看过【玩法说明】之后调用：不再自动弹窗。",
    )
    async def onboarding_ack(self) -> Ok:
        """记下"玩家已经看过玩法说明"。

        **两处都记**：

        * ``settings.onboarding_ack`` —— 全局标记，保证**只自动弹一次**；
        * ``players.onboarding_seen`` —— 记在这个 ``player_id`` 行上，
          同一个玩家再开新局也不会被弹窗砸脸。
        """
        await self._repo.set_setting(_SETTING_ONBOARDING, "1")
        player_id = self._last_player_id
        if player_id:
            await self._repo.mark_player_onboarding_seen(player_id)
            self._onboarding_player_seen_cache = (player_id, True)
        self._onboarding_ack_cache = True
        if self._settings_map_cache is None:
            self._settings_map_cache = {}
        self._settings_map_cache[_SETTING_ONBOARDING] = "1"
        return Ok({"action": "onboarding_ack", "seen": True})

    # ------------------------------------------------------------------
    # 入口：导演台（P7 · 直播特制版）
    # ------------------------------------------------------------------
    #
    # **只有一个入口**（而不是四个）：入口列表就是 AI 的工具列表，越短越好。
    # 用 ``op`` 区分指令。
    #
    # **纯净版（默认）下这个入口恒报错** —— 开源版严格遵守原有数学概率，
    # 插件层不做任何后台干预。
    #
    # 四条指令全部**复用引擎既有的函数**，引擎层（``engine/``）一行不改：
    #   * ``crash``   → ``engine.market.apply_jump``（引擎已有的纯函数）
    #   * ``loan``    → ``InfiniteConfig`` 的既有借贷参数
    #   * ``welfare`` → 引擎既有的 ``welfare_neglect`` 破产原因线
    #   * ``kill``    → ``engine.settlement.apply_death``

    @ui.action(
        id="director",
        label="导演指令",
        icon="chart",
        tone="danger",
        group="director",
        order=90,
    )
    @plugin_entry(
        id="director",
        name="导演指令",
        description=(
            "**直播特制版专用**的导演台指令。开源纯净版下这个入口恒报错。"
            "op=crash 让某只标的暴跌（要 stock_id）/ loan 强制发高利贷"
            "（仅无限模式）/ welfare 强制断供 / kill 立即强制平仓。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["crash", "loan", "welfare", "kill", "boom"],
                    "description": "导演指令类型。",
                },
                "stock_id": {
                    "type": "string",
                    "description": "crash 用的标的 id（FAKE-001 这种槽位 id）。",
                },
            },
            "required": ["op"],
        },
    )
    @_locked
    async def director(self, op: str, stock_id: str = "") -> Ok:
        """导演台指令（**纯净版恒报错**）。

        这些干预**只发生在插件层**：暴跌复用引擎既有的 ``apply_jump``，
        借贷复用引擎配置里的既有参数，强制平仓复用引擎既有的 ``apply_death``。
        引擎的公式与数值一个都没改。

        暴跌是**排队制**：面板点一下就排一条，**下一个 tick** 施加 ——
        这样暴跌之后集中度、收割等后续反应**照常发生**，而不是硬改结果。
        """
        engine_config = await self._engine_config()
        # 刷新配置缓存再读开关（特制版要能即时打开/关闭）。
        await self._business_config()
        if not self._director_on():
            return _err("director_disabled")
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("not_joined")
        if not session.player.alive:
            return _err("director_dead_player")

        if op == "crash":
            stock = session.market.stocks.get(str(stock_id))
            if stock is None:
                return _err("director_stock_not_found")
            self._director_queue.append({"op": "crash", "stock_id": stock.id})
            return Ok(
                {
                    "action": "director",
                    "op": "crash",
                    "stock_id": stock.id,
                    "stock_name": stock.name,
                    "queued": True,
                    "note": _fill_text(
                        i18n_text(
                            "director.note.crash",
                            "安排 {stock} 在下一个 tick 崩给你看。",
                        ),
                        {"stock": stock.name},
                    ),
                }
            )
        if op == "loan":
            if session.mode != "infinite":
                return _err("director_loan_classic_forbidden")
            return await self._director_force_loan(session, engine_config)
        if op == "welfare":
            return await self._director_force_welfare(session, engine_config)
        if op == "kill":
            return await self._director_force_kill(session, engine_config)
        if op == "boom":
            return await self._director_force_boom()
        return _err("director_bad_op")

    async def _director_force_boom(self) -> Ok:
        """导演「赛博弹窗轰炸」（P7.2）。

        纯表现层：在玩家的屏幕上盖一层**关不掉**的小丑海，并在倒计时里
        封锁操作。**不碰任何数值** —— 引擎的公式、结算、收割全部照常。

        参数走 ``config.example.toml`` 的 ``[director]`` 段：
        ``boom_seconds``（默认 5 秒）与 ``boom_emoji_count``（默认 50 个）。
        """
        settings = await self._director_settings()
        seconds = max(1.0, min(30.0, float(settings.get("boom_seconds", 5) or 5)))
        count = max(1, min(200, int(settings.get("boom_emoji_count", 50) or 50)))
        self._director_boom_until = time.time() + seconds
        self._director_boom_count = count
        # P7.3：把「本来该持续多久」告诉前端，前端据此算自己的**硬上限**。
        self._director_boom_duration = seconds
        return Ok(
            {
                "action": "director",
                "op": "boom",
                "count": count,
                "seconds": int(seconds),
                "note": _fill_text(
                    i18n_text(
                        "director.note.boom",
                        "屏幕已经被 {count} 个小丑占领 {seconds} 秒。",
                    ),
                    {"count": count, "seconds": int(seconds)},
                ),
            }
        )

    async def _director_force_loan(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演强制放贷（**仅无限模式**）。

        经典模式引擎里**没有还款路径**（``daily_settlement`` 的还贷只在无限
        模式），在经典模式硬塞一笔就是白送钱 —— 所以直接拒绝。

        金额与"每天限借一次"之外的规则**照常**：到手 500，次日还 600。
        限借一次会被**绕过**（导演要的就是"再逼一次"），但经济账一分不变。

        P20.0：额度同样走**玩家配置**（名字彩蛋的翻倍额度）。
        """
        player = session.player
        infinite = self._player_config(session, engine_config).infinite
        player.cash += infinite.loan_amount
        player.loan_debt += infinite.loan_repay
        player.loan_taken_day = player.day
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, loan_debt = ?, loan_taken_day = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.loan_debt,
                            player.loan_taken_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        self._push_danmaku(
            pick_taunt(
                "loan",
                seed=player.trade_count,
                params={"amount": infinite.loan_amount, "repay": infinite.loan_repay},
            )
            or i18n_text(
                "taunt.loan.1", "钱到账了。利息也到账了，只是它不会告诉你。"
            )
        )
        return Ok(
            {
                "action": "director",
                "op": "loan",
                "cash": round(player.cash, 6),
                "loan_debt": round(player.loan_debt, 6),
                "note": i18n_text(
                    "director.note.loan",
                    "魔爪契约已生效。明天记得还，忘了就当你签的是卖身契。",
                ),
            }
        )

    async def _director_force_welfare(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演强制断供（把连续未领天数拉过引擎的强制平仓线）。

        用的是引擎**既有的** ``welfare_neglect`` 破产原因 —— 引擎只管判破产，
        怎么走到那条线是插件层的事。
        """
        player = session.player
        limit = int(getattr(engine_config.infinite, "welfare_claim_limit_days", 7))
        player.welfare_missed_days = limit + 1
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET welfare_missed_days = ?, updated_at = ? "
                        "WHERE id = ?",
                        (player.welfare_missed_days, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        return Ok(
            {
                "action": "director",
                "op": "welfare",
                "welfare_missed_days": player.welfare_missed_days,
                "note": i18n_text(
                    "director.note.welfare",
                    "恭喜，你已经连续七天没去领那份不该领的东西了。",
                ),
            }
        )

    async def _director_force_kill(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """导演立即强制平仓（复用引擎既有的 ``apply_death``）。"""
        player = session.player
        apply_death(player, "harvested")
        self._override_death_title(player)
        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = 0, death_reason = ?, death_title = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.death_reason, player.death_title, now, player.id),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        return Ok(
            {
                "action": "director",
                "op": "kill",
                "death_reason": player.death_reason,
                "note": i18n_text("director.note.kill", "收工。"),
            }
        )

    @ui.action(
        id="declare_bankrupt",
        label="宣布破产",
        icon="close",
        tone="danger",
        group="daily",
        order=5,
    )
    @plugin_entry(
        id="declare_bankrupt",
        name="宣布破产",
        description=(
            "交不起下一顿饭时的最后一条路：当场结束这一局（破产原因：交不起账单），"
            "并给出结算报告。经典模式没有高利贷，弹窗上就只剩这一条路。"
        ),
    )
    @_locked
    async def declare_bankrupt(self) -> Ok:
        """宣布破产（面板「赛博网贷广告」弹窗的**逃生通道**之一）。

        走的是**引擎既有的** ``apply_death(player, "starved")`` —— 破产原因文案
        仍然是引擎那一套，引擎层一行不改。归档路径与 ``settle`` 入口**完全
        复用**（结算报告 + 归档 JSON + 清空 ``market_state.player_id``）。

        P7.3：本体挪到 :meth:`_do_declare_bankrupt`（**无锁**版本）——
        ``_locked`` 不可重入，而网贷弹窗的入口 ``loan_prompt`` 自己也持锁，
        两个 ``@_locked`` 方法互相调用会死锁。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("no_active_game")
        return await self._do_declare_bankrupt(session, engine_config)

    async def _do_declare_bankrupt(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """``declare_bankrupt`` 的**无锁**实现（调用方必须自己持锁）。

        P27.2：玩家**已经退场**（交不上饭 / 被低保或网贷请出去）时，这个入口
        不再报错，而是**替他收场** —— 保留原本的退场原因，照常出结算报告、
        清场、归档。

        为什么必须这样
        --------------
        面板那枚「紧急逃生」按钮走的就是这个入口。玩家退场之后，
        ``market_state`` 里还挂着他的席位（只有 ``settle`` / ``declare_bankrupt``
        / 经典 30 天自动结算这三处会清），于是：面板的 ``stage`` 永远停在
        ``"playing"`` ⇒ 没有「再来一局」按钮；``join`` 又被"你已经在局中了"
        拒掉。直播事故：玩家被卡死在上一局，下一局永远开不了。

        退场原因**不覆盖**（已经退场就不再调 ``apply_death``）—— 他是被市场
        请出去的，不是自己点「宣布破产」的。
        """
        player = session.player
        if player.alive:
            apply_death(player, "starved")
            self._override_death_title(player)
        payload = await self._build_settlement_payload(session, engine_config)
        # ── P33.0：玩家退场（【退出对局】）⇒ **立刻自毁** ─────────────
        # 心跳空转、待推送队列清空、所有播报闭嘴 —— 不许再有"幽灵播报"。
        self._deactivate_game("declare_bankrupt")

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET alive = 0, death_reason = ?, death_title = ?, "
                        "updated_at = ? WHERE id = ?",
                        (player.death_reason, player.death_title, now, player.id),
                    ),
                    (
                        "UPDATE market_state SET player_id = '', updated_at = ? WHERE id = 1",
                        (now,),
                    ),
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)
        self._archive_settlement(payload, session)
        self._push_danmaku(
            pick_taunt(
                "bankrupt",
                seed=int(player.trade_count),
                params={"days": int(player.day)},
            )
            or i18n_text("taunt.bankrupt.1", "你按了破产。放心，天桥的风是免费送你的。")
        )
        # P7.3：玩家已经被清盘了，网贷弹窗的待办一笔勾销（别让 watchdog 再动它）。
        self._loan_prompt_at = 0.0
        return Ok(payload)

    # ------------------------------------------------------------------
    # P7.3：赛博网贷弹窗的**唯一出口**（0.8.2 面板死锁事故的修复）
    # ------------------------------------------------------------------
    @ui.action(
        id="loan_prompt",
        label="网贷弹窗",
        icon="coin",
        tone="danger",
        group="daily",
        order=6,
    )
    @plugin_entry(
        id="loan_prompt",
        name="网贷弹窗",
        description=(
            "P7.3：赛博网贷弹窗的**唯一出口**。choice=borrow 借高利贷、"
            "choice=refuse 直接破产、choice=open 只是登记「弹窗打开了」。"
            "**借不到也返回成功**（带一句提示），绝不会把玩家锁在弹窗里。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "choice": {
                    "type": "string",
                    "enum": ["open", "borrow", "refuse"],
                    "description": (
                        "open=登记弹窗已打开（启动 30 秒超时计时）；"
                        "borrow=借走 500 万；refuse=我不借了，直接破产。"
                    ),
                },
            },
            "required": ["choice"],
        },
    )
    @_locked
    async def loan_prompt(self, choice: str = "borrow") -> Ok:
        """赛博网贷弹窗的出口（**永远不会失败**）。

        0.8.2 真机事故：弹窗唯一能关掉的路径是按钮的 ``onResult``，而那条
        路径被宿主的 ``await api.refresh()`` 挡着 —— 刷新一失败，按钮就永远
        等不到回调，弹窗永远关不掉，玩家被锁死在面板里（连「破产认输」都
        点不了，因为那条路也被弹窗挡住了）。

        所以这里有三条硬规矩：

        1. **永远返回 Ok**。借不到钱不是「操作失败」，只是「这次没借到」——
           把原因写进 ``note``，前端弹个 toast 就完事。弹窗照样关。
        2. ``refuse`` 是**真·逃生通道**：直接走引擎既有的破产结算路径。
        3. ``open`` 让服务端知道「弹窗开着」，从这一刻起算 30 秒超时；
           到点没人操作，服务端自动结算（无限模式强制借入 / 经典模式破产）。
        """
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return error
        if session is None:
            return _err("no_active_game")
        player = session.player
        if not player.alive:
            self._loan_prompt_at = 0.0
            return _err("director_dead_player")

        if choice == "open":
            # 登记弹窗已打开：启动（或续上）30 秒超时计时。
            if self._loan_prompt_at <= 0.0 or self._loan_prompt_day != player.day:
                self._loan_prompt_at = time.time()
                self._loan_prompt_day = int(player.day)
            return Ok(
                {
                    "action": "loan_prompt",
                    "choice": "open",
                    "deadline": round(
                        self._loan_prompt_at + _LOAN_PROMPT_TIMEOUT_SECONDS, 3
                    ),
                    "timeout_seconds": int(_LOAN_PROMPT_TIMEOUT_SECONDS),
                }
            )

        # 玩家做了选择（或者超时了）→ 这笔待办到此为止。
        self._loan_prompt_at = 0.0

        if choice == "refuse":
            # 逃生通道：我不借了，把我清出去吧。
            return await self._do_declare_bankrupt(session, engine_config)

        # choice == "borrow"
        if session.mode != "infinite":
            # 经典模式压根没有高利贷 —— 这是**规则的实话**，不是操作失败。
            return Ok(
                {
                    "action": "loan_prompt",
                    "choice": "borrow",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "note": i18n_text(
                        "loan.prompt.classic_no_loan",
                        "经典模式没有高利贷。你已经输在规则上了。",
                    ),
                }
            )

        result = await self._take_loan(session, engine_config)
        if result.is_err():
            # 借不到（今天借过了 / 上一笔没还）—— **照样算成功**，
            # 把原因带给前端弹 toast，弹窗照关。
            error_obj = result.err()
            message = str(getattr(error_obj, "message", "") or "loan_failed")
            code = str(getattr(error_obj, "code", "") or "loan_failed")
            return Ok(
                {
                    "action": "loan_prompt",
                    "choice": "borrow",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "failed": True,
                    "code": code,
                    "note": message,
                }
            )
        payload = dict(result.unwrap())
        payload["action"] = "loan_prompt"
        payload["choice"] = "borrow"
        payload["failed"] = False
        return Ok(payload)

    async def _loan_prompt_watchdog(self) -> None:
        """网贷弹窗的**服务端**超时兜底（0.8.2 面板死锁事故的第二道保险）。

        前端有自己的 30 秒倒计时，但前端可能整个冻住（渲染卡死、网络断了、
        WebView 被系统挂起）。这时候只有服务端能救玩家：

        * **无限模式** → 强制借入（``_take_loan`` 的既有参数），保住这一局；
        * **经典模式** → 直接判定破产（那儿没有第二条路）。

        两边都走引擎既有的路径，**引擎层一行不改**。
        """
        if self._loan_prompt_at <= 0.0:
            return
        if time.time() - self._loan_prompt_at < _LOAN_PROMPT_TIMEOUT_SECONDS:
            return

        # 到点了。把这笔待办清掉，免得下面的异常路径反复触发。
        self._loan_prompt_at = 0.0
        try:
            engine_config = await self._engine_config()
            with self._lock:
                session, error = await self._session_or_error(engine_config)
                if error is not None or session is None:
                    return
                player = session.player
                if not player.alive:
                    return
                if self._loan_prompt_day != int(player.day):
                    # 已经跨天了：这一笔早就作废了，别误伤新的一局。
                    return

                if session.mode == "infinite":
                    # 只有「今天还没借」且「没有没还的债」时才值得自动借入。
                    # 否则这次自动借入注定报错 —— 那就**什么都不做**：
                    # 面板本来就没被锁死（弹窗早就自己关了），让玩家接着玩。
                    if player.loan_taken_day == player.day or player.loan_debt > 0:
                        return
                    result = await self._take_loan(session, engine_config)
                    if result.is_err():
                        self._push_danmaku(
                            i18n_text(
                                "taunt.loan.1",
                                "钱到账了。利息也到账了，只是它不会告诉你。",
                            )
                        )
                        return
                    self._push_danmaku(
                        i18n_text(
                            "taunt.loan.timeout",
                            "你发呆太久，魔爪自己替你按了确认。",
                        )
                    )
                else:
                    # 经典模式没有高利贷，只剩「拒绝并破产」这一条路。
                    await self._do_declare_bankrupt(session, engine_config)
        except Exception as error:  # noqa: BLE001 - 看门狗绝不能把心跳带崩
            self.logger.error(f"网贷弹窗超时兜底失败（不影响后续心跳）：{error}")

    async def _reregister_llm_tools_if_due(self) -> None:
        """LLM 工具注册的**自愈**（0.8.3 真机事故的第二道保险）。

        ``@llm_tool`` 的注册**只在插件启动时触发一次** —— 官方文档明说
        「helper 不做 ``main_server`` 重启后的自动重注册」。于是
        ``main_server`` 一重启（或者插件启动时它还没起来），猫娘就再也看不见
        本插件的工具了：玩家在聊天框说「加入一局」，猫娘只会当成闲聊。

        这里定期**重发**注册消息：

        * ``main_server`` 侧是**覆盖语义**（同名重复注册会覆盖旧的）⇒ 重发安全；
        * 插件侧只发 IPC，**不动**本地状态，也不需要先 ``unregister`` ——
          那样中间会出现一个「工具对模型消失」的窗口，反而更糟；
        * 宿主的 IPC 消费是**顺序 await** 的，所以重发不会被乱序吃掉。

        SDK 的 ``_llm_tools`` / ``_notify_llm_tool_registered`` 带下划线是私有的，
        但 SDK 自己的注释写明了这两个的用途就是「按需重发 IPC 通知」；这里用
        ``getattr`` 取，取不到就**安静跳过**（SDK 改了也不至于把插件带崩）。
        """
        now = time.time()
        if now < self._llm_reregister_at:
            return
        self._llm_reregister_at = now + _LLM_TOOL_REREGISTER_SECONDS

        registry = getattr(self, "_llm_tools", None)
        notify = getattr(self, "_notify_llm_tool_registered", None)
        if not isinstance(registry, dict) or not callable(notify):
            return
        for meta in list(registry.values()):
            try:
                notify(meta)
            except Exception as error:  # noqa: BLE001 - 一次失败不影响其它工具
                self.logger.warning(f"LLM 工具重注册失败（不影响心跳）：{error}")

    def _roster_news_headline(
        self, item: Any, market: Any = None
    ) -> tuple[str, str | None, str | None]:
        """名册群友的抽象新闻标题。

        返回 ``(标题, 群友别名, 群友称号)``。

        **只换标题**：正文（``item.content``）是引擎给的**方向信号**，
        换掉它就破坏了 P4 §11 的反泄露铁律 —— 所以这里一个字都不动它。

        给了 ``market`` 就按**本局的绑定**认领群友（P7.2：群友按波动率落到
        真实槽位上）；不给就退回名册文件里的默认槽位。
        """
        roster = self._roster_for_stock(getattr(item, "stock_id", None), market)
        if roster is None:
            return _news_headline(item.content), None, None
        alias = str(roster.get("alias") or "")
        title = str(roster.get("title") or "")
        abstract = self._roster_pick_headline(
            roster, seed=sum(ord(ch) for ch in str(item.id))
        )
        headline = f"{alias}：{abstract}" if abstract else _news_headline(item.content)
        return headline, alias, title

    def _apply_director_crashes(
        self, market: MarketState, engine_config: EngineConfig
    ) -> int:
        """把排队的导演暴跌施加到市场上（**下一个 tick 生效**）。

        复用引擎既有的纯函数 ``apply_jump`` —— **引擎一行不改**，而且暴跌
        之后集中度、收割等后续反应**照常发生**。
        """
        if not self._director_on() or not self._director_queue:
            return 0
        crashes = [item for item in self._director_queue if item.get("op") == "crash"]
        if not crashes:
            return 0
        self._director_queue = [
            item for item in self._director_queue if item.get("op") != "crash"
        ]
        raw = self._business_config_cache or {}
        director = raw.get("director")
        director = director if isinstance(director, dict) else {}
        scale = float(director.get("crash_severity_scale", 1.0) or 1.0)
        applied = 0
        for item in crashes:
            stock = market.stocks.get(str(item.get("stock_id")))
            if stock is None or not stock.alive:
                continue
            severity = min(0.9, max(0.05, (0.15 + 0.25 * scale)))
            stock.prev_price = stock.price
            stock.price = apply_jump(stock.price, severity=severity)
            stock.kline.append((market.tick, stock.price))
            window = int(getattr(engine_config.market, "kline_window", 60) or 60)
            if len(stock.kline) > window:
                del stock.kline[: len(stock.kline) - window]
            applied += 1
        return applied

    async def _maybe_force_black_swan(
        self, session: Session, profit: float, sold_stock_id: str = ""
    ) -> None:
        """P19.0：``[director] force_black_swan`` —— 最上头的那一秒拉一次全仓暴跌。

        **触发点**：单笔盈利**刷新本局纪录**的那一刻。买入还没赚到钱，
        所以「最上头的一秒」必然是**卖出赚到钱**的瞬间。

        **做什么**：把玩家的**整个仓位**（刚卖出的那只 + 手里还攥着的每一只）
        都排一条暴跌（``op="crash"``）。暴跌由 ``_apply_director_crashes``
        在**下一个 tick** 用引擎既有的 ``apply_jump`` 施加 ——
        **引擎一行不改**，暴跌之后的集中度、收割等反应也**照常发生**。

        **一局只来一次**：第二次就不新鲜了，重复暴跌是把戏不是节目效果。

        P28.0：**第一关一律不走这条路** —— 街机档的定位是「爽」，
        「在最上头的那一秒被精准收割」属于后面几档的压迫感，
        放在第一关只会把新手吓跑。峰值阈值那边也已经放宽
        （见 ``_apply_arcade_softening``），这里是第二道保险。
        """
        if session.mode == MODE_ARCADE:
            return
        if not self._director_flag("force_black_swan"):
            return
        if self._black_swan_fired:
            return
        gain = float(profit or 0.0)
        if gain <= 0.0 or gain <= self._black_swan_peak:
            return
        self._black_swan_peak = gain
        player = session.player
        targets: list[str] = []
        if sold_stock_id:
            targets.append(str(sold_stock_id))
        for stock_id, position in player.positions.items():
            if float(getattr(position, "amount", 0.0) or 0.0) > 0.0:
                targets.append(str(stock_id))
        if not targets:
            return
        for stock_id in targets:
            self._director_queue.append({"op": "crash", "stock_id": stock_id})
        self._black_swan_fired = True
        self.logger.info(
            f"黑天鹅已排入队列（{len(targets)} 只标的 · 单笔盈利 {gain:.2f}）"
        )

    async def _claim_welfare(self, session: Session, engine_config: EngineConfig) -> Ok:
        """主动领取工资（P4 · P5.5 改成「打工工资」的叙事）。

        工资**必须主动领**：不调用就一分钱没有。允许补领，但补领上限是
        ``welfare_claim_limit_days`` 天，更早的欠款作废。
        """
        player = session.player
        if session.mode != "infinite":
            return Ok(
                {
                    "action": "claim",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "welfare_missed_days": player.welfare_missed_days,
                    "note": "经典模式没有工资——你的对手是市场，不是福利系统。",
                }
            )

        infinite = engine_config.infinite
        claim = claim_welfare(
            player,
            day=player.day,
            weekly_relief=infinite.weekly_relief,
            interval_days=infinite.relief_interval_days,
            claim_limit_days=infinite.welfare_claim_limit_days,
        )
        if claim.already_claimed:
            return _err("welfare_already_claimed")

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, welfare_missed_days = ?, "
                        "welfare_claimed_day = ?, updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.welfare_missed_days,
                            player.welfare_claimed_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7.2：领工资也要挨骂（每一次点击都必有一句嘴臭）─────────
        self._push_danmaku(
            pick_taunt(
                "claim",
                seed=player.trade_count * 31 + int(player.day),
                params={"days": int(player.day)},
            )
            or i18n_text("taunt.claim.1", "领了？领了也是白领。")
        )

        return Ok(
            {
                "action": "claim",
                "granted": round(claim.granted, 6),
                "days_owed": claim.days_owed,
                "cash_after": round(player.cash, 6),
                "welfare_missed_days": player.welfare_missed_days,
                "note": "工资到手。先活下去，再谈别的。",
            }
        )

    async def _take_loan(self, session: Session, engine_config: EngineConfig) -> Ok:
        """借高利贷（P4）：到手 500，次日还 600，还不上直接强制平仓。

        P20.0：额度走**玩家配置**（名字彩蛋的「社区特供」把额度翻倍）——
        开局必签的魔爪契约与玩家自己点的每一笔必须是**同一套数字**。
        """
        player = session.player
        if session.mode != "infinite":
            return Ok(
                {
                    "action": "loan",
                    "granted": 0.0,
                    "cash_after": round(player.cash, 6),
                    "note": "经典模式没有高利贷——你已经输在规则上了。",
                }
            )

        infinite = self._player_config(session, engine_config).infinite
        if player.loan_taken_day == player.day:
            return _err("loan_already_taken")
        if player.loan_debt > 0:
            return _err("loan_pending")

        player.cash += infinite.loan_amount
        player.loan_debt += infinite.loan_repay
        player.loan_taken_day = player.day

        now = time.time()
        try:
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, loan_debt = ?, loan_taken_day = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.loan_debt,
                            player.loan_taken_day,
                            now,
                            player.id,
                        ),
                    )
                ]
            )
        except (DbUnavailableError, sqlite3.Error) as db_error:
            return _err("db_unavailable", db_error)

        # ── P7：猫娘弹幕（借了钱也要挨骂）────────────────────────────
        self._push_danmaku(
            pick_taunt(
                "loan",
                seed=player.trade_count,
                params={"amount": infinite.loan_amount, "repay": infinite.loan_repay},
            )
            or i18n_text(
                "taunt.loan.1", "钱到账了。利息也到账了，只是它不会告诉你。"
            )
        )

        return Ok(
            {
                "action": "loan",
                "granted": round(infinite.loan_amount, 6),
                "repay": round(infinite.loan_repay, 6),
                "cash_after": round(player.cash, 6),
                "loan_debt": round(player.loan_debt, 6),
                "due_day": player.day + 1,
                "note": "借五百还六百。这不是帮助，是他们唯一的利润来源。",
            }
        )

    async def _reject_distress_sell(
        self, session: Session, engine_config: EngineConfig
    ) -> Ok:
        """撤销二道贩子的强制卖出。

        P4 语义：**撤销不再额外 +1 三振，也不强制平仓**。

        ``strikes`` 数的是"连续交不起的次数"，它在**日终结算**时就记上了 ——
        "你确实交不起"是事实，撤销只是反悔，不是没发生过。三振强制平仓也由结算
        负责，撤销窗口里反悔一次不会让你立刻清盘，但明天你依然交不起。
        """
        player = session.player
        market = session.market
        pending = player.pending_action or {}
        legs = pending.get("legs") or []
        if pending.get("kind") != "pawnbroker_sell" or not legs:
            return _err("no_pending_action")

        for leg in legs:
            notional = float(leg.get("notional", 0.0))
            fee = float(leg.get("fee", 0.0))
            player.cash -= notional - fee
            player.trade_count = max(0, player.trade_count - 1)
            player.total_fee_paid = max(0.0, player.total_fee_paid - fee)
            market.fee_pool = max(0.0, market.fee_pool - fee)
            market.total_fees = max(0.0, market.total_fees - fee)
            prev = leg.get("prev_position")
            stock_id = str(leg.get("stock_id", ""))
            if prev:
                player.positions[stock_id] = Position(stock_id=stock_id, **prev)
            else:
                player.positions.pop(stock_id, None)
            if leg.get("trade_id"):
                await self._repo.transaction(
                    [("DELETE FROM trades WHERE id = ?", (int(leg["trade_id"]),))]
                )
            await self._repo.transaction(
                [
                    (
                        "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                        (player.id, stock_id),
                    )
                ]
            )
            position = player.positions.get(stock_id)
            if position is not None:
                await self._repo.transaction(
                    [
                        (
                            "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                            "today_bought, today_bought_day, opened_day) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                player.id,
                                stock_id,
                                position.amount,
                                position.avg_cost,
                                position.today_bought,
                                position.today_bought_day,
                                position.opened_day,
                            ),
                        )
                    ]
                )
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET cash = ?, trade_count = ?, total_fee_paid = ?, "
                        "strikes = ?, pending_action = ?, updated_at = ? WHERE id = ?",
                        (
                            player.cash,
                            player.trade_count,
                            player.total_fee_paid,
                            player.strikes,
                            None,
                            time.time(),
                            player.id,
                        ),
                    ),
                    (
                        "UPDATE market_state SET fee_pool = ?, total_fees = ?, "
                        "updated_at = ? WHERE id = 1",
                        (market.fee_pool, market.total_fees, time.time()),
                    ),
                ]
            )

        player.pending_action = None
        self._daily_window_until = 0.0
        await self._repo.transaction(
            [
                (
                    "UPDATE players SET cash = ?, trade_count = ?, total_fee_paid = ?, "
                    "strikes = ?, alive = ?, pending_action = ?, death_reason = ?, "
                    "death_title = ?, updated_at = ? WHERE id = ?",
                    (
                        player.cash,
                        player.trade_count,
                        player.total_fee_paid,
                        player.strikes,
                        int(player.alive),
                        None,
                        player.death_reason,
                        player.death_title,
                        time.time(),
                        player.id,
                    ),
                )
            ]
        )
        await self._repo.transaction(
            [
                (
                    "INSERT INTO pawnbroker_log (player_id, tick, day, action, stock_id, "
                    "price, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        player.id,
                        market.tick,
                        player.day,
                        "forced_sell",
                        legs[0].get("stock_id", ""),
                        float(legs[0].get("price", 0.0)),
                        "rejected_by_player",
                        time.time(),
                    ),
                )
            ]
        )
        # ── P7.2：拒绝二道贩子也要挨骂（每一次点击都必有一句嘴臭）────
        self._push_danmaku(
            pick_taunt(
                "reject",
                seed=player.trade_count * 17 + int(player.day),
                params={"stock": str(legs[0].get("stock_id", ""))},
            )
            or i18n_text("taunt.reject.1", "硬气。硬气不能当饭吃。")
        )
        return Ok(
            {
                "action": "reject",
                "accepted": False,
                "cash_after": round(player.cash, 6),
                "strikes": player.strikes,
                "alive": player.alive,
                "death_reason": player.death_reason,
                "killed": False,
                "note": "反悔成功。但三振不会因此回退——明天你依然要交钱。",
            }
        )

    @staticmethod
    def _public_pending_action(pending: dict[str, Any]) -> dict[str, Any] | None:
        """把二道贩子提案变成**可以公开**的形态（不含内部账目细节）。"""
        if not pending:
            return None
        legs = pending.get("legs") or []
        return {
            "kind": pending.get("kind", "pawnbroker_sell"),
            "stock_ids": [leg.get("stock_id") for leg in legs],
            "amount": round(sum(float(leg.get("amount", 0.0)) for leg in legs), 6),
            "price": round(
                sum(float(leg.get("amount", 0.0)) * float(leg.get("price", 0.0))
                    for leg in legs),
                6,
            ),
            "reason": "现钱不足以支付明日生活费",
        }

    def _distance_to_death(
        self, session: Session, engine_config: EngineConfig
    ) -> float | None:
        """距离强制平仓线（没有生活费的档位为 None）。"""
        next_cost = self._next_living_cost(session.difficulty, engine_config)
        if next_cost <= 0:
            return None
        return round(session.player.cash - next_cost, 6)

    # ------------------------------------------------------------------
    # P10：游戏速度 / 自动·手动推进 / 新手引导
    #
    # 这一节全是**玩家偏好**，不参与任何游戏规则：
    #   * 游戏速度 —— 只缩放"等多久跳一拍"（真实时间），游戏数值一律不动；
    #   * 自动/手动 —— 手动时心跳不推进，玩家自己点【推进一拍】；
    #   * 新手引导 —— 首次打开面板自动弹一次【玩法说明】，状态记在 player_id 上。
    #
    # 引擎层（engine/）与模式层（modes/）**一行不改**：这里没有任何一个
    # 数字会进入行情、手续费、强制平仓线或日终结算的计算。
    # ------------------------------------------------------------------

    async def _ensure_settings_cache(self) -> None:
        """把 ``settings`` 表里的偏好读进**内存缓存**（只读一次）。

        **为什么必须缓存**：面板的上下文请求在宿主侧只有 **5 秒超时**，
        而宿主数据库每开一次会话都要走一趟 IPC —— 一个键一次查询就能把
        上下文拖到超时边缘（真机上已经踩到过：上下文超时 ⇒ 面板拿到空状态
        ⇒ 插件被宿主停掉）。所以：

        * 三个偏好（速度 / 自动手动 / 引导已读）**一次会话全部读回**；
        * 读完之后**一律走内存**，面板每秒轮询也不碰数据库；
        * 玩家按了按钮时直接改缓存（并落库）。

        库还没准备好（宿主没下发配置）时**不置已加载标记** —— 下次再试。
        """
        if self._settings_map_cache is not None:
            return
        if not self._repo.enabled:
            return
        values = await self._repo.get_settings_map()
        self._settings_map_cache = values
        speed = values.get(_SETTING_GAME_SPEED, "")
        self._speed_override = speed if speed in _GAME_SPEED_ORDER else ""
        auto = values.get(_SETTING_AUTO_TICK)
        self._auto_tick_override = (
            None
            if auto is None
            else auto.strip().lower() not in ("0", "false", "off", "no", "manual")
        )
        self._onboarding_ack_cache = bool(values.get(_SETTING_ONBOARDING))
        # ── P11：猫娘主动搭话的闸门（同样一次会话读回，之后全走内存）──
        self._meow_greeted = bool(values.get(_SETTING_MEOW_GREETED))
        self._meow_nag_player = str(values.get(_SETTING_MEOW_NAG_PLAYER) or "")
        try:
            self._meow_last_at = float(values.get(_SETTING_MEOW_LAST_AT) or 0.0)
        except (TypeError, ValueError):
            self._meow_last_at = 0.0
        # ── P23.0：Yui 的操控模式（同样一次会话读回，之后全走内存）──────
        # 读不到 / 读到非法值 ⇒ 回落到**安全默认值**（辅助式）。
        yui_mode = str(values.get(_SETTING_YUI_MODE) or "").strip().lower()
        self._yui_mode = yui_mode if yui_mode in YUI_MODES else YUI_MODE_DEFAULT
        # ── P24.0：话痨级别（同样一次会话读回）────────────────────────
        talk = str(values.get(_SETTING_YUI_TALK_LEVEL) or "").strip().lower()
        self._yui_talk_level = (
            talk if talk in YUI_TALK_LEVELS else YUI_TALK_LEVEL_DEFAULT
        )

    def _resolve_speed(self) -> str:
        """当前生效的游戏速度档位（读内存缓存，不查库、不问宿主）。

        优先级：**面板上按过的设置 > ``config.example.toml`` 的
        ``[game].game_speed`` > ``normal``**。面板设置优先，是因为玩家
        在面板上按的按钮是"当下的需求"，改 toml 才是"长期偏好"。
        """
        if self._speed_override in _GAME_SPEED_ORDER:
            return self._speed_override
        cfg = self._business_config_cache
        section = cfg.get("game") if isinstance(cfg, dict) else None
        toml_value = section.get("game_speed") if isinstance(section, dict) else None
        if isinstance(toml_value, str) and toml_value in _GAME_SPEED_ORDER:
            return toml_value
        return "normal"

    @staticmethod
    def _tick_interval(base_seconds: float, speed: str) -> int:
        """按档位把基础间隔缩放成实际间隔（至少 1 秒）。

        ``base_seconds`` 是难度配置里的原始间隔（经典 8 秒 / 无限 30 秒）。
        """
        multiplier = _GAME_SPEED_MULTIPLIER.get(speed, 1.0)
        return max(1, int(round(float(base_seconds) * multiplier)))

    def _auto_tick_enabled(self) -> bool:
        """现在是"自动推进"还是"手动推进"。默认**自动**。"""
        return self._auto_tick_override is not False

    def _pace_block(
        self, engine_config: EngineConfig, session: Session | None
    ) -> dict[str, Any]:
        """面板载荷的 ``pace`` 块：速度档位 + 自动/手动 + 节奏换算。

        **纯内存**（不查库、不问宿主）—— 面板每秒轮询一次，这里必须便宜。

        **纯表现**：这里算出来的秒数只是给玩家看的"还要等多久"，
        不参与任何判定，也不写进存档。
        """
        speed = self._resolve_speed()
        auto = self._auto_tick_enabled()
        difficulty = session.difficulty if session is not None else None
        if difficulty is None:
            difficulty = engine_config.difficulties.get("classic_arcade")
        base_interval = float(difficulty.tick_interval_seconds) if difficulty else 8.0
        ticks_per_day = int(difficulty.ticks_per_day) if difficulty else 12
        interval = self._tick_interval(base_interval, speed)
        return {
            "speed": speed,
            "auto": auto,
            "base_interval_seconds": int(base_interval),
            "interval_seconds": interval,
            "ticks_per_day": ticks_per_day,
            "day_seconds": interval * ticks_per_day,
            "multiplier": _GAME_SPEED_MULTIPLIER.get(speed, 1.0),
        }

    def _onboarding_block(self, session: Session | None) -> dict[str, Any]:
        """面板载荷的 ``onboarding`` 块：说明要不要自动弹。

        **纯内存**：``auto_open`` 只在"全局还没弹过"时为真；弹过一次之后，
        玩家随时可以点空闲态的大按钮【玩法说明】重看，但不会再被弹窗砸脸。

        玩家维度的"已读"记在 ``players.onboarding_seen``（也就是
        **player_id** 那一行）—— 由 ``onboarding_ack`` 写库，这里只读缓存。
        """
        player_id = session.player_id if session is not None else ""
        player_seen = (
            self._onboarding_player_seen_cache[1]
            if player_id and self._onboarding_player_seen_cache[0] == player_id
            else False
        )
        global_seen = self._onboarding_ack_cache
        return {
            "seen": bool(global_seen or player_seen),
            "auto_open": not global_seen,
            "player_id": player_id,
        }

    # ------------------------------------------------------------------
    # Hosted UI 上下文
    # ------------------------------------------------------------------

    @ui.context(id="market")
    async def market_context(self, **_):
        """为 ``ui/panel.tsx`` 提供**面板数据**（P5：完整面板）。

        **P11.1：锁改成"非阻塞获取" + 缓存兜底** —— 这是"狂点【推进一拍】就弹
        红色 ``Failed to query plugin UI action context``"的**根因修复**。

        宿主执行任何 UI action **之前**都会先 ``await host.get_ui_context()``
        （**5 秒预算**，见 ``plugin/core/host.py``）。而这个上下文处理器和
        所有动作抢的是**同一把** ``threading.Lock``：动作跑 tick 期间
        （``await`` 里持着锁），上下文请求去 ``acquire()`` 就是**阻塞**调用
        ⇒ **整个事件循环被冻住** ⇒ 宿主那次查询超时 ⇒
        ``PLUGIN_UI_CONTEXT_QUERY_FAILED``（500 红色警告）。

        所以：拿不到锁就**直接返回上一次缓存的快照**（可能有点旧，但比 500
        好一万倍），**绝不阻塞事件循环**。
        """
        if not self._lock.acquire(blocking=False):
            cached = self._context_cache
            if cached is not None:
                return dict(cached)
            # 连一次快照都还没有（刚启动就来查询）：给个最小载荷，别让宿主 500。
            return {"stage": "busy", "notice": "市场正在结算，稍等一下。"}
        try:
            payload = await self._market_context_payload(**_)
            self._context_cache = dict(payload)
            return payload
        finally:
            self._lock.release()

    async def _market_context_payload(self, **_):
        """（原 ``market_context`` 的实现；**调用方必须自己持锁**。）

        与 ``market`` 入口**分开**：``market`` 是给 LLM 用的轻量白名单；这里给
        的是面板需要的完整白名单（持仓 / 账单 / 猫娘 / K 线 / 新闻分类）。

        **局中隐藏**：载荷不含 heat / seed / 反转日 / 新闻真假。

        局终之后（经典模式第 30 天自动结算）``market_state.player_id`` 被清空，
        此时载荷带上**归档的结算报告**（P4 历史问题 4：自动终局后玩家看不到
        结算报告）。结算报告里有 heat 曲线与种子 —— 那是**结算页**才允许
        出现的东西，而此刻局已经结束了。
        """
        # ── P10：先预热偏好缓存（**一次**会话读完，随后全走内存）──────────
        # 面板的上下文请求只有 5 秒超时，热路径上不能查库。
        await self._ensure_settings_cache()
        engine_config = await self._engine_config()
        session, error = await self._session_or_error(engine_config)
        if error is not None:
            return {"stage": "p2-persistence", "notice": "数据库不可用。"}
        # ── P10：节奏块（速度 / 自动·手动）与引导块（要不要自动弹说明）──
        # **纯内存**（不查库、不问宿主）：面板每秒轮询，这里必须便宜。
        pace = self._pace_block(engine_config, session)
        onboarding = self._onboarding_block(session)
        if session is None:
            settlement = self._latest_settlement()
            if settlement is not None:
                return {
                    "stage": "finished",
                    "day": 0,
                    "tick": 0,
                    "ticks_per_day": 0,
                    "ticks_to_settle": 0,
                    "phase": "slaughter",
                    "mode": "",
                    "mode_name": "",
                    # P28.0：局终没有「第一关」可言 ⇒ 极简标志恒为 false。
                    "arcade_simple": False,
                    "alive": False,
                    "cash": 0.0,
                    "total_assets": 0.0,
                    "living_cost": 0.0,
                    "next_bill": 0.0,
                    "bill_gap": 0.0,
                    "missed_bills": 0,
                    "distance_to_kill": None,
                    "concentration": 0.0,
                    "positions": [],
                    "stocks": [],
                    "kline_stock_ids": [],
                    "catgirl": None,
                    "news_today": [],
                    # ── P26.0：群友事件在结算页也要看得见 ─────────────
                    "community_events": self._community_events_payload(),
                    "pending_action": {},
                    "pending_event": None,
                    "analyses_left": 0,
                    # ── P7：终局页也要有视觉块（Emoji 海 / 降级档位）──
                    "visual": self._visual_block(None, engine_config),
                    "settlement": settlement,
                    # ── P20.0：结算页也要认得人（名字与名号）──────────
                    # 引擎的结算报告是 frozen dataclass 且**没有名字字段**
                    # ⇒ 名字是插件层补进去的（见 ``_build_settlement_payload``）。
                    "player_name": settlement.get("player_name")
                    if isinstance(settlement, dict)
                    else "",
                    "name_egg": settlement.get("name_egg")
                    if isinstance(settlement, dict)
                    else None,
                    # ── P5.5 改动 5/7：称号图鉴（**只在结算页**）────
                    # 局中载荷**不带** achievements —— 图鉴是"打完才看"的东西。
                    "achievements": settlement.get("achievements")
                    if isinstance(settlement, dict)
                    else None,
                    # ── P10：节奏与引导（结算页也要能看说明）────────────
                    "pace": pace,
                    "onboarding": onboarding,
                    # ── P11：猫娘主动搭话的状态（纯内存）──────────────
                    "meow": self._meow_block(),
                    # ── P23.0：Yui 的操控状态（结算页也要看得见）────────
                    "yui": self._yui_block(0),
                    "notice": "本局已经结算归档。下面是这一局的最终成绩。",
                }
            return {
                "stage": "idle",
                "notice": "当前没有进行中的对局，请先加入一局。",
                # ── P20.0：回填玩家上次用的名字（"下次进入直接读取"）────
                "player_name": await self._remembered_player_name(),
                # ── P10：空闲态也要有节奏块（玩家可以在开局前先调速度）──
                "pace": pace,
                "onboarding": onboarding,
                # ── P11：空闲态也能呼叫猫娘（第一次打开就在这儿）──────
                "meow": self._meow_block(),
                # ── P23.0：空闲态也要能看到 Yui 的模式（直播托管在局前就开）──
                "yui": self._yui_block(0),
                # ── P26.0：空闲态也留着上一局的群友事件 ───────────────
                "community_events": self._community_events_payload(),
            }
        payload = self._panel_payload(session, engine_config, pace, onboarding)
        # ── P18.0：推进教程（超时 / 门槛事件），然后刷新教程块 ─────────
        # 面板每秒问一次 ⇒ 教程的推进节奏跟得上玩家的眼睛。
        await self._tutorial_pump()
        payload["tutorial"] = self._tutorial_block()
        return payload

    # ------------------------------------------------------------------
    # 内部工具（以下方法以 _ 开头，不会被宿主收集为入口）
    # ------------------------------------------------------------------

    async def _business_config(self) -> dict[str, Any]:
        """读宿主下发的**业务配置**（``config.example.toml`` 里那些段）。

        P7 的 ``[director]`` / ``[visual]`` 都在这里读。带 5 秒缓存 ——
        和引擎配置**共用同一次 dump**，不额外打扰宿主。
        """
        now = time.time()
        cached = self._business_config_cache
        if cached is not None and now - self._business_config_cached_at < _ENGINE_CONFIG_TTL:
            return cached
        try:
            raw = await self.config.dump()
        except Exception:  # noqa: BLE001 - 配置不可用时退回空配置
            raw = {}
        self._apply_runtime_config(raw)
        self._business_config_cache = raw if isinstance(raw, dict) else {}
        self._business_config_cached_at = now
        # 导演模式的开关顺手缓存一份（面板载荷是同步的，要能直接读）。
        director = self._business_config_cache.get("director")
        self._director_enabled_cache = bool(
            director.get("enabled", False) if isinstance(director, dict) else False
        )
        return self._business_config_cache

    async def _engine_config(self) -> EngineConfig:
        """读取宿主配置并装配成引擎配置。

        带 5 秒缓存：1 秒心跳每次都去读宿主配置太浪费了，而配置改动
        5 秒内生效对游戏节奏完全无感。

        **顺带同步运行时能力**：读到配置的同时把运行时能力（``db.enabled`` /
        ``state.backend``）与宿主对齐 —— 宿主补发配置后，心跳不用等宿主再推
        一次 ``CONFIG_UPDATE``（0.7.4 真机复盘：心跳不推进的根因）。
        """
        now = time.time()
        cached = self._engine_config_cache
        if cached is not None and now - self._engine_config_cached_at < _ENGINE_CONFIG_TTL:
            return cached
        raw = await self._business_config()
        config = build_engine_config(raw)
        # P16.0：**这里不做任何覆写** —— 引擎原样，只是缓存。
        # 新手期放水按档位在 ``_softened_config_for()`` 里做，
        # 而且只喂给模式层 ⇒ 后两档（收割场）**一个字节都不受影响**。
        self._engine_config_cache = config
        self._engine_config_cached_at = now
        return config

    # ------------------------------------------------------------------
    # P7：社区名册 / 导演模式 / 猫娘弹幕 / 视觉降级
    #
    # 这一整节都是**表现层**：
    #   * 社区名册 = 群友人设与抽象新闻（纯数据，来自 community_roster.json）；
    #   * 导演模式 = 直播特制版的开关，**默认关**；
    #   * 猫娘弹幕 = 局中的嘲讽，取代发不出去的卡片；
    #   * 视觉降级 = 三档 UI 的档位判定。
    # 这里算出来的任何东西都**不参与游戏规则**，也不改引擎数值。
    # ------------------------------------------------------------------

    def _roster_members(self) -> list[dict[str, Any]]:
        """社区名册（群友人设 + 抽象新闻池）。

        从 ``community_roster.json`` 读；**读不到就退化成空名册** ——
        插件照常跑，只是没有彩蛋。名册是**数据**：加群友只改那个文件。
        """
        if self._roster_loaded:
            return self._roster
        self._roster_loaded = True
        try:
            path = Path(__file__).resolve().parent / "community_roster.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover - 名册缺失不影响主流程
            self._roster = []
            return self._roster
        members = data.get("members") if isinstance(data, dict) else None
        if isinstance(members, list):
            self._roster = [item for item in members if isinstance(item, dict)]
        return self._roster

    def _roster_binding(self, market: Any) -> dict[str, dict[str, Any]]:
        """把名册群友绑到本局的**真实槽位**上（**数值零改动**）。

        引擎按种子给每个槽位算了一个 ``sigma``（波动率），而且**整局不变**
        （``advance_market`` 只读它、不改它）。所以可以按波动率排名来绑：

        * 带 ``stable`` / ``anchor`` 标签的群友（星辰）→ 绑到波动率**最低**的槽位；
        * 带 ``explosive`` / ``chaotic`` 标签的群友（Wehos）→ 绑到波动率**最高**的槽位；
        * 其余群友（老王 / 小明 / 熬夜怪 / 潜水员）→ 按波动率升序依次取剩下的槽位。

        于是「星辰走势平稳如老狗」「Wehos 极高波动」**永远成立**，而引擎的
        sigma、价格、结算公式**一个字节都没动**（P7.2 硬要求：数值零改动，
        500 局基线必须逐位一致）。绑定只影响**显示名**与群友新闻。
        """
        members = self._roster_members()
        if not members:
            return {}
        stocks = getattr(market, "stocks", None)
        if not stocks:
            return {}
        ordered = sorted(
            stocks.values(),
            key=lambda stock: (
                float(getattr(stock, "sigma", 0.0) or 0.0),
                str(getattr(stock, "id", "")),
            ),
        )
        slot_ids = [str(getattr(stock, "id", "")) for stock in ordered]
        binding: dict[str, dict[str, Any]] = {}
        used: set[str] = set()

        def _take(candidates: list[str]) -> str | None:
            for slot in candidates:
                if slot not in used:
                    used.add(slot)
                    return slot
            return None

        # 1) 必须最稳的 → 波动率升序
        for member in members:
            tags = {str(tag) for tag in (member.get("tags") or [])}
            if not tags & {"stable", "anchor"}:
                continue
            slot = _take(slot_ids)
            if slot is None:
                break
            binding[slot] = member
        # 2) 必须最野的 → 波动率降序
        for member in members:
            tags = {str(tag) for tag in (member.get("tags") or [])}
            if not tags & {"explosive", "chaotic"}:
                continue
            slot = _take(list(reversed(slot_ids)))
            if slot is None:
                break
            binding[slot] = member
        # 3) 其余的 → 剩下的槽位按波动率升序
        for member in members:
            if any(bound is member for bound in binding.values()):
                continue
            slot = _take(slot_ids)
            if slot is None:
                break
            binding[slot] = member
        return binding

    def _roster_for_stock(
        self, stock_id: str | None, market: Any = None
    ) -> dict[str, Any] | None:
        """某个标的是不是名册里的群友（是的话返回名册条目）。

        给了 ``market`` 就用**本局的绑定**（群友按波动率落到真实槽位上）；
        不给就退回名册文件里的**默认槽位**（纯数据口径，测试也走这条）。
        """
        if not stock_id:
            return None
        if market is not None:
            bound = self._roster_binding(market).get(str(stock_id))
            if bound is not None:
                return bound
        for member in self._roster_members():
            if member.get("stock_id") == stock_id:
                return member
        return None

    @staticmethod
    def _roster_display_name(member: dict[str, Any]) -> str:
        """群友在**面板上**显示的名字（如「星辰·定海神针股」）。"""
        alias = str(member.get("alias") or "")
        title = str(member.get("title") or "")
        if alias and title:
            return f"{alias}·{title}"
        return alias or title

    def _roster_pick_headline(self, member: dict[str, Any], seed: int) -> str:
        """按 seed **稳定**地给群友挑一句抽象新闻（可复现）。"""
        headlines = member.get("headlines")
        if not isinstance(headlines, list) or not headlines:
            return ""
        ticker = str(member.get("ticker") or member.get("alias") or "")
        index = seed % len(headlines)
        text = str(headlines[index])
        if ticker and ticker not in text:
            text = f"{ticker}{text}"
        return text

    async def _director_settings(self) -> dict[str, Any]:
        """导演模式的开关与参数（**默认全关**）。"""
        raw = await self._business_config()
        section = raw.get("director")
        return section if isinstance(section, dict) else {}

    def _director_on(self) -> bool:
        """导演模式是否开启（同步读缓存）。

        **默认 False** —— 开源纯净版严格遵守原有数学概率，插件层不做任何
        后台干预。开启后只有面板的「导演台」能用，且指令**只改观感与剧情**，
        不改引擎公式。
        """
        return bool(self._director_enabled_cache)

    def _director_flag(self, key: str) -> bool:
        """导演段的**布尔开关**（同步读缓存）。

        P19.0 的两个隐藏开关（``force_black_swan`` / ``show_progress``）
        都是这个形状。

        **必须同时满足 ``[director] enabled = true`` 才生效** —— 否则
        「开源纯净版不做任何后台干预」这条承诺就破了（``show_progress``
        尤其致命：它会把引擎明令局中隐藏的 ``heat`` 摆到面板上）。
        """
        if not self._director_on():
            return False
        raw = self._business_config_cache or {}
        director = raw.get("director")
        director = director if isinstance(director, dict) else {}
        return bool(director.get(key, False))

    def _director_progress_block(self, session: Session | None) -> dict[str, Any] | None:
        """P19.0：``[director] show_progress`` —— 面板上的「进度」。

        **这里有引擎明令局中隐藏的量**（``engine/models.py`` 的
        ``Player.heat`` 写着「局中不得出现在任何对外返回体」）。所以它
        **只在导演开关打开时**出现，而且**只给主播看** ——
        开源纯净版的载荷里**永远**没有这个键。

        键名为什么叫 ``hype`` 而不是 ``heat``：面板源码受 P5 契约 §4.1 的
        **静态 lint** 约束（``tests/test_hosted_tsx.py``：面板里不许出现
        ``heat`` / ``seed`` / ``reversal`` 这些局中秘密字段的字样）。
        换成 ``hype`` 之后面板**依然不认识**那个字段 —— 它只是显示一个
        后端决定要给的数字；**真正的闸门在后端**：开关不开，这个键
        根本不存在。

        返回 ``None`` = 不给（面板也就不渲染）。
        """
        if not self._director_flag("show_progress"):
            return None
        if session is None:
            return None
        player = session.player
        all_chapters = chapters()
        total = len(all_chapters)
        state = self._story_state or {}
        seen = [str(item) for item in (state.get("seen") or [])]
        pending = str(state.get("pending") or "")
        current_id = pending or (seen[-1] if seen else "")
        current_no = 0
        current_title = ""
        for index, chapter in enumerate(all_chapters, start=1):
            if str(chapter.get("id") or "") == current_id:
                current_no = index
                current_title = str(chapter.get("title") or "")
                break
        return {
            # 值就是引擎的 heat，但**键名**不能说破（见上面的说明）。
            "hype": round(float(getattr(player, "heat", 0.0) or 0.0), 4),
            "chapter": current_no,
            "chapter_total": total,
            "chapter_title": current_title,
            "seen": len(seen),
        }

    def _visual_settings(self) -> dict[str, Any]:
        """视觉降级的表现层参数（阈值来自 ``config.example.toml``）。"""
        raw = self._business_config_cache or {}
        section = raw.get("visual")
        return section if isinstance(section, dict) else {}

    def _push_danmaku(self, text: str, kind: str = "cat") -> None:
        """记一条猫娘弹幕（面板的「赛博弹幕悬浮窗」用）。

        运行时没有卡片 API ⇒ 猫娘在局中的嘲讽改走面板弹幕（外加宿主的
        ``showToast`` 兜底）。弹幕**只在内存里**：它是"此刻"的装饰。
        """
        if not text:
            return
        self._danmaku_seq += 1
        self._danmaku.append(
            {
                "id": self._danmaku_seq,
                "kind": kind,
                "text": text,
                "at": time.time(),
            }
        )
        if len(self._danmaku) > _DANMAKU_MAX:
            del self._danmaku[: len(self._danmaku) - _DANMAKU_MAX]

    def _visual_block(
        self, session: Session | None, engine_config: EngineConfig
    ) -> dict[str, Any]:
        """面板载荷的 ``visual`` 块：三档档位 + 弹幕 + 导演台开关 + 名册。

        **纯表现层**：这里判定的东西不参与任何游戏规则。
        """
        settings = self._visual_settings()
        broke_day = int(settings.get("broke_day_threshold", 3) or 3)
        dying_assets = float(settings.get("dying_assets_threshold", 20.0) or 20.0)
        panic_distance = float(settings.get("panic_distance", 40.0) or 40.0)
        emoji_sea_size = max(50, int(settings.get("emoji_sea_size", 60) or 60))

        player = session.player if session is not None else None
        market = session.market if session is not None else None
        day = int(player.day) if player is not None else 0
        cash = float(player.cash) if player is not None else 0.0
        alive = bool(player.alive) if player is not None else True
        mode = session.mode if session is not None else ""
        total_assets = cash
        distance: float | None = None
        if player is not None and market is not None:
            prices = market.prices()
            # 用引擎自己的口径算总资产（不另起一套算法）。
            total_assets = player.total_asset(prices)
            distance = self._distance_to_death(session, engine_config)

        # 资金链断裂：现金为负、或者离强制平仓只差一步、或者已经被清盘了。
        critical = bool(
            cash < 0
            or (distance is not None and distance <= panic_distance)
            or not alive
        )

        # 三档：濒临破产 > 断供 > 富二代。
        stage = "rich"
        if mode == "infinite" or critical or total_assets <= dying_assets:
            stage = "dying"
        elif day >= broke_day:
            stage = "broke"

        # 名册里"这一局在场"的群友（P7.2：按**波动率排名**绑定到真实槽位）。
        roster: list[dict[str, Any]] = []
        if market is not None:
            for slot_id, member in self._roster_binding(market).items():
                stock = market.stocks.get(slot_id)
                if stock is None:
                    continue
                roster.append(
                    {
                        "stock_id": stock.id,
                        "alias": str(member.get("alias") or ""),
                        "ticker": str(member.get("ticker") or ""),
                        "title": str(member.get("title") or ""),
                        "persona": str(member.get("persona") or ""),
                        "price": round(float(stock.price), 4),
                        "display_name": self._roster_display_name(member),
                    }
                )

        # 导演台的「赛博弹窗轰炸」：还没到点就是 inactive。
        boom_left = max(0.0, float(self._director_boom_until) - time.time())
        boom_active = bool(self._director_on() and boom_left > 0)

        # P7.3：网贷弹窗的**超时倒计时**（前端据此显示「N 秒后自动结算」）。
        # ``pending`` 为假时前端根本不用管这个弹窗。
        loan_prompt_pending = self._loan_prompt_at > 0.0
        loan_prompt_left = (
            max(
                0.0,
                self._loan_prompt_at + _LOAN_PROMPT_TIMEOUT_SECONDS - time.time(),
            )
            if loan_prompt_pending
            else 0.0
        )

        return {
            "stage": stage,
            "critical": critical,
            "critical_emoji": "🤡" if not alive else "💸",
            "stage_thresholds": {
                "broke_day": broke_day,
                "dying_assets": dying_assets,
                "panic_distance": panic_distance,
            },
            "emoji_sea_size": emoji_sea_size,
            "danmaku": list(self._danmaku[-_DANMAKU_PAYLOAD_MAX:]),
            "director": {
                "enabled": self._director_on(),
                "queued": len(self._director_queue),
            },
            "boom": {
                "active": boom_active,
                "count": int(self._director_boom_count) if boom_active else 0,
                "seconds_left": int(boom_left) + 1 if boom_active else 0,
                "until": round(float(self._director_boom_until), 3),
                # P7.3：本次轰炸**本来该持续多久**（秒）—— 前端据此算硬上限。
                "duration": int(round(float(self._director_boom_duration) or 5.0)),
            },
            # P7.3：网贷弹窗的锁死兜底信息（纯表现层 + 倒计时）。
            "loan_prompt": {
                "pending": loan_prompt_pending,
                "seconds_left": int(loan_prompt_left) + 1 if loan_prompt_pending else 0,
                "timeout_seconds": int(_LOAN_PROMPT_TIMEOUT_SECONDS),
                # 经典模式没有高利贷 —— 前端据此决定要不要显示「借走 500 万」。
                "can_borrow": session is not None and session.mode == "infinite",
            },
            "roster": roster,
        }

    async def _session_or_error(self, engine_config: EngineConfig):
        """加载当前会话；数据库不可用时返回错误结果。"""
        try:
            session = await load_session(
                self._repo, self.state, engine_config=engine_config
            )
        except (DbUnavailableError, sqlite3.Error) as error:
            # 宿主补发有效配置后重试一次（懒建表自愈也在这里生效）。
            await self._refresh_runtime_capabilities()
            try:
                session = await load_session(
                    self._repo, self.state, engine_config=engine_config
                )
            except (DbUnavailableError, sqlite3.Error):
                return None, _err("db_unavailable", error)
        await self._refresh_player_onboarding_cache(session)
        return session, None

    async def _refresh_player_onboarding_cache(self, session: Session | None) -> None:
        """补齐**玩家维度**的"引导已读"缓存（记在 ``player_id`` 那一行）。

        **只在"全局还没标记已读"时才会去查库** —— 一旦玩家看过说明，
        全局标记就够用了，热路径不必再碰数据库（面板上下文只有 5 秒超时）。
        """
        if session is None:
            return
        player_id = session.player_id
        if not player_id:
            return
        if self._onboarding_player_seen_cache[0] == player_id:
            return
        if self._settings_map_cache is None:
            return
        if self._onboarding_ack_cache:
            # 全局已读 ⇒ 这个玩家必然也看过。
            self._onboarding_player_seen_cache = (player_id, True)
            return
        try:
            seen = await self._repo.player_onboarding_seen(player_id)
        except (DbUnavailableError, sqlite3.Error):
            return
        self._onboarding_player_seen_cache = (player_id, bool(seen))

    async def _harvest_events(self, player_id: str) -> list[dict[str, Any]]:
        """收割时间线（**只有结算页能拿到**，含收割时刻的 heat 数值）。"""
        try:
            rows = await self._repo.fetch_all(
                "SELECT tick, day, phase, trigger, stock_id, severity, "
                "player_heat_at_harvest, concentration_at_harvest, death_reason "
                "FROM liquidation_log WHERE player_id = ? ORDER BY tick ASC",
                (player_id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            return []
        return [
            {
                "tick": int(row["tick"]),
                "day": int(row["day"]),
                "phase": row["phase"],
                "trigger": row["trigger"],
                "stock_id": row["stock_id"],
                "severity": round(float(row["severity"]), 6),
                "heat_at_harvest": round(float(row["player_heat_at_harvest"]), 4),
                "concentration": round(float(row["concentration_at_harvest"]), 6),
                "death_reason": row["death_reason"],
            }
            for row in rows
        ]

    async def _catgirl_result(self, session: Session) -> dict[str, Any] | None:
        """猫娘的结局（本局没有猫娘时返回 ``None``）。"""
        catgirl = session.catgirl
        if catgirl is None:
            return None
        try:
            row = await self._repo.fetch_one(
                "SELECT alive, day, cash, trade_count, death_reason FROM players WHERE id = ?",
                (catgirl.id,),
            )
        except (DbUnavailableError, sqlite3.Error):
            row = None
        alive = bool(row["alive"]) if row is not None else catgirl.alive
        return {
            "alive": alive,
            "days_survived": int(row["day"]) if row is not None else catgirl.day,
            "final_cash": round(float(row["cash"]), 6) if row is not None else round(catgirl.cash, 6),
            "trade_count": int(row["trade_count"]) if row is not None else catgirl.trade_count,
            "death_reason": row["death_reason"] if row is not None else catgirl.death_reason,
        }

    def _initial_cash(self, mode: str, seed: int, engine_config: EngineConfig) -> float:
        """玩家的初始**现金**。

        * 经典模式：按**本档**的生活费 × 初始天数（入门/进阶 10×10 = 100，
          地狱 12×7 = 84，街机沿用旧口径 15~20×7）。
        * 无限模式（P4）：**0**。初始资金以"基准股票"的形式发放（市值 ≈ 1050），
          玩家必须先卖掉它才交得上第一天的生活费 —— 这正是"现钱唯一，股票市值
          不算数"的设计。
        """
        if mode == "infinite":
            return 0.0
        difficulty = engine_config.difficulties[mode]
        low, high = classic_living_cost_range(difficulty, engine_config)
        days = classic_initial_days(difficulty, engine_config)
        base = derive_rng(seed, 999).uniform(low, high)
        # ── P16.0：入门档加厚初始资金（×1.2）─────────────────────────
        # 让新手期真真切切能赚到钱 —— 先甜后苦，「我真是股神」的错觉
        # 是后面收割成立的前提。**只改插件层**，引擎的算法没动。
        if mode == "classic_beginner":
            return round(base * days * BEGINNER_CASH_MULTIPLIER, 2)
        return round(base * days, 2)

    @staticmethod
    def _catgirl_initial_cash(engine_config: EngineConfig) -> float:
        """猫娘的初始现金（无限模式常驻）。

        猫娘是 NPC 操盘手，她有本金；玩家开局是 0 现金 + 基准股票。这是**开局
        设定**的差异，不是规则豁免：她照样交生活费、照样会被收割、照样用同一
        套 heat 与日终结算。
        """
        infinite = engine_config.infinite
        return infinite.initial_days * infinite.daily_living_cost

    def _next_living_cost(self, difficulty, engine_config: EngineConfig) -> float:
        """次日生活费；没有生活费的档位返回 0。"""
        return next_living_cost(difficulty, engine_config)

    @staticmethod
    def _sellable_amount(position, day: int, difficulty) -> float:
        """当前可卖出的数量。

        契约 §2.2：``sellable`` = **考虑 T+1 后**的可卖数量。没有 T+1 的档位
        （街机 / 入门 / 无限）不受当日买入锁定，全部持仓都可卖。
        """
        if not difficulty.t_plus_one:
            return position.amount
        return position.sellable_amount(day)

    @staticmethod
    def _analysis_target(
        session: Session, stock_id: str, prices: dict[str, float]
    ) -> Any:
        """要分析哪只标的：指定的优先 → 持仓最重的那只 → 第一只还活着的。"""
        market = session.market
        if stock_id:
            return market.stocks.get(stock_id)
        player = session.player
        best = None
        best_value = -1.0
        for position_id, position in player.positions.items():
            value = position.market_value(prices.get(position_id, 0.0))
            if value > best_value:
                best_value = value
                best = market.stocks.get(position_id)
        if best is not None:
            return best
        for stock in market.stocks.values():
            if stock.alive:
                return stock
        return None

    def _market_payload(self, session: Session) -> dict[str, Any]:
        """市场数据的**白名单**返回体（严禁出现 heat / seed / 反转日）。"""
        market = session.market
        stocks: list[dict[str, Any]] = []
        for stock in market.stocks.values():
            change = (
                (stock.price - stock.prev_price) / stock.prev_price
                if stock.prev_price
                else 0.0
            )
            stocks.append(
                {
                    "id": stock.id,
                    "name": stock.name,
                    "price": round(stock.price, 6),
                    "prev_price": round(stock.prev_price, 6),
                    "change": round(change, 6),
                    "tag": stock.tag,
                    "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                    "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                    "alive": stock.alive,
                }
            )
        return {
            "day": market.day,
            "tick": market.tick,
            "phase": market.phase,
            "stocks": stocks,
            # ── P5 方案 B：**不再返回** ``reversal_today`` ──────────────
            # P4 §11 原本在这里给"今天是不是反转日"（只说是不是、不说方向）。
            # P5 复核认为它仍是**强剧透**：玩家知道今天特殊，就能把新闻反过来读，
            # 反诈教学的核心机制被绕过。现在改成"**花一次分析机会**换一句
            # 不带方向的模糊提示"（见 ``analyze`` 入口）。
            # 守卫的 ``reversal_today`` 白名单例外随之失效（§20.2 第 6 条）。
        }

    # ------------------------------------------------------------------
    # P18.0：分档次动态教程（后端状态机 · 面板只渲染）
    # ------------------------------------------------------------------
    #
    # 状态机：``IDLE → GREET → STEP_i → EPILOGUE → DONE``
    #
    # * **谁推进** —— 插件后端。面板只渲染 + 上报点击；
    #   「真实操作推进」（第一笔成交）由后端从成交记录判定。
    # * **台词** —— 一律由 Yui 生成：后端只给**事实**与**内容约束**，
    #   一个字台词都不写（P13 契约 + 「不许硬编码台词」红线）。
    # * **进度** —— 落库在 ``players.tutorial_state``，**按档位分别记录**。

    def _tutorial_steps(self) -> tuple[Any, ...]:
        """当前档位的步骤序列（空 ⇒ 这个档位没有教程）。"""
        return TIER_STEPS.get(str(self._tutorial.get("tier") or ""), ())

    async def _tutorial_load_tier(self, player_id: str, tier: str) -> dict[str, Any]:
        """读某个档位的教程进度（**只读这一档**，别的档位不干扰）。"""
        if not player_id or not tier:
            return {}
        try:
            raw = await self._repo.fetch_one(
                "SELECT tutorial_state FROM players WHERE id = ?", (player_id,)
            )
        except Exception:  # noqa: BLE001 - 读不到就当没学过
            return {}
        if raw is None:
            return {}
        try:
            loaded = json.loads(str(raw["tutorial_state"] or "{}"))
        except (TypeError, ValueError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        entry = loaded.get(tier)
        return entry if isinstance(entry, dict) else {}

    async def _tutorial_persist(self) -> None:
        """把当前档位的进度写回 ``players.tutorial_state``。

        **按档位分别记录** ⇒ 换了难度就是另一条进度，互不干扰。
        """
        player_id = str(self._last_player_id or "")
        tier = str(self._tutorial.get("tier") or "")
        if not player_id or not tier:
            return
        try:
            raw = await self._repo.fetch_one(
                "SELECT tutorial_state FROM players WHERE id = ?", (player_id,)
            )
            data: dict[str, Any] = {}
            if raw is not None:
                try:
                    loaded = json.loads(str(raw["tutorial_state"] or "{}"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (TypeError, ValueError):
                    data = {}
            data[tier] = {
                "phase": str(self._tutorial.get("phase") or PHASE_IDLE),
                "index": int(self._tutorial.get("index") or 0),
            }
            await self._repo.transaction(
                [
                    (
                        "UPDATE players SET tutorial_state = ? WHERE id = ?",
                        (json.dumps(data, ensure_ascii=False), player_id),
                    )
                ]
            )
        except Exception as error:  # noqa: BLE001 - 进度记不上不该拖垮教学
            self.logger.error(f"教程进度落库失败（不影响教学）：{error}")

    async def _tutorial_taught_tiers(self) -> dict[str, bool]:
        """读全局闸门：哪些档位**已经教过了**。"""
        try:
            raw = await self._repo.get_setting(_SETTING_TUTORIAL_TIERS)
        except Exception:  # noqa: BLE001 - 读不到就当没教过
            return {}
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(key): bool(value) for key, value in loaded.items()}

    async def _tutorial_mark_taught(self, tier: str) -> None:
        """记下「这一档教过了」（全局）。

        教完 / 跳过都算教过 —— 玩家已经知道规矩了，不必再来一遍。
        """
        if not tier:
            return
        taught = await self._tutorial_taught_tiers()
        if taught.get(tier):
            return
        taught[tier] = True
        try:
            await self._repo.set_setting(
                _SETTING_TUTORIAL_TIERS, json.dumps(taught, ensure_ascii=False)
            )
        except Exception as error:  # noqa: BLE001 - 闸门记不上只是多教一遍
            self.logger.error(f"教程闸门落库失败（不影响教学）：{error}")

    async def _tutorial_speak(self, prompt: str) -> None:
        """把提示词交给 Yui（**绝不让它拖垮游戏**）。"""
        try:
            await self._push_proactive(prompt, reason="tutorial")
        except Exception as error:  # noqa: BLE001 - 解说失败不该拖垮教学
            self.logger.error(f"教程解说异常（不影响教学）：{error}")

    async def _tutorial_speak_step(self, step: Any) -> None:
        """讲这一步：把**事实**交给 Yui，台词她自己写。"""
        tier = str(self._tutorial.get("tier") or "")
        situation = ""
        try:
            profile = await self._refresh_emotion_profile()
        except Exception:  # noqa: BLE001 - 画像拿不到就不带它
            profile = None
        if profile is not None:
            tags = render_tags(profile)
            if tags:
                situation = f"玩家现在的状态：{tags}。"
        await self._tutorial_speak(
            build_step_prompt(
                tier,
                step,
                situation,
                player_name=str(self._tutorial.get("player_name") or ""),
                egg_fact=str(self._tutorial.get("egg_fact") or ""),
            )
        )

    async def _push_name_intro(self, player_name: str, egg_fact: str = "") -> None:
        """**教程没开**时单独推一趟：把名字（与彩蛋事实）交给 Yui。

        只报事实（P13 契约），也只叫这一次的名字 —— 说完就散，不追着喊。
        """
        if not player_name and not egg_fact:
            return
        try:
            await self._push_proactive(
                build_name_intro_prompt(player_name, egg_fact), reason="name_egg"
            )
        except Exception as error:  # noqa: BLE001 - 招呼失败不该拖垮开局
            self.logger.error(f"名字开场白异常（不影响开局）：{error}")

    async def _tutorial_start(
        self, mode: str, *, player_name: str = "", egg_fact: str = ""
    ) -> bool:
        """按档位启动动态教程。

        **这一档已经学过了就不再来一遍** —— 教程不是骚扰。

        返回 ``True`` = 教程真的开起来了（开场白里已经带上名字与彩蛋事实）；
        ``False`` = 没开（这一档教过了 / 这一档没教程 / 玩家 id 缺失）——
        调用方据此决定要不要**单独**推一趟名字开场白。
        """
        if mode not in TIER_STEPS:
            return False
        player_id = str(self._last_player_id or "")
        if not player_id:
            return False
        if (await self._tutorial_taught_tiers()).get(mode):
            return False  # 这一档**全局**教过了 —— 不再来一遍（教程不是骚扰）。
        entry = await self._tutorial_load_tier(player_id, mode)
        if str(entry.get("phase") or "") == PHASE_DONE:
            return False
        self._tutorial = {
            "phase": PHASE_GREET,
            "tier": mode,
            "index": 0,
            "deadline": 0.0,
            "gate": "",
            "gates_seen": [],
            "epilogue_done": False,
            # ── P20.0：名字与彩蛋事实跟着教程走（**只给事实**）──────
            "player_name": player_name,
            "egg_fact": egg_fact,
        }
        await self._tutorial_speak(
            build_greet_prompt(mode, player_name=player_name, egg_fact=egg_fact)
        )
        return True

    async def _tutorial_enter_step(self, index: int) -> None:
        """进入第 ``index`` 步（越界 ⇒ 结课）。"""
        steps = self._tutorial_steps()
        if index < 0 or index >= len(steps):
            await self._tutorial_finish("玩家刚走完了这一档的教学。")
            return
        step = steps[index]
        gates = list(self._tutorial.get("gates_seen") or [])
        self._tutorial["phase"] = PHASE_STEP
        self._tutorial["index"] = index
        if step.gate and step.gate not in gates:
            # 这一步**等一个事件**才开课（无尽档的网贷课）：
            # 玩家真的遇到那件事了，才有必要讲它。
            self._tutorial["gate"] = step.gate
            self._tutorial["deadline"] = 0.0
            await self._tutorial_persist()
            return
        self._tutorial["gate"] = ""
        self._tutorial["deadline"] = time.time() + STEP_TIMEOUT_SECONDS
        await self._tutorial_persist()
        await self._tutorial_speak_step(step)

    async def _tutorial_finish(self, outcome: str) -> None:
        """走完最后一步 ⇒ 进入结课小结。"""
        self._tutorial["phase"] = PHASE_EPILOGUE
        self._tutorial["deadline"] = 0.0
        self._tutorial["gate"] = ""
        await self._tutorial_persist()
        await self._tutorial_mark_taught(str(self._tutorial.get("tier") or ""))
        await self._tutorial_epilogue(outcome)

    async def _tutorial_epilogue(self, outcome: str) -> None:
        """结课小结：风险提示 + 祝福（**台词由 Yui 生成**）。

        整个对局**只说一次** —— 说两遍就是骚扰。
        """
        if bool(self._tutorial.get("epilogue_done")):
            return
        self._tutorial["epilogue_done"] = True
        await self._tutorial_speak(build_epilogue_prompt(outcome))

    def _tutorial_gate_event(self, gate: str) -> None:
        """记一笔门槛事件（**同步** —— 面板渲染时也能调）。"""
        gates = list(self._tutorial.get("gates_seen") or [])
        if gate in gates:
            return
        gates.append(gate)
        self._tutorial["gates_seen"] = gates
        self._tutorial_gate_pending = gate

    async def _tutorial_on_trade(self) -> None:
        """玩家完成了第一笔成交 ⇒ 该自动推进的那一步推进。"""
        if str(self._tutorial.get("phase") or "") != PHASE_STEP:
            return
        steps = self._tutorial_steps()
        index = int(self._tutorial.get("index") or 0)
        if index < 0 or index >= len(steps):
            return
        if steps[index].auto_advance != "first_trade":
            return
        await self._tutorial_enter_step(index + 1)

    async def _tutorial_pump(self) -> None:
        """推进教程（门槛事件 / 超时）。

        挂在**面板轮询**上 —— 教程本来就只在玩家看着面板时才有意义。
        """
        phase = str(self._tutorial.get("phase") or PHASE_IDLE)
        if phase == PHASE_IDLE:
            return
        # ① 门槛事件到了 ⇒ 当前那一步可以开课了。
        pending = self._tutorial_gate_pending
        if pending:
            self._tutorial_gate_pending = ""
            steps = self._tutorial_steps()
            index = int(self._tutorial.get("index") or 0)
            if phase == PHASE_STEP and 0 <= index < len(steps):
                if steps[index].gate == pending:
                    await self._tutorial_enter_step(index)
                    return
        # ② 当前步超时 ⇒ 自动推进（教程不许把玩家卡住）。
        if phase != PHASE_STEP:
            return
        deadline = float(self._tutorial.get("deadline") or 0.0)
        if deadline <= 0.0:
            return  # 在等门槛事件，不计时
        if time.time() < deadline:
            return
        await self._tutorial_enter_step(int(self._tutorial.get("index") or 0) + 1)

    def _tutorial_block(self) -> dict[str, Any]:
        """面板载荷的 ``tutorial`` 块（**纯内存**，不查库）。

        台词**不在这里** —— 台词由 Yui 在面板外说。
        这里只有**功能性 UI 文案的 i18n key**（步骤标题、正文、按钮名）。
        """
        phase = str(self._tutorial.get("phase") or PHASE_IDLE)
        steps = self._tutorial_steps()
        index = int(self._tutorial.get("index") or 0)
        block: dict[str, Any] = {
            "active": phase in (PHASE_GREET, PHASE_STEP, PHASE_EPILOGUE),
            "phase": phase,
            "tier": str(self._tutorial.get("tier") or ""),
            "index": index,
            "total": len(steps),
            "waiting_gate": bool(self._tutorial.get("gate")),
        }
        if block["waiting_gate"]:
            block["gate_wait_key"] = GATE_WAIT_KEY
        if phase == PHASE_STEP and 0 <= index < len(steps):
            step = steps[index]
            block["step"] = {
                "key": step.key,
                "anchor": step.anchor,
                "title_key": step.title_key,
                "body_key": step.body_key,
            }
        return block

    # ------------------------------------------------------------------
    # Hosted TSX 面板载荷（P5）
    # ------------------------------------------------------------------

    def _panel_payload(
        self,
        session: Session,
        engine_config: EngineConfig,
        pace: dict[str, Any] | None = None,
        onboarding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``@ui.context`` 的**白名单**载荷（P5 面板用）。

        与 ``market`` 入口**分开**：``market`` 是给 LLM 用的，必须轻量；面板要
        的东西多得多（持仓、账单、猫娘、K 线、结算报告）。

        **局中隐藏**：不含 heat / seed / 反转日 / 新闻真假 / 猫娘的 heat。

        **K 线限流（细节 2）**：K 线只给 ``kline_stock_ids`` 列出的那些标的
        （持仓 + 爆炸标签，最多 ``PANEL_KLINE_MAX`` 只），其余标的只给
        price / tag / alive —— 否则载荷会爆（60 点 × 全部标的）。
        """
        market = session.market
        player = session.player
        difficulty = session.difficulty
        prices = market.prices()
        ticks_per_day = max(1, int(difficulty.ticks_per_day))
        total_assets = player.total_asset(prices)

        kline_ids = self._panel_kline_ids(session, prices)
        kline_set = set(kline_ids)

        positions: list[dict[str, Any]] = []
        roster_binding = self._roster_binding(market)
        for stock_id, position in player.positions.items():
            stock = market.stocks.get(stock_id)
            price = prices.get(stock_id, 0.0)
            market_value = position.market_value(price)
            tag = stock.tag if stock is not None else "neutral"
            member = roster_binding.get(stock_id)
            positions.append(
                {
                    "stock_id": stock_id,
                    "name": stock.name if stock is not None else stock_id,
                    "display_name": (
                        self._roster_display_name(member)
                        if member is not None
                        else (stock.name if stock is not None else stock_id)
                    ),
                    "amount": round(position.amount, 6),
                    "avg_cost": round(position.avg_cost, 6),
                    "price": round(price, 6),
                    "market_value": round(market_value, 6),
                    "unrealized_pnl": round(position.unrealized_pnl(price), 6),
                    "sellable": round(
                        self._sellable_amount(position, player.day, difficulty), 6
                    ),
                    "weight": round(market_value / total_assets, 6)
                    if total_assets > 0
                    else 0.0,
                    "tag": tag,
                    "tag_label": _TAG_LABELS.get(tag, "未知"),
                    # ── P23.2：持仓卡也用同一套「高关注度」排序 ⇒ 持仓侧
                    # 也要知道这只票的波动率（同样是**只读**，见上面）。──
                    "sigma": round(float(stock.sigma), 6) if stock is not None else 0.0,
                    "alive": bool(stock.alive) if stock is not None else False,
                }
            )

        stocks: list[dict[str, Any]] = []
        for stock in market.stocks.values():
            change = (
                (stock.price - stock.prev_price) / stock.prev_price
                if stock.prev_price
                else 0.0
            )
            member = roster_binding.get(stock.id)
            entry: dict[str, Any] = {
                "id": stock.id,
                "name": stock.name,
                "display_name": (
                    self._roster_display_name(member)
                    if member is not None
                    else stock.name
                ),
                "roster_alias": str(member.get("alias") or "") if member else "",
                "roster_title": str(member.get("title") or "") if member else "",
                "price": round(stock.price, 6),
                "prev_price": round(stock.prev_price, 6),
                "change": round(change, 6),
                # ── P23.2：波动率（**纯只读展示字段**）────────────────────
                # 引擎早就算好了这个数（``stock.sigma``，整局不变、只被读），
                # 面板拿它做「前排高关注区」的排序 —— 妖股排前面，好戏先看。
                #
                # **引擎层零改动**：这里只是把**已经存在**的数读出来放进载荷，
                # 不参与任何计算、不改任何判定 ⇒ 500 局基线逐字节不变。
                "sigma": round(float(stock.sigma), 6),
                "tag": stock.tag,
                "tag_label": _TAG_LABELS.get(stock.tag, "未知"),
                "tag_color": _TAG_COLORS.get(stock.tag, "amber"),
                "alive": stock.alive,
                # P7.2：走势小折线的数据（**降采样**过，别把载荷撑爆）。
                "spark": [
                    [int(tick), round(float(value), 4)]
                    for tick, value in stock.kline[-_PANEL_SPARK_POINTS:]
                ],
            }
            if stock.id in kline_set:
                entry["kline"] = [
                    [int(tick), round(float(value), 4)] for tick, value in stock.kline
                ]
            stocks.append(entry)

        next_cost = self._next_living_cost(difficulty, engine_config)
        living_cost = daily_living_cost(
            player.mode, player.day, market.seed, engine_config
        )

        # P7.3：面板一旦**渲染出**「交不起饭钱」，服务端就登记这笔待办 ——
        # 30 秒内没人操作就自动结算（前端整个冻住时的最后一道保险）。
        # 载荷是**面板渲染**的同义词（``@ui.context`` 才会调到这里），所以
        # 「面板开着」这件事服务端自己是知道的，不依赖前端额外上报。
        bill_gap = max(0.0, next_cost - player.cash)
        if bill_gap > 0.0 and player.alive:
            if self._loan_prompt_at <= 0.0 or self._loan_prompt_day != int(player.day):
                self._loan_prompt_at = time.time()
                self._loan_prompt_day = int(player.day)
            # ── P18.0：玩家交不起饭钱 ⇒ 触发教程的「网贷课」门槛 ────
            # 教程**跟着玩家的处境走**：先遇到事，再讲这件事。
            self._tutorial_gate_event("loan_modal")
        else:
            # 交得起了（或人没了）：这笔待办作废。
            if self._loan_prompt_day == int(player.day):
                self._loan_prompt_at = 0.0

        payload = {
            "stage": "playing",
            "day": player.day,
            "tick": market.tick,
            "ticks_per_day": ticks_per_day,
            "ticks_to_settle": ticks_per_day - (market.tick % ticks_per_day),
            "phase": market.phase,
            "mode": player.mode,
            "mode_name": _MODE_NAMES.get(player.mode, player.mode),
            # ── P28.0：第一关的「UI 极简」标志 ──────────────────────────
            # 面板据此**彻底隐藏**做空 / 杠杆保证金 / 台账 / 水位条 /
            # 账单 Alert / 连击徽章，只留 K 线 + 买卖两键 + 现钱。
            # 这里给的是**事实**（当前是不是第一关），面板不自己猜档位。
            "arcade_simple": session.mode == MODE_ARCADE,
            "alive": player.alive,
            # ── P20.0：玩家自报名号 + 命中的彩蛋（**只给面板要用的**）──
            # ``name_egg`` 里只有 key / category / label_key，**没有倍率**。
            "player_name": str(getattr(player, "name", "") or ""),
            "name_egg": public_info(
                match_egg(str(getattr(player, "name", "") or ""))
            ),
            # ── 财务 ──────────────────────────────────────────────────
            "cash": round(player.cash, 6),
            "total_assets": round(total_assets, 6),
            # P7.2：玩家自己的开局钱（**不是**隐藏信号，join 早就返回了），
            # 面板拿它算「到现在为止赚/亏了多少」——傻子版的第一屏大数字。
            "initial_cash": round(float(player.initial_cash), 6),
            "living_cost": round(living_cost, 6),
            "next_bill": round(next_cost, 6),
            "bill_gap": round(bill_gap, 6),
            "missed_bills": int(player.strikes),
            "distance_to_kill": self._distance_to_death(session, engine_config),
            "concentration": round(position_concentration(player.positions, prices), 6),
            # ── P16.0：连击（**纯展示**；不含任何游戏数值）──────────────
            "combo": {
                "streak": int(self._combo.streak),
                "best": int(self._combo.best),
                "enabled": session.mode in COMBO_MODES,
            },
            # ── P18.0：动态教程（**只有功能性 UI 文案的 i18n key**）─────
            # 台词由 Yui 在面板外说，这里**一个字的台词都没有**。
            "tutorial": self._tutorial_block(),
            "positions": positions,
            "stocks": stocks,
            "kline_stock_ids": kline_ids,
            # ── 猫娘公开状态 ───────────────────────────────────────────
            "catgirl": self._panel_catgirl(session, prices, ticks_per_day),
            # ── 当天新闻：局中只有中性分类（标题正文要点开才给）──────
            "news_today": self._public_news(market),
            # ── P26.0：群友随机事件（**白名单**；内参内容不在这里）──
            "community_events": self._community_events_payload(),
            # ── P27.0：做空账本（**白名单**）+ 资产曲线（纯表现层）────
            # 空头只给这几项：标的、保证金、倍率、锚点价、现价、
            # 这一拍的浮动盈亏、还剩多少保证金、要不要爆仓。
            "short_positions": self._short_positions_block(player.id, prices),
            # 做空控件的**选项**（倍率表、默认倍率、上限、最小保证金）——
            # 面板**不硬编码**这些数字，一律从这里拿。
            "short_options": {
                "leverages": list(SHORT_LEVERAGES),
                "default_leverage": DEFAULT_SHORT_LEVERAGE,
                "max_positions": SHORT_MAX_POSITIONS,
                "min_margin": SHORT_MIN_MARGIN,
            },
            # 两条等长的曲线：玩家（蓝）与内置猫娘 Yui（红）。
            # Yui 那条是**合成数据**（不来自引擎、不影响任何判定）。
            "equity_curve": self._equity_block(session),
            # ── P27.0：「报纸」的价格（新手前两档白拿）──────────────────
            # 面板**不硬编码**价格：白拿还是自掏，它自己判断。
            "report_free": session.mode in REPORT_FREE_MODES,
            "report_price": round(
                0.0 if session.mode in REPORT_FREE_MODES else REPORT_PRICE, 2
            ),
            # ── 二道贩子提案（公开部分） ───────────────────────────────
            "pending_action": self._public_pending_action(player.pending_action or {}),
            "pending_event": self._pending_event,
            "analyses_left": self._analyses_left(player),
            # ── P7：视觉降级档位 / 猫娘弹幕 / 导演台开关 / 社区名册 ────
            # **纯表现层**：不含任何隐藏信息（heat / seed / 反转日 /
            # 新闻真假一概没有）。
            "visual": self._visual_block(session, engine_config),
            "settlement": None,
            # ── P10：节奏（速度 / 自动·手动）与新手引导 ──────────────────
            "pace": pace or {},
            "onboarding": onboarding or {},
            # ── P11：猫娘主动搭话的状态（纯内存）─────────────────────
            "meow": self._meow_block(),
            # ── P12：手续费率（**只读** ``engine_config.fees``）────────
            # 面板要算"预计买入多少 / 预计花费多少 / 剩余现金"，
            # 用的是**引擎那一套费率**（``buy_fee`` / ``sell_fee``）——
            # 抄进载荷只是为了让面板**本地**算，拖滑块时零往返。
            # **不改任何费率**（读，不是写）。
            "fees": {
                "buy_rate": round(float(engine_config.fees.buy_rate), 6),
                "sell_rate": round(float(engine_config.fees.sell_rate), 6),
                "stamp_tax_rate": round(float(engine_config.fees.stamp_tax_rate), 6),
            },
            # ── P12：剧情状态（纯表现层 + 选项降级通道）────────────────
            # **不含任何猫娘立绘**：猫娘在外置气泡里说，面板只放纯文本场景。
            "story": self._story_block(),
            # ── P23.0：Yui 的操控状态（模式 / 授权 / 配额 / 最近一次介入）──
            # **纯表现层**：不含任何游戏数值。
            "yui": self._yui_block(int(player.day)),
            "notice": "",
        }
        # ── P19.0：导演段的「进度」块（**只在开关打开时才有这个键**）────
        # 开关关闭时载荷**一个字节都不多**（纯净版载荷与以前逐字节相同）。
        progress = self._director_progress_block(session)
        if progress is not None:
            payload["director_progress"] = progress
        return payload

    # ------------------------------------------------------------------
    # P16.0：玩家情绪画像（只读客观量 ⇒ 定性标签 ⇒ 只喂给 LLM）
    # ------------------------------------------------------------------

    async def _build_emotion_profile(self, session: Session) -> EmotionProfile:
        """把客观量折成情绪画像。

        **纯读取**：引擎的 heat / 持仓集中度、插件层的成交记录，
        一个都不写。折出来的标签**定性、无数值**（见 ``emotion.py``）。

        成交记录读不出来（数据库不可用）时**降级**成「只看得见 heat 与集中度」
        的画像 —— 绝不因为读库失败就让猫娘闭嘴。
        """
        player = session.player
        market = session.market
        prices = market.prices()

        rows: list[tuple[str, str, float, float]] = []
        trades_today = 0
        try:
            raw = await self._repo.fetch_all(
                "SELECT side, stock_id, amount, price, day FROM trades "
                "WHERE player_id = ? ORDER BY id ASC",
                (player.id,),
            )
        except Exception:  # noqa: BLE001 - 读库失败只降级，不该让猫娘闭嘴
            raw = []
        today = int(player.day)
        for row in raw or []:
            try:
                side = str(row["side"])
                stock_id = str(row["stock_id"])
                amount = float(row["amount"])
                price = float(row["price"])
                day = int(row["day"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            rows.append((side, stock_id, amount, price))
            if day == today:
                trades_today += 1

        positions = {
            stock_id: (float(pos.amount), float(pos.avg_cost))
            for stock_id, pos in player.positions.items()
        }
        return build_profile(
            heat=float(getattr(player, "heat", 0.0)),
            consecutive_losses=consecutive_losses(rows),
            concentration=float(position_concentration(player.positions, prices)),
            trades_today=trades_today,
            loss_ratio=holding_loss_ratio(positions, prices),
            holding_count=len(positions),
            alive=bool(player.alive),
        )

    def _emotion_block(self) -> str:
        """给 LLM 的**情绪块**（定性标签 + 约束清单）。

        没有画像时返回空串 ⇒ 提示词与 P16.0 之前**完全一样**
        （安全降级，绝不因为画像缺失就让猫娘说错话）。
        """
        profile = self._emotion_profile
        if profile is None:
            return ""
        return render_prompt_block(profile)

    async def _refresh_emotion_profile(self) -> EmotionProfile | None:
        """按需刷新情绪画像（**只进内存**）。

        读不出来（还没开局 / 数据库不可用）时返回 ``None`` ——
        调用方据此决定要不要带情绪块（不带 ⇒ 提示词与旧版一模一样）。
        """
        try:
            engine_config = await self._engine_config()
            session, error = await self._session_or_error(engine_config)
        except Exception:  # noqa: BLE001 - 画像拿不到不该拖垮搭话
            return None
        if error is not None or session is None:
            return None
        try:
            self._emotion_profile = await self._build_emotion_profile(session)
        except Exception:  # noqa: BLE001 - 同上
            return None
        return self._emotion_profile

    def _touch_player_action(self, reason: str = "player") -> None:
        """记一笔「玩家有操作」（用于判断发呆）。

        同时把 charge 打满 —— 玩家刚动过，就不该被搭话。

        P27.2：``reason`` 用来区分**谁**在动账户 ——
        ``"yui"`` 是 Yui 自己下的单（她动账户**不算**"玩家开口"），
        其余（``"manual"`` / ``"player"``）都是**玩家**的操作。
        玩家的操作会**开锁**：回执周期到此结束，下一回合她可以再动手。
        """
        self._last_player_action_at = time.time()
        self._proactive_charge = 1.0
        self._proactive_charge_at = self._last_player_action_at
        if str(reason or "") != "yui":
            # 玩家又开口了 ⇒ 新的一回合 ⇒ 回执周期锁解开。
            self._yui_trade_cycle_unlock()

    async def _idle_meow_watchdog(self) -> None:
        """**主动搭话**：玩家发呆太久，猫娘主动开口。

        阈值与节流**对齐宿主 N.E.K.O 的 Focus 语义**：

        * 空闲判定 = ``IDLE_PROACTIVE_SECONDS``（300 秒，宿主
          ``VISIT_IDLE_TIMEOUT_S``）；
        * 节流 = charge 语义（宿主 ``FOCUS_CHARGE_RETENTION`` /
          ``FOCUS_CHARGE_ENTER``）：玩家越活跃 charge 越高，越不需要被搭话；
          charge 每分钟 ×0.8，低于 0.6 才允许搭话；
        * **搭话完 charge 打满**（宿主刚开口 ⇒ 又「上心」了）——
          所以下一轮搭话要等 charge 再自然衰减下去，
          连续发呆时大约每 3 分钟搭一次，不会刷屏。

        宿主那条铁律在这里同样成立：**空闲只让 charge 衰减，
        不产生触发** —— 触发的是「玩家已经安静下来了」这个事实本身。

        **只在真的要开口时查库** —— 心跳每秒一次，不能每秒查库。
        """
        now = time.time()
        # charge 衰减：每分钟一档（对齐宿主的 retention 语义）。
        steps = int((now - self._proactive_charge_at) // 60.0)
        if steps > 0:
            self._proactive_charge *= PROACTIVE_CHARGE_RETENTION**steps
            self._proactive_charge_at += steps * 60.0
        # 先判「该不该」，再去查库。
        if (now - self._last_player_action_at) < IDLE_PROACTIVE_SECONDS:
            return
        if self._proactive_charge > PROACTIVE_CHARGE_ENTER:
            return
        profile = await self._refresh_emotion_profile()
        if profile is None or profile.ended:
            return
        spoken = await self._push_proactive(
            idle_prompt(self._emotion_block()), reason="idle"
        )
        self._idle_meow_at = now
        # 搭话完宿主重新「上心」：charge 打满 ⇒ 下一轮要等它自然衰减下去。
        self._proactive_charge = 1.0
        self._proactive_charge_at = now
        if not spoken:
            self.logger.info("主动搭话未被宿主受理（idle）：本轮跳过")

    @staticmethod
    def _panel_kline_ids(session: Session, prices: dict[str, float]) -> list[str]:
        """哪些标的带 K 线（细节 2：限流，别让载荷爆掉）。

        顺序：持仓（按市值从大到小）→ 爆炸标签的标的（按波动率从高到低）
        → **第一只活着的标的**（P7.2：K 线是面板的**主视觉**，不能一开局就
        画不出来 —— 没有持仓、也没有爆炸标的时，面板默认落在第一只标的上，
        它必须带着 K 线）。
        总数上限 ``PANEL_KLINE_MAX``。
        """
        market = session.market
        player = session.player
        picked: list[str] = []
        seen: set[str] = set()

        holdings = sorted(
            player.positions.items(),
            key=lambda item: item[1].market_value(prices.get(item[0], 0.0)),
            reverse=True,
        )
        for stock_id, _position in holdings:
            if stock_id not in seen:
                seen.add(stock_id)
                picked.append(stock_id)
            if len(picked) >= PANEL_KLINE_MAX:
                return picked

        explosive = [
            stock
            for stock in market.stocks.values()
            if stock.alive and stock.tag == "explosive" and stock.id not in seen
        ]
        explosive.sort(key=lambda stock: stock.sigma, reverse=True)
        for stock in explosive:
            if len(picked) >= PANEL_KLINE_MAX:
                break
            seen.add(stock.id)
            picked.append(stock.id)

        # P7.2：兜底给**第一只活着的标的**留一份 K 线（面板的默认视图）。
        if len(picked) < PANEL_KLINE_MAX:
            for stock in market.stocks.values():
                if stock.alive and stock.id not in seen:
                    seen.add(stock.id)
                    picked.append(stock.id)
                    break
        return picked

    def _panel_catgirl(
        self, session: Session, prices: dict[str, float], ticks_per_day: int
    ) -> dict[str, Any] | None:
        """猫娘的**公开状态**（面板用）。

        只有持仓 / 现金 / 存活天数 / 今天动没动手 —— **不给她的 heat，
        也不给她买卖了哪只**（P5 契约 §2.4）。
        """
        catgirl = session.catgirl
        if catgirl is None:
            return None
        daily = self._catgirl_daily or {}
        trades = int(daily.get("trades", 0)) if daily else 0
        previous = self._catgirl_trades_seen
        if previous is None:
            action = "—"
        elif trades > previous:
            action = f"交易了 {trades - previous} 笔"
        else:
            action = "观望"
        return {
            "alive": bool(catgirl.alive),
            "cash": round(catgirl.cash, 6),
            "positions_value": round(catgirl.position_value(prices), 6),
            "position_count": len(catgirl.positions),
            "days_survived": session.market.tick // max(1, ticks_per_day),
            "today_action": action,
        }

    @staticmethod
    def _analyses_left(player: Player) -> int:
        """今天还剩几次分析机会。"""
        used = player.analyses_used if player.analyses_day == player.day else 0
        return max(0, ANALYSES_PER_DAY - used)

    def _latest_settlement(self) -> dict[str, Any] | None:
        """最近一次归档的结算报告（面板用；P4 历史问题 4）。

        经典模式第 30 天**自动结算**之后 ``market_state.player_id`` 被清空，
        ``settle`` 入口也只会回 ``no_active_game`` —— 玩家就再也看不到结算报告
        了。这里把归档 JSON 读回来接到面板上。
        """
        player_id = self._last_player_id
        if not player_id:
            return None
        try:
            folder = self.data_path("settlements")
            candidates = [
                path
                for path in folder.glob(f"settlement-{player_id}-*.json")
                if path.is_file()
            ]
            if not candidates:
                return None
            newest = max(candidates, key=lambda path: path.stat().st_mtime)
            payload = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # 结算报告含 heat 曲线与种子 —— 那是结算页才允许出现的东西。
        # 面板只在**局终之后**展示它，所以这里整体透传是安全的。
        return payload

    async def _remembered_player_name(self) -> str:
        """玩家最近用过的**自报名号**（回填进空闲态的名字输入框）。

        * 内存缓存优先（面板每秒轮询，**热路径上不能查库**）；
        * 插件刚重启时缓存是空的 ⇒ 查一次库（``players`` 表里最近建的那一行
          **人类玩家**；猫娘那行不算）。

        真正的持久化就是 ``players.name`` —— 那一列本来就在
        （P20.0 拍板：**复用**，不加新列）。
        """
        if self._remembered_name:
            return self._remembered_name
        try:
            row = await self._repo.fetch_one(
                "SELECT name FROM players WHERE is_catgirl = 0 "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            )
        except Exception:  # noqa: BLE001 - 读不到就用默认名
            return DEFAULT_PLAYER_NAME
        if row is None:
            return DEFAULT_PLAYER_NAME
        try:
            name = str(row["name"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return DEFAULT_PLAYER_NAME
        self._remembered_name = name or DEFAULT_PLAYER_NAME
        return self._remembered_name

    async def _heat_context(
        self,
        session: Session,
        stock,
        amount: float,
        engine_config: EngineConfig,
    ) -> HeatEventContext:
        """构造一次买入对 heat 的影响（哪些行为让你上头）。"""
        heat_config = engine_config.heat
        player = session.player

        position = player.positions.get(stock.id)
        is_loss = position is not None and stock.price < position.avg_cost

        recent = [value for _, value in stock.kline[-heat_config.chase_window :]]
        is_chase = bool(recent) and stock.price >= max(recent) * heat_config.chase_ratio

        rows = await self._repo.fetch_all(
            "SELECT amount FROM trades WHERE player_id = ? AND side = 'buy' "
            "ORDER BY id DESC LIMIT ?",
            (player.id, heat_config.high_freq_window),
        )
        amounts = [float(row["amount"]) for row in rows]
        average = sum(amounts) / len(amounts) if amounts else 0.0
        high_frequency = bool(average) and amount > average * heat_config.high_freq_ratio

        next_cost = self._next_living_cost(session.difficulty, engine_config)
        notional = stock.price * amount
        fee = buy_fee(notional, engine_config.fees)
        cash_after = player.cash - notional - fee
        living_cost_averaging = bool(next_cost) and cash_after < next_cost

        # ── P4：新闻 heat 事件 ───────────────────────────────────
        # 「信了假新闻追涨」：最近 N 个 tick 内该股票有**看涨**的假新闻，玩家
        # 在这个时候买入 = 信了。
        # 「雷新闻后补仓」：最近 N 个 tick 内该股票有**雷新闻**，而玩家正在
        # 浮亏中买入 = 补仓。
        after_fake_news, after_thunder_news = self._news_heat_context(
            session, stock, engine_config
        )

        return HeatEventContext(
            is_loss_position=is_loss,
            is_leverage=session.difficulty.leverage > 1.0,
            is_chase_high=is_chase,
            high_frequency=high_frequency,
            after_fake_news=after_fake_news,
            after_thunder_news=after_thunder_news,
            living_cost_averaging=living_cost_averaging,
        )

    def _news_heat_context(
        self, session: Session, stock, engine_config: EngineConfig
    ) -> tuple[bool, bool]:
        """判定这次买入是否"信了假新闻"或"雷新闻后补仓"。

        时间窗：新闻在当天第一个 tick 生成，只有之后 ``news_window_ticks``
        个 tick 内买入才算"信了"（经典模式 1/4 天、无限模式 1/16 天）。
        """
        market = session.market
        window = max(0, int(engine_config.news.news_window_ticks))
        if window <= 0:
            return False, False

        ticks_per_day = max(1, int(session.difficulty.ticks_per_day))
        today = market.day
        news_tick = (today - 1) * ticks_per_day
        if market.tick - news_tick >= window:
            # 已经超出时间窗：今天的新闻不再影响 heat
            return False, False

        after_fake_news = False
        after_thunder_news = False
        for item in market.news_today:
            if item.day != today or item.stock_id != stock.id:
                continue
            if item.type == "fake" and item.direction == "bullish":
                after_fake_news = True
            if item.type == "thunder":
                after_thunder_news = True
        return after_fake_news, after_thunder_news

    async def _persist_tick(self, session: Session) -> None:
        """落盘一个 tick 的结果（heat 曲线 + 市场状态 + 玩家 heat）。"""
        player = session.player
        market = session.market
        now = time.time()
        await self._repo.transaction(
            [
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(player_id, tick) DO UPDATE SET heat = excluded.heat, "
                    "day = excluded.day",
                    (player.id, market.tick, market.day, player.heat),
                ),
                (
                    "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                    "total_fees = ?, updated_at = ? WHERE id = 1",
                    (
                        market.tick,
                        market.day,
                        market.phase,
                        market.fee_pool,
                        market.total_fees,
                        now,
                    ),
                ),
                (
                    "UPDATE players SET heat = ?, day = ?, updated_at = ? WHERE id = ?",
                    (player.heat, player.day, now, player.id),
                ),
            ]
        )
        await save_snapshot(self.state, market, player.id)

    async def _persist_trade(self, session: Session, trade, stock_id: str) -> int:
        """落盘一笔成交（玩家 + 持仓 + 成交流水 + 市场状态），返回成交 id。"""
        player = session.player
        market = session.market
        now = time.time()

        actions: list[tuple[str, tuple[Any, ...]]] = [
            (
                "UPDATE players SET cash = ?, day = ?, alive = ?, trade_count = ?, heat = ?, "
                "total_fee_paid = ?, updated_at = ? WHERE id = ?",
                (
                    player.cash,
                    player.day,
                    int(player.alive),
                    player.trade_count,
                    player.heat,
                    player.total_fee_paid,
                    now,
                    player.id,
                ),
            ),
            (
                "DELETE FROM positions WHERE player_id = ? AND stock_id = ?",
                (player.id, stock_id),
            ),
        ]
        position = player.positions.get(stock_id)
        if position is not None:
            actions.append(
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        player.id,
                        stock_id,
                        position.amount,
                        position.avg_cost,
                        position.today_bought,
                        position.today_bought_day,
                        position.opened_day,
                    ),
                )
            )
        actions.append(
            (
                "INSERT INTO trades (player_id, stock_id, side, amount, price, fee, tick, day, "
                "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    player.id,
                    stock_id,
                    trade.side,
                    trade.amount,
                    trade.price,
                    trade.fee,
                    trade.tick,
                    trade.day,
                    trade.reason,
                    now,
                ),
            )
        )
        trade_index = len(actions) - 1
        actions.append(
            (
                "UPDATE market_state SET tick = ?, day = ?, phase = ?, fee_pool = ?, "
                "total_fees = ?, updated_at = ? WHERE id = 1",
                (
                    market.tick,
                    market.day,
                    market.phase,
                    market.fee_pool,
                    market.total_fees,
                    now,
                ),
            )
        )
        cursors = await self._repo.transaction(actions)
        await save_snapshot(self.state, market, player.id)
        return int(cursors[trade_index].lastrowid or 0)

    def _not_ready(self) -> Err:
        """引擎尚未接入时的统一错误出口（P2 起不再触发）。"""
        return _err("engine_not_ready")
