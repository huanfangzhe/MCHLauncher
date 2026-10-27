# -*- mode: python ; coding: utf-8 -*-
# 开发者：幻方者（https://space.bilibili.com/3546815194467241）
"""PyInstaller 打包配置（单文件 exe）。

构建：
    python -m PyInstaller MCHLauncherForPy.spec --noconfirm --clean --distpath exe

产物：exe/MCHLauncherForPy.exe
（build.bat 会在打包后按 main.py 里的 APP_VERSION 重命名为
 exe/MCHLauncherForPy_<版本>.exe，例如 exe/MCHLauncherForPy_1.1.1.exe）

体积相关的开关（环境变量）：
    MCHL_FULL_DOTNET=1  保留 pythonnet 的 CoreCLR 程序集（默认已裁掉，约省 2.5MB）
    MCHL_ONEDIR=1       产出文件夹版（启动更快，整体体积略大）
    MCHL_CONSOLE=1      产出带控制台的调试版
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller 注入
ICON = ROOT / "图标.ico"

# 设置 MCHL_CONSOLE=1 会额外产出一个带控制台的调试版本，方便看报错
DEBUG_CONSOLE = os.environ.get("MCHL_CONSOLE") == "1"
EXE_NAME = "MCHLauncherForPy-debug" if DEBUG_CONSOLE else "MCHLauncherForPy"

# 设置 MCHL_ONEDIR=1 产出“文件夹版”：不用每次启动都把 28MB 解包到临时目录，
# 启动可快约 0.4s（实测），代价是分发时要整个文件夹一起拷。
ONEDIR = os.environ.get("MCHL_ONEDIR") == "1"

# 随 exe 一起打包的静态资源
WEB_DIR = ROOT / "web"

# 这些图片启动时由 _sync_web_images() 自动生成，或属于用户自己的背景图，不打进包
SKIP_WEB_IMAGES = {
    "app-icon.png",
    "app-icon.ico",
    "steve.png",
    "background.jpg",
    "background.jpeg",
    "background.png",
}


def collect_web() -> list[tuple[str, str]]:
    """按文件收集 web 目录，跳过运行时可再生的图片。"""
    items: list[tuple[str, str]] = []
    for path in sorted(WEB_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.parent.name == "img" and path.name.lower() in SKIP_WEB_IMAGES:
            continue
        rel_dir = path.relative_to(WEB_DIR).parent.as_posix()
        dest = "web" if rel_dir == "." else "web/" + rel_dir
        items.append((str(path), dest))
    return items


datas = collect_web() + [
    (str(ICON), "."),
    (str(ROOT / "图标.png"), "."),
]
pictures = ROOT / "Pictures"
if pictures.is_dir():
    datas.append((str(pictures), "Pictures"))

# 去掉用不到的大块依赖，减小体积（体积越小，单文件启动解包越快）
excludes = [
    # GUI / 绘图
    "tkinter",
    "_tkinter",
    "turtle",
    "turtledemo",
    "idlelib",
    "PIL",
    "numpy",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "IPython",
    # 开发 / 测试工具
    "pytest",
    "unittest",
    "doctest",
    "pydoc",
    "pydoc_data",
    "pdb",
    "test",
    "setuptools",
    "pkg_resources",
    "pip",
    "wheel",
    "ensurepip",
    "lib2to3",
    "xmlrpc",
    "cProfile",
    "pstats",
    # 启动器用不到
    "sqlite3",
    "curses",
    # 标准库中未被使用的大件
    # 注意：html / calendar 被 pywebview 的 bottle 间接使用，不能排除
    "asyncio",
    "multiprocessing",
    "decimal",
    "statistics",
    "fractions",
    "zoneinfo",
    "xml",
    "xml.etree",
    "bz2",
    "lzma",
    "argparse",
    "optparse",
    "getopt",
    "csv",
    "difflib",
]

analysis = Analysis(  # noqa: F821
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    # 2：去掉 docstring / assert，pyc 更小
    optimize=2,
)

# pythonnet 自带的 XML 文档只给 IDE 看，运行不需要，剔除以减小体积
analysis.datas = [  # noqa: F821
    item
    for item in analysis.datas  # noqa: F821
    if not (
        str(item[0]).lower().endswith(".xml")
        and "pythonnet" in str(item[0]).lower()
    )
]

# ---------------------------------------------------------------------------
# 二次瘦身（实测可省约 2.5MB 解包体积）
#  pythonnet/runtime 里绝大部分是 CoreCLR 用的 System.*.dll；Windows 上
#  pythonnet 走 .NET Framework（Win10 1903+ 自带 4.8），这些程序集用不到，
#  只留 Python.Runtime.dll 与它的 deps.json。
#
#  注意：不要动 webview/lib/runtimes 下的 win-x64 / win-x86 / win-arm64。
#  pywebview 会按 RID 逐个探测 WebView2Loader.dll，少一个就会报
#  “Cannot find win-arm64”，界面直接起不来。
#
#  需要保留 CoreCLR 依赖时：set MCHL_FULL_DOTNET=1 再打包。
# ---------------------------------------------------------------------------
KEEP_CORECLR = os.environ.get("MCHL_FULL_DOTNET") == "1"
KEEP_RUNTIME_FILES = {"python.runtime.dll", "python.runtime.deps.json"}


def _slim_item(item) -> bool:
    dest = "/" + str(item[0]).replace("\\", "/").lower()
    name = dest.rsplit("/", 1)[-1]
    if "/pythonnet/runtime/" in dest:
        return name in KEEP_RUNTIME_FILES
    return True


if not KEEP_CORECLR:
    analysis.binaries = [  # noqa: F821
        item for item in analysis.binaries if _slim_item(item)  # noqa: F821
    ]
    analysis.datas = [  # noqa: F821
        item for item in analysis.datas if _slim_item(item)  # noqa: F821
    ]

pyz = PYZ(analysis.pure)  # noqa: F821

if ONEDIR:
    exe = EXE(  # noqa: F821
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=EXE_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=DEBUG_CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ICON) if ICON.is_file() else None,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name=EXE_NAME,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=EXE_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,  # 开了 UPX 解压更慢，反而拖慢启动
        runtime_tmpdir=None,
        console=DEBUG_CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ICON) if ICON.is_file() else None,
    )
