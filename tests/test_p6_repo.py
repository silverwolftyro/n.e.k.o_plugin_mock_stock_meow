"""P6（仓库化与收口 · v0.7.0）的单测。

守六条线：

1. **「一贫如洗」选项 B**：必须是**活着**结算且总资产为 0 才解锁；
   破产了只解锁对应破产原因成就；图鉴仍然是 8 个。
2. **错误码走 i18n 查表**：插件层**零**硬编码中文错误消息；
   每个用到的错误码在 ``i18n/zh-CN.json`` 里都有条目；实际报错消息来自 i18n。
3. **kline action（P6 档 2 第 6 项）**：按需拉任意标的的 K 线；局中隐藏规则照旧。
4. **仓库化**：CI 工作流 / ruff 配置 / .gitignore / 独立 git 仓库 / origin 命名。
5. **版本号** = 0.7.1。
6. **面板文案全走 i18n**：静态 / 动态键都必须在 i18n 里存在；
   面板代码（去注释后）零硬编码中文。

> 0.7.1 修掉了 0.7.0 在**真实宿主**里启动即崩的私有 SDK 导入
> （``plugin.sdk.shared.core.cards``）；边界守卫见 ``tests/test_sdk_boundary.py``。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from conftest import run
from plugins.mock_stock_meow.storage.repo import Repo

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_I18N_PATH = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_INIT_SOURCE = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
_PANEL_SOURCE = (_PLUGIN_ROOT / "ui" / "panel.tsx").read_text(encoding="utf-8")


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", ""))


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_PATH.read_text(encoding="utf-8"))


def _kill_player(repo: Repo, reason: str = "starved") -> None:
    run(
        repo.transaction(
            [
                (
                    "UPDATE players SET alive = 0, death_reason = ?, "
                    "death_title = '引擎给的原始称号' WHERE is_catgirl = 0",
                    (reason,),
                )
            ]
        )
    )


# ------------------------------------------------- ① 「一贫如洗」选项 B


def test_broke_achievement_needs_the_player_to_be_alive(plugin) -> None:
    """破产了 → **不**解锁「一贫如洗」（只解锁对应破产原因成就）。"""
    run(plugin.join(mode="infinite", seed=7200))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    run(plugin.settle())
    rows = run(repo.fetch_all("SELECT title FROM achievements"))
    titles = {str(row["title"]) for row in rows}
    assert "颗粒无收" in titles
    assert "一贫如洗" not in titles, "破产了不该解锁「一贫如洗」"


def test_broke_achievement_unlocks_for_a_living_broke_player(plugin) -> None:
    """活着且总资产为 0 → 解锁「一贫如洗」。"""
    run(plugin.join(mode="classic_arcade", seed=7201))
    repo = Repo(plugin.db)
    # 账上没钱、没有任何持仓 —— 但人还活着。
    run(
        repo.transaction(
            [
                (
                    "UPDATE players SET cash = 0, alive = 1, death_reason = NULL, "
                    "death_title = NULL WHERE is_catgirl = 0",
                    (),
                ),
                (
                    "DELETE FROM positions WHERE player_id = "
                    "(SELECT id FROM players WHERE is_catgirl = 0)",
                    (),
                ),
            ]
        )
    )
    run(plugin.settle())
    rows = run(repo.fetch_all("SELECT title FROM achievements"))
    titles = {str(row["title"]) for row in rows}
    assert "一贫如洗" in titles, "活着且身无分文应该解锁「一贫如洗」"


def test_achievements_still_has_eight_entries(plugin) -> None:
    """改口径后图鉴**仍然是 8 个**（不新增）。"""
    from plugins.mock_stock_meow.cards import ACHIEVEMENTS

    assert len(ACHIEVEMENTS) == 8
    run(plugin.join(mode="infinite", seed=7202))
    repo = Repo(plugin.db)
    _kill_player(repo, "liquidated")
    payload = _value(run(plugin.settle()))
    achievements = payload["achievements"]
    assert achievements["total"] == 8
    assert len(achievements["unlocked"]) + len(achievements["locked"]) == 8


# ------------------------------------------------- ② 错误码走 i18n 查表


def test_plugin_layer_has_no_hardcoded_chinese_error_messages() -> None:
    """插件层**零**硬编码中文错误消息（AST 扫描 ``_err`` 调用）。"""
    tree = ast.parse(_INIT_SOURCE)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "_err":
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any("一" <= ch <= "鿿" for ch in arg.value):
                    offenders.append((node.lineno, arg.value))
    assert not offenders, f"错误码里还写着中文：{offenders}"


def test_every_used_error_code_exists_in_i18n() -> None:
    """每个用到的错误码在 i18n 里都有条目。"""
    table = _i18n()
    codes = set(re.findall(r'_err\(\s*"([a-z_]+)"', _INIT_SOURCE))
    assert len(codes) >= 20, f"扫到的错误码太少：{codes}"
    missing = sorted(code for code in codes if f"error.{code}" not in table)
    assert not missing, f"i18n 缺这些错误码：{missing}"


def test_engine_error_codes_exist_in_i18n() -> None:
    """引擎侧的 5 个错误码也在 i18n 里（引擎消息经查表转发）。"""
    from engine.errors import (
        EngineError,
        InsufficientCashError,
        InvalidAmountError,
        StockNotFoundError,
        TPlusOneError,
    )

    table = _i18n()
    for error in (
        InsufficientCashError,
        InvalidAmountError,
        StockNotFoundError,
        TPlusOneError,
        EngineError,
    ):
        assert f"error.{error.code}" in table, f"i18n 缺 error.{error.code}"


def test_error_message_comes_from_i18n(plugin) -> None:
    """实际报错时，消息**等于** i18n 里的值（不是代码里另写一份）。"""
    table = _i18n()
    run(plugin.join(mode="classic_arcade", seed=7203))
    result = run(plugin.buy("FAKE-not-exist", 1.0))
    assert _error_code(result) == "stock_not_found"
    assert str(result.err()) == table["error.stock_not_found"]


def test_unknown_identity_error_comes_from_i18n(plugin) -> None:
    """另一个错误码也走查表。"""
    table = _i18n()
    result = run(plugin.join(mode="classic_arcade", seed=7204, identity="nope"))
    assert _error_code(result) == "invalid_identity"
    assert str(result.err()) == table["error.invalid_identity"]


# ------------------------------------------------- ③ kline action


def test_kline_action_returns_a_kline(plugin) -> None:
    """按需拉 K 线：任意标的都能拉。"""
    run(plugin.join(mode="classic_arcade", seed=7205))
    # K 线是逐 tick 累积的，先走几个 tick。
    for _ in range(3):
        run(plugin._run_one_tick())
    market = _value(run(plugin.market()))
    stock_id = market["stocks"][0]["id"]
    payload = _value(run(plugin.kline(stock_id)))
    assert payload["stock_id"] == stock_id
    assert isinstance(payload["kline"], list)
    assert payload["kline"], "应该有 K 线点"
    for point in payload["kline"]:
        assert len(point) == 2


def test_kline_action_rejects_an_unknown_stock(plugin) -> None:
    """不存在的标的 → ``stock_not_found``。"""
    run(plugin.join(mode="classic_arcade", seed=7206))
    assert _error_code(run(plugin.kline("FAKE-nope"))) == "stock_not_found"


def test_kline_action_needs_a_game(plugin) -> None:
    """没开局 → ``not_joined``。"""
    assert _error_code(run(plugin.kline("FAKE-000"))) == "not_joined"


def test_kline_action_hides_internal_fields(plugin) -> None:
    """K 线载荷不得出现 heat / seed / 反转日。"""
    run(plugin.join(mode="classic_arcade", seed=7207))
    market = _value(run(plugin.market()))
    stock_id = market["stocks"][0]["id"]
    payload = _value(run(plugin.kline(stock_id)))
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for word in ("heat", "seed", "reversal"):
        assert word not in blob, f"K 线载荷泄漏了内部字段：{word}"


def test_kline_action_is_registered_as_a_ui_action(plugin) -> None:
    """``kline`` 是第 9 个 ``@ui.action``。"""
    meta = getattr(plugin.kline, "__neko_ui_action__", None)
    assert meta is not None
    assert meta["id"] == "kline"


def test_panel_has_the_kline_fetch_button() -> None:
    """面板 K 线区块有「拉这只的K线」按钮。"""
    assert "panel.kline_fetch" in _PANEL_SOURCE
    assert 'actionById("kline")' in _PANEL_SOURCE


# ------------------------------------------------- ④ 仓库化


def test_github_workflows_exist() -> None:
    """官方脚手架的 CI 工作流在位。"""
    verify = _PLUGIN_ROOT / ".github" / "workflows" / "verify.yml"
    release = _PLUGIN_ROOT / ".github" / "workflows" / "release.yml"
    assert verify.is_file(), "缺 .github/workflows/verify.yml"
    assert release.is_file(), "缺 .github/workflows/release.yml"
    assert "mock_stock_meow" in verify.read_text(encoding="utf-8")


def test_ruff_config_and_vscode_exist() -> None:
    """官方脚手架的 ruff 配置与编辑器配置在位。"""
    assert (_PLUGIN_ROOT / "ruff.toml").is_file()
    assert (_PLUGIN_ROOT / ".vscode" / "settings.json").is_file()
    assert (_PLUGIN_ROOT / ".vscode" / "tasks.json").is_file()


def test_gitignore_covers_runtime_dirs() -> None:
    """``.gitignore`` 覆盖 data/ cache/ config/（运行期产物不进仓库）。"""
    text = (_PLUGIN_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("data/", "cache/", "config/", "__pycache__/", ".pytest_cache/"):
        assert pattern in text, f".gitignore 少了 {pattern}"


def test_plugin_has_its_own_git_repository() -> None:
    """插件目录有自己的 git 仓库。"""
    assert (_PLUGIN_ROOT / ".git").exists()


def test_origin_uses_the_market_naming_convention() -> None:
    """origin 指向 ``n.e.k.o_plugin_<plugin_id>``（Market 命名约定）。

    ``.git`` 后缀**可有可无**：本地开发习惯写 ``...n.e.k.o_plugin_mock_stock_meow.git``，
    而 CI 的 ``actions/checkout`` 配的是 ``https://github.com/<owner>/<repo>``（无后缀）。
    两种都是同一个仓库的合法 remote URL，所以断言只钉**仓库名**。
    """
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(_PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "没有配置 origin"
    url = result.stdout.strip()
    assert url.rstrip("/").removesuffix(".git").endswith(
        "n.e.k.o_plugin_mock_stock_meow"
    ), url


def test_gitattributes_pins_lf() -> None:
    """行尾钉死 LF（Windows 开发 + Linux CI）。"""
    text = (_PLUGIN_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=lf" in text


def test_worktree_has_no_crlf_files() -> None:
    """工作区**没有** CRLF 文件（行尾统一 LF）。"""
    result = subprocess.run(
        ["git", "ls-files", "--eol"],
        cwd=str(_PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    crlf = [row for row in result.stdout.splitlines() if "w/crlf" in row]
    assert not crlf, f"这些文件被写成了 CRLF：{crlf}"


# ------------------------------------------------- ⑤ 版本号


def test_version_is_0_7_4() -> None:
    """版本号 = 0.10.5。

    版本史：

    * 0.7.1 —— 删掉 ``plugin.sdk.shared.core`` 私有导入；
    * 0.7.2 —— 去掉运行时**不存在**的 ``CardSubmissionError``；
    * 0.7.3 —— 真机上"表从未建成"（懒建表自愈 + 拉配置重试）；
    * 0.7.4 —— 真机上"tick 不推进"（运行时能力对齐 + 清单兜底 + 卡片能力探测）；
    * 0.8.0 —— P7：改名 ``mock_stock_meow`` + 双版本（导演模式）。
    * 0.8.3 —— P7.3：弹窗防死锁（网贷弹窗逃生通道 + 超时兜底 + 轰炸硬截止）。
    * 0.8.4 —— P7.4：入口打通（面板开局按钮 + ``@llm_tool`` + 轮询自愈）。
    * 0.9.0 —— P8：正式化《拟股喵喵》+ 清丽轻科技风 UI 重构。
    * 0.9.2 —— P8.1：结算页占位符/枚举修复 + K 线开局提示 + 操作区与梗图打磨。
    * 0.9.3 —— P10：新手引导（玩法说明弹窗 + 官方 guide 面）+ 节奏控制。
    * 0.9.4 —— P11：猫娘外置交互引导（主动搭话 + 呼叫猫娘 + 兜底弹窗）。
    * 0.9.5 —— P11.1：并发根治（上下文非阻塞 + 推进节流 + 面板请求锁）。
    * 0.9.6 —— P12.0：剧情引擎（四幕叙事 + 外置 Yui 驱动）+ 交易 GUI 化（滑块仓位）。
    * 0.10.5 —— P21.5：面板白屏根因修复（持仓区块少一层 ``<Stack>`` 包裹 ⇒ 三元 else 分支里塞了两个并列表达式 ⇒ TS 报 ``TS1005``、宿主 sucrase 报错被回退吞掉 ⇒ 真机白屏）+ 新增纯 Python 的 TSX 结构体检守卫。
    * 0.10.6 —— P23.0：极简肾上腺素 UI（全宽巨幅 K 线 + 数字老虎机 + 濒死视觉发病）+ Yui 操控端口（三模式：直播专用 / 辅助式 / 纯看戏）。
    * 0.10.7 —— P23.1：修真机暴露的四个交互 / 渲染 Bug（大 K 线开局塌陷、局中切不了 Yui 模式、Yui 开局误报失败、插件停止后的红错轰炸）。
    * 0.10.8 —— P23.2：直播看板布局（左右 60/40 分栏 + 锁死一屏不滚动 + 节奏与 Yui 模式压成一行工具条 + 买卖按钮改紧凑档 + 妖股优先排序 + 次要信息进【更多】弹窗）。
    * 0.10.9 —— P24.0：Yui 直播托管主动化（每隔 N 拍自动开回合 + 现金/持仓/行情注入 + 三道限流）+【话痨级别】三档（安静/正常/话痨）+ 新闻与重大事件强制插话 + yui_trade 标的模糊匹配 + 授权宣告去复读机化。
    * 0.11.1 —— P26.0：直播防翻车兜底（面板动作 3 秒硬超时 ⇒ 先解锁再弹黄色Toast + ``ErrorBoundary`` 防白屏 + 心跳 / tick 流水线硬超时自愈、卡死即跳拍）+ 群友随机事件（浅忆QanYi「天降 Token」5% 触发、每局上限 3 次；云玩家「免费内参」25% 触发，内参只走 ``push_message`` 给 Yui、面板侧只留一枚「情报：云玩家版」标签）。
    * 0.11.2 —— P26.1：直播护栏（Yui 伪预知「危险感知」：插件层算波动率与持仓集中度 ⇒ 把最极端的公开数据当**事实**喂给 Yui，几拍后给价格对账；``[director] live_god_mode`` 强制续命锁：现金跌破生活费红线时强制垫付（群友浅忆QanYi 天降 Token，独立上限 6 次/局）+ 满仓暴跌血量见底时安排一次「庄家洗盘」假反弹（下一拍 +10%，一局 1 次）；live 回合多两行**事实行**（直播还没收工 + 玩家当下真能用的操作）。引擎层 / 模式层 / 500 局基线**零改动**。
    * 0.11.3 —— P27.0：直播版最终强化（极简做空机制：面板【做空】按钮 + 杠杆倍率 1x/10x/50x + 保证金，标的跌了赚「跌幅 × 倍率 × 保证金」、涨了扣，保证金亏光强制平仓爆仓 —— 整本账在插件层 ``short_book.py``，不动数据库列；Wehos 专属破产彩蛋：名字里含「Wehos」且因破产结算时弹专属卡「你亏光了最后一块钱，被 Yui 强行抓走变成了开发猫。」，不走普通破产画面；玩家 vs 内置猫娘 Yui 的资产迷你折线图：Yui 前 10 拍极差、10 拍后稳定上升、末点必定高于玩家，配文「你的对手是猫娘，你连她都比不过。」；新手教程补「报纸与团队」：开局白拿老爸给的团队与高级报纸，之后要自己买（新增 ``buy_report`` 动作，新手前两档白拿、后面 60 元/份、当天有效、最多 3 份/天，复用既有分析额度机制）。引擎层 / 模式层 / 500 局基线**零改动**。
    * 0.11.4 —— P27.1：紧急补丁（Yui 交易幻觉根治 + 面板极简卡片化）。**幻觉**：``YUI_AUTHORITY_PROMPT`` 加 ``YUI_TRADE_FACTS`` —— 绝对不许在没调用 ``mock_stock_meow_yui_trade``、或者调用了却没拿到成交结果时说自己买了/卖了任何东西；工具报错（找不到标的 / 钱不够 / 没货可卖 / 次数用完）时**照那条错误实说**，严禁编造「买到了哪只票」；成交后推**回执**（X 股 Y 股票、花费 Z 元、账户剩 W 元），失败推**失败回执**，两者末尾都带「这一轮不要再下单」防自触发循环。**面板**：顶部概览卡（总资产 + 赚亏 + 现金红线 + 与猫娘 Yui 的对比）、K 线换成深色圆角画布、玩家 vs Yui 资产曲线压在 K 线正下方、持仓搬到右列、底部全宽**三键操作台**（掏钱买 / 割肉卖 / 做空）+ 上方杠杆保证金输入条、冗余文字全部折叠进【更多】弹窗、K 线高度 240/35vh/450 → 210/26vh/330（守卫同步）。引擎层 / 模式层 / 500 局基线**零改动**。
    * 0.11.5 —— P27.2：终极压测与防追单校验。**防追单（双保险）**：提示词层新增 ``YUI_RECEIPT_CYCLE_DIRECTIVE``（「【系统强制指令】本回合只允许做汇报，严禁再调用任何交易工具，直到玩家下一次主动和你说话。」），授权提示 / 成交回执 / 失败回执 / 直播主动回合**四处同源引用**；代码层新增**回执周期锁**（成交后上锁，玩家下次主动操作 / 下一个 tick / 新开局才开锁，锁住期间第二单直接被拒且**一条消息都不许推**）；失败回执改 ``ai_behavior="read"`` 只进上下文、不触发回合。**配额**：真实上限是 ``YUI_TRADES_PER_DAY = 3`` 单/天（**不是 60** —— 60 是心跳日志间隔与工具重注册间隔），并修掉「配额跨局不归零」的 bug（``join`` 尾部归零）。**出口修复**：玩家被市场请出去之后，面板那枚「紧急逃生」按钮（``declare_bankrupt``）照常收场 —— 保留原本退场原因、照旧出结算报告 / 清场 / 归档；聊天里说「加入一局」也能直接开新局。**压测**：新增 ``tests/stress_live.py``（模拟真实点击流：推进一拍 + 一键梭哈，盯死锁 / 心跳卡死 / UI 渲染异常 / OOM / 追单 / 配额六件事）与 ``tests/test_p272_stress_guard.py``（5 局缩小版进 CI 闸门）。**UI 探针**：``tests/test_p272_ui_probe.py`` 验证 ``#131d33`` 深色画布（``background-color`` 与 ``background-image`` 两条独立声明 ⇒ 渐变失效也不白屏）、圆角不被宿主裁切、1080P 下三键（掏钱买 / 割肉卖 / 做空）三等列不折行。引擎层 / 模式层 / 500 局基线**零改动**。
    * 0.12.0 —— P28.0：终极冲刺（并行开发 + 第一关极度降智 + 直播模式分化）。**第一关降智大砍**：``classic_arcade`` 走**极简 UI**（面板只留超大 K 线图、【掏钱买】【割肉卖】两个大按钮、当前【现钱】）—— 做空按钮、杠杆/保证金输入、多标的台账、资金水位条、账单 Alert、连击/情绪徽章**全部隐藏**；``heat.peak_threshold`` 在插件层被抬到 ``ARCADE_PEAK_THRESHOLD``（引擎层 / 模式层**零改动**）⇒「最上头时暴跌」在第一关**失效**；卖出若亏了由系统**暗中补钱**（补偿额 = 本笔亏损 ⇒ 单笔净盈亏恒为 0）⇒ 玩家在第一关**永远是赚的**。**直播模式分化**：新增第四档 ``YUI_MODE_DUO = "duo"``（**不复用** ``assist``）=【人机协同双打】—— 玩家手动买卖、Yui **一根手指都不碰**（``_yui_gate`` 硬拒交易、``llm_yui_grant`` 连授权窗口都不许开），只疯狂点评、给「云玩家内参」（可能是假的）、亏钱时嘲笑赚钱时吹捧（新模块 ``duo_persona.py`` + ``meow.yui_duo_commentary``）。【Yui 全权代打（纯看戏）】沿用 ``live``。**假新闻的活人感**：新模块 ``fakenews.py`` 在插件层按天记下当天的假新闻；玩家**买过报纸**且当天**真的亏了钱**⇒ 次日开盘把**事实**（几条假的、涉及哪些标的、亏了多少）推给 Yui，她在聊天框里**爆发生气**，替玩家承担被骗的情绪。引擎层 / 模式层 / 500 局基线**零改动**。
    * 0.10.4 —— P21.0：体验减负（渐进式 UI 三档密度 + 全屏沉浸 + 新手兜底幽灵提示 + 术语去金融化 + 名字限 6 字）。
    * 0.10.3 —— P20.0：命名系统与名字彩蛋引擎（名字输入 + 落库 + 四类彩蛋 + AI 名字注入）。
    * 0.10.2 —— P19.0：入门档教程补全（复用街机 4 步 + 第 4 步注入新事实） + 直播特供版两个隐藏开关（force_black_swan / show_progress）。
    * 0.10.1 —— P18.0：分档次动态教程（街机 4 步 / 命运挑战 3 步 / 无尽 6 步含延迟网贷节点 + 动态聚焦 UI + Yui 动态解说 + 结课小结）。
    * 0.10.0 —— P16.0：前两关爽感重塑（连击徽章 + 盈利数字跳动 + 弱化水位条与台账）+ Yui 深度情绪感知（情绪画像喂提示词 + 主动搭话）。
    * 0.9.10 —— P15.0：原创化去品牌化（源码零明文清理旧作专有名词 + 引擎层守卫升级为代码指纹）。
    * 0.9.9 —— P14.1：去死亡化合规整改（玩家可见文案全面去死亡化 + 引擎死因插件层全覆盖映射）。
    * 0.9.8 —— P14.0：资金水位可视化（生活费红线）+ 多标的仓位总限制联动 + 模式引导重构（街机 / 命运挑战 / 无尽）。
    * 0.9.7 —— P13.0：Yui 灵魂化（交还叙事权）+ 交易去量子化（连续仓位 + 直输）。
    """
    from plugins.mock_stock_meow import PLUGIN_VERSION

    assert PLUGIN_VERSION == "0.12.0-live-ultimate"
    toml = (_PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.12.0-live-ultimate"' in toml


# ------------------------------------------------- ⑥ 面板文案全走 i18n


def _strip_tsx_comments(source: str) -> str:
    """去掉 TSX 的块注释与行注释，只留真正会跑的代码。"""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def test_panel_i18n_keys_all_exist() -> None:
    """面板里每一个**静态** ``t("...")`` 键在 i18n 里都有条目。

    动态键（模板字符串）另由 :func:`test_panel_dynamic_i18n_keys_all_exist` 覆盖。
    """
    table = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _PANEL_SOURCE))
    assert used, "面板里没有解析到任何静态 i18n 键，正则可能失效了"
    missing = sorted(key for key in used if key not in table)
    assert not missing, f"面板引用了 i18n 里不存在的键：{missing}"


def test_panel_dynamic_i18n_keys_all_exist() -> None:
    """面板里**动态** ``t(`...`)`` 的取值域在 i18n 里都有条目。"""
    table = _i18n()
    for key in (
        "phase.honeymoon",
        "phase.trap",
        "phase.slaughter",
        "analysis.green",
        "analysis.yellow",
        "analysis.red",
    ):
        assert key in table, f"动态 i18n 键缺失：{key}"

    # P8.1：结算页的**英文枚举**翻译（破产原因 / 入场身份 / 结局）也走动态键，
    # 形态是 ``${prefix}.${key}`` —— 前缀固定，取值由后端给。
    for prefix in ("death_reason", "identity.label", "ending"):
        assert any(key.startswith(prefix + ".") for key in table), (
            f"动态 i18n 前缀 {prefix}. 在词典里一条都没有"
        )

    dynamic = set(re.findall(r"\bt\(\s*`([^`]*)`", _PANEL_SOURCE))
    assert dynamic == {
        'analysis.${analysis.rating}',
        'phase.${state.phase || "honeymoon"}',
        '${prefix}.${key}',
    }, f"面板的动态 i18n 键形态变了，需要同步检查取值域：{dynamic}"


def test_panel_has_no_hardcoded_chinese_outside_comments() -> None:
    """面板**代码**（去注释后）零硬编码中文 —— 文案必须走 i18n。

    中文注释是允许的（这是给维护者看的），硬编码的界面文案不是。
    """
    code = _strip_tsx_comments(_PANEL_SOURCE)
    offenders = [
        (number, line.strip())
        for number, line in enumerate(code.split("\n"), start=1)
        if re.search(r"[\u4e00-\u9fff]", line)
    ]
    assert not offenders, f"面板里有硬编码中文文案：{offenders}"
