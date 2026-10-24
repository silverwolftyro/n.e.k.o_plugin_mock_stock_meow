"""P2 测试夹具。

三件事
------
1. 让 pytest 能直接 ``import engine``（插件根目录加入 ``sys.path``）——
   这是 P1 引擎层单测的既有约定。
2. 让 ``import plugins.mock_stock_meow`` 可用：把 ``plugins/`` 的父目录加入
   ``sys.path``，``plugins`` 成为一个 **PEP 420 命名空间包**。这与宿主加载
   插件的方式一致（``plugin/core/host.py`` 要求 ``parts[0] == "plugins"``），
   因此本插件内部一律使用**相对导入**（``from .engine...`` / ``from ..engine...``），
   在测试里也能原样复现。
3. 提供假 ctx + 已建表的插件实例，让入口测试跑在**真实的**宿主 SDK 存储之上
   （``self.db`` / ``self.state`` 由 ``NekoPluginBase.__init__`` 创建，
   插件不得自己 new）。数据根目录由 ``NEKO_STORAGE_SELECTED_ROOT`` 隔离到
   ``tmp_path``，绝不污染真实用户数据。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Coroutine

import pytest

#: 插件自身目录（``.../plugins/mock_stock_meow``）。
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
#: ``plugins/`` 命名空间包的父目录（``.../plugin``）。
_PLUGINS_PARENT = _PLUGIN_ROOT.parents[1]

for _path in (str(_PLUGIN_ROOT), str(_PLUGINS_PARENT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 宿主 SDK（``plugin.sdk.plugin``）由 venv 的可编辑安装提供；源码树不在
# ``sys.path`` 上时给出明确报错，而不是让人猜。
try:  # pragma: no cover - 仅在环境异常时触发
    import plugin.sdk.plugin  # noqa: F401
except ModuleNotFoundError as error:  # pragma: no cover
    raise RuntimeError(
        "宿主 SDK 不可用：请用 N.E.K.O-main/.venv 的 python 运行测试"
    ) from error


__all__ = ["FakeCtx", "make_plugin", "run"]


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    """同步地跑一个协程（避免依赖 pytest-asyncio）。"""
    return asyncio.run(coro)


class FakeCtx:
    """最小可用的宿主 ctx。

    只实现插件真正用到的那几个成员：``plugin_id`` / ``logger`` /
    ``config_path`` / ``metadata`` / ``bus`` / ``_effective_config`` /
    ``get_own_config``。其余成员由 SDK 的 :class:`SdkContext` 兜底。
    """

    plugin_id = "mock_stock_meow"
    logger = None

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or Path("mock_stock_meow.toml")
        self.metadata: dict[str, Any] = {}
        self.bus: dict[str, Any] = {}
        self._effective_config: dict[str, Any] = {
            "plugin": {
                "store": {"enabled": True},
                "database": {"enabled": True, "name": "mock_stock_meow.db"},
            },
            "plugin_state": {"backend": "file"},
        }
        self.config_payload: dict[str, Any] = {}

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": self.config_payload}

    async def update_own_config(
        self, updates: dict[str, Any], timeout: float = 10.0
    ) -> dict[str, Any]:
        self.config_payload.update(updates)
        return {"config": self.config_payload}

    async def get_system_config(self, timeout: float = 5.0) -> dict[str, Any]:
        return {"config": {}}


def make_plugin(ctx: FakeCtx) -> Any:
    """按宿主的加载方式实例化插件（``plugins.mock_stock_meow`` 包内导入）。"""
    from plugins.mock_stock_meow import MockStockMeowPlugin

    return MockStockMeowPlugin(ctx)


@pytest.fixture()
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCtx:
    """假 ctx + 隔离的数据根目录。"""
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    return FakeCtx(config_path=tmp_path / "plugin.toml")


@pytest.fixture()
def plugin(ctx: FakeCtx) -> Any:
    """已执行 startup（建表完成）的插件实例。"""
    instance = make_plugin(ctx)
    result = run(instance.on_startup())
    assert result.is_ok()
    return instance
