# -*- coding: utf-8 -*-
"""P34.0：把宿主的**官方幽灵光标**素材转成内联 base64（面板可直接用）。

素材来源（宿主仓库，官方美术资源）::

    static/assets/tutorial/ghost-cursor/default-ghost-cursor.png   （常态猫爪）
    static/assets/tutorial/ghost-cursor/click-ghost-cursor.png     （点击猫爪）

为什么必须是 **data URI**（而不是 ``<img src="./assets/xxx.png">``）：
面板跑在宿主给的 ``srcdoc`` + ``sandbox="allow-scripts"`` iframe 里，
那是 **opaque origin**；相对路径会落到宿主页面（``/ui/plugins/...``）上，
宿主**不服务插件目录** ⇒ 必然 404 ⇒ 破图。只有内联的 data URI 才保证显示。
（这条规矩是 P8.0 立下的，见 ``tools/gen_assets.py``。）

用法::

    python tools/gen_ghost_assets.py

会写出：

* ``ui/assets/ghost-cursor-default.png`` —— 常态猫爪（源文件，人可读）；
* ``ui/assets/ghost-cursor-click.png``   —— 点击猫爪（源文件）；
* ``ui/assets/ghost_cursor.ts``          —— 上面两张图的 **base64 data URI**。

授权：宿主仓库的美术资源随主仓协议分发（NOTICE 记载为 Apache License 2.0），
老板已授权本插件内嵌使用；这里保留源文件与出处注释，便于追溯。

⚠ 行尾纪律：一律**按字节**写（``newline="\\n"``）——
``write_text`` 在 Windows 上默认会把 LF 写成 CRLF，当场触发
``test_worktree_has_no_crlf_files``。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ASSETS = Path(__file__).resolve().parents[1] / "ui" / "assets"

#: 宿主仓库里那两张图的**相对路径**。
HOST_RELATIVE = Path("static") / "assets" / "tutorial" / "ghost-cursor"

#: 源文件名 → 插件里的文件名。
COPY_MAP = {
    "default-ghost-cursor.png": "ghost-cursor-default.png",
    "click-ghost-cursor.png": "ghost-cursor-click.png",
}


def _has_materials(root: Path) -> bool:
    return (root / HOST_RELATIVE).is_dir()


def _host_root() -> Path:
    """宿主源码树根目录（找不到就报错，别瞎猜）。"""
    env = os.environ.get("NEKO_HOST_ROOT")
    if env:
        candidate = Path(env)
        if _has_materials(candidate):
            return candidate
        raise SystemExit(f"[FAIL] NEKO_HOST_ROOT 指向的目录里没有素材：{candidate}")

    # 候选一：宿主 SDK（由 N.E.K.O-main/.venv 以可编辑方式安装）。
    candidates: list[Path] = []
    try:
        import plugin  # 宿主 SDK

        sdk_path = Path(plugin.__file__).resolve()
        candidates.extend(sdk_path.parents[:4])
    except Exception as error:  # noqa: BLE001 - 没装 SDK 就换一条路找
        print(f"[WARN] 宿主 SDK 不可用（{error}），改为从磁盘上找")

    # 候选二：本插件所在的工作区往上（开发机上是
    # ``<workspace>/neko-猫娘股市/plugin/plugins/mock_stock_meow``）。
    candidates.extend(Path(__file__).resolve().parents[:8])

    for candidate in candidates:
        if _has_materials(candidate):
            return candidate
    raise SystemExit(
        "[FAIL] 找不到宿主素材目录 "
        f"（找过 {len(candidates)} 个候选根）；"
        "请设环境变量 NEKO_HOST_ROOT 指向 N.E.K.O-main 仓库根目录"
    )


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    root = _host_root()
    source_dir = root / HOST_RELATIVE
    print(f"--- 宿主素材目录：{source_dir}")

    ASSETS.mkdir(parents=True, exist_ok=True)
    uris: list[tuple[str, str]] = []
    for host_name, local_name in COPY_MAP.items():
        source = source_dir / host_name
        if not source.is_file():
            print(f"[FAIL] 缺素材：{source}")
            return 2
        target = ASSETS / local_name
        shutil.copyfile(source, target)
        print(f"  {host_name} -> {local_name}  ({target.stat().st_size} bytes)")
        uris.append((local_name, _data_uri(target)))

    lines = [
        "/**",
        " * P34.0：官方「幽灵猫爪」引导的**内联素材**（编译产物，勿手改）。",
        " *",
        " * 来源：宿主仓库 ``static/assets/tutorial/ghost-cursor/`` 的官方美术资源",
        " * （老板已授权内嵌使用；随主仓协议分发，NOTICE 记为 Apache License 2.0）。",
        " *",
        " * 为什么是 data URI：面板跑在 ``srcdoc`` + ``sandbox=\"allow-scripts\"`` 的",
        " * opaque origin iframe 里，相对路径会 404 ⇒ 破图（见 ``tools/gen_assets.py``）。",
        " *",
        " * 重新生成：``python tools/gen_ghost_assets.py``。",
        " *",
        " * 这里**不含任何违禁元素**。",
        " */",
        "",
        "/** 常态猫爪（默认指针）。 */",
        f"export const GHOST_CURSOR_DEFAULT = {json.dumps(uris[0][1])}",
        "",
        "/** 点击猫爪（落到目标上的那一瞬）。 */",
        f"export const GHOST_CURSOR_CLICK = {json.dumps(uris[1][1])}",
        "",
    ]
    (ASSETS / "ghost_cursor.ts").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"  ghost_cursor.ts: {(ASSETS / 'ghost_cursor.ts').stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
