"""宿主 SDK 导入边界守卫。

**背景（真实启动事故）**：

插件 0.7.0 在真实 N.E.K.O 宿主里启动即崩：

    Plugin 'juliumei_market' lifecycle event 'startup' failed:
    Process crashed: No module named 'plugin.sdk.shared.core.cards'

根因是插件直接 import 了宿主**内部实现** ``plugin.sdk.shared.core.cards``。
在开发机上（源码树可 import）看不出来，一打包进宿主就炸。

宿主 SDK 的根命名空间写得很清楚（``plugin/sdk/__init__.py``）：

    The `shared` subpackage is an internal implementation detail and
    should NOT be imported directly by plugin developers.

插件**只准**从公开门面导入：

| 允许 | 用途 |
|:--|:--|
| ``plugin.sdk.plugin`` | 插件开发主门面（``Ok`` / ``Err`` / ``SdkError`` / 装饰器 / ``CardSubmissionError`` …） |
| ``plugin.sdk`` | SDK 根命名空间（``EVENT_META_ATTR`` 等常量） |
| ``plugin.sdk.adapter`` | 适配器开发门面 |

本文件把这些规则固化成**回归测试**：以后谁再写错一条 import，单测立刻红。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: 插件**唯一**允许的宿主 SDK 导入前缀。
ALLOWED_SDK_PREFIXES = (
    "plugin.sdk.plugin",
    "plugin.sdk.adapter",
    "plugin.sdk",
)

#: 宿主内部实现（插件一律不许碰）。
FORBIDDEN_HOST_ROOTS = {
    "plugin",  # 逐条判定：只允许上面三个公开门面
    "utils",  # 源码树根的工具包（plugin_card_fields 等）
    "main_logic",
    "app",
    "services",
    "core",
    "brain",
    "_types",
}


def _plugin_sources() -> list[Path]:
    """插件目录下全部 Python 源文件（排除缓存）。"""
    return sorted(
        path
        for path in _PLUGIN_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and ".git" not in path.parts
    )


def _absolute_imports(path: Path) -> list[tuple[str, int]]:
    """收集文件里所有**绝对** import 的完整模块名与行号（相对 import 不计）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import（from . import x）—— 插件内部自用，合法
                continue
            if node.module:
                found.append((node.module, node.lineno))
    return found


def _is_allowed(module: str) -> bool:
    if not module.startswith("plugin."):
        return True  # 非宿主 SDK 的绝对 import（标准库 / pytest 等），不归本测试管
    return any(module == prefix or module.startswith(prefix + ".") for prefix in ALLOWED_SDK_PREFIXES)


def test_no_private_sdk_imports_anywhere() -> None:
    """**全项目**扫描：不得 import 宿主内部实现（``plugin.sdk.shared.core`` 等）。"""
    offenders: list[str] = []
    for path in _plugin_sources():
        relative = path.relative_to(_PLUGIN_ROOT).as_posix()
        for module, lineno in _absolute_imports(path):
            root = module.split(".")[0]
            if root not in FORBIDDEN_HOST_ROOTS:
                continue
            if not _is_allowed(module):
                offenders.append(f"{relative}:{lineno} -> {module}")
    assert not offenders, (
        "插件不得导入宿主内部实现（只能走公开门面 plugin.sdk.plugin / plugin.sdk）："
        f"{offenders}"
    )


def test_shared_subpackage_is_never_imported() -> None:
    """``plugin.sdk.shared`` 一个字都不能出现（它在宿主里是内部实现）。"""
    offenders: list[str] = []
    for path in _plugin_sources():
        relative = path.relative_to(_PLUGIN_ROOT).as_posix()
        for module, lineno in _absolute_imports(path):
            if module == "plugin.sdk.shared" or module.startswith("plugin.sdk.shared."):
                offenders.append(f"{relative}:{lineno} -> {module}")
    assert not offenders, f"不得导入 plugin.sdk.shared.*：{offenders}"


def test_cards_are_created_through_the_public_context_api() -> None:
    """创建卡片**必须**走 ``self.ctx.create_card(...)``。

    历史事故：曾直接 import ``plugin.sdk.shared.core.cards.create_card``，
    导致打包后在真实宿主里启动即崩。
    """
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ① 不能再有裸 ``create_card`` 的自由函数导入
    bare_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith(".cards")
        and any(alias.name == "create_card" for alias in node.names)
    ]
    assert not bare_imports, f"不得导入 create_card 自由函数：{bare_imports}"

    # ② 必须真的调到了 ctx 的方法上
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_card"
    ]
    assert calls, "插件应当通过 ctx.create_card(...) 创建卡片"
    for call in calls:
        assert isinstance(call.func.value, ast.Attribute), (
            f"create_card 必须是 ctx 上的方法调用（行 {call.lineno}）"
        )
        assert call.func.value.attr == "ctx", (
            f"create_card 应当挂在 ctx 上（行 {call.lineno}）"
        )


def test_card_submission_error_comes_from_the_public_facade() -> None:
    """``CardSubmissionError`` 必须从公开门面 ``plugin.sdk.plugin`` 导入。"""
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    facades = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "plugin.sdk.plugin"
    ]
    assert facades, "应当从 plugin.sdk.plugin 导入"
    imported = {
        alias.name for node in facades for alias in node.names
    }
    assert "CardSubmissionError" in imported, (
        "CardSubmissionError 应当从 plugin.sdk.plugin 导入"
    )


def test_plugin_imports_without_the_private_shared_subpackage() -> None:
    """在**没有** ``plugin.sdk.shared`` 的环境里，插件必须照样能 import。

    这是 0.7.0 启动事故的**复现脚本**。

    真实宿主只提供**公开门面**（``plugin.sdk.plugin`` 等），``plugin.sdk.shared``
    是宿主自己的内部实现，不会暴露给插件进程。0.7.0 的插件代码直引
    ``plugin.sdk.shared.core.cards`` → 打包进宿主后启动即崩：

        Process crashed: No module named 'plugin.sdk.shared.core.cards'

    本测试把 ``plugin.sdk.shared.*`` 从 ``sys.modules`` 里摘掉并挂上拦截器，
    再重新 import 插件 —— 模拟"宿主只给公开门面"的受限环境。
    """
    import importlib
    import sys

    # ① 公开门面先加载进来（它内部会用到 shared，但那是宿主自己的事）。
    import plugin.sdk.plugin  # noqa: F401

    private_names = [name for name in sys.modules if name.startswith("plugin.sdk.shared")]
    saved_private = {name: sys.modules[name] for name in private_names}
    for name in private_names:
        del sys.modules[name]

    plugin_names = [
        name for name in sys.modules if name.startswith("plugins.juliumei_market")
    ]
    saved_plugin = {name: sys.modules[name] for name in plugin_names}
    for name in plugin_names:
        del sys.modules[name]

    class _BlockPrivateShared:
        """拦截一切 ``plugin.sdk.shared*`` 的导入。"""

        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith("plugin.sdk.shared"):
                raise ModuleNotFoundError(f"No module named {fullname!r}")
            return None

    blocker = _BlockPrivateShared()
    sys.meta_path.insert(0, blocker)
    try:
        module = importlib.import_module("plugins.juliumei_market")
        assert module.PLUGIN_VERSION
        assert module.JuliumeiMarketPlugin is not None
    except ModuleNotFoundError as error:  # pragma: no cover - 回归时才会走到
        raise AssertionError(
            f"插件在只有公开门面的宿主环境里 import 失败：{error}"
        ) from error
    finally:
        sys.meta_path.remove(blocker)
        # 还原现场，不污染其它测试
        for name in list(sys.modules):
            if name.startswith("plugins.juliumei_market"):
                del sys.modules[name]
        sys.modules.update(saved_plugin)
        sys.modules.update(saved_private)
