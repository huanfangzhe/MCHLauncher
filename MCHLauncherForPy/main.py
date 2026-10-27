"""MCHLauncherForPy —— 基于 Python + HTML 的《我的世界》国际版启动器（界面原型）。

作者：幻方者
哔哩哔哩：https://space.bilibili.com/3546815194467241

启动器启动时会自动在自身所在目录下创建 .minecraft 目录，作为游戏数据
（版本、资源、日志等）的存放位置。界面由 web/ 目录下的 HTML/CSS/JS 实现，通过
pywebview 嵌入系统 WebView2 渲染。
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

try:
    import webview
except ImportError:  # pragma: no cover - 仅在缺少依赖时触发
    sys.exit(
        "未找到 pywebview 依赖，请先执行：\n"
        "    pip install -r requirements.txt"
    )

import authlib_auth
import bedrock_manage
import game_launcher
import java_runtime
import loader_install
import mod_download
import mod_manage
import modpack_install
import ms_auth
import runtime_utils
import skin_avatar
import version_download

APP_NAME = "MCHLauncherForPy"
# 开发者：幻方者（B 站主页）
AUTHOR_NAME = "幻方者"
AUTHOR_URL = "https://space.bilibili.com/3546815194467241"
# 版本号同时用作界面资源缓存的标记：改版本会让打包版重新解出 web 目录
APP_VERSION = "1.1.1"
WINDOW_TITLE = APP_NAME
WINDOW_WIDTH = 1120
WINDOW_HEIGHT = 760
WINDOW_MIN_SIZE = (980, 680)
WINDOW_BG_COLOR = "#cfe8f2"
DATA_DIR_NAME = ".minecraft"

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

if getattr(sys, "frozen", False):
    # PyInstaller 等打包后的运行目录：exe 所在目录（.minecraft 等数据仍放这里）
    BASE_DIR = Path(sys.executable).resolve().parent
    # 随 exe 打进包里的只读资源解包在临时目录
    RES_DIR = Path(getattr(sys, "_MEIPASS", str(BASE_DIR)))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RES_DIR = BASE_DIR


def _cache_dir() -> Path:
    """打包运行时用来存放解出来的界面资源（可写、可持久）。"""
    root = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or str(Path.home())
    )
    return Path(root) / APP_NAME


def _web_bundle_marker(web_root: Path) -> str:
    """按内置 web 的内容算指纹。

    以前只比对版本号，同版本号下改了界面（重新打包）会被旧缓存挡住，
    现在内容一变就会重新解包，不用为了刷新缓存刻意改版本号。
    """
    digest = hashlib.md5()
    try:
        files = sorted(item for item in web_root.rglob("*") if item.is_file())
    except OSError:
        return f"{APP_VERSION}-unknown"
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(path.relative_to(web_root).as_posix().encode("utf-8"))
        digest.update(str(len(data)).encode("utf-8"))
        digest.update(hashlib.md5(data).digest())
    return f"{APP_VERSION}-{digest.hexdigest()[:12]}"


def _prepare_web_dir() -> Path:
    """返回界面资源目录。

    源码运行时直接用项目里的 web；打包后把内置 web 解到用户目录，
    这样自定义背景图等写入不会落进随时会被清理的临时解包目录。
    """
    local_web = BASE_DIR / "web"
    if not getattr(sys, "frozen", False):
        return local_web

    bundled = RES_DIR / "web"
    if not bundled.is_dir():
        return local_web

    target = _cache_dir() / "web"
    marker = target / ".bundle-version"
    expected = _web_bundle_marker(bundled)
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected:
            return target
    except OSError:
        pass

    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, target, dirs_exist_ok=True)
        marker.write_text(expected, encoding="utf-8")
        return target
    except OSError:
        return bundled


WEB_DIR = _prepare_web_dir()
INDEX_HTML = WEB_DIR / "index.html"
# 页面可访问的图片目录（pywebview 内置服务器只映射 web 目录）
WEB_IMG_DIR = WEB_DIR / "assets" / "img"

# 版本目录结构：.minecraft/versions/<版本名>/MCHL/Setup.ini
# 版本内的 MCHL 目录是 PCL 等启动器的通用约定，保持不变
SETUP_SUBDIR = "MCHL"
SETUP_FILE_NAME = "Setup.ini"
LEGACY_DATA_DIR_NAME = "MCHL"
PICTURES_DIR = BASE_DIR / "Pictures"
# 打包内置的预设图标（exe 旁边没有 Pictures 时使用）
BUNDLED_PICTURES_DIR = RES_DIR / "Pictures"


def picture_path(file_name: str) -> Path:
    """优先取 exe 目录 / 项目目录里的图片，其次取打包内置的。"""
    for folder in (PICTURES_DIR, BUNDLED_PICTURES_DIR):
        candidate = folder / file_name
        if candidate.is_file():
            return candidate
    return PICTURES_DIR / file_name


def resource_file(*parts: str) -> Path | None:
    """按“程序目录 → 打包资源目录”的顺序查找随程序附带的文件。"""
    for base in (BASE_DIR, RES_DIR):
        candidate = base.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


# ---- 运行环境兼容性（Windows 7 及以上） -----------------------------
# WebView2 运行时的客户端 GUID（EdgeUpdate 用它登记已安装版本）
WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# 微软官方常青版安装引导程序（Win7 SP1 请改用 109 固定版）
WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_DOC_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def windows_version() -> tuple[int, int, int]:
    """返回 (major, minor, build)，取不到时返回 (0, 0, 0)。"""
    try:
        info = sys.getwindowsversion()
        return int(info.major), int(info.minor), int(info.build)
    except Exception:  # noqa: BLE001 - 非 Windows 或接口异常
        return (0, 0, 0)


def windows_display_name() -> str:
    """把版本号翻成好读的系统名。"""
    major, minor, build = windows_version()
    if major == 0:
        return "未知系统"
    if major == 10 and build >= 22000:
        return "Windows 11"
    if major == 10:
        return "Windows 10"
    if major == 6 and minor == 3:
        return "Windows 8.1"
    if major == 6 and minor == 2:
        return "Windows 8"
    if major == 6 and minor == 1:
        return "Windows 7"
    if major == 6 and minor == 0:
        return "Windows Vista"
    return f"Windows {major}.{minor}"


def webview2_runtime_version() -> str:
    """读取已安装的 WebView2 运行时版本，未安装时返回空字符串。"""
    try:
        import winreg
    except ImportError:  # 非 Windows 环境
        return ""
    bases = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    )
    for root, base in bases:
        try:
            with winreg.OpenKey(root, f"{base}\\{WEBVIEW2_CLIENT_GUID}") as key:
                value, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        text = str(value or "").strip()
        if text and text != "0.0.0.0":
            return text
    return ""


def show_message(text: str, title: str = "", flags: int = 0x00000040) -> int:
    """弹出系统消息框；窗口还没起来时也能用。返回按钮编号。"""
    caption = title or APP_NAME
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, caption, flags))
    except Exception:  # noqa: BLE001 - 极简环境退化成控制台输出
        print(f"[{APP_NAME}] {text}")
        return 0


def prepare_webview_gui() -> str:
    """决定界面渲染引擎，缺失运行时时给出可执行的提示。

    界面依赖 WebView2 渲染，IE 内核（mshtml）撑不起这套样式，
    所以这里不静默降级，而是明确提示去安装。
    """
    if not sys.platform.startswith("win"):
        return ""

    if webview2_runtime_version():
        return "edgechromium"

    extra = ""
    if windows_version()[:2] == (6, 1):
        extra = "\n提示：Windows 7 只能使用 109 及更早版本的 WebView2 运行时。"
    answer = show_message(
        "未检测到 Microsoft Edge WebView2 运行时，启动器界面无法显示。\n\n"
        f"当前系统：{windows_display_name()}{extra}\n\n"
        "是否现在打开官方下载页面？安装完成后重新打开启动器即可。",
        flags=0x00000004 | 0x00000040,  # MB_YESNO | MB_ICONINFORMATION
    )
    if answer == 6:  # IDYES
        try:
            webbrowser.open(WEBVIEW2_DOWNLOAD_URL)
        except Exception:  # noqa: BLE001 - 打不开浏览器就算了
            pass
    return "edgechromium"

# 版本图标：预设图标放在 Pictures 目录，自定义图标保存在版本的 MCHL 目录
ICON_PRESET_FILES = {
    "grass": "grass.png",
    "fabric": "fabric.png",
    "forge": "forge.png",
}
ICON_FILE_STEM = "icon"
ICON_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
ICON_MAX_BYTES = 2 * 1024 * 1024


def _repair_legacy_path(raw: str) -> str:
    """把仍指向旧版 MCHL 数据目录的路径修正为新的数据目录。"""
    value = (raw or "").strip()
    if not value:
        return ""
    path = Path(value)
    if path.exists():
        return value
    legacy_dir = BASE_DIR / LEGACY_DATA_DIR_NAME
    if DATA_DIR == legacy_dir:
        return value
    try:
        relative = path.relative_to(legacy_dir)
    except ValueError:
        return value
    candidate = DATA_DIR / relative
    return str(candidate) if candidate.exists() else value


def _migrate_state_paths(state_file: Path, legacy: Path, target: Path) -> None:
    """把 launcher.json 中指向旧数据目录的绝对路径改写到新目录。"""
    try:
        raw = state_file.read_text(encoding="utf-8")
    except OSError:
        return
    old_prefix = str(legacy)
    if old_prefix.lower() not in raw.lower():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return

    new_prefix = str(target)

    def fix(value: object) -> object:
        if isinstance(value, str):
            if value.lower().startswith(old_prefix.lower()):
                return new_prefix + value[len(old_prefix):]
            return value
        if isinstance(value, dict):
            return {key: fix(item) for key, item in value.items()}
        if isinstance(value, list):
            return [fix(item) for item in value]
        return value

    try:
        state_file.write_text(
            json.dumps(fix(data), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _resolve_data_dir(base: Path, name: str) -> Path:
    """确定数据目录：优先新名字，旧版 MCHL 存在时自动迁移。"""
    target = base / name
    legacy = base / LEGACY_DATA_DIR_NAME
    if target.exists() or not legacy.is_dir():
        return target
    try:
        legacy.rename(target)
    except OSError:
        # 迁移失败（目录被占用等）时继续使用旧目录
        return legacy
    _migrate_state_paths(target / "launcher.json", legacy, target)
    return target


DATA_DIR = _resolve_data_dir(BASE_DIR, DATA_DIR_NAME)
VERSIONS_DIR = DATA_DIR / "versions"
STATE_FILE = DATA_DIR / "launcher.json"
HOME_FILE = DATA_DIR / "home.json"
JAVA_DIR = DATA_DIR / "java"
# 基岩版安装包下载目录（按版本号分文件夹存放）
BEDROCK_DIR = DATA_DIR / "bedrock"
LIBRARIES_DIR = DATA_DIR / "libraries"
ASSETS_DIR = DATA_DIR / "assets"
LOGS_DIR = DATA_DIR / "logs"

# 全局设置（内存、玩家名、窗口模式、Java 路径）存放在数据目录下的 MCHL 文件夹
GLOBAL_SETTINGS_DIR = DATA_DIR / "MCHL"
GLOBAL_SETUP_FILE = GLOBAL_SETTINGS_DIR / SETUP_FILE_NAME
# 正版登录信息（含 refresh token，切勿外传）
AUTH_FILE = GLOBAL_SETTINGS_DIR / "auth.json"
# 第三方外置登录信息（含 accessToken，切勿外传）
AUTHLIB_FILE = GLOBAL_SETTINGS_DIR / "authlib.json"
# 外置登录需要的 authlib-injector 组件
AUTHLIB_INJECTOR_FILE = GLOBAL_SETTINGS_DIR / "authlib-injector.jar"
# 正版皮肤与头像缓存
SKINS_DIR = GLOBAL_SETTINGS_DIR / "skins"
# 正版档案（名字 / 皮肤地址）的同步间隔，避免每次刷新头像都请求 Mojang
PROFILE_SYNC_INTERVAL = 300

# Setup.ini 中与模组加载器相关的字段
LOADER_FIELDS = (
    ("VersionForge", "Forge"),
    ("VersionNeoForge", "NeoForge"),
    ("VersionFabric", "Fabric"),
    ("VersionLegacyFabric", "Legacy Fabric"),
    ("VersionQuilt", "Quilt"),
    ("VersionLiteLoader", "LiteLoader"),
    ("VersionCleanroom", "Cleanroom"),
    ("VersionLabyMod", "LabyMod"),
    ("VersionOptiFine", "OptiFine"),
)

# 没有 Setup.ini 时，从版本 JSON 的 id / mainClass 里推断加载器
LOADER_NAME_HINTS = (
    ("neoforge", "NeoForge"),
    ("forge", "Forge"),
    ("fabric", "Fabric"),
    ("quilt", "Quilt"),
    ("optifine", "OptiFine"),
    ("liteloader", "LiteLoader"),
    ("cleanroom", "Cleanroom"),
)


def ensure_data_dir() -> Path:
    """在运行目录下创建 .minecraft 数据目录与其 MCHL 设置目录。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        GLOBAL_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return DATA_DIR


FORBIDDEN_NAME_CHARS = '<>:"/\\|?*'
WINDOW_MODES = ("default", "launcher", "maximized", "minimized")
LOGIN_MODES = ("offline", "microsoft", "authlib")
# 进入启动器时默认显示的板块
DEFAULT_VIEWS = ("home", "launch", "bedrock", "download", "settings")


def _version_parts(version: str) -> tuple:
    """把版本号拆成可比较的元组（不足四位补 0）。"""
    parts: list[int] = []
    for chunk in str(version or "").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def validate_version_name(name: str) -> str:
    """校验版本名，合法时返回空字符串。"""
    text = (name or "").strip()
    if not text:
        return "版本名不能为空"
    if len(text) > 64:
        return "版本名不能超过 64 个字符"
    if text in (".", ".."):
        return "版本名不合法"
    if any(char in FORBIDDEN_NAME_CHARS for char in text):
        return '版本名不能包含 < > : " / \\ | ? * 等字符'
    if text.endswith("."):
        return "版本名不能以句点结尾"
    return ""


def resolve_version_dir(name: str, root: str = "") -> Path | None:
    """把版本名解析为某个 versions 目录下的已存在版本目录。

    root 为空时使用默认的 .minecraft/versions。
    """
    text = (name or "").strip()
    if validate_version_name(text):
        return None

    base = Path(root) if root else VERSIONS_DIR
    try:
        base_resolved = base.resolve()
        path = (base / text).resolve()
    except OSError:
        return None
    if path.parent != base_resolved or not path.is_dir():
        return None
    return path


def parse_setup_ini(path: Path) -> dict[str, str]:
    """解析 PCL 风格的 Setup.ini（每行 "Key:Value"）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    meta: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "[")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def extract_loader(meta: dict[str, str]) -> str:
    """从 Setup.ini 参数中提取模组加载器描述。"""
    labels: list[str] = []
    for key, label in LOADER_FIELDS:
        value = meta.get(key, "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered == "false":
            continue
        if lowered == "true":
            labels.append(label)
        else:
            labels.append(f"{label} {value}")
    return ", ".join(labels)


def version_settings_from_setup(meta: dict) -> dict:
    """从 Setup.ini 读取版本级启动设置（缺省时给出默认值）。"""
    raw_check = str(meta.get("CheckFiles", "True")).strip().lower()
    check_files = raw_check not in ("false", "0", "no", "off")

    # 0 表示跟随启动器的全局内存设置
    try:
        memory = int(str(meta.get("MemoryMB", "") or 0))
    except ValueError:
        memory = 0
    memory = 0 if memory <= 0 else min(max(memory, 512), 65536)

    return {
        "java_path": str(meta.get("JavaPath", "") or "").strip(),
        "check_files": check_files,
        "memory_mb": memory,
        "extra_jvm": str(meta.get("ExtraJVM", "") or "").strip(),
        "extra_game": str(meta.get("ExtraGame", "") or "").strip(),
    }


def write_setup_fields(setup_path: Path, fields: dict[str, str]) -> None:
    """更新 Setup.ini 中的若干字段（不存在则追加）。"""
    try:
        lines = setup_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []

    for key, value in fields.items():
        new_line = f"{key}:{value}"
        prefix = f"{key}:"
        for index, line in enumerate(lines):
            if line.startswith(prefix):
                lines[index] = new_line
                break
        else:
            lines.append(new_line)

    setup_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


DEFAULT_MEMORY_MB = 8192
DEFAULT_USERNAME = "Steve"
MEMORY_MIN_MB = 1024
MEMORY_MAX_MB = 32768


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_status() -> tuple[int, int]:
    """返回 (物理内存总量, 当前可用内存)，单位 MB；获取失败返回 0。"""
    try:
        if os.name == "nt":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys // (1024 * 1024))
                available = int(status.ullAvailPhys // (1024 * 1024))
                return total, available
            return 0, 0
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = int(page_size * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
        try:
            available = int(page_size * os.sysconf("SC_AVPHYS_PAGES") // (1024 * 1024))
        except (ValueError, OSError):
            available = total
        return total, available
    except Exception:  # noqa: BLE001 - 取不到时退回默认值
        return 0, 0


def total_memory_mb() -> int:
    """返回物理内存总量（MB），获取失败时返回 0。"""
    return memory_status()[0]


def available_memory_mb() -> int:
    """返回当前可用内存（MB），获取失败时返回 0。"""
    return memory_status()[1]


def auto_memory_mb() -> int:
    """按当前可用内存实时给出建议的最大内存（MB）。

    取当前可用内存的 70%，不超过物理内存的 75%，限制在 2–16 GB 并向下取整到 512 MB。
    """
    total, available = memory_status()
    if available <= 0:
        if total <= 0:
            return DEFAULT_MEMORY_MB
        value = int(total * 0.4)
    else:
        value = int(available * 0.7)
        if total > 0:
            value = min(value, int(total * 0.75))
    value = max(2048, min(value, 16384))
    return value - value % 512


def _is_hex_color(value: str) -> bool:
    """判断是否为 #RRGGBB 形式的颜色值。"""
    text = (value or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text[1:])


def read_global_settings() -> dict:
    """读取 MCHL/Setup.ini 中的全局设置。"""
    meta = parse_setup_ini(GLOBAL_SETUP_FILE) if GLOBAL_SETUP_FILE.is_file() else {}

    try:
        memory = int(str(meta.get("MemoryMB", "") or 0))
    except ValueError:
        memory = 0
    if memory <= 0:
        memory = DEFAULT_MEMORY_MB
    memory = max(MEMORY_MIN_MB, min(memory, MEMORY_MAX_MB))

    auto_raw = str(meta.get("MemoryAuto", "") or "").strip().lower()
    memory_auto = auto_raw in ("true", "1", "yes", "on")

    window_mode = str(meta.get("WindowMode", "") or "").strip()
    if window_mode not in WINDOW_MODES:
        window_mode = "default"

    username = str(meta.get("UserName", "") or "").strip() or DEFAULT_USERNAME

    # 未写入过该字段时默认开启：只影响首次生成的 options.txt
    lang_raw = str(meta.get("AutoLangZH", "") or "").strip().lower()
    auto_lang_zh = lang_raw not in ("false", "0", "no", "off")

    # 版本隔离：未写入过该字段时默认开启，每个版本使用自己的游戏目录
    isolation_raw = str(meta.get("VersionIsolation", "") or "").strip().lower()
    version_isolation = isolation_raw not in ("false", "0", "no", "off")

    theme_color = str(meta.get("ThemeColor", "") or "").strip()
    if not _is_hex_color(theme_color):
        theme_color = ""

    try:
        background_blur = int(str(meta.get("BackgroundBlur", "") or 0))
    except ValueError:
        background_blur = 0
    background_blur = max(0, min(background_blur, 24))

    background_image = str(meta.get("BackgroundImage", "") or "").strip()
    if background_image and not (WEB_IMG_DIR / background_image).is_file():
        background_image = ""

    # 空字符串表示还没记录过，界面按账号状态自行推断
    login_mode = str(meta.get("LoginMode", "") or "").strip().lower()
    if login_mode not in LOGIN_MODES:
        login_mode = ""

    default_view = str(meta.get("DefaultView", "") or "").strip().lower()
    if default_view not in DEFAULT_VIEWS:
        default_view = "home"

    total_mb, available_mb = memory_status()

    return {
        "memory_mb": auto_memory_mb() if memory_auto else memory,
        "memory_manual_mb": memory,
        "memory_auto": memory_auto,
        "memory_total_mb": total_mb,
        "memory_available_mb": available_mb,
        "username": username[:16],
        "window_mode": window_mode,
        "auto_lang_zh": auto_lang_zh,
        "version_isolation": version_isolation,
        "theme_color": theme_color,
        "background_image": background_image,
        "background_blur": background_blur,
        "login_mode": login_mode,
        "default_view": default_view,
        "java_path": _repair_legacy_path(str(meta.get("JavaPath", "") or "")),
    }


def version_game_dir(version_dir: Path) -> Path:
    """按全局「版本隔离」设置返回游戏目录（存档/配置/模组所在位置）。

    开启时每个版本一个独立目录；关闭时所有版本共用数据根目录。
    """
    meta = parse_setup_ini(GLOBAL_SETUP_FILE) if GLOBAL_SETUP_FILE.is_file() else {}
    raw = str(meta.get("VersionIsolation", "") or "").strip().lower()
    if raw in ("false", "0", "no", "off"):
        return DATA_DIR
    return version_dir


def write_global_settings(fields: dict[str, str]) -> None:
    """把全局设置写入 MCHL/Setup.ini。"""
    if not fields:
        return
    try:
        GLOBAL_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        write_setup_fields(GLOBAL_SETUP_FILE, fields)
    except OSError:
        pass


def default_icon_key(loader: str) -> str:
    """按模组加载器给出默认图标标识。"""
    text = (loader or "").lower()
    if "forge" in text:
        return "forge"
    if "fabric" in text or "quilt" in text:
        return "fabric"
    return "grass"


def resolve_version_icon(
    folder: Path, meta: dict, loader: str = ""
) -> tuple[str, Path | None]:
    """解析版本图标，返回 (图标标识, 图片路径)。

    标识为 grass / fabric / forge（预设）或 custom（版本目录内的自定义图片）；
    Setup.ini 中的 Icon 为空时按模组加载器自动匹配。
    """
    raw = str(meta.get("Icon", "") or "").strip()
    if raw:
        key = raw.lower()
        if key in ICON_PRESET_FILES:
            preset = picture_path(ICON_PRESET_FILES[key])
            if preset.is_file():
                return key, preset
        else:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = folder / SETUP_SUBDIR / raw
            if candidate.is_file():
                return "custom", candidate
    key = default_icon_key(loader or extract_loader(meta))
    preset = picture_path(ICON_PRESET_FILES[key])
    return key, preset if preset.is_file() else None


_icon_cache: dict[str, tuple[float, str]] = {}


def icon_data_url(path: Path | None) -> str:
    """把图片读成 data URL（按修改时间缓存），失败时返回空字符串。"""
    if path is None:
        return ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    key = str(path).lower()
    cached = _icon_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    mime = ICON_MIME_TYPES.get(path.suffix.lower(), "image/png")
    url = f"data:{mime};base64,{payload}"
    _icon_cache[key] = (mtime, url)
    return url


def _png_data_url(path: Path) -> str:
    """把 PNG 文件转成可直接用于 img.src 的 data URL。"""
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{payload}"


def clear_custom_icon(setup_dir: Path) -> None:
    """删除版本目录里残留的自定义图标文件。"""
    for old in setup_dir.glob(f"{ICON_FILE_STEM}.*"):
        if old.is_file() and old.suffix.lower() in ICON_MIME_TYPES:
            try:
                old.unlink()
            except OSError:
                pass


def _version_json_info(folder: Path) -> tuple[Path | None, dict]:
    """定位并解析版本目录里的版本 JSON（优先同名文件）。"""
    path = game_launcher.find_version_json(folder)
    if path is None:
        return None, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return path, {}
    return path, data if isinstance(data, dict) else {}


def _version_jar_path(folder: Path) -> Path | None:
    """版本目录里的客户端 jar（优先同名文件）。"""
    same = folder / f"{folder.name}.jar"
    if same.is_file():
        return same
    jars = sorted(folder.glob("*.jar"))
    return jars[0] if jars else None


def _loader_from_json(folder_name: str, data: dict) -> str:
    """从版本 JSON 的 id / mainClass / inheritsFrom 推断加载器。"""
    text = " ".join(
        str(item or "")
        for item in (
            folder_name,
            data.get("id"),
            data.get("mainClass"),
            data.get("inheritsFrom"),
        )
    ).lower()
    for token, label in LOADER_NAME_HINTS:
        if token in text:
            return label
    return ""


def _mc_version_from_json(folder_name: str, data: dict) -> str:
    """从版本 JSON 推断对应的原版版本号。"""
    inherits = str(data.get("inheritsFrom") or "").strip()
    if inherits:
        return inherits
    version_id = str(data.get("id") or "").strip()
    if version_id:
        return version_id
    jar_id = str(data.get("jar") or "").strip()
    return jar_id or folder_name


def _release_time_from_json(data: dict) -> str:
    return str(data.get("releaseTime") or data.get("time") or "").strip()


def build_version_entry(folder: Path) -> dict | None:
    """读取单个版本文件夹，返回版本信息。

    识别方式：目录里只要有版本 JSON（或客户端 jar）就算一个版本，
    MCHL/Setup.ini 只用于保存启动器自己的设置，不再作为识别前提。
    """
    setup_path = folder / SETUP_SUBDIR / SETUP_FILE_NAME
    has_setup = setup_path.is_file()
    json_path, json_data = _version_json_info(folder)
    jar_path = _version_jar_path(folder)

    if not has_setup and json_path is None and jar_path is None:
        return None

    meta = parse_setup_ini(setup_path) if has_setup else {}
    # Hidden:1 的原版只是加载器依赖，不进入版本列表
    if str(meta.get("Hidden", "")).strip().lower() in ("1", "true", "yes"):
        return None

    mc_version = str(meta.get("VersionOriginal") or meta.get("VersionVanillaName") or "").strip()
    if not mc_version:
        mc_version = _mc_version_from_json(folder.name, json_data)
    loader = extract_loader(meta) or _loader_from_json(folder.name, json_data)
    info = str(meta.get("Info") or "").strip() or " ".join(
        part for part in (mc_version, loader) if part
    )
    release_time = str(meta.get("ReleaseTime") or "").strip() or _release_time_from_json(json_data)
    if not release_time:
        try:
            release_time = datetime.fromtimestamp(
                (json_path or folder).stat().st_mtime, timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except OSError:
            release_time = ""

    icon_key, icon_path = resolve_version_icon(folder, meta, loader)

    return {
        "name": folder.name,
        "path": str(folder),
        "setup_path": str(setup_path),
        "has_setup": has_setup,
        "json_path": str(json_path) if json_path else "",
        "jar_path": str(jar_path) if jar_path else "",
        "mc_version": mc_version,
        "loader": loader,
        "info": info,
        "release_time": release_time,
        # 版本图标：icon 为可直接用于 img src 的 data URL
        "icon_key": icon_key,
        "icon": icon_data_url(icon_path),
        # 版本级启动设置（Java、文件校验、内存、附加参数）
        "settings": version_settings_from_setup(meta),
        # 完整参数留存，供后续下载 / 启动版本时使用
        "setup": meta,
    }


def _read_setup_meta(folder: Path) -> dict:
    """读取版本目录的 Setup.ini，失败时返回空字典。"""
    setup_path = folder / SETUP_SUBDIR / SETUP_FILE_NAME
    if not setup_path.is_file():
        return {}
    try:
        return parse_setup_ini(setup_path) or {}
    except Exception:  # noqa: BLE001 - 解析失败按无效目录处理
        return {}


def _is_valid_version_dir(folder: Path) -> bool:
    """目录中是否存在可识别的版本：有 Setup.ini、版本 JSON 或客户端 jar 都算。"""
    if _read_setup_meta(folder):
        return True
    if _version_json_info(folder)[0] is not None:
        return True
    return _version_jar_path(folder) is not None


def _ensure_setup_file(folder: Path) -> Path | None:
    """确保版本目录里有 MCHL/Setup.ini，没有就按版本 JSON 补一份。"""
    setup_path = folder / SETUP_SUBDIR / SETUP_FILE_NAME
    if setup_path.is_file():
        return setup_path

    _, data = _version_json_info(folder)
    mc_version = _mc_version_from_json(folder.name, data) if data else folder.name
    loader = _loader_from_json(folder.name, data) if data else ""
    fields = {"VersionOriginal": mc_version, "VersionVanillaName": folder.name}
    info = " ".join(part for part in (mc_version, loader) if part)
    if info:
        fields["Info"] = info
    release = _release_time_from_json(data) if data else ""
    if release:
        fields["ReleaseTime"] = release

    try:
        setup_path.parent.mkdir(parents=True, exist_ok=True)
        write_setup_fields(setup_path, fields)
    except OSError:
        return None
    return setup_path


def _is_hidden_version_dir(folder: Path) -> bool:
    """是否是仅作为加载器依赖、不在列表显示的原版。"""
    meta = _read_setup_meta(folder)
    return str(meta.get("Hidden", "")).strip().lower() in ("1", "true", "yes")


def _hide_version_dir(folder: Path) -> bool:
    """把已存在的原版标记为仅依赖，从版本列表隐藏。"""
    if _is_hidden_version_dir(folder):
        return True
    setup_path = _ensure_setup_file(folder)
    if setup_path is None:
        return False
    try:
        text = setup_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    try:
        setup_path.write_text(text + "Hidden:1\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _unhide_version_dir(folder: Path) -> bool:
    """把仅作为依赖的原版恢复成可在版本列表中显示。"""
    setup_path = folder / SETUP_SUBDIR / SETUP_FILE_NAME
    try:
        lines = setup_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    kept = [
        line for line in lines
        if not line.strip().lower().startswith("hidden:")
    ]
    if len(kept) == len(lines):
        return True
    try:
        setup_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _hide_dependency_versions() -> int:
    """把被加载器版本引用的原版标记为隐藏，避免列表里重复出现。

    只迁移一次：完成后在 state 里记标记，之后用户显式下载的原版保持可见。
    """
    state = read_state()
    if state.get("dependency_hidden_migrated"):
        return 0

    try:
        folders = [item for item in VERSIONS_DIR.iterdir() if item.is_dir()]
    except OSError:
        return 0

    referenced: set[str] = set()
    for folder in folders:
        meta = _read_setup_meta(folder)
        if not meta or not extract_loader(meta):
            continue
        parent = str(meta.get("VersionOriginal") or "").strip()
        if parent and parent != folder.name:
            referenced.add(parent)

    hidden = 0
    for name in referenced:
        folder = VERSIONS_DIR / name
        if not folder.is_dir():
            continue
        if not _is_valid_version_dir(folder) or _is_hidden_version_dir(folder):
            continue
        if _hide_version_dir(folder):
            hidden += 1

    state["dependency_hidden_migrated"] = True
    write_state(state)
    return hidden


def _rmtree_force(folder: Path) -> bool:
    """尽力删除目录：遇到只读文件时去掉只读属性后重试。"""

    def _retry(func, path, _exc=None):  # noqa: ANN001 - shutil 回调签名
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(folder, onexc=_retry)
        else:
            shutil.rmtree(folder, onerror=_retry)
    except OSError:
        pass
    return not folder.exists()


def _version_depends_on(name: str, root: Path) -> bool:
    """是否仍有版本 JSON 的 inheritsFrom 指向 name。"""
    try:
        folders = [item for item in root.iterdir() if item.is_dir()]
    except OSError:
        return True  # 无法判断时保守保留

    for folder in folders:
        try:
            json_path = game_launcher.find_version_json(folder)
            if json_path is None:
                continue
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("inheritsFrom") or "") == name:
            return True
    return False


def _remove_orphan_dependency(name: str, root: Path) -> None:
    """删除加载器版本后，清理不再被继承的隐藏原版目录。"""
    target = root / name
    if not target.is_dir() or not _is_hidden_version_dir(target):
        return
    if _version_depends_on(name, root):
        return
    _rmtree_force(target)


def _cleanup_orphan_hidden_versions() -> int:
    """清理不再被任何版本继承的隐藏原版目录（删除版本后的残留）。"""
    try:
        folders = [item for item in VERSIONS_DIR.iterdir() if item.is_dir()]
    except OSError:
        return 0

    referenced: set[str] = set()
    for folder in folders:
        try:
            json_path = game_launcher.find_version_json(folder)
            if json_path is None:
                continue
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            parent = str(data.get("inheritsFrom") or "").strip()
            if parent:
                referenced.add(parent)

    removed = 0
    for folder in folders:
        if not _is_hidden_version_dir(folder):
            continue
        if folder.name in referenced:
            continue
        if _rmtree_force(folder):
            removed += 1
    return removed


def _flatten_inherited_versions() -> int:
    """把仍依赖单独原版目录的加载器版本合并成单文件夹。

    合并成功后若原版已不再被任何版本继承，会一并删除，保证一个版本一个目录。
    """
    try:
        folders = [item for item in VERSIONS_DIR.iterdir() if item.is_dir()]
    except OSError:
        return 0

    merged = 0
    for folder in folders:
        meta = _read_setup_meta(folder)
        if not meta or not extract_loader(meta):
            continue
        try:
            json_path = game_launcher.find_version_json(folder)
            if json_path is None:
                continue
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        parent = str(data.get("inheritsFrom") or "").strip()
        if not parent or not (VERSIONS_DIR / parent).is_dir():
            continue
        if game_launcher.flatten_version(folder, VERSIONS_DIR):
            merged += 1
            _remove_orphan_dependency(parent, VERSIONS_DIR)
    return merged


def _remove_residual_version_dir(folder: Path) -> bool:
    """删除下载中断留下的无效版本目录（仅限 versions 目录内）。"""
    try:
        resolved = folder.resolve()
        root = VERSIONS_DIR.resolve()
    except OSError:
        return False
    if resolved.parent != root:
        return False
    try:
        shutil.rmtree(resolved)
    except OSError:
        return False
    return True


def read_state() -> dict:
    try:
        if STATE_FILE.is_file():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def write_state(state: dict) -> None:
    try:
        ensure_data_dir()
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# 自定义 HTML 小组件：数量与单个体积上限
CUSTOM_WIDGET_ID_PREFIX = "custom-"
CUSTOM_WIDGET_MAX = 12
CUSTOM_WIDGET_HTML_LIMIT = 200_000


def normalize_custom_widgets(data: dict) -> list[dict]:
    """把 home.json 里的自定义小组件整理成界面可直接使用的结构。"""
    items = data.get("custom_widgets")
    result: list[dict] = []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        widget_id = str(item.get("id") or "").strip()
        if not widget_id.startswith(CUSTOM_WIDGET_ID_PREFIX):
            continue
        name = str(item.get("name") or "").strip()[:24] or "自定义小组件"
        result.append(
            {
                "id": widget_id,
                "name": name,
                "html": str(item.get("html") or ""),
                "wide": bool(item.get("wide")),
            }
        )
    return result


def read_home() -> dict:
    """读取首页小组件与使用统计。"""
    try:
        if HOME_FILE.is_file():
            data = json.loads(HOME_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def write_home(data: dict) -> None:
    try:
        ensure_data_dir()
        HOME_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _sync_selected_version(name: str, entry: dict | None) -> None:
    """版本信息变化后同步已保存的选中版本。"""
    if entry is None:
        return
    state = read_state()
    selected = state.get("selected_version")
    if isinstance(selected, dict) and selected.get("name") == name:
        state["selected_version"] = entry
        write_state(state)


def ensure_global_settings() -> None:
    """创建 MCHL 设置目录，并把旧版 launcher.json 中的全局设置迁移进去。"""
    try:
        GLOBAL_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    existing = parse_setup_ini(GLOBAL_SETUP_FILE) if GLOBAL_SETUP_FILE.is_file() else {}

    # 修正仍指向旧版 MCHL 数据目录的 Java 路径
    stored_java = str(existing.get("JavaPath", "") or "")
    fixed_java = _repair_legacy_path(stored_java)
    if fixed_java and fixed_java != stored_java:
        write_global_settings({"JavaPath": fixed_java})
        existing["JavaPath"] = fixed_java

    state = read_state()
    legacy_settings = state.get("settings")
    legacy_java = state.get("java_path")
    if not isinstance(legacy_settings, dict) and not legacy_java:
        return

    fields: dict[str, str] = {}

    if isinstance(legacy_settings, dict):
        if "memory_mb" in legacy_settings and not existing.get("MemoryMB"):
            try:
                memory = max(1024, min(int(legacy_settings["memory_mb"]), 32768))
                fields["MemoryMB"] = str(memory)
            except (TypeError, ValueError):
                pass
        username = str(legacy_settings.get("username") or "").strip()
        if username and not existing.get("UserName"):
            fields["UserName"] = username[:16]
        mode = str(legacy_settings.get("window_mode") or "").strip()
        if mode in WINDOW_MODES and not existing.get("WindowMode"):
            fields["WindowMode"] = mode
        state.pop("settings", None)

    if isinstance(legacy_java, str) and legacy_java.strip():
        if not existing.get("JavaPath"):
            fields["JavaPath"] = _repair_legacy_path(legacy_java)
        state.pop("java_path", None)

    write_global_settings(fields)
    write_state(state)


# ---- 拖放导入（Windows WM_DROPFILES） --------------------------------
WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
# 回调对象必须保活，否则 Python 回收后窗口过程指向野指针
_DROP_HANDLER_REFS: list = []


def _read_dropped_files(hdrop: int) -> list[str]:
    """从 HDROP 句柄里取出拖入的文件路径。"""
    shell32 = ctypes.WinDLL("shell32")
    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT

    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths: list[str] = []
    for index in range(count):
        length = shell32.DragQueryFileW(hdrop, index, None, 0)
        if length <= 0:
            continue
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
        if buffer.value:
            paths.append(buffer.value)

    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish(hdrop)
    return paths


def install_drop_files(hwnd: int, callback) -> bool:
    """让窗口接受文件拖放，拖入时在后台线程调用 callback(paths)。"""
    user32 = ctypes.WinDLL("user32")
    shell32 = ctypes.WinDLL("shell32")

    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragAcceptFiles(hwnd, True)

    get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    get_long.restype = ctypes.c_void_p
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]

    set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    set_long.restype = ctypes.c_void_p
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

    call_proc = user32.CallWindowProcW
    call_proc.restype = ctypes.c_ssize_t
    call_proc.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]

    old_proc = get_long(hwnd, GWLP_WNDPROC)
    if not old_proc:
        return False

    wndproc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def _proc(h, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_DROPFILES:
            try:
                paths = _read_dropped_files(int(wparam))
            except Exception:  # noqa: BLE001 - 拖放解析失败不能影响窗口
                paths = []
            if paths:
                try:
                    threading.Thread(
                        target=callback, args=(paths,), daemon=True
                    ).start()
                except Exception:  # noqa: BLE001
                    pass
            return 0
        return call_proc(old_proc, h, msg, wparam, lparam)

    handler = wndproc_type(_proc)
    _DROP_HANDLER_REFS.append((handler, old_proc))
    set_long(hwnd, GWLP_WNDPROC, ctypes.cast(handler, ctypes.c_void_p))
    return True


class LauncherApi:
    """暴露给前端 JS 调用的接口（仅窗口控制等基础能力）。"""

    def __init__(self) -> None:
        self._window: "webview.Window | None" = None
        self._hwnd: int | None = None
        self._java_cache: list[dict] | None = None
        self._last_emit = 0.0
        # 整合包安装期间把各阶段进度统一归到同一个下载任务
        # 用线程局部变量：只影响发起安装的那条线程，不干扰并发的普通下载
        self._scope = threading.local()
        self._launch_process: subprocess.Popen | None = None
        # 允许多开：保存所有仍在运行的游戏进程及其版本目录
        self._launch_processes: list[dict] = []
        self._launch_started_at = 0.0
        self._launch_window_mode = "default"
        self._window_applied = False
        # 正版账号缓存与进行中的设备码登录会话
        self._account: dict | None = None
        self._account_loaded = False
        self._profile_synced_at = 0.0
        self._device_login: dict | None = None
        # 第三方外置登录账号缓存
        self._authlib: dict | None = None
        self._authlib_loaded = False

    def attach(self, window: "webview.Window") -> None:
        self._window = window

    # ---- 窗口控制 -------------------------------------------------
    def minimize(self) -> bool:
        if self._window is not None:
            self._window.minimize()
            return True
        return False

    def close(self) -> bool:
        if self._window is not None:
            self._window.destroy()
            return True
        return False

    def fit_window(self) -> bool:
        """按屏幕工作区把窗口居中，并保证不超出可用区域（物理像素）。"""
        hwnd = self._find_hwnd()
        if not hwnd:
            return False
        try:
            user32 = ctypes.WinDLL("user32")

            spi_get_work_area = user32.SystemParametersInfoW
            spi_get_work_area.argtypes = [
                wintypes.UINT,
                wintypes.UINT,
                ctypes.c_void_p,
                wintypes.UINT,
            ]
            spi_get_work_area.restype = wintypes.BOOL

            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            set_window_pos.restype = wintypes.BOOL

            work_area = wintypes.RECT()
            spi_get_work_area(0x0030, 0, ctypes.byref(work_area), 0)  # SPI_GETWORKAREA

            hwnd_wrap = wintypes.HWND(hwnd)
            scale = dpi_for_window(hwnd_wrap) / 96.0

            work_width = work_area.right - work_area.left
            work_height = work_area.bottom - work_area.top
            width = int(min(WINDOW_WIDTH, work_width / scale - 60) * scale)
            height = int(min(WINDOW_HEIGHT, work_height / scale - 60) * scale)

            pos_x = work_area.left + (work_width - width) // 2
            pos_y = work_area.top + (work_height - height) // 2

            # SWP_NOZORDER | SWP_NOACTIVATE
            set_window_pos(hwnd_wrap, None, pos_x, pos_y, width, height, 0x0004 | 0x0010)
            return True
        except Exception:
            return False

    def apply_rounded_corners(self) -> bool:
        """在 Windows 11 上为无边框窗口启用系统圆角（旧系统上自动忽略）。"""
        hwnd = self._find_hwnd()
        if not hwnd:
            return False
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            set_attribute = dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            set_attribute.restype = ctypes.c_long
            preference = ctypes.c_int(DWMWCP_ROUND)
            result = set_attribute(
                wintypes.HWND(hwnd),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
            return result == 0
        except Exception:
            return False

    # ---- 信息 -----------------------------------------------------
    def get_app_info(self) -> dict:
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            # 开发者：幻方者
            "author": AUTHOR_NAME,
            "author_url": AUTHOR_URL,
            "data_dir": str(DATA_DIR),
            "base_dir": str(BASE_DIR),
        }

    def open_data_dir(self) -> bool:
        try:
            ensure_data_dir()
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    # ---- 版本 -----------------------------------------------------
    def list_versions(self, root: str = "") -> list[dict]:
        """扫描指定 versions 目录下的版本（默认 .minecraft/versions）。"""
        base = Path(root) if root else VERSIONS_DIR
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            return []

        entries: list[dict] = []
        try:
            folders = sorted(base.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return []

        for folder in folders:
            if not folder.is_dir():
                continue
            entry = build_version_entry(folder)
            if entry is not None:
                entries.append(entry)

        # 新版本排前面，缺少时间信息的排最后
        entries.sort(key=lambda entry: (entry["release_time"], entry["name"]), reverse=True)
        return entries

    def select_version(self, name: str, root: str = "") -> dict | None:
        """记录当前选中的版本，参数写入 .minecraft/launcher.json。"""
        if not isinstance(name, str) or not name:
            return None
        folder = resolve_version_dir(name, root)
        if folder is None:
            return None
        entry = build_version_entry(folder)
        if entry is None:
            return None
        state = read_state()
        state["selected_version"] = entry
        write_state(state)
        return entry

    def list_version_roots(self) -> list[dict]:
        """返回可用的游戏文件夹（versions 目录）列表。"""
        roots: list[dict] = [
            {"path": str(VERSIONS_DIR), "is_default": True, "name": f"{DATA_DIR_NAME}/versions"}
        ]

        state = read_state()
        extras = state.get("version_roots")
        if isinstance(extras, list):
            seen = {str(Path(item["path"]).resolve()).lower() for item in roots}
            for raw in extras:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                path = Path(raw)
                try:
                    key = str(path.resolve()).lower()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                roots.append({"path": str(path), "is_default": False, "name": path.name or raw})

        for item in roots:
            folder = Path(item["path"])
            item["exists"] = folder.is_dir()
            item["version_count"] = len(self.list_versions(item["path"])) if item["exists"] else 0
        return roots

    def pick_version_root(self) -> dict:
        """选择要添加为游戏文件夹的 versions 目录。"""
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}
        try:
            selection = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            return {"ok": False, "error": "无法打开文件夹对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        path = selection[0] if isinstance(selection, (list, tuple)) else selection
        return self.add_version_root(str(path))

    def add_version_root(self, path: str) -> dict:
        """把指定的 versions 目录加入游戏文件夹列表。"""
        text = str(path or "").strip()
        if not text:
            return {"ok": False, "error": "路径不能为空"}

        folder = Path(text)
        if not folder.is_dir():
            return {"ok": False, "error": "文件夹不存在"}

        try:
            resolved = folder.resolve()
        except OSError:
            return {"ok": False, "error": "路径无效"}

        # 允许选择包含 versions 的游戏根目录
        candidates_dir = resolved / "versions"
        if candidates_dir.is_dir() and not any(resolved.glob("*/MCHL/Setup.ini")):
            resolved = candidates_dir

        state = read_state()
        extras = state.get("version_roots")
        if not isinstance(extras, list):
            extras = []

        if str(resolved).lower() != str(VERSIONS_DIR.resolve()).lower():
            known = {str(item).lower() for item in extras if isinstance(item, str)}
            if str(resolved).lower() not in known:
                extras.append(str(resolved))
                state["version_roots"] = extras
                write_state(state)

        return {"ok": True, "roots": self.list_version_roots(), "path": str(resolved)}

    def remove_version_root(self, path: str) -> dict:
        """从列表中移除添加的游戏文件夹（默认目录不可移除）。"""
        text = str(path or "").strip()
        if not text:
            return {"ok": False, "error": "路径不能为空"}
        try:
            resolved = str(Path(text).resolve()).lower()
        except OSError:
            return {"ok": False, "error": "路径无效"}

        state = read_state()
        extras = state.get("version_roots")
        if not isinstance(extras, list):
            return {"ok": False, "error": "没有可移除的文件夹"}

        kept = []
        for item in extras:
            if not isinstance(item, str):
                continue
            try:
                key = str(Path(item).resolve()).lower()
            except OSError:
                key = str(item).lower()
            if key != resolved:
                kept.append(item)
        if len(kept) == len([x for x in extras if isinstance(x, str)]):
            return {"ok": False, "error": "该文件夹不在列表中"}
        state["version_roots"] = kept
        write_state(state)
        return {"ok": True, "roots": self.list_version_roots()}

    def delete_version(self, name: str, root: str = "") -> dict:
        """删除版本目录（前端需二次确认）。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在或已被删除"}

        # 正在运行的游戏会占用 jar/natives，先拦下来避免删一半
        for item in self._live_launch_processes():
            try:
                running_dir = Path(str(item.get("path", ""))).resolve()
            except OSError:
                continue
            if running_dir == folder:
                return {"ok": False, "error": "该版本的游戏正在运行，请先关闭游戏再删除"}

        # 删除前先记录是否依赖某个原版、以及依赖的是谁（没有 Setup.ini 时看版本 JSON）
        meta = _read_setup_meta(folder)
        _, json_data = _version_json_info(folder)
        parent_name = str(meta.get("VersionOriginal") or "").strip() or str(
            json_data.get("inheritsFrom") or ""
        ).strip()
        has_loader = bool(extract_loader(meta)) or bool(_loader_from_json(folder.name, json_data))

        if not _rmtree_force(folder):
            return {
                "ok": False,
                "error": "删除失败：部分文件被占用，请关闭游戏或相关程序后重试",
            }

        # 加载器版本删除后，若没有其它版本依赖它的原版，一并清理
        if has_loader and parent_name:
            _remove_orphan_dependency(parent_name, folder.parent)

        state = read_state()
        selected = state.get("selected_version")
        if isinstance(selected, dict) and selected.get("name") == name:
            state.pop("selected_version", None)
            write_state(state)
        return {"ok": True}

    def rename_version(self, old_name: str, new_name: str, root: str = "") -> dict:
        """修改版本文件夹名。"""
        source = resolve_version_dir(old_name, root)
        if source is None:
            return {"ok": False, "error": "版本不存在"}
        problem = validate_version_name(new_name)
        if problem:
            return {"ok": False, "error": problem}

        target = source.parent / new_name.strip()
        if target.exists():
            return {"ok": False, "error": f"版本名 “{target.name}” 已存在"}
        try:
            source.rename(target)
        except OSError as exc:
            return {"ok": False, "error": f"重命名失败：{exc}"}

        # 同步 Setup.ini 中的版本名，保持与目录名一致
        setup_path = target / SETUP_SUBDIR / SETUP_FILE_NAME
        if setup_path.is_file():
            try:
                lines = setup_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                patched = [
                    f"VersionVanillaName:{target.name}"
                    if line.startswith("VersionVanillaName:")
                    else line
                    for line in lines
                ]
                setup_path.write_text("\n".join(patched) + "\n", encoding="utf-8")
            except OSError:
                pass

        entry = build_version_entry(target)
        state = read_state()
        selected = state.get("selected_version")
        if isinstance(selected, dict) and selected.get("name") == old_name and entry:
            state["selected_version"] = entry
            write_state(state)
        return {"ok": True, "entry": entry}

    def open_version_dir(self, name: str, root: str = "") -> bool:
        folder = resolve_version_dir(name, root)
        if folder is None:
            return False
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    def get_version_detail(self, name: str, root: str = "") -> dict | None:
        """返回版本详情与 mod 列表。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return None
        entry = build_version_entry(folder)
        if entry is None:
            return None

        mods_dir = version_game_dir(folder) / "mods"
        return {
            **entry,
            "mods_dir": str(mods_dir),
            "mods": mod_manage.scan_mods(mods_dir),
            "can_mod": bool(entry.get("loader")),
        }

    def update_version_settings(self, name: str, patch: dict, root: str = "") -> dict:
        """保存版本级启动设置（Java、文件校验、内存、附加参数）。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}
        if not isinstance(patch, dict):
            return {"ok": False, "error": "参数错误"}

        setup_path = _ensure_setup_file(folder)
        if setup_path is None:
            return {"ok": False, "error": "版本目录不可写，无法保存设置"}

        fields: dict[str, str] = {}

        if "java_path" in patch:
            raw = str(patch.get("java_path") or "").strip()
            if raw:
                info = java_runtime.probe_java(Path(raw))
                if info is None:
                    return {"ok": False, "error": "所选文件不是有效的 java.exe"}
                fields["JavaPath"] = info["path"]
            else:
                fields["JavaPath"] = ""

        if "check_files" in patch:
            fields["CheckFiles"] = "True" if patch.get("check_files") else "False"

        if "memory_mb" in patch:
            try:
                memory = int(patch.get("memory_mb") or 0)
            except (TypeError, ValueError):
                memory = 0
            memory = 0 if memory <= 0 else min(max(memory, 512), 65536)
            fields["MemoryMB"] = str(memory)

        if "extra_jvm" in patch:
            fields["ExtraJVM"] = str(patch.get("extra_jvm") or "").strip()

        if "extra_game" in patch:
            fields["ExtraGame"] = str(patch.get("extra_game") or "").strip()

        if "icon" in patch:
            raw_icon = str(patch.get("icon") or "").strip()
            key = raw_icon.lower()
            if key in ("", "auto"):
                fields["Icon"] = ""
                clear_custom_icon(folder / SETUP_SUBDIR)
            elif key in ICON_PRESET_FILES:
                fields["Icon"] = key
                clear_custom_icon(folder / SETUP_SUBDIR)
            else:
                return {"ok": False, "error": "不支持的图标选项"}

        if not fields:
            return {"ok": False, "error": "没有需要保存的设置"}

        try:
            write_setup_fields(setup_path, fields)
        except OSError as exc:
            return {"ok": False, "error": f"保存失败：{exc}"}

        entry = build_version_entry(folder)
        _sync_selected_version(name, entry)

        settings = version_settings_from_setup(parse_setup_ini(setup_path))
        return {"ok": True, "settings": settings, "entry": entry}

    def get_icon_presets(self) -> dict:
        """返回预设图标（data URL），供版本设置页展示。"""
        return {
            key: icon_data_url(picture_path(name))
            for key, name in ICON_PRESET_FILES.items()
        }

    def pick_version_icon(self, name: str, root: str = "") -> dict:
        """选择本地图片作为版本图标（复制到版本的 MCHL 目录）。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}
        setup_path = _ensure_setup_file(folder)
        if setup_path is None:
            return {"ok": False, "error": "版本目录不可写，无法保存图标"}
        setup_dir = setup_path.parent
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}

        try:
            selection = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("图片 (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)",),
            )
        except Exception:
            return {"ok": False, "error": "无法打开文件对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        source = Path(list(selection)[0] if isinstance(selection, (list, tuple)) else selection)
        suffix = source.suffix.lower()
        if suffix not in ICON_MIME_TYPES:
            return {"ok": False, "error": "仅支持 PNG / JPG / WEBP / GIF / BMP 图片"}
        try:
            if source.stat().st_size > ICON_MAX_BYTES:
                return {"ok": False, "error": "图片过大，请选择 2 MB 以内的图片"}
        except OSError as exc:
            return {"ok": False, "error": f"无法读取图片：{exc}"}

        clear_custom_icon(setup_dir)
        dest = setup_dir / f"{ICON_FILE_STEM}{suffix}"
        try:
            shutil.copyfile(source, dest)
            write_setup_fields(setup_path, {"Icon": dest.name})
        except OSError as exc:
            return {"ok": False, "error": f"保存图标失败：{exc}"}

        entry = build_version_entry(folder)
        _sync_selected_version(name, entry)
        return {"ok": True, "entry": entry}

    def pick_mod_files(self, name: str, root: str = "") -> dict:
        """选择 mod 文件并复制到该版本的 mods 目录。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}
        try:
            selection = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=("Mod 文件 (*.jar)",),
            )
        except Exception:
            return {"ok": False, "error": "无法打开文件对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        paths = list(selection) if isinstance(selection, (list, tuple)) else [selection]
        return self._copy_mods(folder, [str(path) for path in paths])

    def add_mod_files(self, name: str, paths: list, root: str = "") -> dict:
        """接收拖拽得到的文件路径并复制到 mods。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}
        if not isinstance(paths, list):
            return {"ok": False, "error": "参数错误"}
        return self._copy_mods(folder, [str(path) for path in paths])

    def _copy_mods(self, folder: Path, paths: list[str]) -> dict:
        mods_dir = version_game_dir(folder) / "mods"
        try:
            mods_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"无法创建 mods 目录：{exc}"}

        added: list[str] = []
        skipped: list[str] = []
        for raw in paths:
            source = Path(raw)
            if not source.is_file() or source.suffix.lower() != ".jar":
                skipped.append(source.name)
                continue
            dest = mods_dir / source.name
            if dest.exists():
                skipped.append(source.name)
                continue
            try:
                shutil.copy2(source, dest)
                added.append(source.name)
            except OSError:
                skipped.append(source.name)

        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "mods": mod_manage.scan_mods(mods_dir),
        }

    # ---- 已安装模组管理 -------------------------------------------
    # ---- 拖入整合包 -------------------------------------------------
    def enable_drop_import(self) -> bool:
        """让主窗口接受拖入的整合包文件。"""
        hwnd = self._find_hwnd()
        if not hwnd:
            return False
        # WebView2 默认会自己吞掉外部文件拖放，先关掉它才能收到 WM_DROPFILES
        self._disable_webview_external_drop()
        try:
            return install_drop_files(hwnd, self._handle_dropped_files)
        except Exception:  # noqa: BLE001 - 拖放不可用时退回按钮导入
            return False

    def _disable_webview_external_drop(self) -> None:
        """把 WebView2 的 AllowExternalDrop 关掉，让拖放落到宿主窗口。"""
        window = self._window
        form = getattr(window, "native", None) if window is not None else None
        if form is None:
            return

        def _walk(node, depth: int) -> None:
            if depth > 4:
                return
            try:
                children = list(node.Controls)
            except Exception:  # noqa: BLE001 - 非 WinForms 容器直接跳过
                return
            for child in children:
                try:
                    type_name = str(child.GetType().FullName)
                except Exception:  # noqa: BLE001
                    type_name = ""
                if "WebView2" in type_name:
                    try:
                        child.AllowExternalDrop = False
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                _walk(child, depth + 1)

        _walk(form, 0)

    def _handle_dropped_files(self, paths: list[str]) -> None:
        archives = [
            path
            for path in paths
            if str(path).lower().endswith((".zip", ".mrpack"))
        ]
        if not archives:
            self._notify_modpack_drop(
                {"ok": False, "error": "只支持拖入 .zip / .mrpack 格式的整合包"}
            )
            return

        target = Path(archives[0])
        # 自建任务 id：前端据此建一条导入任务，安装进度只写这条任务
        task_id = f"modpack-drop-{int(time.time() * 1000)}"
        self._set_modpack_scope(task_id, True)
        self._notify_modpack_drop(
            {"started": True, "name": target.name, "task": task_id}
        )
        try:
            result = self._import_modpack_file(target)
        except Exception as exc:  # noqa: BLE001 - 后台线程里兜底
            result = {"ok": False, "error": f"导入失败：{exc}"}
        finally:
            self._clear_modpack_scope()

        payload = dict(result or {})
        payload.pop("cancelled", None)
        payload["task"] = task_id
        self._notify_modpack_drop(payload)

    def _notify_modpack_drop(self, payload: dict) -> None:
        window = self._window
        if window is None:
            return
        try:
            script = (
                "window.mchl && window.mchl.modpackDrop("
                + json.dumps(payload, ensure_ascii=False)
                + ");"
            )
            window.evaluate_js(script)
        except Exception:  # noqa: BLE001 - 前端尚未就绪时忽略
            pass

    # ---- 整合包导入 / 导出 -------------------------------------------
    def import_modpack(self, task_id: str = "") -> dict:
        """选择整合包文件并导入（支持 MCHL 与 PCL 导出的 zip）。"""
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪"}

        dialog_kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
        if dialog_kind is None:
            dialog_kind = webview.OPEN_DIALOG
        try:
            selection = window.create_file_dialog(
                dialog_kind,
                allow_multiple=False,
                file_types=("整合包 (*.zip;*.mrpack)", "所有文件 (*.*)"),
            )
        except Exception as exc:  # noqa: BLE001 - 对话框异常直接反馈给界面
            return {"ok": False, "error": f"无法打开文件选择器：{exc}"}
        if not selection:
            return {"ok": False, "cancelled": True}

        path = selection[0] if isinstance(selection, (list, tuple)) else selection
        # 绑定前端任务：安装期间的进度只更新这条任务，不占用下载页进度条
        self._set_modpack_scope(str(task_id or "").strip(), True)
        try:
            return self._import_modpack_file(Path(path))
        except Exception as exc:  # noqa: BLE001 - 后台异常统一反馈给界面
            return {"ok": False, "error": f"导入失败：{exc}"}
        finally:
            self._clear_modpack_scope()

    def _import_modpack_file(self, archive: Path) -> dict:
        source = Path(archive)
        if not source.is_file():
            return {"ok": False, "error": "整合包文件不存在"}

        if source.suffix.lower() == ".mrpack":
            return self._import_pack_archive(source)

        temp_dir = DATA_DIR / "temp" / f"import-{int(time.time() * 1000)}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                modpack_install.extract_zip(source, temp_dir)
            except (zipfile.BadZipFile, OSError) as exc:
                return {"ok": False, "error": f"无法解压整合包：{exc}"}

            root = modpack_install.find_pack_root(temp_dir)
            if root is None:
                # PCL 导出的整合包外层只有 modpack.mrpack，按 Modrinth 流程装
                return self._import_pack_archive(source, temp_dir)

            folders = modpack_install.find_pack_versions(root)
            if not folders:
                return {"ok": False, "error": "整合包里没有找到可用的游戏版本"}

            imported: list[dict] = []
            for folder in folders:
                version_name = self._unique_version_name(folder.name)
                dest = VERSIONS_DIR / version_name
                try:
                    shutil.copytree(folder, dest)
                except OSError as exc:
                    return {"ok": False, "error": f"复制版本 {folder.name} 失败：{exc}"}

                if version_name != folder.name:
                    self._rename_version_files(dest, folder.name, version_name)

                # PCL / 纯文件夹版本统一补上 MCHL/Setup.ini
                modpack_install.normalize_version_dir(dest)
                # inheritsFrom 的版本并入父版本，保证能独立启动
                try:
                    game_launcher.flatten_version(dest, VERSIONS_DIR)
                except Exception:  # noqa: BLE001 - 合并失败不影响导入
                    pass
                imported.append({"name": version_name, "path": str(dest)})

            # 主版本：优先带模组加载器的那个，共享文件并入它
            primary = ""
            for item in imported:
                if extract_loader(_read_setup_meta(Path(item["path"]))):
                    primary = item["name"]
                    break
            if not primary:
                primary = imported[0]["name"]

            merged = modpack_install.merge_shared_files(root, VERSIONS_DIR / primary)
            entry = build_version_entry(VERSIONS_DIR / primary)

            return {
                "ok": True,
                "version": primary,
                "entry": entry,
                "imported": imported,
                "merged": merged,
                "count": len(imported),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _import_pack_archive(
        self, archive: Path, temp_dir: Path | None = None
    ) -> dict:
        """按 Modrinth / CurseForge 清单安装整合包。

        PCL 导出的 zip 里往往只有一个 modpack.mrpack，需要先从中取出清单再安装。
        """
        pack_path = Path(archive)
        if temp_dir is not None:
            nested = self._find_nested_modpack(Path(temp_dir))
            if nested is not None:
                pack_path = nested

        info = modpack_install.read_pack(pack_path)
        if not info.get("ok"):
            reason = str(info.get("error") or "").strip()
            message = "整合包里既没有 versions 目录，也不是 Modrinth / CurseForge 整合包"
            if reason:
                message += f"（{reason}）"
            return {"ok": False, "error": message}

        # 复用下载页的安装流程：装好对应游戏版本 + 加载器，再铺开包内文件
        result = self._install_modpack(pack_path, "", False, "")
        if not result.get("ok"):
            return result

        name = str(result.get("version") or "").strip()
        if name:
            folder = VERSIONS_DIR / name
            if folder.is_dir():
                entry = build_version_entry(folder)
                if entry:
                    result["entry"] = entry
        return result

    def _find_nested_modpack(self, folder: Path) -> Path | None:
        """在解压目录里找 .mrpack（只看两层，避免大包全盘扫描）。"""
        try:
            for child in sorted(folder.iterdir()):
                if child.is_file() and child.suffix.lower() == ".mrpack":
                    return child
                if not child.is_dir():
                    continue
                for sub in sorted(child.iterdir()):
                    if sub.is_file() and sub.suffix.lower() == ".mrpack":
                        return sub
        except OSError:
            return None
        return None

    def _unique_version_name(self, name: str) -> str:
        base = str(name or "").strip() or "整合包"
        candidate = base
        index = 2
        while (VERSIONS_DIR / candidate).exists():
            candidate = f"{base} ({index})"
            index += 1
        return candidate

    def _rename_version_files(self, folder: Path, old: str, new: str) -> None:
        """版本目录改名后同步 json / jar 文件名与 json 内的 id。"""
        for suffix in (".json", ".jar"):
            source = folder / f"{old}{suffix}"
            if not source.is_file():
                continue
            try:
                source.rename(folder / f"{new}{suffix}")
            except OSError:
                continue

        json_path = folder / f"{new}.json"
        if not json_path.is_file():
            return
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("id") != old:
            return
        data["id"] = new
        try:
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def export_modpack(self, name: str, root: str = "") -> dict:
        """把某个版本导出为整合包 zip（MCHL 结构，PCL 也能读取）。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}

        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪"}

        dialog_kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
        if dialog_kind is None:
            dialog_kind = webview.SAVE_DIALOG
        try:
            selection = window.create_file_dialog(
                dialog_kind,
                save_filename=f"{folder.name}.zip",
                file_types=("整合包 (*.zip)",),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"无法打开保存对话框：{exc}"}
        if not selection:
            return {"ok": False, "cancelled": True}

        target = selection if isinstance(selection, str) else selection[0]
        target_path = Path(target)
        if target_path.suffix.lower() != ".zip":
            target_path = target_path.with_suffix(".zip")
        return self._export_modpack_file(folder, target_path)

    def _export_modpack_file(self, folder: Path, target: Path) -> dict:
        meta = _read_setup_meta(folder)
        mc_version = str(
            meta.get("VersionOriginal") or meta.get("VersionVanillaName") or ""
        )
        loader = extract_loader(meta)

        files: list[tuple[Path, str]] = []
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(folder)
            if rel.parts and rel.parts[0].lower() in modpack_install.EXPORT_SKIP_DIRS:
                continue
            files.append((path, rel.as_posix()))

        total = len(files)
        pack_setup = modpack_install.build_pack_setup(folder.name, mc_version, loader)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # jar / zip 本身已压缩，用 STORED 直接打包，速度更快
            with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as zf:
                zf.writestr(
                    f"{modpack_install.PACK_SUBDIR}/{modpack_install.SETUP_FILE}",
                    pack_setup,
                )
                for index, (path, rel) in enumerate(files, start=1):
                    zf.write(path, f"versions/{folder.name}/{rel}")
                    if index % 50 == 0:
                        self._emit_progress(
                            {
                                "kind": "modpack",
                                "done": index,
                                "total": total,
                                "text": f"正在打包整合包 {index}/{total}",
                            }
                        )
        except (OSError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": f"导出失败：{exc}"}

        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        return {"ok": True, "path": str(target), "size": size, "files": total}

    def get_version_mods(self, name: str, root: str = "") -> dict:
        """列出某版本已安装的模组（含已禁用）。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}
        return {"ok": True, "mods": mod_manage.scan_mods(version_game_dir(folder) / "mods")}

    def set_mod_enabled(
        self, name: str, root: str, file: str, enabled: bool
    ) -> dict:
        """启用 / 禁用模组：在 xxx.jar 与 xxx.jar.disabled 之间改名。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}

        mods_dir = version_game_dir(folder) / "mods"
        target = mod_manage.resolve_mod_file(mods_dir, file)
        if target is None:
            return {"ok": False, "error": "模组文件不存在"}

        try:
            mod_manage.set_enabled(target, bool(enabled))
        except FileExistsError as exc:
            return {"ok": False, "error": f"同名文件已存在：{exc}"}
        except OSError as exc:
            return {"ok": False, "error": f"无法修改模组状态：{exc}"}

        return {"ok": True, "mods": mod_manage.scan_mods(mods_dir)}

    def delete_version_mod(self, name: str, root: str, file: str) -> dict:
        """删除某个模组文件。"""
        folder = resolve_version_dir(name, root)
        if folder is None:
            return {"ok": False, "error": "版本不存在"}

        mods_dir = version_game_dir(folder) / "mods"
        target = mod_manage.resolve_mod_file(mods_dir, file)
        if target is None:
            return {"ok": False, "error": "模组文件不存在"}

        try:
            target.unlink()
        except OSError as exc:
            return {"ok": False, "error": f"删除失败：{exc}"}

        return {"ok": True, "mods": mod_manage.scan_mods(mods_dir)}

    def get_home_state(self) -> dict:
        """首页小组件布局与使用统计。"""
        data = read_home()
        widgets = data.get("widgets")
        if not isinstance(widgets, list):
            widgets = []
        last_launch = data.get("last_launch")
        return {
            "widgets": [str(item) for item in widgets if isinstance(item, str)],
            "play_seconds": max(0, int(data.get("play_seconds") or 0)),
            "last_launch": last_launch if isinstance(last_launch, dict) else None,
            "custom_widgets": normalize_custom_widgets(data),
        }

    def save_home_state(self, patch: dict) -> dict:
        """保存首页小组件布局 / 累计陪伴时长 / 上次启动记录。"""
        if not isinstance(patch, dict):
            return {"ok": False, "error": "参数无效"}
        data = read_home()

        widgets = patch.get("widgets")
        if isinstance(widgets, list):
            cleaned: list[str] = []
            for item in widgets[:24]:
                if isinstance(item, str) and item and item not in cleaned:
                    cleaned.append(item)
            data["widgets"] = cleaned

        seconds = patch.get("play_seconds")
        if isinstance(seconds, (int, float)) and seconds >= 0:
            data["play_seconds"] = int(seconds)

        last_launch = patch.get("last_launch")
        if isinstance(last_launch, dict):
            name = str(last_launch.get("name") or "").strip()
            if name:
                data["last_launch"] = {
                    "name": name,
                    "time": int(last_launch.get("time") or time.time()),
                }

        write_home(data)
        return {"ok": True, **self.get_home_state()}

    def save_custom_widget(self, patch: dict) -> dict:
        """新建或更新一个自定义 HTML 小组件。

        patch 里带 id 表示编辑已有条目，不带则新建并自动排到首页末尾。
        """
        if not isinstance(patch, dict):
            return {"ok": False, "error": "参数无效"}

        html = str(patch.get("html") or "")
        if not html.strip():
            return {"ok": False, "error": "HTML 内容不能为空"}
        if len(html) > CUSTOM_WIDGET_HTML_LIMIT:
            return {"ok": False, "error": "HTML 内容过大（上限 200 KB）"}

        data = read_home()
        items = normalize_custom_widgets(data)

        widget_id = str(patch.get("id") or "").strip()
        name = str(patch.get("name") or "").strip()[:24] or "自定义小组件"
        wide = bool(patch.get("wide"))

        target = next((item for item in items if item["id"] == widget_id), None)
        created = False
        if target is None:
            if len(items) >= CUSTOM_WIDGET_MAX:
                return {
                    "ok": False,
                    "error": f"最多只能添加 {CUSTOM_WIDGET_MAX} 个自定义小组件",
                }
            widget_id = f"{CUSTOM_WIDGET_ID_PREFIX}{os.urandom(4).hex()}"
            target = {"id": widget_id}
            items.append(target)
            created = True

        target["name"] = name
        target["html"] = html
        target["wide"] = wide
        data["custom_widgets"] = items

        widgets = data.get("widgets")
        widgets = [str(item) for item in widgets] if isinstance(widgets, list) else []
        if created and widget_id not in widgets:
            widgets.append(widget_id)
        data["widgets"] = widgets

        write_home(data)
        return {
            "ok": True,
            "created": created,
            "id": widget_id,
            "custom_widgets": items,
            **self.get_home_state(),
        }

    def delete_custom_widget(self, widget_id: str) -> dict:
        """删除自定义小组件，同时从首页布局里移除。"""
        key = str(widget_id or "").strip()
        if not key:
            return {"ok": False, "error": "参数无效"}

        data = read_home()
        data["custom_widgets"] = [
            item for item in normalize_custom_widgets(data) if item["id"] != key
        ]
        widgets = data.get("widgets")
        if isinstance(widgets, list):
            data["widgets"] = [item for item in widgets if str(item) != key]
        write_home(data)
        return {"ok": True, **self.get_home_state()}

    def import_custom_widget_file(self) -> dict:
        """选择本地网页文件，读成文本交给自定义小组件编辑器。"""
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪"}

        dialog_kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
        if dialog_kind is None:
            dialog_kind = webview.OPEN_DIALOG

        try:
            paths = window.create_file_dialog(
                dialog_kind,
                file_types=(
                    "网页文件 (*.html;*.htm)",
                    "文本文件 (*.txt)",
                    "所有文件 (*.*)",
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 对话框失败统一反馈
            return {"ok": False, "error": f"无法打开文件选择器：{exc}"}

        if not paths:
            return {"ok": True, "cancelled": True}

        source = Path(str(paths[0]))
        try:
            raw = source.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": f"读取文件失败：{exc}"}

        text = ""
        for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            return {"ok": False, "error": "无法识别文件编码"}

        if len(text) > CUSTOM_WIDGET_HTML_LIMIT:
            return {"ok": False, "error": "文件过大（上限 200 KB）"}

        return {"ok": True, "name": source.stem[:24], "html": text}

    def get_state(self) -> dict:
        """返回启动器状态（上次选中的版本等），并修复已失效的选择。"""
        state = read_state()
        selected = state.get("selected_version")
        selected_root = ""
        changed = False

        if isinstance(selected, dict):
            folder = self._locate_version_folder(selected)
            if folder is None:
                selected = None
            else:
                entry = build_version_entry(folder)
                if entry is None:
                    selected = None
                else:
                    selected = entry
                    selected_root = str(folder.parent)
                    if state.get("selected_version") != entry:
                        state["selected_version"] = entry
                        changed = True
            if selected is None:
                state.pop("selected_version", None)
                changed = True
        else:
            selected = None

        if changed:
            write_state(state)

        return {
            "selected_version": selected,
            "selected_root": selected_root,
            "versions_dir": str(VERSIONS_DIR),
            "java": self._resolve_current_java(),
            "settings": self.get_settings(),
            "account": self.get_account(),
            "authlib": self.get_authlib_account(),
        }

    def _locate_version_folder(self, selected: dict) -> Path | None:
        """按已保存的版本信息找回版本目录（路径失效时按版本名重试）。"""
        folder = Path(str(selected.get("path", "")))
        if folder.is_dir():
            return folder

        name = str(selected.get("name", "") or "").strip()
        if not name:
            return None

        bases = [VERSIONS_DIR]
        extras = read_state().get("version_roots")
        if isinstance(extras, list):
            bases.extend(Path(item) for item in extras if isinstance(item, str) and item.strip())
        for base in bases:
            candidate = base / name
            if candidate.is_dir():
                return candidate
        return None

    # ---- 正版账号（微软登录） -------------------------------------
    def get_account(self) -> dict:
        """返回正版账号概要。令牌只留在后端，不下发给界面。"""
        account = self._cached_account()
        if not account:
            return {"logged_in": False}
        return {
            "logged_in": True,
            "name": str(account.get("name") or ""),
            "uuid": str(account.get("uuid") or ""),
            "expired": ms_auth.is_expired(account),
        }

    def ms_login_start(self) -> dict:
        """发起设备码登录：申请代码后交给后台线程轮询，界面只查进度。"""
        self._stop_device_login()
        try:
            session = ms_auth.request_device_code()
        except ms_auth.AuthError as exc:
            return {"ok": False, "error": str(exc)}

        session["state"] = "pending"
        session["stage"] = "等待在浏览器中完成授权"
        session["error"] = ""
        session["stop"] = False
        session["deadline"] = time.monotonic() + int(session["expires_in"])
        self._device_login = session

        threading.Thread(
            target=self._device_login_worker, args=(session,), daemon=True
        ).start()
        return {
            "ok": True,
            "user_code": session["user_code"],
            "verification_uri": session["verification_uri"],
            "expires_in": session["expires_in"],
            "interval": session["interval"],
        }

    def ms_login_status(self) -> dict:
        """查询登录进度：只读内存，不发网络请求，可以高频调用。"""
        session = self._device_login
        if session is None:
            return {"ok": True, "state": "idle", "stage": "", "error": ""}

        payload = {
            "ok": True,
            "state": session["state"],
            "stage": session["stage"],
            "error": session["error"],
        }
        if session["state"] == "ok":
            payload["account"] = self.get_account()
        return payload

    def ms_login_cancel(self) -> dict:
        """放弃当前登录（关闭弹窗时调用）。"""
        self._stop_device_login()
        return {"ok": True}

    def _stop_device_login(self) -> None:
        session = self._device_login
        if session is not None:
            session["stop"] = True
        self._device_login = None

    def _device_login_worker(self, session: dict) -> None:
        """后台轮询授权结果，授权完成后直接走完整认证链。"""
        # 微软给的 interval 是下限，这里压到 2~3 秒，用户点完授权能更快回到界面
        interval = min(3.0, max(2.0, float(session["interval"])))

        while not session["stop"] and time.monotonic() < session["deadline"]:
            try:
                result = ms_auth.poll_device_token(
                    str(session["device_code"]), str(session["client_id"])
                )
            except ms_auth.AuthError as exc:
                self._fail_device_login(session, str(exc))
                return

            status = str(result.get("status") or "")
            if status == "ok":
                session["state"] = "working"
                session["stage"] = "正在验证 Xbox 账户"
                try:
                    account = ms_auth.complete_login(
                        result["token"],
                        str(session["client_id"]),
                        lambda text: session.__setitem__("stage", text),
                    )
                except ms_auth.AuthError as exc:
                    self._fail_device_login(session, str(exc))
                    return
                if session["stop"]:
                    return
                self._store_account(account)
                session["state"] = "ok"
                session["stage"] = "登录成功"
                return

            if status == "slow_down":
                interval = min(8.0, interval + 5.0)

            # 分片睡眠，关掉弹窗后线程能立刻退出
            waited = 0.0
            while waited < interval and not session["stop"]:
                time.sleep(0.1)
                waited += 0.1

        if not session["stop"] and session["state"] != "ok":
            self._fail_device_login(session, "登录码已过期，请重新登录")

    def _fail_device_login(self, session: dict, message: str) -> None:
        """记录失败原因，等界面下一次查进度时取走。"""
        if session["stop"]:
            return
        session["state"] = "error"
        session["error"] = message
        session["stage"] = message

    def ms_logout(self) -> dict:
        """退出正版账号并删除本地令牌。"""
        self._stop_device_login()
        self._store_account(None)
        return {"ok": True, "account": {"logged_in": False}}

    def open_external_url(self, url: str) -> dict:
        """用系统默认浏览器打开链接（仅允许 http/https）。"""
        text = str(url or "").strip()
        if not text.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "只允许打开 http/https 链接"}
        try:
            webbrowser.open(text)
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响其他功能
            return {"ok": False, "error": "无法打开浏览器"}
        return {"ok": True}

    def get_account_avatar(self) -> dict:
        """返回正版账号头像（皮肤头颅的 data URL），失败时由界面回落到默认头像。

        会先尝试同步一次正版档案，避免官网换皮肤 / 本地皮肤地址丢失后
        头像一直停留在旧皮肤或默认皮肤。
        """
        account = self._account_with_fresh_profile()
        if not account:
            return {"ok": False, "reason": "未登录"}
        return self._build_avatar(
            str(account.get("uuid") or ""),
            str(account.get("skin_url") or ""),
        )

    def _account_with_fresh_profile(self) -> dict | None:
        """返回正版账号，并按需从 Mojang 同步名字与皮肤地址。

        每隔 PROFILE_SYNC_INTERVAL 秒最多请求一次；令牌过期时会先静默续期，
        网络失败则退回本地缓存，不阻断头像显示。
        """
        account = self._active_account()
        if not account:
            return None

        now = time.time()
        if now - self._profile_synced_at < PROFILE_SYNC_INTERVAL:
            return account

        self._profile_synced_at = now
        try:
            profile = ms_auth.fetch_profile(str(account.get("access_token") or ""))
        except ms_auth.AuthError:
            return account

        fresh = dict(account)
        fresh["name"] = profile.get("name") or account.get("name")
        fresh["uuid"] = profile.get("uuid") or account.get("uuid")
        fresh["skin_url"] = profile.get("skin_url") or ""
        if fresh != account:
            self._store_account(fresh)
        # _store_account 会把同步时间归零，这里再恢复，避免每次头像请求都打 Mojang
        self._profile_synced_at = now
        return fresh

    def _build_avatar(self, cache_key: str, skin_url: str) -> dict:
        """按皮肤地址生成/复用头像：下载皮肤 → 裁头颅 → 缓存 → data URL。"""
        key = cache_key.replace("-", "").lower()
        if not key or not skin_url:
            return {"ok": False, "reason": "账号缺少皮肤信息"}

        head_file = SKINS_DIR / f"{key}-head.png"
        meta_file = SKINS_DIR / f"{key}.json"
        if head_file.is_file() and self._avatar_cache_valid(meta_file, skin_url):
            return {"ok": True, "avatar": _png_data_url(head_file)}

        skin_file = SKINS_DIR / f"{key}.png"
        try:
            SKINS_DIR.mkdir(parents=True, exist_ok=True)
            runtime_utils.download_file(skin_url, skin_file, label="皮肤")
            head_file.write_bytes(
                skin_avatar.head_from_skin(skin_file.read_bytes(), 64)
            )
            meta_file.write_text(
                json.dumps({"url": skin_url}, ensure_ascii=False), encoding="utf-8"
            )
        except (OSError, runtime_utils.DownloadError, skin_avatar.SkinError):
            return {"ok": False, "reason": "头像生成失败"}
        return {"ok": True, "avatar": _png_data_url(head_file)}

    def _avatar_cache_valid(self, meta_file: Path, skin_url: str) -> bool:
        """皮肤地址没变就复用已经生成好的头像。"""
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and str(data.get("url") or "") == skin_url

    # ---- 皮肤（正版上传 / 离线本地皮肤） ---------------------------
    def pick_ms_skin(self, variant: str = "classic") -> dict:
        """选择本地 PNG 上传为正版皮肤，成功后返回新的头像。"""
        account = self._active_account()
        if not account:
            return {"ok": False, "error": "请先登录正版账号"}
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}

        try:
            selection = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("皮肤图片 (*.png)",),
            )
        except Exception:  # noqa: BLE001 - 对话框失败统一提示
            return {"ok": False, "error": "无法打开文件对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        source = Path(selection[0] if isinstance(selection, (list, tuple)) else selection)
        try:
            data = source.read_bytes()
            width, height, _ = skin_avatar.decode_png(data)
        except OSError as exc:
            return {"ok": False, "error": f"读取皮肤失败：{exc}"}
        except skin_avatar.SkinError as exc:
            return {"ok": False, "error": str(exc)}
        if width != 64 or height not in (64, 32):
            return {"ok": False, "error": "正版皮肤尺寸必须是 64x64 或 64x32"}

        token = str(account.get("access_token") or "")
        try:
            ms_auth.upload_skin(token, data, variant)
        except ms_auth.AuthError as exc:
            return {"ok": False, "error": str(exc)}

        # 上传成功后档案里的皮肤地址会换成新的哈希，顺带更新本地缓存
        try:
            profile = ms_auth.fetch_profile(token)
        except ms_auth.AuthError:
            profile = {}
        if profile:
            account = dict(account)
            account["name"] = profile.get("name") or account.get("name")
            account["uuid"] = profile.get("uuid") or account.get("uuid")
            account["skin_url"] = profile.get("skin_url") or account.get("skin_url")
            self._store_account(account)

        avatar = self._build_avatar(
            str(account.get("uuid") or ""),
            str(account.get("skin_url") or ""),
        )
        avatar_data = avatar.get("avatar", "")
        result = {
            "ok": True,
            "avatar": avatar_data,
            "account": self.get_account(),
        }
        if not profile:
            result["warning"] = "皮肤已上传，但未能刷新档案，头像稍后更新"
        elif not avatar_data:
            result["warning"] = "皮肤已上传，但头像生成失败，请稍后重试"
        return result

    def get_offline_avatar(self, username: str = "") -> dict:
        """返回离线账号的自定义皮肤头像；没有设置过皮肤时 ok 为 False。"""
        name = (username or "").strip()
        if not name:
            return {"ok": False, "custom": False, "reason": "未填写玩家名"}

        skin_file = self._offline_skin_file(name)
        if not skin_file.is_file():
            return {"ok": False, "custom": False, "reason": "没有自定义皮肤"}

        head_file = self._offline_head_file(name)
        meta_file = self._offline_meta_file(name)
        try:
            mtime = skin_file.stat().st_mtime_ns
            if head_file.is_file() and self._local_avatar_valid(meta_file, mtime):
                return {"ok": True, "custom": True, "avatar": _png_data_url(head_file)}
            head_file.write_bytes(skin_avatar.head_from_skin(skin_file.read_bytes(), 64))
            meta_file.write_text(json.dumps({"mtime": mtime}), encoding="utf-8")
        except (OSError, skin_avatar.SkinError):
            return {"ok": False, "custom": False, "reason": "皮肤文件损坏"}
        return {"ok": True, "custom": True, "avatar": _png_data_url(head_file)}

    def pick_offline_skin(self, username: str = "") -> dict:
        """选择本地 PNG 作为离线账号皮肤，并尽量同步到 CustomSkinLoader。"""
        name = (username or "").strip()
        if not name:
            return {"ok": False, "error": "请先填写玩家名"}
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}

        try:
            selection = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("皮肤图片 (*.png)",),
            )
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "无法打开文件对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        source = Path(selection[0] if isinstance(selection, (list, tuple)) else selection)
        try:
            data = source.read_bytes()
            head = skin_avatar.head_from_skin(data, 64)
        except OSError as exc:
            return {"ok": False, "error": f"读取皮肤失败：{exc}"}
        except skin_avatar.SkinError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            SKINS_DIR.mkdir(parents=True, exist_ok=True)
            skin_file = self._offline_skin_file(name)
            skin_file.write_bytes(data)
            head_file = self._offline_head_file(name)
            head_file.write_bytes(head)
            self._offline_meta_file(name).write_text(
                json.dumps({"mtime": skin_file.stat().st_mtime_ns}), encoding="utf-8"
            )
        except OSError as exc:
            return {"ok": False, "error": f"保存皮肤失败：{exc}"}

        synced = self._sync_offline_skin(name, data)
        return {
            "ok": True,
            "custom": True,
            "avatar": _png_data_url(head_file),
            "synced": synced,
        }

    def clear_offline_skin(self, username: str = "") -> dict:
        """删除离线账号的自定义皮肤（启动器缓存与 CustomSkinLoader 副本）。"""
        name = (username or "").strip()
        if not name:
            return {"ok": False, "error": "请先填写玩家名"}

        skin_file = self._offline_skin_file(name)
        old = b""
        try:
            if skin_file.is_file():
                old = skin_file.read_bytes()
        except OSError:
            old = b""

        for path in (
            skin_file,
            self._offline_head_file(name),
            self._offline_meta_file(name),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        target = self._csl_skin_file(name)
        if old and target is not None and target.is_file():
            try:
                if target.read_bytes() == old:
                    target.unlink()
            except OSError:
                pass
        return {"ok": True, "custom": False}

    def export_skin(self, mode: str = "offline", username: str = "") -> dict:
        """把当前皮肤文件另存到用户选择的位置，方便外部编辑或备份。"""
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}

        default_name = "skin.png"
        if str(mode or "").strip().lower() == "microsoft":
            account = self._cached_account()
            if not account:
                return {"ok": False, "error": "请先登录正版账号"}
            player = str(account.get("name") or "skin")
            key = str(account.get("uuid") or "").replace("-", "").lower()
            skin_file = SKINS_DIR / f"{key}.png"
            meta_file = SKINS_DIR / f"{key}.json"
            skin_url = str(account.get("skin_url") or "")
            stale = bool(skin_url) and not self._avatar_cache_valid(meta_file, skin_url)
            if stale or not skin_file.is_file():
                if not skin_url:
                    return {"ok": False, "error": "该账号还没有自定义皮肤"}
                try:
                    SKINS_DIR.mkdir(parents=True, exist_ok=True)
                    runtime_utils.download_file(skin_url, skin_file, label="皮肤")
                    meta_file.write_text(
                        json.dumps({"url": skin_url}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except (OSError, runtime_utils.DownloadError) as exc:
                    return {"ok": False, "error": f"下载皮肤失败：{exc}"}
            source = skin_file
            default_name = f"{player}.png"
        else:
            player = (username or "").strip()
            if not player:
                return {"ok": False, "error": "请先填写玩家名"}
            skin_file = self._offline_skin_file(player)
            if not skin_file.is_file():
                return {"ok": False, "error": "当前没有自定义皮肤"}
            source = skin_file
            default_name = f"{player}.png"

        dialog_type = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
        if dialog_type is None:
            dialog_type = webview.SAVE_DIALOG
        try:
            selection = self._window.create_file_dialog(
                dialog_type,
                save_filename=default_name,
                file_types=("PNG 图片 (*.png)", "所有文件 (*.*)"),
            )
        except Exception:  # noqa: BLE001 - 平台差异较大，统一提示
            return {"ok": False, "error": "无法打开保存对话框"}
        if not selection:
            return {"ok": False, "cancelled": True}

        target_text = selection[0] if isinstance(selection, (list, tuple)) else selection
        target = Path(str(target_text))
        if not target.suffix:
            target = target.with_suffix(".png")
        try:
            target.write_bytes(source.read_bytes())
        except OSError as exc:
            return {"ok": False, "error": f"保存皮肤失败：{exc}"}
        return {"ok": True, "path": str(target)}

    def reset_ms_skin(self) -> dict:
        """把正版账号皮肤恢复成默认；重置前先把原皮肤备份到 skins/backup。"""
        account = self._active_account()
        if not account:
            return {"ok": False, "error": "请先登录正版账号"}

        key = str(account.get("uuid") or "").replace("-", "").lower()
        player = str(account.get("name") or key or "skin")
        skin_file = SKINS_DIR / f"{key}.png"
        meta_file = SKINS_DIR / f"{key}.json"
        skin_url = str(account.get("skin_url") or "")

        # 先把线上皮肤落到本地，再进行不可逆的重置
        try:
            if skin_url and (
                not skin_file.is_file()
                or not self._avatar_cache_valid(meta_file, skin_url)
            ):
                SKINS_DIR.mkdir(parents=True, exist_ok=True)
                runtime_utils.download_file(skin_url, skin_file, label="皮肤")
                meta_file.write_text(
                    json.dumps({"url": skin_url}, ensure_ascii=False),
                    encoding="utf-8",
                )
        except (OSError, runtime_utils.DownloadError):
            pass

        backup_path = ""
        if skin_file.is_file():
            try:
                backup_dir = SKINS_DIR / "backup"
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = backup_dir / f"{player}-{stamp}.png"
                backup.write_bytes(skin_file.read_bytes())
                backup_path = str(backup)
            except OSError:
                backup_path = ""

        try:
            ms_auth.reset_skin(str(account.get("access_token") or ""))
        except ms_auth.AuthError as exc:
            return {"ok": False, "error": str(exc)}

        for name in (f"{key}.png", f"{key}-head.png", f"{key}.json"):
            try:
                (SKINS_DIR / name).unlink(missing_ok=True)
            except OSError:
                pass

        account = dict(account)
        account["skin_url"] = ""
        self._store_account(account)
        return {
            "ok": True,
            "account": self.get_account(),
            "avatar": "",
            "backup": backup_path,
        }

    def _offline_skin_file(self, name: str) -> Path:
        """离线皮肤按玩家名（忽略大小写）存放，改名后不会串用。"""
        key = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:16]
        return SKINS_DIR / f"offline-{key}.png"

    def _offline_head_file(self, name: str) -> Path:
        skin_file = self._offline_skin_file(name)
        return skin_file.with_name(skin_file.stem + "-head.png")

    def _offline_meta_file(self, name: str) -> Path:
        return self._offline_skin_file(name).with_suffix(".json")

    def _local_avatar_valid(self, meta_file: Path, mtime: int) -> bool:
        """皮肤文件没变就复用已经生成好的头像。"""
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and data.get("mtime") == mtime

    def _csl_skin_dir(self) -> Path | None:
        """当前版本里 CustomSkinLoader 的本地皮肤目录（没装该模组时返回 None）。"""
        state = read_state()
        entry = state.get("selected_version")
        if not isinstance(entry, dict):
            return None
        folder = Path(str(entry.get("path") or ""))
        if not folder.is_dir():
            return None

        csl_dir = version_game_dir(folder) / "CustomSkinLoader"
        has_mod = False
        mods_dir = version_game_dir(folder) / "mods"
        if mods_dir.is_dir():
            try:
                has_mod = any(
                    "customskinloader" in item.name.lower()
                    for item in mods_dir.iterdir()
                )
            except OSError:
                has_mod = False
        if not has_mod and not csl_dir.is_dir():
            return None
        return csl_dir / "LocalSkin" / "skins"

    def _csl_skin_file(self, name: str) -> Path | None:
        folder = self._csl_skin_dir()
        if folder is None:
            return None
        return folder / f"{name}.png"

    def _sync_offline_skin(self, name: str, skin_bytes: bytes | None = None) -> bool:
        """把离线皮肤复制到 CustomSkinLoader 目录；未安装该模组时返回 False。"""
        name = (name or "").strip()
        target = self._csl_skin_file(name) if name else None
        if target is None:
            return False

        if skin_bytes is None:
            try:
                skin_bytes = self._offline_skin_file(name).read_bytes()
            except OSError:
                return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(skin_bytes)
        except OSError:
            return False
        return True

    def _cached_account(self) -> dict | None:
        """内存缓存的账号，首次访问时从磁盘读取。"""
        if not self._account_loaded:
            self._account_loaded = True
            self._account = ms_auth.load_account(AUTH_FILE)
        return self._account

    def _active_account(self) -> dict | None:
        """启动游戏前取账号：令牌过期时静默续期，失败则退回离线。"""
        account = self._cached_account()
        if not account:
            return None
        if not ms_auth.is_expired(account):
            return account
        try:
            fresh = ms_auth.refresh_account(account)
        except ms_auth.AuthError:
            self._store_account(None)
            return None
        self._store_account(fresh)
        return fresh

    def _store_account(self, account: dict | None) -> None:
        """更新内存与磁盘上的账号信息（account 为 None 表示退出登录）。"""
        self._account = account
        self._account_loaded = True
        # 账号信息变了，下一次请求头像时重新同步一次正版档案
        self._profile_synced_at = 0.0
        if account:
            try:
                ms_auth.save_account(AUTH_FILE, account)
            except OSError:
                pass
        else:
            ms_auth.clear_account(AUTH_FILE)

    # ---- 第三方外置登录（authlib-injector） ------------------------
    def get_authlib_account(self) -> dict:
        """返回第三方登录账号概要。令牌只留在后端。"""
        account = self._cached_authlib()
        if not account:
            return {"logged_in": False}
        return {
            "logged_in": True,
            "name": str(account.get("name") or ""),
            "uuid": str(account.get("uuid") or ""),
            "server": str(account.get("server") or ""),
        }

    def authlib_login(self, server: str, username: str, password: str) -> dict:
        """第三方外置登录：账号密码换令牌，并顺带取一次皮肤地址。"""
        user = str(username or "").strip()
        secret = str(password or "")
        if not user or not secret:
            return {"ok": False, "error": "请先填写账号和密码"}

        client_token = authlib_auth.new_client_token()
        try:
            account = authlib_auth.authenticate(server, user, secret, client_token)
        except authlib_auth.AuthlibError as exc:
            return {"ok": False, "error": str(exc)}

        account["skin_url"] = authlib_auth.fetch_skin_url(
            account["server"], account["uuid"]
        )
        account["user_type"] = "mojang"
        self._store_authlib(account)
        return {"ok": True, "account": self.get_authlib_account()}

    def authlib_logout(self) -> dict:
        """退出第三方账号，并尽量让服务器作废当前令牌。"""
        account = self._cached_authlib()
        if account:
            authlib_auth.invalidate(
                str(account.get("server") or ""),
                str(account.get("access_token") or ""),
                str(account.get("client_token") or ""),
            )
        self._store_authlib(None)
        return {"ok": True, "account": {"logged_in": False}}

    def get_authlib_avatar(self) -> dict:
        """返回第三方账号头像（皮肤头颅的 data URL）。"""
        account = self._cached_authlib()
        if not account:
            return {"ok": False, "reason": "未登录"}

        account_uuid = str(account.get("uuid") or "")
        skin_url = str(account.get("skin_url") or "")
        if not skin_url and account_uuid:
            skin_url = authlib_auth.fetch_skin_url(
                str(account.get("server") or ""), account_uuid
            )
            if skin_url:
                account["skin_url"] = skin_url
                self._store_authlib(account)
        return self._build_avatar("al" + account_uuid, skin_url)

    def _cached_authlib(self) -> dict | None:
        """内存缓存的第三方账号，首次访问时从磁盘读取。"""
        if not self._authlib_loaded:
            self._authlib_loaded = True
            try:
                data = json.loads(AUTHLIB_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            self._authlib = (
                data
                if isinstance(data, dict) and str(data.get("access_token") or "")
                else None
            )
        return self._authlib

    def _store_authlib(self, account: dict | None) -> None:
        """更新内存与磁盘上的第三方账号信息。"""
        self._authlib = account
        self._authlib_loaded = True
        if account:
            try:
                AUTHLIB_FILE.parent.mkdir(parents=True, exist_ok=True)
                AUTHLIB_FILE.write_text(
                    json.dumps(account, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        else:
            try:
                AUTHLIB_FILE.unlink(missing_ok=True)
            except OSError:
                pass

    def _active_authlib(self) -> dict | None:
        """启动前确认令牌可用，失效时尝试自动刷新。"""
        account = self._cached_authlib()
        if not account:
            return None

        server = str(account.get("server") or "")
        token = str(account.get("access_token") or "")
        client_token = str(account.get("client_token") or "")
        try:
            if authlib_auth.validate(server, token, client_token):
                return account
        except authlib_auth.AuthlibError:
            # 服务器暂时连不上时不要直接拦下启动，交给游戏自己判断
            return account

        try:
            refreshed = authlib_auth.refresh(server, token, client_token)
        except authlib_auth.AuthlibError:
            self._store_authlib(None)
            return None

        refreshed["skin_url"] = str(account.get("skin_url") or "")
        refreshed["user_type"] = "mojang"
        self._store_authlib(refreshed)
        return refreshed

    # ---- Java -----------------------------------------------------
    def get_java_state(self) -> dict:
        """返回当前使用的 Java 与扫描结果。"""
        return {
            "current": self._resolve_current_java(),
            "candidates": self._java_candidates(),
            "java_dir": str(JAVA_DIR),
        }

    def scan_java_env(self) -> dict:
        """全盘扫描系统里的 Java（设置页「自动扫描」）。"""
        try:
            self._java_cache = java_runtime.scan_java(
                [JAVA_DIR], deep=True, on_progress=self._java_scan_progress
            )
        finally:
            self._notify_java_scan({"done": True})
        return {
            "current": self._resolve_current_java(),
            "candidates": self._java_cache,
            "java_dir": str(JAVA_DIR),
        }

    def _java_scan_progress(self, visited: int, path: str) -> None:
        self._notify_java_scan({"count": int(visited), "path": str(path)})

    def _notify_java_scan(self, payload: dict) -> None:
        window = self._window
        if window is None:
            return
        try:
            window.evaluate_js(
                "window.mchl && window.mchl.javaScan("
                + json.dumps(payload, ensure_ascii=False)
                + ");"
            )
        except Exception:  # noqa: BLE001 - 前端尚未就绪时忽略
            pass

    def set_java_path(self, path: str) -> dict:
        """手动指定 Java 路径。"""
        info = java_runtime.probe_java(Path(path)) if isinstance(path, str) else None
        if info is None:
            return {"ok": False, "error": "所选文件不是有效的 java.exe"}
        write_global_settings({"JavaPath": info["path"]})
        return {"ok": True, "java": info}

    def pick_java_file(self, apply: bool = True) -> dict:
        """通过文件对话框手动添加 java.exe。

        apply 为 False 时只返回探测结果，用于版本级 Java 设置。
        """
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}
        try:
            selection = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Java 可执行文件 (*.exe)",),
            )
        except Exception:
            return {"ok": False, "error": "无法打开文件对话框"}

        if not selection:
            return {"ok": False, "cancelled": True}

        path = selection[0] if isinstance(selection, (list, tuple)) else selection
        info = java_runtime.probe_java(Path(str(path)))
        if info is None:
            return {"ok": False, "error": "所选文件不是有效的 java.exe"}

        if apply:
            write_global_settings({"JavaPath": info["path"]})
        return {"ok": True, "java": info}

    def download_java(self, major: int = 0) -> dict:
        """下载指定主版本的 JRE（默认按当前版本需求）。"""
        required = int(major or 0)
        if required <= 0:
            required = self._required_java_major()
        self._emit_progress({"kind": "java", "done": 0, "total": 0, "text": f"准备下载 Java {required}"})
        info = java_runtime.download_java(required, JAVA_DIR, self._progress_callback("java"))
        if info is None:
            return {"ok": False, "error": f"Java {required} 下载失败，请检查网络或手动添加 Java"}
        write_global_settings({"JavaPath": info["path"]})
        self._java_cache = None
        self._emit_progress({"kind": "java", "done": 1, "total": 1, "text": "Java 下载完成"})
        return {"ok": True, "java": info}

    # ---- 基岩版 ---------------------------------------------------
    def get_bedrock_state(self) -> dict:
        """返回基岩版的系统支持情况与各渠道的本地安装信息。"""
        supported = bedrock_manage.is_supported()
        channels: list[dict] = []
        if supported:
            for key in ("release", "preview"):
                meta = bedrock_manage.channel_meta(key)
                channels.append(
                    {
                        "id": key,
                        "label": meta["label"],
                        "name": meta["name"],
                        "installed": bedrock_manage.installed_info(key),
                    }
                )
        installed = next(
            (item["installed"] for item in channels if item["id"] == "release"), None
        )
        return {
            "supported": supported,
            "build": bedrock_manage.sys_build(),
            "installed": installed,
            "channels": channels,
            "download_dir": str(BEDROCK_DIR),
        }

    def list_bedrock_versions(self) -> dict:
        """列出各渠道当前可下载的版本，以及已下载到本地的安装包。"""
        if not bedrock_manage.is_supported():
            return {"versions": [], "errors": ["当前系统不支持基岩版"], "download_dir": str(BEDROCK_DIR)}

        versions: list[dict] = []
        errors: list[str] = []
        for key in ("release", "preview"):
            meta = bedrock_manage.channel_meta(key)
            try:
                brief = bedrock_manage.version_brief(key)
            except bedrock_manage.BedrockError as exc:
                errors.append(f"{meta['label']}：{exc}")
                continue
            version = str(brief.get("version") or "")
            versions.append(
                {
                    "id": key,
                    "channel": key,
                    "label": meta["label"],
                    "name": brief.get("name") or meta["name"],
                    "version": version,
                    "size": int(brief.get("size") or 0),
                    "files": int(brief.get("files") or 0),
                    "cached": bool(version and self._bedrock_cached_dir(key, version)),
                }
            )

        versions.extend(self._bedrock_cached_versions())
        return {
            "versions": versions,
            "errors": errors,
            "download_dir": str(BEDROCK_DIR),
        }

    def launch_bedrock(self, channel: str = "release") -> dict:
        """启动某个渠道已安装的基岩版（走 shell:appsFolder）。"""
        return bedrock_manage.launch(channel)

    def open_bedrock_store(self, channel: str = "release") -> dict:
        """打开 Microsoft Store 里对应渠道的基岩版页面。"""
        return bedrock_manage.open_store(channel)

    def open_xbox_app(self) -> dict:
        """打开 Xbox 应用（基岩版账号在游戏内登录）。"""
        return bedrock_manage.open_xbox_app()

    def install_bedrock_version(self, version_id: str = "release") -> dict:
        """下载并安装指定渠道（或已下载到本地的版本）的基岩版。

        version_id 取值：release / preview，或 list_bedrock_versions 返回的 cached:<目录名>。
        """
        if not bedrock_manage.is_supported():
            return {"ok": False, "error": "当前系统不支持基岩版（需要 Windows 10 1809 及以上）"}

        raw = str(version_id or "").strip()
        if raw.startswith("cached:"):
            return self._install_bedrock_cached(raw.split(":", 1)[1])

        channel = raw or "release"
        meta = bedrock_manage.channel_meta(channel)
        self._bedrock_progress(f"正在查询{meta['label']}版本…")
        try:
            payload = bedrock_manage.latest_files(
                progress=self._bedrock_progress, channel=channel
            )
        except bedrock_manage.BedrockError as exc:
            self._bedrock_progress("查询版本失败")
            return {"ok": False, "error": f"查询版本失败：{exc}"}

        version = str(payload.get("version") or "")
        target = self._bedrock_cached_dir(channel, version) or (
            BEDROCK_DIR / f"{channel}-{version or 'latest'}"
        )
        try:
            paths = bedrock_manage.download_packages(
                payload, target, progress=self._bedrock_download_progress
            )
        except bedrock_manage.BedrockError as exc:
            self._bedrock_progress("下载失败")
            return {"ok": False, "error": f"下载失败：{exc}"}

        result = bedrock_manage.install_packages(
            paths, progress=self._bedrock_progress, channel=channel
        )
        if not result.get("ok"):
            return result

        self._emit_progress(
            {
                "kind": "bedrock",
                "done": 1,
                "total": 1,
                "text": f"{meta['label']} {version} 安装完成",
            }
        )
        return {"ok": True, "version": version, "channel": channel, "label": meta["label"]}

    def install_bedrock_latest(self) -> dict:
        """下载并安装最新正式版（保留旧接口）。"""
        return self.install_bedrock_version("release")

    def list_bedrock_mirror(self, force: bool = False) -> dict:
        """第三方版本库（mcappx.com）里的全部 UWP 版本，含历史版本。"""
        try:
            data = bedrock_manage.mirror_versions(force=bool(force))
        except bedrock_manage.BedrockError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "versions": [],
                "source": bedrock_manage.MIRROR_SOURCE,
            }

        release = bedrock_manage.installed_info("release") or {}
        preview = bedrock_manage.installed_info("preview") or {}
        return {
            "ok": True,
            "source": bedrock_manage.MIRROR_SOURCE,
            "updated": data.get("updated") or "",
            "installed": {
                "release": release.get("version", ""),
                "preview": preview.get("version", ""),
            },
            "versions": data.get("versions") or [],
        }

    def install_bedrock_mirror(self, version: str, allow_downgrade: bool = False) -> dict:
        """下载并安装版本库里的指定版本；低于已安装版本时必须先卸载。"""
        if not bedrock_manage.is_supported():
            return {"ok": False, "error": "当前系统不支持基岩版（需要 Windows 10 1809 及以上）"}

        target = str(version or "").strip()
        if not target:
            return {"ok": False, "error": "未指定要安装的版本"}
        try:
            data = bedrock_manage.mirror_versions()
        except bedrock_manage.BedrockError as exc:
            return {"ok": False, "error": str(exc)}

        entry = next(
            (item for item in (data.get("versions") or []) if item.get("version") == target),
            None,
        )
        if entry is None:
            return {"ok": False, "error": "版本库里没有这个版本"}
        if not entry.get("available"):
            return {"ok": False, "error": "该版本的安装包已无法获取（更新记录已失效或已丢失）"}
        if not entry.get("installable"):
            return {
                "ok": False,
                "error": "GDK 版本只提供 .msixvc 安装包，无法用启动器安装，请点「下载」后通过 Microsoft Store 安装",
            }

        self._bedrock_progress(f"正在获取 {target} 的下载地址…")
        try:
            resolved = bedrock_manage.resolve_update_url(entry.get("update_id") or "")
        except bedrock_manage.BedrockError as exc:
            self._bedrock_progress("获取下载地址失败")
            return {"ok": False, "error": str(exc)}

        probe = bedrock_manage.probe_package(resolved["url"])
        filename = probe.get("filename") or f"Microsoft.MinecraftUWP_{target}_x64.appx"
        channel = bedrock_manage.package_channel(filename)
        meta = bedrock_manage.channel_meta(channel)
        package_version = bedrock_manage.package_version(filename)

        current = bedrock_manage.installed_info(channel) or {}
        current_version = str(current.get("version") or "")
        downgrade = bool(
            current_version
            and package_version
            and _version_parts(current_version) > _version_parts(package_version)
        )
        if downgrade and not allow_downgrade:
            return {
                "ok": False,
                "need_downgrade": True,
                "installed": current_version,
                "target_version": package_version,
                "label": meta["label"],
                "error": (
                    f"已安装 {current_version}，安装 {package_version} 需要先卸载现有版本"
                ),
            }

        folder = BEDROCK_DIR / f"mirror-{target}"
        try:
            package = bedrock_manage.download_update(
                resolved["url"],
                folder,
                filename=filename,
                progress=self._bedrock_download_progress,
            )
        except bedrock_manage.BedrockError as exc:
            self._bedrock_progress("下载失败")
            return {"ok": False, "error": str(exc)}

        if downgrade:
            self._bedrock_progress(f"正在卸载 {current_version}…")
            removed = bedrock_manage.uninstall(channel, progress=self._bedrock_progress)
            if not removed.get("ok"):
                return removed

        result = bedrock_manage.install_packages(
            [package], progress=self._bedrock_progress, channel=channel
        )
        if not result.get("ok"):
            return result

        self._emit_progress(
            {
                "kind": "bedrock",
                "done": 1,
                "total": 1,
                "text": f"基岩版 {target} 安装完成",
            }
        )
        return {
            "ok": True,
            "version": target,
            "channel": channel,
            "label": meta["label"],
            "removed": downgrade,
        }

    def download_bedrock_mirror(self, version: str) -> dict:
        """只下载版本库里的安装包（GDK 的 msixvc 只能下载，不能用启动器安装）。"""
        target = str(version or "").strip()
        if not target:
            return {"ok": False, "error": "未指定要下载的版本"}
        try:
            data = bedrock_manage.mirror_versions()
        except bedrock_manage.BedrockError as exc:
            return {"ok": False, "error": str(exc)}

        entry = next(
            (item for item in (data.get("versions") or []) if item.get("version") == target),
            None,
        )
        if entry is None:
            return {"ok": False, "error": "版本库里没有这个版本"}
        if not entry.get("available"):
            return {"ok": False, "error": "该版本的安装包已无法获取（更新记录已失效或已丢失）"}

        folder = BEDROCK_DIR / f"mirror-{target}"
        build_type = str(entry.get("build_type") or "UWP")

        if build_type == "GDK":
            self._bedrock_progress(f"正在下载 {target} 的 msixvc…")
            try:
                package = bedrock_manage.download_msixvc(
                    entry.get("url") or "",
                    folder,
                    version=target,
                    progress=self._bedrock_download_progress,
                )
            except bedrock_manage.BedrockError as exc:
                self._bedrock_progress("下载失败")
                return {"ok": False, "error": str(exc)}
        else:
            self._bedrock_progress(f"正在获取 {target} 的下载地址…")
            try:
                resolved = bedrock_manage.resolve_update_url(entry.get("update_id") or "")
            except bedrock_manage.BedrockError as exc:
                self._bedrock_progress("获取下载地址失败")
                return {"ok": False, "error": str(exc)}
            probe = bedrock_manage.probe_package(resolved["url"])
            filename = probe.get("filename") or f"Microsoft.MinecraftUWP_{target}_x64.appx"
            try:
                package = bedrock_manage.download_update(
                    resolved["url"],
                    folder,
                    filename=filename,
                    progress=self._bedrock_download_progress,
                )
            except bedrock_manage.BedrockError as exc:
                self._bedrock_progress("下载失败")
                return {"ok": False, "error": str(exc)}

        size = 0
        try:
            size = package.stat().st_size
        except OSError:
            size = 0
        self._emit_progress(
            {
                "kind": "bedrock",
                "done": 1,
                "total": 1,
                "text": f"{target} 安装包下载完成",
                "task": "bedrock",
            }
        )
        return {
            "ok": True,
            "version": target,
            "build_type": build_type,
            "path": str(package),
            "size": size,
            "downloaded_only": True,
        }

    def open_bedrock_save_dir(self) -> bool:
        """打开基岩版存档目录，方便降级前备份。"""
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return False
        for family in (
            "Microsoft.MinecraftUWP_8wekyb3d8bbwe",
            "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe",
        ):
            worlds = (
                Path(local)
                / "Packages"
                / family
                / "LocalState"
                / "games"
                / "com.mojang"
                / "minecraftWorlds"
            )
            for candidate in (worlds, worlds.parent, worlds.parent.parent):
                if not candidate.is_dir():
                    continue
                try:
                    os.startfile(str(candidate))  # type: ignore[attr-defined]
                    return True
                except Exception:  # noqa: BLE001 - 打开失败直接返回
                    return False
        return False

    def _bedrock_cached_dir(self, channel: str, version: str) -> Path | None:
        """已下载的安装包目录（按渠道 + 版本号命名）。"""
        name = f"{channel}-{version}" if version else ""
        if not name:
            return None
        folder = BEDROCK_DIR / name
        return folder if folder.is_dir() else None

    def _bedrock_packages(self, folder: Path) -> list[Path]:
        """目录里所有可安装的包文件。"""
        suffixes = (".appx", ".msix", ".appxbundle", ".msixbundle")
        try:
            return sorted(
                item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in suffixes
            )
        except OSError:
            return []

    def _bedrock_cached_versions(self) -> list[dict]:
        """扫描下载目录，把已经下好的安装包也列进版本列表。"""
        try:
            folders = [item for item in BEDROCK_DIR.iterdir() if item.is_dir()]
        except OSError:
            return []

        cached: list[dict] = []
        for folder in sorted(folders, key=lambda item: item.name, reverse=True):
            packages = self._bedrock_packages(folder)
            if not packages:
                continue
            channel = bedrock_manage.package_channel(packages[0].name)
            meta = bedrock_manage.channel_meta(channel)
            version = folder.name.split("-", 1)[1] if "-" in folder.name else folder.name
            total = 0
            for item in packages:
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
            cached.append(
                {
                    "id": f"cached:{folder.name}",
                    "channel": channel,
                    "label": meta["label"],
                    "name": "已下载的安装包",
                    "version": version,
                    "size": total,
                    "files": len(packages),
                    "cached": True,
                }
            )
        return cached

    def _install_bedrock_cached(self, folder_name: str) -> dict:
        """安装已经下载到本地的安装包。"""
        name = str(folder_name or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return {"ok": False, "error": "安装包目录无效"}
        folder = BEDROCK_DIR / name
        packages = self._bedrock_packages(folder)
        if not folder.is_dir() or not packages:
            return {"ok": False, "error": "安装包已不存在，请重新下载"}

        channel = bedrock_manage.package_channel(packages[0].name)
        self._bedrock_progress(f"正在安装本地安装包 {name}…")
        result = bedrock_manage.install_packages(
            packages, progress=self._bedrock_progress, channel=channel
        )
        if result.get("ok"):
            self._emit_progress(
                {"kind": "bedrock", "done": 1, "total": 1, "text": "安装完成"}
            )
        return result

    def install_bedrock_local(self) -> dict:
        """选择本地安装包并安装基岩版。"""
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪"}
        try:
            selection = window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("基岩版安装包 (*.msix;*.msixbundle;*.appx;*.appxbundle)",),
            )
        except Exception as exc:  # noqa: BLE001 - 对话框失败统一反馈
            return {"ok": False, "error": f"无法打开文件选择器：{exc}"}
        if not selection:
            return {"ok": True, "cancelled": True}

        path = selection[0] if isinstance(selection, (list, tuple)) else selection
        self._bedrock_progress("正在安装本地安装包…")
        result = bedrock_manage.install_local(str(path), progress=self._bedrock_progress)
        if result.get("ok"):
            self._emit_progress(
                {"kind": "bedrock", "done": 1, "total": 1, "text": "安装完成"}
            )
        return result

    def open_bedrock_dir(self) -> bool:
        """打开基岩版安装包的下载目录。"""
        try:
            BEDROCK_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(BEDROCK_DIR))  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    # 基岩版的进度同时驱动页面进度条和右下角下载任务（task 固定为 bedrock）
    def _bedrock_progress(self, text: str) -> None:
        self._emit_progress(
            {"kind": "bedrock", "done": 0, "total": 0, "text": str(text), "task": "bedrock"}
        )

    def _bedrock_download_progress(self, done: int, total: int, text: str = "") -> None:
        self._emit_progress(
            {"kind": "bedrock", "done": done, "total": total, "text": text, "task": "bedrock"}
        )

    # ---- 下载板块 -------------------------------------------------
    def fetch_versions(self, mirror: bool = False) -> dict:
        """获取可下载的游戏版本清单（官方源 / 镜像源）。"""
        return version_download.fetch_manifest(bool(mirror))

    def download_version(self, version_id: str, mirror: bool = False) -> dict:
        """下载并安装一个原版游戏版本。"""
        return self.download_game(version_id, "vanilla", "", mirror)

    def download_game(
        self,
        version_id: str,
        loader: str = "vanilla",
        loader_version: str = "",
        mirror: bool = False,
        name: str = "",
        install_fabric_api: bool = False,
        fabric_api_version: str = "",
    ) -> dict:
        """下载游戏，并在需要时一并安装模组加载器。

        name 用于自定义版本文件夹名（不能与已有版本重名）；
        install_fabric_api 为真时，Fabric 版本会顺带下载 Fabric API，
        fabric_api_version 留空表示使用自动匹配的最新版本。
        """
        if not isinstance(version_id, str) or not version_id:
            return {"ok": False, "error": "缺少版本号"}

        loader = str(loader or "vanilla").lower()
        if loader not in ("vanilla", *loader_install.SETUP_FIELDS):
            return {"ok": False, "error": f"暂不支持加载器 {loader}"}

        folder_name = (str(name or "")).strip() or version_id
        problem = validate_version_name(folder_name)
        if problem:
            return {"ok": False, "error": problem}

        # 加载器版本不能与原版共用目录，否则安装时必然冲突
        if loader != "vanilla" and folder_name == version_id:
            label = loader_install.LOADER_LABELS.get(loader, loader)
            folder_name = f"{version_id}-{label}"
            problem = validate_version_name(folder_name)
            if problem:
                return {"ok": False, "error": problem}

        existing = VERSIONS_DIR / folder_name
        if existing.exists():
            if _is_valid_version_dir(existing):
                # 之前只作为加载器依赖安装的原版，用户显式下载同名原版时转为可见
                if loader == "vanilla" and _is_hidden_version_dir(existing):
                    if not _unhide_version_dir(existing):
                        return {
                            "ok": False,
                            "error": f"版本 “{folder_name}” 已作为依赖安装，恢复显示失败，请重试",
                        }
                    self._emit_progress(
                        {"kind": "done", "done": 1, "total": 1, "text": f"{folder_name} 已存在"}
                    )
                    return {
                        "ok": True,
                        "version": folder_name,
                        "path": str(existing),
                        "type_label": "",
                    }
                return {"ok": False, "error": f"版本名 “{folder_name}” 已存在"}
            # 上次下载中断留下的残缺目录（没有 Setup.ini），清理后重新安装
            if not _remove_residual_version_dir(existing):
                return {
                    "ok": False,
                    "error": f"目录 “{folder_name}” 已存在且无法清理，请手动删除后重试",
                }

        self._emit_progress(
            {"kind": "download", "done": 0, "total": 0, "text": f"准备下载 {version_id}"}
        )

        if loader == "vanilla":
            result = version_download.install_version(
                version_id,
                bool(mirror),
                VERSIONS_DIR,
                LIBRARIES_DIR,
                ASSETS_DIR,
                self._dependency_progress,
                target_name=folder_name,
            )
            if result.get("ok"):
                self._emit_progress(
                    {"kind": "done", "done": 1, "total": 1, "text": f"{folder_name} 下载完成"}
                )
            return result

        # 加载器版本需要继承标准命名的原版，因此父版本沿用版本号目录
        base_dir = VERSIONS_DIR / version_id
        created_parent = False
        if not base_dir.is_dir() or not _is_valid_version_dir(base_dir):
            # 残缺目录先清理，避免加载器继承到一个无效的原版
            if base_dir.exists() and not _remove_residual_version_dir(base_dir):
                return {
                    "ok": False,
                    "error": f"原版目录 “{version_id}” 已存在且无法清理，请手动删除后重试",
                }
            base_result = version_download.install_version(
                version_id,
                bool(mirror),
                VERSIONS_DIR,
                LIBRARIES_DIR,
                ASSETS_DIR,
                self._dependency_progress,
                # 仅作为 Fabric/Quilt/Forge 的依赖，不单独出现在版本列表
                hidden=True,
            )
            if not base_result.get("ok"):
                return base_result
            created_parent = True

        result = self.install_loader(
            loader, version_id, loader_version, mirror, target_name=folder_name
        )

        if result.get("ok"):
            target_dir = Path(str(result.get("path") or (VERSIONS_DIR / folder_name)))
            # 把原版文件并进加载器目录，使其独立成单个 versions 文件夹
            merged = game_launcher.flatten_version(
                target_dir, VERSIONS_DIR, folder_name
            )
            if merged and (created_parent or _is_hidden_version_dir(base_dir)):
                _remove_orphan_dependency(version_id, VERSIONS_DIR)

        if result.get("ok") and install_fabric_api and loader == "fabric":
            target_dir = Path(str(result.get("path") or (VERSIONS_DIR / folder_name)))
            self._emit_progress(
                {"kind": "fabricapi", "done": 0, "total": 0, "text": "正在下载 Fabric API"}
            )
            api_result = version_download.install_fabric_api(
                target_dir,
                version_id,
                str(fabric_api_version or ""),
                self._dependency_progress,
            )
            result["fabric_api"] = api_result
            if not api_result.get("ok"):
                result["warning"] = api_result.get("error", "Fabric API 下载失败")

        return result

    def fetch_loaders(self, mc_version: str, mirror: bool = False) -> dict:
        """查询某个原版版本可用的模组加载器版本。"""
        return loader_install.list_loaders(str(mc_version or ""), bool(mirror))

    def fetch_loader_versions(
        self, loader: str, mc_version: str, mirror: bool = False
    ) -> dict:
        """查询单个加载器在指定游戏版本下可用的版本（新→旧）。"""
        loader = str(loader or "").lower()
        if loader not in loader_install.SETUP_FIELDS:
            return {"ok": False, "error": "请选择有效的模组加载器"}

        version_id = str(mc_version or "").strip()
        if not version_id:
            return {"ok": False, "error": "缺少游戏版本"}

        versions = [
            item
            for item in loader_install.available_versions(loader, version_id, bool(mirror))
            if item
        ]
        if not versions:
            label = loader_install.LOADER_LABELS.get(loader, loader)
            return {"ok": False, "error": f"{label} 暂不支持 {version_id}"}

        return {"ok": True, "loader": loader, "versions": versions[:60]}

    def fetch_fabric_api(self, mc_version: str) -> dict:
        """查询适配指定游戏版本的 Fabric API。"""
        return version_download.fetch_fabric_api(str(mc_version or ""))

    def install_loader(
        self,
        loader: str,
        mc_version: str,
        loader_version: str = "",
        mirror: bool = False,
        target_name: str = "",
    ) -> dict:
        """为指定原版版本安装模组加载器。"""
        loader = str(loader or "").lower()
        if loader not in loader_install.SETUP_FIELDS:
            return {"ok": False, "error": f"暂不支持加载器 {loader or '-'}"}
        if not mc_version or not (VERSIONS_DIR / str(mc_version)).is_dir():
            return {"ok": False, "error": f"请先下载原版 {mc_version or ''}".strip()}
        if not loader_version:
            versions = loader_install.available_versions(
                loader, str(mc_version or ""), bool(mirror)
            )
            if not versions:
                return {"ok": False, "error": "未找到可用的加载器版本"}
            loader_version = versions[0]

        java_exe = ""
        required = loader_install.INSTALLER_JAVA.get(loader)
        if required:
            self._emit_progress(
                {"kind": "loader", "done": 0, "total": 0, "text": "正在准备 Java 运行时"}
            )
            java_info = self._pick_java(required)
            if java_info is None:
                java_info = java_runtime.download_java(
                    required, JAVA_DIR, self._progress_callback("java")
                )
            if java_info is None:
                return {"ok": False, "error": f"未找到可用的 Java {required}"}
            java_exe = java_info["path"]
            game_launcher.set_native_arch_from_java(java_exe)

        self._emit_progress(
            {
                "kind": "loader",
                "done": 0,
                "total": 0,
                "text": f"正在安装 {loader_install.LOADER_LABELS.get(loader, loader)} {loader_version}",
            }
        )
        result = loader_install.install_loader(
            loader,
            str(mc_version or ""),
            str(loader_version),
            DATA_DIR,
            VERSIONS_DIR,
            LIBRARIES_DIR,
            java_exe=java_exe,
            mirror=bool(mirror),
            on_progress=self._loader_progress,
            target_name=str(target_name or ""),
        )
        if result.get("ok"):
            self._emit_progress(
                {"kind": "done", "done": 1, "total": 1, "text": "模组适配器安装完成"}
            )
        return result

    # ---- 模组下载 -------------------------------------------------
    def search_mods(
        self,
        source: str = "all",
        query: str = "",
        mc_version: str = "",
        loader: str = "",
        limit: int = 30,
        offset: int = 0,
        project_type: str = "mod",
    ) -> dict:
        """从 CurseForge / Modrinth 搜索内容，支持翻页。

        project_type：mod / modpack / resourcepack / datapack。
        """
        return mod_download.search(
            source,
            query,
            mc_version,
            loader,
            limit=limit,
            offset=offset,
            project_type=project_type,
        )

    def fetch_mod_versions(
        self,
        source: str,
        project_id: str,
        mc_version: str = "",
        loader: str = "",
        limit: int = 60,
        project_type: str = "mod",
    ) -> dict:
        """获取项目的可下载文件；不传版本 / 加载器时返回全部，由界面自行筛选。"""
        return mod_download.fetch_versions(
            source,
            project_id,
            mc_version,
            loader,
            limit=limit,
            project_type=project_type,
        )

    def resolve_mod_save_path(
        self,
        file_name: str = "",
        name: str = "",
        root: str = "",
        project_type: str = "mod",
    ) -> dict:
        """直接算出当前选中版本下的默认保存路径（不弹对话框）。

        模组 → mods，整合包 → modpacks，资源包 → resourcepacks，
        光影包 → shaderpacks，数据包 → datapacks。
        """
        content_type = str(project_type or "mod").strip().lower()
        if content_type not in (
            "mod",
            "modpack",
            "resourcepack",
            "shaderpack",
            "datapack",
        ):
            content_type = "mod"
        folders = {
            "mod": "mods",
            "modpack": "modpacks",
            "resourcepack": "resourcepacks",
            "shaderpack": "shaderpacks",
            "datapack": "datapacks",
        }

        folder = resolve_version_dir(name, root) if name else None
        if folder is None:
            # 前端没传版本信息时，退回当前保存的选择
            entry = read_state().get("selected_version")
            if isinstance(entry, dict):
                candidate = Path(str(entry.get("path") or ""))
                if candidate.is_dir():
                    folder = candidate
        if folder is None:
            return {"ok": False, "error": "还没有选择版本，无法确定保存目录"}

        target_dir = folder / folders[content_type]
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"无法创建保存目录：{exc}"}

        suggested = Path(str(file_name or "")).name
        if not suggested:
            suggested = "mod.jar" if content_type == "mod" else "download.zip"
        return {
            "ok": True,
            "path": str(target_dir / suggested),
            "dir": str(target_dir),
        }

    def pick_mod_save_path(
        self,
        file_name: str = "",
        name: str = "",
        root: str = "",
        project_type: str = "mod",
    ) -> dict:
        """弹出保存对话框，按内容类型默认定位到对应目录。"""
        if self._window is None:
            return {"ok": False, "error": "窗口不可用"}

        content_type = str(project_type or "mod").strip().lower()
        if content_type not in (
            "mod",
            "modpack",
            "resourcepack",
            "shaderpack",
            "datapack",
        ):
            content_type = "mod"
        folders = {
            "mod": "mods",
            "modpack": "modpacks",
            "resourcepack": "resourcepacks",
            "shaderpack": "shaderpacks",
            "datapack": "datapacks",
        }

        suggested = Path(str(file_name or "")).name
        if not suggested:
            suggested = "mod.jar" if content_type == "mod" else "download.zip"
        folder = resolve_version_dir(name, root) if name else None
        if folder is None:
            # 前端没传版本信息时，退回当前保存的选择
            entry = read_state().get("selected_version")
            if isinstance(entry, dict):
                candidate = Path(str(entry.get("path") or ""))
                if candidate.is_dir():
                    folder = candidate
        start_dir = (
            folder / folders[content_type] if folder is not None else DATA_DIR
        )
        try:
            start_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            start_dir = DATA_DIR

        # pywebview 6 起推荐使用 FileDialog 枚举，旧常量仍可用
        dialog_type = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
        if dialog_type is None:
            dialog_type = webview.SAVE_DIALOG

        if content_type == "mod":
            file_types = ("Mod 文件 (*.jar)", "所有文件 (*.*)")
        else:
            file_types = ("Zip 压缩包 (*.zip)", "所有文件 (*.*)")

        try:
            selection = self._window.create_file_dialog(
                dialog_type,
                directory=str(start_dir),
                save_filename=suggested,
                file_types=file_types,
            )
        except Exception:  # noqa: BLE001 - 平台差异较大，统一提示
            return {"ok": False, "error": "无法打开保存对话框"}

        if not selection:
            return {"ok": False, "cancelled": True}
        chosen = selection[0] if isinstance(selection, (list, tuple)) else selection
        text = str(chosen or "").strip()
        if not text:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": text}

    def download_mod(
        self,
        url: str,
        dest: str,
        task_id: str = "",
        name: str = "",
        project_type: str = "mod",
    ) -> dict:
        """下载文件，并把进度推送到右下角下载任务。"""
        target = str(dest or "").strip()
        if not target:
            return {"ok": False, "error": "缺少保存路径"}
        label = str(name or "").strip() or "模组"
        content_type = str(project_type or "mod").strip().lower()
        if content_type not in (
            "mod",
            "modpack",
            "resourcepack",
            "shaderpack",
            "datapack",
        ):
            content_type = "mod"

        def _progress(done: int, total: int, _text: str = "") -> None:
            self._emit_progress(
                {
                    "kind": "mod",
                    "task": str(task_id or ""),
                    "done": done,
                    "total": total,
                    "text": label,
                }
            )

        self._emit_progress(
            {
                "kind": "mod",
                "task": str(task_id or ""),
                "done": 0,
                "total": 0,
                "text": f"准备下载 {label}",
            }
        )
        result = mod_download.download(
            url, Path(target), _progress, label, content_type
        )
        if result.get("ok"):
            saved = Path(str(result.get("path") or target)).name
            self._emit_progress(
                {
                    "kind": "done",
                    "task": str(task_id or ""),
                    "done": 1,
                    "total": 1,
                    "text": f"已保存 {saved}",
                }
            )
        return result

    # ---- 整合包安装 -------------------------------------------------
    def prepare_modpack(
        self,
        url: str = "",
        file_name: str = "",
        pack_name: str = "",
        task_id: str = "",
    ) -> dict:
        """下载并解析整合包，返回给用户确认版本名的信息。

        临时文件保留给随后的 install_modpack 使用，安装完成或下次准备时清理。
        """
        link = str(url or "").strip()
        if not link:
            return {"ok": False, "error": "缺少整合包下载地址"}

        temp_dir = DATA_DIR / "temp"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"无法创建临时目录：{exc}"}

        self._clean_temp_modpacks(temp_dir)
        archive = temp_dir / f"modpack-{int(time.time())}{self._modpack_suffix(file_name)}"

        self._set_modpack_scope(str(task_id or "").strip(), False)
        try:
            downloaded = mod_download.download(
                link,
                archive,
                self._progress_callback("modpack"),
                "整合包",
                "modpack",
            )
            if not downloaded.get("ok"):
                return downloaded
            info = modpack_install.read_pack(archive)
        finally:
            self._clear_modpack_scope()

        if not info.get("ok"):
            self._discard_modpack(archive)
            return info

        mc_version = str(info.get("mc_version") or "").strip()
        title = str(info.get("name") or pack_name or "").strip()
        return {
            "ok": True,
            "archive": str(archive),
            "name": title,
            "mc_version": mc_version,
            "loader": str(info.get("loader") or "").lower(),
            "loader_version": str(info.get("loader_version") or ""),
            "file_count": len(info.get("files") or []),
            "suggest_name": self._modpack_folder_name(
                "", mc_version, title or str(pack_name or "")
            ),
        }

    def install_modpack(
        self,
        url: str = "",
        file_name: str = "",
        pack_name: str = "",
        mirror: bool = False,
        task_id: str = "",
        name: str = "",
        archive: str = "",
    ) -> dict:
        """安装整合包：补齐对应游戏版本与加载器，再把包内模组装进版本目录。"""
        path = self._modpack_archive(archive)
        owns_archive = path is None
        if path is None:
            link = str(url or "").strip()
            if not link:
                return {"ok": False, "error": "缺少整合包下载地址"}
            temp_dir = DATA_DIR / "temp"
            try:
                temp_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "error": f"无法创建临时目录：{exc}"}
            path = temp_dir / f"modpack-{int(time.time())}{self._modpack_suffix(file_name)}"

        self._set_modpack_scope(str(task_id or "").strip(), False)
        try:
            if owns_archive:
                self._emit_progress(
                    {"kind": "modpack", "done": 0, "total": 0, "text": "正在下载整合包"}
                )
                downloaded = mod_download.download(
                    str(url or ""),
                    path,
                    self._progress_callback("modpack"),
                    "整合包",
                    "modpack",
                )
                if not downloaded.get("ok"):
                    return downloaded
            return self._install_modpack(
                path, str(pack_name or ""), bool(mirror), str(name or "")
            )
        finally:
            self._clear_modpack_scope()
            if owns_archive:
                self._discard_modpack(path)

    def _modpack_suffix(self, file_name: str) -> str:
        suffix = Path(str(file_name or "")).suffix.lower()
        return suffix if suffix in (".zip", ".mrpack") else ".zip"

    def _modpack_archive(self, raw: str) -> Path | None:
        """校验前端传回的临时整合包路径，只允许 .minecraft/temp 下的文件。"""
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            path = Path(text).resolve()
            path.relative_to((DATA_DIR / "temp").resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    def _clean_temp_modpacks(self, temp_dir: Path) -> None:
        try:
            for item in temp_dir.glob("modpack-*"):
                if item.is_file() and item.suffix.lower() in (".zip", ".mrpack"):
                    item.unlink(missing_ok=True)
        except OSError:
            pass

    def _discard_modpack(self, archive: Path) -> None:
        try:
            Path(archive).unlink(missing_ok=True)
        except OSError:
            pass

    def _install_modpack(
        self, archive: Path, pack_name: str, mirror: bool, user_name: str = ""
    ) -> dict:
        info = modpack_install.read_pack(archive)
        if not info.get("ok"):
            return info

        mc_version = str(info.get("mc_version") or "").strip()
        loader = str(info.get("loader") or "").lower()
        loader_version = str(info.get("loader_version") or "").strip()
        if loader and loader not in loader_install.SETUP_FIELDS:
            label = loader_install.LOADER_LABELS.get(loader, loader)
            return {"ok": False, "error": f"整合包使用的加载器暂不支持：{label}"}
        if not loader:
            loader, loader_version = "vanilla", ""

        folder_name = self._modpack_folder_name(
            user_name, mc_version, str(info.get("name") or pack_name)
        )
        if not folder_name:
            return {"ok": False, "error": "无法生成版本名，请检查整合包名称"}

        loader_label = loader_install.LOADER_LABELS.get(loader, loader)
        head = f"正在安装 {mc_version}"
        if loader != "vanilla":
            head += f" · {loader_label} {loader_version}"
        self._emit_progress({"kind": "modpack", "done": 0, "total": 0, "text": head})

        created = self.download_game(
            mc_version, loader, loader_version, mirror, name=folder_name
        )
        if not created.get("ok"):
            note = str(created.get("error") or "版本安装失败")
            return {"ok": False, "error": f"{note}（整合包需要 {mc_version}）"}

        version_dir = Path(str(created.get("path") or (VERSIONS_DIR / folder_name)))
        if not version_dir.is_dir():
            return {"ok": False, "error": "版本目录创建失败"}

        written = modpack_install.apply_overrides(
            archive, version_dir, info.get("override_dirs") or []
        )
        installed, skipped, failed = self._install_modpack_files(info, version_dir)

        note = f"已安装 {installed} 个模组"
        if skipped:
            note += f"，跳过 {skipped} 个已存在文件"
        if written:
            note += f"，覆盖文件 {written} 个"

        warning = ""
        if failed:
            preview = "、".join(str(item.get("name") or "文件") for item in failed[:3])
            warning = f"{len(failed)} 个文件安装失败：{preview}"
            if len(failed) > 3:
                warning += " 等"

        self._emit_progress(
            {
                "kind": "modpack",
                "done": 1,
                "total": 1,
                "text": f"{folder_name} 安装完成",
            }
        )

        return {
            "ok": True,
            "version": folder_name,
            "path": str(version_dir),
            "mc_version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "installed": installed,
            "skipped": skipped,
            "failed": failed,
            "warning": warning,
            "summary": note,
        }

    def _modpack_folder_name(
        self, raw: str, mc_version: str, pack_name: str = ""
    ) -> str:
        """生成版本目录名：优先用用户输入，默认「游戏版本-整合包名」。"""
        base = str(raw or "").strip()
        if not base:
            title = str(pack_name or "").strip() or "整合包"
            version_text = str(mc_version or "").strip()
            # 包名本身已带游戏版本时不再重复拼前缀
            if version_text and not title.lower().startswith(version_text.lower()):
                base = f"{version_text}-{title}"
            else:
                base = title
        for char in FORBIDDEN_NAME_CHARS:
            base = base.replace(char, " ")
        base = " ".join(base.split())[:58].strip()
        if not base or validate_version_name(base):
            base = f"{mc_version}-整合包"

        candidate = base
        index = 2
        while (VERSIONS_DIR / candidate).exists():
            candidate = f"{base} ({index})"
            if validate_version_name(candidate):
                candidate = f"{base}-{index}"
            index += 1
        return candidate

    def _install_modpack_files(
        self, info: dict, version_dir: Path
    ) -> tuple[int, int, list[dict]]:
        """下载整合包声明的文件到版本目录，返回 (成功, 跳过, 失败列表)。"""
        files = [item for item in info.get("files") or [] if isinstance(item, dict)]
        total = len(files)
        if not total:
            return 0, 0, []

        if info.get("format") == "curseforge":
            # manifest 只有 projectID / fileID，先一次性换成文件名与下载地址
            lookup = mod_download.curseforge_file_info(
                [
                    (item.get("project_id"), item.get("file_id"))
                    for item in files
                ]
            ).get("files") or {}
            for item in files:
                key = f"{item.get('project_id')}:{item.get('file_id')}"
                found = lookup.get(key) or {}
                item["url"] = str(found.get("url") or "")
                name = Path(str(found.get("file_name") or "")).name
                item["path"] = f"mods/{name}" if name else ""
                item["name"] = name

        game_root = version_game_dir(version_dir)
        try:
            (game_root / "mods").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        version_root = game_root.resolve()
        installed = 0
        skipped = 0
        failed: list[dict] = []
        lock = threading.Lock()

        def _download(item: dict) -> None:
            nonlocal installed, skipped
            rel = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            link = str(item.get("url") or "")
            label = str(item.get("name") or Path(rel).name or "文件")
            if not rel or not link:
                with lock:
                    failed.append({"name": label, "error": "无法获取下载地址"})
                return

            dest = (game_root / rel).resolve()
            try:
                dest.relative_to(version_root)
            except ValueError:
                with lock:
                    failed.append({"name": label, "error": "文件路径不合法"})
                return
            if dest.is_file():
                with lock:
                    skipped += 1
                return

            # 整合包给的路径就是最终路径，不能再按模组规则补 .jar 后缀
            try:
                runtime_utils.download_file(link, dest, label=label, retries=1)
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响整体
                with lock:
                    failed.append({"name": label, "error": f"下载失败：{exc}"})
                return

            with lock:
                installed += 1

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_download, item) for item in files]
            for index, future in enumerate(as_completed(futures), start=1):
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - 单个文件失败不影响整体
                    pass
                self._emit_progress(
                    {
                        "kind": "modpack",
                        "done": index,
                        "total": total,
                        "text": f"正在安装模组 {index}/{total}",
                    }
                )

        return installed, skipped, failed

    # ---- 设置 -----------------------------------------------------
    def get_settings(self) -> dict:
        config = read_global_settings()
        return {
            "memory_mb": int(config["memory_mb"]),
            "memory_manual_mb": int(config["memory_manual_mb"]),
            "memory_auto": bool(config["memory_auto"]),
            "memory_total_mb": int(config["memory_total_mb"]),
            "memory_available_mb": int(config["memory_available_mb"]),
            "username": config["username"],
            "window_mode": config["window_mode"],
            "auto_lang_zh": bool(config["auto_lang_zh"]),
            "version_isolation": bool(config["version_isolation"]),
            "theme_color": config["theme_color"],
            "background_image": config["background_image"],
            "background_blur": int(config["background_blur"]),
            "login_mode": config["login_mode"],
            "default_view": config["default_view"],
        }

    def get_memory_state(self) -> dict:
        """返回实时内存状态（自动分配时随系统占用变化）。"""
        return self.get_settings()

    def set_settings(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            return self.get_settings()

        fields: dict[str, str] = {}

        if "memory_auto" in patch:
            fields["MemoryAuto"] = "True" if patch.get("memory_auto") else "False"

        if "memory_mb" in patch:
            try:
                fields["MemoryMB"] = str(
                    max(MEMORY_MIN_MB, min(int(patch["memory_mb"]), MEMORY_MAX_MB))
                )
            except (TypeError, ValueError):
                pass

        if "username" in patch:
            name = str(patch["username"]).strip()
            if name:
                fields["UserName"] = name[:16]

        if "login_mode" in patch:
            mode = str(patch.get("login_mode") or "").strip().lower()
            if mode in LOGIN_MODES:
                fields["LoginMode"] = mode

        if "window_mode" in patch:
            mode = str(patch["window_mode"] or "").strip()
            if mode in WINDOW_MODES:
                fields["WindowMode"] = mode

        if "auto_lang_zh" in patch:
            fields["AutoLangZH"] = "True" if patch.get("auto_lang_zh") else "False"

        if "version_isolation" in patch:
            fields["VersionIsolation"] = "True" if patch.get("version_isolation") else "False"

        if "theme_color" in patch:
            color = str(patch.get("theme_color") or "").strip()
            if not color:
                fields["ThemeColor"] = ""
            elif _is_hex_color(color):
                fields["ThemeColor"] = color

        if "background_blur" in patch:
            try:
                blur = max(0, min(int(patch["background_blur"]), 24))
                fields["BackgroundBlur"] = str(blur)
            except (TypeError, ValueError):
                pass

        if "default_view" in patch:
            view = str(patch.get("default_view") or "").strip().lower()
            if view in DEFAULT_VIEWS:
                fields["DefaultView"] = view

        write_global_settings(fields)
        return self.get_settings()

    def choose_background_image(self) -> dict:
        """让用户选择自定义背景图，复制到 web 资源目录并保存设置。"""
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪"}

        dialog_kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
        if dialog_kind is None:
            dialog_kind = webview.OPEN_DIALOG

        try:
            paths = window.create_file_dialog(
                dialog_kind,
                file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.bmp;*.webp)",),
            )
        except Exception as exc:  # noqa: BLE001 - 对话框失败统一反馈
            return {"ok": False, "error": f"无法打开文件选择器：{exc}"}

        if not paths:
            return {"ok": True, "cancelled": True}

        source = Path(str(paths[0]))
        if not source.is_file():
            return {"ok": False, "error": "选择的文件不存在"}

        extension = source.suffix.lower()
        if extension not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            return {"ok": False, "error": "仅支持 PNG / JPG / BMP / WEBP 图片"}

        try:
            WEB_IMG_DIR.mkdir(parents=True, exist_ok=True)
            for old in WEB_IMG_DIR.glob("background.*"):
                old.unlink(missing_ok=True)
            filename = f"background{extension}"
            shutil.copy2(source, WEB_IMG_DIR / filename)
        except OSError as exc:
            return {"ok": False, "error": f"复制背景图片失败：{exc}"}

        write_global_settings({"BackgroundImage": filename})
        return {"ok": True, "file": filename, "name": source.name}

    def clear_background_image(self) -> dict:
        """恢复默认渐变背景。"""
        try:
            for old in WEB_IMG_DIR.glob("background.*"):
                old.unlink(missing_ok=True)
        except OSError:
            pass
        write_global_settings({"BackgroundImage": ""})
        return {"ok": True}

    # ---- 启动游戏 -------------------------------------------------
    def launch_game(self, login_mode: str = "offline") -> dict:
        """检查 Java 与依赖后启动游戏。

        login_mode 取 offline / microsoft / authlib，对应界面选择的登录方式；
        传入 True/False 时按旧行为解释（True = 微软登录）。
        """
        if isinstance(login_mode, bool):
            login_mode = "microsoft" if login_mode else "offline"
        login_mode = str(login_mode or "offline").lower()
        state = read_state()
        entry = state.get("selected_version")
        if not isinstance(entry, dict):
            return {"ok": False, "error": "请先选择一个游戏版本"}

        version_dir = Path(str(entry.get("path", "")))
        if not version_dir.is_dir():
            return {"ok": False, "error": "版本目录不存在，请重新选择版本"}

        # 开始新的启动流程前清掉旧进程引用，避免误杀正在运行的游戏
        self._launch_process = None
        self._launch_started_at = 0.0

        meta = game_launcher.load_version_meta(version_dir, version_dir.parent)
        if meta is None:
            return {"ok": False, "error": "未找到版本 JSON，无法启动"}

        settings = self.get_settings()
        # 按界面选择的登录方式取账号；外置登录还要额外注入 authlib-injector
        account = None
        agent_args: list[str] = []
        if login_mode == "microsoft":
            account = self._active_account()
        elif login_mode == "authlib":
            account = self._active_authlib()
            if account is None:
                return {"ok": False, "error": "第三方登录状态已失效，请重新登录"}
            self._emit_progress(
                {"kind": "loader", "done": 0, "total": 0, "text": "正在准备外置登录组件"}
            )
            injector = authlib_auth.ensure_injector(AUTHLIB_INJECTOR_FILE)
            if injector is None:
                return {
                    "ok": False,
                    "error": "外置登录组件 authlib-injector 下载失败，请检查网络后重试",
                }
            agent_args = [f"-javaagent:{injector}={account.get('server', '')}"]
        else:
            # 离线启动：装了 CustomSkinLoader 的话把本地皮肤同步进去
            self._sync_offline_skin(str(settings.get("username") or ""))
        required = int((meta.get("javaVersion") or {}).get("majorVersion") or 8)

        setup_path = version_dir / SETUP_SUBDIR / SETUP_FILE_NAME
        version_cfg = version_settings_from_setup(
            parse_setup_ini(setup_path) if setup_path.is_file() else {}
        )

        self._emit_progress({"kind": "java", "done": 0, "total": 0, "text": "正在检查 Java 运行时"})
        java_info = None
        custom_java = version_cfg["java_path"]
        if custom_java:
            java_info = java_runtime.probe_java(Path(custom_java))
            if java_info is None:
                self._emit_progress(
                    {
                        "kind": "java",
                        "done": 0,
                        "total": 0,
                        "text": "版本指定的 Java 不可用，改用自动选择",
                    }
                )
            elif not java_runtime.java_compatible(java_info["major"], required):
                self._emit_progress(
                    {
                        "kind": "java",
                        "done": 0,
                        "total": 0,
                        "text": (
                            f"版本指定的 Java {java_info['major']} 与所需 Java {required} "
                            "不兼容，改用自动选择"
                        ),
                    }
                )
                java_info = None

        if java_info is None:
            java_info = self._pick_java(required)
        if java_info is None:
            self._emit_progress(
                {"kind": "java", "done": 0, "total": 0, "text": f"未检测到 Java {required}，开始下载"}
            )
            java_info = java_runtime.download_java(
                required, JAVA_DIR, self._progress_callback("java")
            )
            if java_info is None:
                return {
                    "ok": False,
                    "error": f"未检测到可用的 Java {required}，自动下载失败，请在版本设置中指定 Java",
                }
            write_global_settings({"JavaPath": java_info["path"]})
            # 新下载的 Java 需要重新扫描，否则同一次运行里还会重复下载
            self._java_cache = None

        # 原生库架构跟随实际启动的 Java：x64 Java 在 ARM64 系统上很常见，
        # 用错架构的 natives 会让 LWJGL 直接崩溃
        game_launcher.set_native_arch_from_java(java_info["path"])

        if version_cfg["check_files"]:
            self._emit_progress({"kind": "deps", "done": 0, "total": 0, "text": "正在校验游戏文件"})
            errors = game_launcher.prepare_dependencies(
                meta,
                version_dir,
                LIBRARIES_DIR,
                ASSETS_DIR,
                version_dir / "natives",
                self._dependency_progress,
            )
            if errors:
                return {"ok": False, "error": "依赖补全失败：" + errors[0]}

            missing = game_launcher.missing_forge_files(meta, LIBRARIES_DIR)
            if missing:
                return {
                    "ok": False,
                    "error": "该版本缺少 Forge 安装文件（SRG 客户端库），请用 PCL 或官方安装器补全后再启动",
                }
        else:
            client_jar = game_launcher.client_jar_path(meta, version_dir)
            if not client_jar.is_file():
                return {
                    "ok": False,
                    "error": "客户端 jar 缺失，请重新下载版本或开启文件校验",
                }

        extra_jvm = list(agent_args)
        if version_cfg["extra_jvm"]:
            extra_jvm.extend(shlex.split(version_cfg["extra_jvm"]))
        extra_game = shlex.split(version_cfg["extra_game"]) if version_cfg["extra_game"] else []

        window_mode = settings["window_mode"]
        resolution = None
        if window_mode == "launcher":
            resolution = game_launcher.window_client_size(self._find_hwnd() or 0)

        # 版本隔离开启时用版本自己的目录，关闭时所有版本共用数据根目录
        game_dir = version_dir if settings["version_isolation"] else DATA_DIR
        try:
            game_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            game_dir = version_dir

        # 新下载的版本首次启动前写入中文语言设置
        if settings["auto_lang_zh"]:
            game_launcher.apply_auto_language(game_dir)

        try:
            command = game_launcher.build_command(
                meta,
                game_launcher.resolve_javaw(java_info["path"]),
                version_dir,
                LIBRARIES_DIR,
                ASSETS_DIR,
                version_dir / "natives",
                settings["username"],
                int(version_cfg["memory_mb"] or settings["memory_mb"]),
                extra_jvm=extra_jvm,
                extra_game=extra_game,
                resolution=resolution,
                account=account,
                game_dir=game_dir,
            )
            process = game_launcher.start_process(
                command, version_dir, LOGS_DIR / "latest.log", work_dir=game_dir
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败统一反馈
            return {"ok": False, "error": f"启动失败：{exc}"}

        self._launch_process = process
        self._launch_processes.append(
            {
                "process": process,
                "path": str(version_dir),
                "name": str(entry.get("name") or version_dir.name),
                "started": time.monotonic(),
            }
        )
        self._launch_started_at = time.monotonic()
        self._launch_window_mode = window_mode
        self._window_applied = False
        return {
            "ok": True,
            "pid": process.pid,
            "java": java_info["path"],
            "log": str(LOGS_DIR / "latest.log"),
            "instances": len(self._live_launch_processes()),
        }

    def get_launch_progress(self) -> dict:
        """返回游戏启动进度，前端轮询直到窗口出现或进程退出。"""
        process = self._launch_process
        if process is None:
            return {"state": "idle"}

        log_path = LOGS_DIR / "latest.log"
        tail = game_launcher.read_log_tail(log_path)

        code = process.poll()
        if code is not None:
            lines = [line for line in tail.splitlines() if line.strip()]
            return {
                "state": "exited",
                "code": code,
                "text": "游戏进程已退出",
                "tail": lines[-40:],
            }

        hwnd = game_launcher.find_game_window(process.pid)
        if hwnd:
            if not self._window_applied:
                game_launcher.apply_window_mode(hwnd, self._launch_window_mode)
                self._window_applied = True
            return {"state": "ready", "percent": 100, "text": "游戏已启动"}

        percent, text = game_launcher.launch_stage(tail)
        started = self._launch_started_at or time.monotonic()
        elapsed = max(0.0, time.monotonic() - started)

        # 时间兜底：加载较慢时也让进度缓慢前进，避免长时间停在同一步
        percent = min(95, percent + int(elapsed // 5))
        if elapsed >= 20 and percent < 60:
            text = f"正在加载游戏数据…（已等待 {int(elapsed)} 秒）"

        lines = [line for line in tail.splitlines() if line.strip()]
        return {
            "state": "starting",
            "percent": percent,
            "text": text,
            # 实时日志：让启动页能看到游戏输出
            "tail": lines[-40:],
        }

    def read_launch_log(self, limit: int = 400) -> dict:
        """读取 latest.log 末尾内容，供启动日志查看窗口显示。"""
        log_path = LOGS_DIR / "latest.log"
        try:
            size = max(50, min(int(limit or 400), 3000))
        except (TypeError, ValueError):
            size = 400

        if not log_path.is_file():
            return {
                "ok": True,
                "exists": False,
                "path": str(log_path),
                "text": "还没有启动日志，先启动一次游戏吧。",
            }

        try:
            with log_path.open("rb") as fp:
                fp.seek(0, 2)
                total = fp.tell()
                # 最多读 400KB，避免大日志拖慢界面
                fp.seek(max(0, total - 400 * 1024))
                raw = fp.read().decode("utf-8", "ignore")
        except OSError as exc:
            return {"ok": False, "error": f"读取日志失败：{exc}"}

        lines = raw.splitlines()
        return {
            "ok": True,
            "exists": True,
            "path": str(log_path),
            "text": "\n".join(lines[-size:]),
            "lines": min(len(lines), size),
        }

    def open_logs_dir(self) -> bool:
        """打开日志目录，方便查看历史日志文件。"""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    def cancel_launch(self) -> dict:
        """取消正在启动的游戏进程。"""
        process = self._launch_process
        if process is None:
            return {"ok": False, "error": "没有正在启动的游戏"}
        if process.poll() is not None:
            self._launch_process = None
            return {"ok": False, "error": "游戏进程已结束"}

        try:
            process.kill()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"取消失败：{exc}"}

        self._launch_process = None
        return {"ok": True}

    def get_game_state(self) -> dict:
        """返回仍在运行的游戏进程信息（支持同时运行多个实例）。"""
        alive = self._live_launch_processes()
        if not alive:
            return {"running": False}

        earliest = min(item["started"] for item in alive)
        return {
            "running": True,
            "pid": alive[-1]["process"].pid,
            "count": len(alive),
            "pids": [item["process"].pid for item in alive],
            "names": [str(item.get("name") or "") for item in alive],
            "elapsed": int(max(0.0, time.monotonic() - earliest)),
        }

    def kill_game(self) -> dict:
        """强制结束所有正在运行的游戏进程。"""
        alive = self._live_launch_processes()
        if not alive:
            return {"ok": False, "error": "当前没有正在运行的游戏"}

        killed: list[int] = []
        for item in alive:
            process = item["process"]
            if process.poll() is not None:
                continue
            pid = process.pid

            # Windows 下用 taskkill 连子进程一起结束，失败时退回 Popen.kill()
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=0x08000000,
                        timeout=10,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass

            if process.poll() is None:
                try:
                    process.kill()
                except Exception:  # noqa: BLE001
                    continue

            try:
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            killed.append(pid)

        self._launch_processes = []
        self._launch_process = None
        self._launch_started_at = 0.0

        if not killed:
            return {"ok": False, "error": "结束进程失败"}
        return {"ok": True, "killed": killed, "pid": killed[-1]}

    # ---- 内部 -----------------------------------------------------
    def _live_launch_processes(self) -> list[dict]:
        """剔除已退出的游戏进程，返回仍在运行的列表。"""
        alive = [
            item for item in self._launch_processes
            if item["process"].poll() is None
        ]
        self._launch_processes = alive

        process = self._launch_process
        if process is not None and process.poll() is not None:
            self._launch_process = None
        return alive

    def _java_roots(self) -> list[Path]:
        return [JAVA_DIR]

    def _java_candidates(self) -> list[dict]:
        if self._java_cache is None:
            self._java_cache = java_runtime.scan_java(self._java_roots())
        return self._java_cache

    def _resolve_current_java(self) -> dict | None:
        path = read_global_settings().get("java_path")
        if isinstance(path, str) and path:
            info = java_runtime.probe_java(Path(path))
            if info is not None:
                return info
        candidates = self._java_candidates()
        return candidates[0] if candidates else None

    def _required_java_major(self) -> int:
        state = read_state()
        entry = state.get("selected_version")
        if isinstance(entry, dict):
            version_dir = Path(str(entry.get("path", "")))
            meta = game_launcher.load_version_meta(version_dir, version_dir.parent)
            if isinstance(meta, dict):
                return int((meta.get("javaVersion") or {}).get("majorVersion") or 17)
        return 17

    def _pick_java(self, required_major: int) -> dict | None:
        """按版本要求挑选 Java。

        全局设置里的 Java 只作为候选之一，不再强制优先：
        先找主版本最接近（且满足）的 Java，找不到才交给调用方自动下载。
        """
        candidates = list(self._java_candidates())

        saved_path = read_global_settings().get("java_path")
        if isinstance(saved_path, str) and saved_path:
            info = java_runtime.probe_java(Path(saved_path))
            if info is not None and not any(
                item["path"] == info["path"] for item in candidates
            ):
                candidates.append(info)

        return java_runtime.pick_best_java(candidates, required_major)

    def _set_modpack_scope(self, task_id: str, importing: bool) -> None:
        """把后续进度绑定到某个下载任务；作用域只在本线程生效。"""
        task = str(task_id or "").strip()
        self._scope.modpack_task = task
        self._scope.modpack_import = bool(importing) and bool(task)

    def _clear_modpack_scope(self) -> None:
        self._scope.modpack_task = ""
        self._scope.modpack_import = False

    def _emit_progress(self, payload: dict) -> None:
        window = self._window
        if window is None:
            return

        task_id = getattr(self._scope, "modpack_task", "")
        if task_id:
            # 安装整合包时前端的进度条只认 modpack，统一改写阶段名
            payload = dict(payload, kind="modpack", task=task_id)
            if getattr(self._scope, "modpack_import", False):
                # 直接导入整合包：进度只写右下角任务，不碰页面上的任何进度条
                payload["import"] = True

        now = time.monotonic()
        total = int(payload.get("total") or 0)
        done = int(payload.get("done") or 0)
        finished = bool(total) and done >= total
        if not finished and now - self._last_emit < 0.15:
            return
        self._last_emit = now

        script = "window.mchl && window.mchl.progress(" + json.dumps(payload, ensure_ascii=False) + ")"
        try:        
            window.evaluate_js(script)
        except Exception:
            pass

    def _progress_callback(self, kind: str):
        def callback(done: int, total: int, label: str = "") -> None:
            self._emit_progress(
                {"kind": kind, "done": done, "total": total, "text": label}
            )

        return callback

    def _dependency_progress(self, kind: str, done: int, total: int) -> None:
        self._emit_progress({"kind": kind, "done": done, "total": total, "text": ""})

    def _loader_progress(self, kind: str, done: int, total: int, text: str = "") -> None:
        self._emit_progress({"kind": kind, "done": done, "total": total, "text": text})


    def _find_hwnd(self) -> int | None:
        user32 = ctypes.WinDLL("user32")
        if self._hwnd and user32.IsWindow(self._hwnd):
            return self._hwnd

        pid = os.getpid()
        found: list[int] = []
        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, _lparam):  # noqa: ANN001
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value != pid or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value == WINDOW_TITLE:
                    found.append(int(hwnd))
                    return False
            return True

        user32.EnumWindows(enum_proc_type(_callback), 0)
        if not found:
            return None
        self._hwnd = found[0]
        return self._hwnd


def _sync_web_images() -> None:
    """把根目录的图标与头像同步进 web/assets/img。

    pywebview 的内置服务器只映射 web 目录，页面不能直接引用上级目录的文件。
    """
    target_dir = WEB_IMG_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    pairs = [
        (resource_file("图标.png"), target_dir / "app-icon.png"),
        (resource_file("图标.ico"), target_dir / "app-icon.ico"),
        (resource_file("Pictures", "steve.png"), target_dir / "steve.png"),
    ]
    for source, target in pairs:
        if source is None or not source.is_file():
            continue
        try:
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                continue
            shutil.copy2(source, target)
        except OSError:
            continue


def system_dpi() -> int:
    """系统 DPI：Win10 之前没有 GetDpiForSystem，退回 GDI 的 LOGPIXELSX。"""
    try:
        value = int(ctypes.WinDLL("user32").GetDpiForSystem() or 0)
        if value > 0:
            return value
    except Exception:  # noqa: BLE001 - Win10 1607 之前没有这个接口
        pass
    try:
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")
        hdc = user32.GetDC(None)
        if not hdc:
            return 96
        try:
            value = int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            user32.ReleaseDC(None, hdc)
        return value or 96
    except Exception:  # noqa: BLE001 - 取不到就按 100% 处理
        return 96


def dpi_for_window(hwnd) -> int:
    """窗口所在显示器的 DPI：GetDpiForWindow 是 Win10 1607 才有的接口。"""
    try:
        getter = ctypes.WinDLL("user32").GetDpiForWindow
        getter.argtypes = [wintypes.HWND]
        getter.restype = wintypes.UINT
        value = int(getter(hwnd) or 0)
        if value > 0:
            return value
    except Exception:  # noqa: BLE001 - 老系统没有这个接口
        pass
    return system_dpi()


def _dpi_scale() -> float:
    """返回系统缩放比例（创建窗口前也能取到，顺带声明 DPI 感知）。"""
    try:
        user32 = ctypes.WinDLL("user32")
        user32.SetProcessDPIAware()  # pywebview 启动时也会设置，提前声明才能取到真实像素
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时按 100% 处理
        pass
    return system_dpi() / 96.0


def initial_window_geometry() -> dict:
    """按屏幕工作区预先算好居中尺寸（逻辑像素），避免窗口先出现再跳变。"""
    try:
        scale = _dpi_scale()
        user32 = ctypes.WinDLL("user32")
        work_area = wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
            return {}
        work_width = work_area.right - work_area.left
        work_height = work_area.bottom - work_area.top
        # 留 60px 边距，避免窗口顶到任务栏或屏幕边缘
        width = int(min(WINDOW_WIDTH, work_width / scale - 60))
        height = int(min(WINDOW_HEIGHT, work_height / scale - 60))
        return {
            "x": int(work_area.left / scale) + (int(work_width / scale) - width) // 2,
            "y": int(work_area.top / scale) + (int(work_height / scale) - height) // 2,
            "width": width,
            "height": height,
        }
    except Exception:  # noqa: BLE001 - 取不到时退回 create_window 的默认居中
        return {}


def _window_icon_path() -> str:
    """返回窗口图标：优先使用根目录的 图标.ico，否则由 图标.png 转换。"""
    ico = resource_file("图标.ico")
    if ico is not None:
        return str(ico)

    source = resource_file("图标.png")
    if source is None:
        return ""

    cache = GLOBAL_SETTINGS_DIR / "window.ico"
    try:
        if cache.is_file() and cache.stat().st_mtime >= source.stat().st_mtime:
            return str(cache)
    except OSError:
        pass

    try:
        import clr  # noqa: F401 - pythonnet 由 pywebview 提供

        clr.AddReference("System.Drawing")
        from System.Drawing import Bitmap, Icon  # type: ignore[import]
        from System.IO import MemoryStream  # type: ignore[import]

        bitmap = Bitmap(str(source))
        resized = Bitmap(bitmap, 256, 256)
        hicon = resized.GetHicon()
        icon = Icon.FromHandle(hicon)
        stream = MemoryStream()
        icon.Save(stream)
        data = bytes(stream.ToArray())
        stream.Close()
        try:
            ctypes.windll.user32.DestroyIcon(hicon.ToInt64())
        except Exception:  # noqa: BLE001 - 句柄释放失败不影响图标
            pass

        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return str(cache)
    except Exception:  # noqa: BLE001 - 图标失败不应影响启动
        return ""


def main() -> None:
    started = time.perf_counter()

    if not INDEX_HTML.exists():
        sys.exit(f"未找到界面文件：{INDEX_HTML}")

    # 让任务栏使用本程序的图标与分组，而不是 python.exe
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{APP_NAME}.{APP_VERSION}"
        )
    except Exception:  # noqa: BLE001 - 仅影响任务栏分组
        pass

    data_dir = ensure_data_dir()
    ensure_global_settings()
    _hide_dependency_versions()
    _flatten_inherited_versions()
    _cleanup_orphan_hidden_versions()
    _sync_web_images()
    gui = prepare_webview_gui()
    print(f"[{APP_NAME}] data dir: {data_dir}")
    print(f"[{APP_NAME}] global settings: {GLOBAL_SETUP_FILE}")
    print(
        f"[{APP_NAME}] os: {windows_display_name()} / gui: {gui or 'auto'}"
        f" / webview2: {webview2_runtime_version() or '缺失'}"
        f" / python: {sys.version.split()[0]}",
        flush=True,
    )

    api = LauncherApi()
    # 尺寸与位置在创建前就算好，窗口一出现就是最终形态，不再等 0.6s 后跳一下
    geometry = initial_window_geometry()
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(INDEX_HTML),
        js_api=api,
        width=geometry.get("width", WINDOW_WIDTH),
        height=geometry.get("height", WINDOW_HEIGHT),
        x=geometry.get("x"),
        y=geometry.get("y"),
        min_size=WINDOW_MIN_SIZE,
        frameless=True,
        easy_drag=False,
        shadow=True,
        resizable=True,
        background_color=WINDOW_BG_COLOR,
        text_select=False,
    )
    api.attach(window)
    print(f"[{APP_NAME}] api ready ({time.perf_counter() - started:.2f}s)", flush=True)

    def on_start():
        # 句柄需等 GUI 初始化完成后才可取，取到就立刻校正（通常无需再调整）
        for _ in range(20):
            if api.fit_window():
                break
            time.sleep(0.05)
        # 支持把整合包 zip 直接拖到窗口里导入
        api.enable_drop_import()
        ok = api.apply_rounded_corners()
        print(
            f"[{APP_NAME}] window ready (rounded={ok}, "
            f"{time.perf_counter() - started:.2f}s)",
            flush=True,
        )

    try:
        webview.start(
            on_start,
            debug=False,
            icon=_window_icon_path() or None,
            gui=gui or None,
        )
    except Exception as exc:  # noqa: BLE001 - 起不来时给用户可读的提示
        show_message(
            "启动器界面初始化失败：\n\n"
            f"{exc}\n\n"
            f"当前系统：{windows_display_name()}\n"
            f"WebView2 运行时：{webview2_runtime_version() or '未安装'}\n\n"
            "请安装 Microsoft Edge WebView2 运行时后重试：\n"
            f"{WEBVIEW2_DOC_URL}",
            flags=0x00000010,  # MB_ICONERROR
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
