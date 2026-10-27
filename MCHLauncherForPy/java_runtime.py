"""Java 运行时探测：常见目录、环境变量、注册表，以及自动下载。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from runtime_utils import (
    CREATE_NO_WINDOW,
    SSL_CONTEXT,
    USER_AGENT,
    DownloadError,
    download_file,
    extract_zip,
)

ADOPTIUM_URL = (
    "https://api.adoptium.net/v3/binary/latest/{major}/ga/"
    "windows/x64/jre/hotspot/normal/eclipse"
)
TUNA_MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/Adoptium/{major}/{kind}/x64/windows/"
MICROSOFT_JDK_URL = "https://aka.ms/download-jdk/microsoft-jdk-{major}-windows-x64.zip"

# 安装 JDK / JRE 的常见父目录
_COMMON_DIR_NAMES = (
    "Java",
    "Eclipse Adoptium",
    "Eclipse Foundation",
    "AdoptOpenJDK",
    "Microsoft",
    "Amazon Corretto",
    "Zulu",
    "BellSoft",
    "JetBrains",
    "Semeru",
    "SapMachine",
    "RedHat",
    "Tencent",
)


def _iter_java_exe(root: Path, depth: int = 2):
    """在 root 下递归查找 bin/java.exe（限制深度，避免全盘扫描）。"""
    if not root.is_dir():
        return
    direct = root / "bin" / "java.exe"
    if direct.is_file():
        yield direct
    if depth <= 0:
        return
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir():
                yield from _iter_java_exe(child, depth - 1)
        except OSError:
            continue


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    local_appdata = os.environ.get("LOCALAPPDATA")
    user_profile = os.environ.get("USERPROFILE")

    if program_files:
        base = Path(program_files)
        roots.extend(base / name for name in _COMMON_DIR_NAMES)
    if program_files_x86:
        roots.append(Path(program_files_x86) / "Java")
    if local_appdata:
        base = Path(local_appdata)
        roots.append(base / "Programs" / "Eclipse Adoptium")
        roots.append(base / "Programs" / "Java")
        roots.append(base / "Programs" / "Microsoft")
    if user_profile:
        base = Path(user_profile)
        roots.append(base / ".jdks")
        roots.append(base / "scoop" / "apps" / "openjdk")
    return roots


# 全盘扫描时值得进入的目录名（命中才继续往下找，避免遍历无关目录）
_JAVA_DIR_HINTS = (
    "java",
    "jdk",
    "jre",
    "jbr",
    "openjdk",
    "graalvm",
    "corretto",
    "zulu",
    "adoptium",
    "temurin",
    "semeru",
    "sapmachine",
    "liberica",
    "openj9",
    "chocolatey",
    "scoop",
    "jetbrains",
    "microsoft",
    "android",
    "tools",
    "dev",
    "env",
    "runtime",
    "apps",
    "current",
    "latest",
    "default",
)

# 全盘扫描时直接跳过的目录（系统目录、回收站、权限受限目录等）
_SKIP_DIR_NAMES = frozenset(
    {
        "windows",
        "$recycle.bin",
        "system volume information",
        "recovery",
        "perflogs",
        "msocache",
        "config.msi",
        "windowsapps",
        "appdata",
        "node_modules",
        "temp",
        "tmp",
        "cache",
        ".git",
        ".svn",
        "$windows.~bt",
        "$windows.~ws",
        "$sysreset",
        "installer",
        "winsxs",
    }
)


def fixed_drives() -> list[Path]:
    """列出所有本地固定磁盘根目录（跳过光驱、网络盘和移动盘）。"""
    if os.name != "nt":
        return [Path("/")]

    roots: list[Path] = []
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetLogicalDrives.restype = ctypes.c_uint32
        kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint32
        mask = kernel32.GetLogicalDrives()
        for index in range(26):
            if not (mask >> index) & 1:
                continue
            root = Path(f"{chr(ord('A') + index)}:\\")
            try:
                # DRIVE_FIXED = 3：只扫本地硬盘，光驱/网络盘/移动盘跳过
                if kernel32.GetDriveTypeW(str(root)) != 3:
                    continue
            except OSError:
                pass
            roots.append(root)
    except OSError:
        roots = []

    if not roots:
        system_drive = os.environ.get("SystemDrive", "C:")
        roots = [Path(f"{system_drive}\\")]
    return roots


def _walk_java_exe(
    root: Path, max_depth: int = 8, on_progress=None
):
    """在磁盘下逐个目录找 bin/java.exe。

    浅层目录（前 3 层）一律进入，更深的目录只有名字命中 Java 关键字才进入，
    这样既能覆盖 Program Files、scoop、chocolatey 等常见位置，又不会遍历整盘。
    """
    stack: list[tuple[str, int]] = [(str(root), 0)]
    visited = 0
    while stack:
        current, level = stack.pop()
        exe = Path(current) / "bin" / "java.exe"
        try:
            if exe.is_file():
                yield exe
                # Java 安装目录内部不会再有 Java，找到就不再深入
                continue
        except OSError:
            pass

        if level >= max_depth:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name = entry.name.lower()
                    if name in _SKIP_DIR_NAMES:
                        continue
                    if level + 1 > 3 and not any(
                        hint in name for hint in _JAVA_DIR_HINTS
                    ):
                        continue
                    stack.append((entry.path, level + 1))
                    visited += 1
                    if on_progress is not None and visited % 500 == 0:
                        try:
                            on_progress(visited, entry.path)
                        except Exception:  # noqa: BLE001 - 进度回调不能影响扫描
                            pass
        except OSError:
            continue


def _registry_java_homes() -> list[Path]:
    """从注册表读取 JavaSoft 记录的 JavaHome。"""
    try:
        import winreg
    except ImportError:
        return []

    hits: list[Path] = []
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Development Kit"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Runtime Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Development Kit"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment"),
    )
    for hive, sub_key in keys:
        try:
            with winreg.OpenKey(hive, sub_key) as root_key:
                count = winreg.QueryInfoKey(root_key)[0]
                for index in range(count):
                    try:
                        version_name = winreg.EnumKey(root_key, index)
                        with winreg.OpenKey(root_key, version_name) as version_key:
                            home, _ = winreg.QueryValueEx(version_key, "JavaHome")
                            hits.append(Path(str(home)))
                    except OSError:
                        continue
        except OSError:
            continue
    return hits


def parse_major(version: str) -> int:
    """从 "1.8.0_381" / "17.0.8" 中解析主版本号。"""
    if not version:
        return 0
    if version.startswith("1."):
        parts = version.split(".")
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    head = version.split(".")[0].split("-")[0]
    return int(head) if head.isdigit() else 0


def probe_java(exe: Path) -> dict | None:
    """读取 java.exe 的版本信息；无效时返回 None。"""
    try:
        exe = exe.resolve()
    except OSError:
        return None
    if not exe.is_file():
        return None

    home = exe.parent.parent
    version = ""
    vendor = ""

    release_file = home / "release"
    if release_file.is_file():
        try:
            for line in release_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("JAVA_VERSION="):
                    version = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("IMPLEMENTOR="):
                    vendor = line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass

    if not version:
        try:
            completed = subprocess.run(
                [str(exe), "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            output = (completed.stderr or "") + (completed.stdout or "")
            match = re.search(r'version "([^"]+)"', output)
            if match:
                version = match.group(1)
        except (OSError, subprocess.SubprocessError):
            return None

    major = parse_major(version)
    if major <= 0:
        return None

    return {
        "path": str(exe),
        "home": str(home),
        "version": version,
        "major": major,
        "vendor": vendor,
    }


def scan_java(
    extra_roots: list[Path] | tuple[Path, ...] = (),
    deep: bool = False,
    on_progress=None,
) -> list[dict]:
    """扫描系统中可用的 Java，按主版本号从高到低排序。

    deep=True 时遍历所有本地固定磁盘（设置页「自动扫描」用），否则只查常见位置。
    """
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        add(Path(java_home) / "bin" / "java.exe")

    which_java = shutil.which("java")
    if which_java:
        add(Path(which_java))

    for root in _search_roots():
        for exe in _iter_java_exe(root, 2):
            add(exe)

    for root in extra_roots:
        for exe in _iter_java_exe(Path(root), 3):
            add(exe)

    for home in _registry_java_homes():
        add(home / "bin" / "java.exe")

    if deep:
        for drive in fixed_drives():
            for exe in _walk_java_exe(drive, on_progress=on_progress):
                add(exe)

    found: dict[str, dict] = {}
    for candidate in candidates:
        info = probe_java(candidate)
        if info is None:
            continue
        key = info["path"].lower()
        if key not in found:
            found[key] = info

    return sorted(found.values(), key=lambda item: (-item["major"], item["path"].lower()))


def java_compatible(major: int, required_major: int) -> bool:
    """判断 Java 主版本能否启动该游戏版本。

    1.16 及更早的版本（尤其 1.12.2 Forge）只能在对应版本上运行：
    launchwrapper 在 Java 9+ 会因 AppClassLoader 不是 URLClassLoader 直接崩。
    1.17 起对更高版本的 Java 容忍度好得多，允许向上兼容。
    """
    if major == required_major:
        return True
    return required_major >= 16 and major > required_major


def pick_best_java(candidates: list[dict], required_major: int) -> dict | None:
    """挑选能启动版本且主版本最接近的 Java；完全不兼容时返回 None。"""
    usable = [
        item for item in candidates if java_compatible(item["major"], required_major)
    ]
    if not usable:
        return None
    # 精确匹配优先，其次才是最小的更高版本
    return min(usable, key=lambda item: (item["major"] != required_major, item["major"]))


def _tuna_archive_url(major: int) -> str | None:
    """从清华镜像目录中找出最新的 JRE 压缩包。"""
    for kind in ("jre", "jdk"):
        index_url = TUNA_MIRROR.format(major=major, kind=kind)
        try:
            request = urllib.request.Request(index_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
                html = response.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - 镜像不可用时静默跳过
            continue
        matches = re.findall(r'href="(OpenJDK\d+U-[^"]+\.zip)"', html)
        if matches:
            return index_url + matches[-1]
    return None


def java_archive_urls(major: int) -> list[str]:
    """按可用性排序的 Java 安装包地址（国内镜像优先）。"""
    urls: list[str] = []
    tuna = _tuna_archive_url(major)
    if tuna:
        urls.append(tuna)
    if major in (8, 11, 17, 21):
        urls.append(MICROSOFT_JDK_URL.format(major=major))
    urls.append(ADOPTIUM_URL.format(major=major))
    return urls


def download_java(
    major: int,
    target_root: Path,
    on_progress=None,
) -> dict | None:
    """下载并解压指定主版本的 Java 运行时，返回描述信息。"""
    target_root.mkdir(parents=True, exist_ok=True)
    archive = target_root / f"java-{major}.zip"
    extract_dir = target_root / f"java-{major}"

    for url in java_archive_urls(major):
        try:
            download_file(url, archive, on_progress, label=f"Java {major}")
        except DownloadError:
            continue

        try:
            extract_zip(archive, extract_dir)
        except (OSError, zipfile.BadZipFile):
            shutil.rmtree(extract_dir, ignore_errors=True)
            archive.unlink(missing_ok=True)
            continue
        finally:
            archive.unlink(missing_ok=True)

        for exe in _iter_java_exe(extract_dir, 2):
            info = probe_java(exe)
            if info is not None:
                return info

    return None
