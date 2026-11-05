# -*- coding: utf-8 -*-
"""P27.2：玩家被市场请出去之后，**出口必须还在**（不许把人卡死在上一局）。

背景
----
30 局模拟直播压测逼出来的真 bug：玩家交不上饭 / 被低保或网贷请出去之后，
``market_state.player_id`` 还挂着他的席位（只有 ``settle`` / ``declare_bankrupt``
/ 经典 30 天自动结算这三处会清），于是：

* 面板拿到的 ``stage`` 永远停在 ``"playing"`` ⇒ 没有「再来一局」按钮；
* ``join`` 被"你已经在局中了"拒掉；
* 面板那枚「紧急逃生」按钮走的是 ``declare_bankrupt``，而它对已经退场的玩家
  直接报错 ⇒ **唯一的出口也是坏的**。

修法（``__init__.py``）
----------------------
1. ``_do_declare_bankrupt``：玩家**已经退场**时不再报错，而是替他收场 ——
   保留原本的退场原因，照常出结算报告、清场、归档；
2. ``join``：上一局的玩家已经退场 ⇒ 允许开新局（活着的时候仍然拒绝中途换局）。

**刻意不做**的事：不在每次 tick 里自动替退场的玩家结算 —— 那会改掉
"市场还挂着这一局"的既有语义（``status()`` 仍要能查得到他、
经典档第 30 天才自动终局），三条既有守卫（``test_endings`` /
``test_daily``）就是守这个的。
"""

from __future__ import annotations

from typing import Any

from conftest import run
from plugins.mock_stock_meow.storage.repo import Repo


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    return result.unwrap()


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", "") or "")


def _retire_player(plugin: Any, reason: str = "starved") -> None:
    """把玩家直接从库里置为「已退场」（模拟日终结算 / 斩杀的结果）。"""
    run(
        Repo(plugin.db).transaction(
            [
                (
                    "UPDATE players SET alive = 0, death_reason = ? WHERE is_catgirl = 0",
                    (reason,),
                )
            ]
        )
    )


# ---------------------------------------------------------------- 逃生出口


def test_the_escape_button_still_works_for_a_retired_player(plugin) -> None:
    """已经退场的玩家点「紧急逃生」⇒ **照常收场**，不报错。"""
    _value(run(plugin.join(mode="classic_beginner", seed=901)))
    _retire_player(plugin, reason="starved")

    payload = _value(run(plugin.declare_bankrupt()))
    assert isinstance(payload, dict)


def test_escaping_clears_the_market_so_a_new_game_can_start(plugin) -> None:
    """收场之后 ``market_state.player_id`` 被清空 ⇒ 下一局能开。"""
    _value(run(plugin.join(mode="classic_beginner", seed=902)))
    _retire_player(plugin, reason="starved")
    _value(run(plugin.declare_bankrupt()))

    row = run(Repo(plugin.db).fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    assert row["player_id"] == ""


def test_escaping_keeps_the_original_retirement_reason(plugin) -> None:
    """退场原因**不许**被覆盖（他是被市场请出去的，不是自己点破产的）。"""
    _value(run(plugin.join(mode="classic_beginner", seed=903)))
    _retire_player(plugin, reason="liquidated")
    _value(run(plugin.declare_bankrupt()))

    row = run(
        Repo(plugin.db).fetch_one(
            "SELECT death_reason FROM players WHERE is_catgirl = 0"
        )
    )
    assert row["death_reason"] == "liquidated"


def test_the_escape_button_still_archives_the_settlement(plugin) -> None:
    """收场之后要留下结算归档（面板的结算页读它）。"""
    _value(run(plugin.join(mode="classic_beginner", seed=904)))
    _retire_player(plugin, reason="starved")
    _value(run(plugin.declare_bankrupt()))

    payload = run(plugin.market_context())
    assert payload.get("stage") == "finished", payload.get("stage")


# ---------------------------------------------------------------- 开新局


def test_join_over_a_retired_player_starts_a_new_game(plugin) -> None:
    """上一局已经结束了 ⇒ 说「加入一局」直接开新局，不被拒。"""
    _value(run(plugin.join(mode="classic_beginner", seed=905)))
    _retire_player(plugin, reason="starved")
    _value(run(plugin.declare_bankrupt()))

    _value(run(plugin.join(mode="classic_arcade", seed=906)))
    status = _value(run(plugin.status()))
    assert status["alive"] is True


def test_join_over_a_live_player_is_still_refused(plugin) -> None:
    """活着的玩家不许中途换局（既有契约 ``already_joined`` 不变）。"""
    _value(run(plugin.join(mode="classic_beginner", seed=907)))
    assert _error_code(run(plugin.join(seed=908))) == "already_joined"


def test_status_still_reports_a_retired_player(plugin) -> None:
    """退场之后 ``status()`` 仍要查得到他（``alive`` 为假）—— 既有语义不许变。"""
    _value(run(plugin.join(mode="classic_beginner", seed=909)))
    _retire_player(plugin, reason="starved")

    status = _value(run(plugin.status()))
    assert status["alive"] is False
