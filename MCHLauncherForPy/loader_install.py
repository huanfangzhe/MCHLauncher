"""模组加载器（Fabric / Quilt / Forge / NeoForge）查询与安装。"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

import game_launcher
import version_download
from runtime_utils import (
    CREATE_NO_WINDOW,
    SSL_CONTEXT,
    USER_AGENT,
    DownloadError,
    download_file,
)

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_MAVEN = "https://maven.minecraftforge.net"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
MIRROR_MAVEN = version_download.MIRROR_BASE

LOADER_LABELS = {
    "vanilla": "原版",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}

# 安装该加载器时 Setup.ini 需要写入的字段
SETUP_FIELDS = {
    "fabric": "VersionFabric",
    "quilt": "VersionQuilt",
    "forge": "VersionForge",
    "neoforge": "VersionNeoForge",
}

# 安装器所需的最低 Java 版本
INSTALLER_JAVA = {"forge": 8, "neoforge": 17}


def _read_json(url: str, timeout: int = 30, retry: int = 1):
    """读取 JSON；镜像会跳转到不同 CDN 节点，偶发超时因此允许重试。"""
    last_error: Exception | None = None
    for _ in range(retry + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(
                request, timeout=timeout, context=SSL_CONTEXT
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("读取 JSON 失败")


def _read_text(url: str, timeout: int = 30, retry: int = 1) -> str:
    last_error: Exception | None = None
    for _ in range(retry + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(
                request, timeout=timeout, context=SSL_CONTEXT
            ) as response:
                return response.read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("读取文本失败")


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _maven_versions(group_path: str, mirror: bool = False) -> list[str]:
    """读取 Maven 仓库的 maven-metadata.xml，返回版本号列表（新→旧）。"""
    path = f"{group_path}/maven-metadata.xml"
    mirror_url = f"{MIRROR_MAVEN}/maven/{path}"

    if "neoforged" in group_path:
        # NeoForge 官方仓库在国内经常超时，镜像元数据更完整
        urls = [mirror_url, f"https://maven.neoforged.net/releases/{path}"]
    else:
        # 官方仓库在部分网络下会超时，镜像优先可显著提速
        urls = [mirror_url, f"https://maven.minecraftforge.net/{path}"]

    for url in urls:
        try:
            text = _read_text(url, timeout=12)
        except Exception:  # noqa: BLE001 - 依次尝试可用源
            continue
        found = re.findall(r"<version>([^<]+)</version>", text)
        if found:
            return sorted(found, key=_version_key, reverse=True)
    return []


def _neoforge_prefix(mc_version: str) -> str:
    """1.20.4 -> 20.4，1.21.1 -> 21.1。"""
    parts = mc_version.split(".")
    if len(parts) >= 3 and parts[0] == "1":
        return f"{parts[1]}.{parts[2]}."
    if len(parts) == 2:
        return f"{parts[1]}."
    return ""


def available_versions(loader: str, mc_version: str, mirror: bool = False) -> list[str]:
    """查询某个加载器在指定游戏版本下可用的加载器版本（新→旧）。"""
    loader = (loader or "").lower()
    try:
        if loader == "fabric":
            data = _read_json(f"{FABRIC_META}/versions/loader/{mc_version}")
            return [str(item.get("loader", {}).get("version", "")) for item in data]
        if loader == "quilt":
            data = _read_json(f"{QUILT_META}/versions/loader/{mc_version}")
            return [str(item.get("loader", {}).get("version", "")) for item in data]
        if loader == "forge":
            versions = _maven_versions("net/minecraftforge/forge", mirror)
            prefix = f"{mc_version}-"
            return [
                item[len(prefix):]
                for item in versions
                if item.startswith(prefix) and item[len(prefix):]
            ]
        if loader == "neoforge":
            if mc_version.startswith("1.20.1"):
                versions = _maven_versions("net/neoforged/forge", mirror)
                prefix = f"{mc_version}-"
                return [
                    item[len(prefix):]
                    for item in versions
                    if item.startswith(prefix) and item[len(prefix):]
                ]
            prefix = _neoforge_prefix(mc_version)
            if not prefix:
                return []
            # BMCLAPI 提供按游戏版本过滤的列表，优先使用
            try:
                data = _read_json(f"{MIRROR_MAVEN}/neoforge/list/{mc_version}")
                found = [
                    str(item.get("rawVersion", "")).replace("neoforge-", "")
                    for item in data
                    if isinstance(item, dict)
                ]
                found = [item for item in found if item]
                if found:
                    # 接口按从旧到新返回，这里翻转为新版本优先
                    found.reverse()
                    return found
            except Exception:  # noqa: BLE001 - 回退到 Maven 元数据
                pass
            versions = _maven_versions("net/neoforged/neoforge", mirror)
            return [item for item in versions if item.startswith(prefix)]
    except Exception:  # noqa: BLE001 - 网络异常统一返回空列表
        return []
    return []


def list_loaders(mc_version: str, mirror: bool = False) -> dict:
    """返回各加载器在该游戏版本下可选的版本号。"""
    if not mc_version:
        return {"ok": False, "error": "缺少游戏版本"}

    loaders = []
    for key in ("fabric", "quilt", "forge", "neoforge"):
        versions = [item for item in available_versions(key, mc_version, mirror) if item]
        loaders.append(
            {
                "id": key,
                "label": LOADER_LABELS[key],
                "versions": versions[:60],
                "available": bool(versions),
            }
        )

    return {"ok": True, "mc_version": mc_version, "loaders": loaders}


# ---------------------------------------------------------------- 安装


def _ensure_launcher_profiles(data_root: Path) -> None:
    """Forge 安装器要求目录中存在 launcher_profiles.json。"""
    path = data_root / "launcher_profiles.json"
    if path.is_file():
        return
    path.write_text(
        json.dumps(
            {"profiles": {}, "settings": {"language": "zh_CN"}, "version": 3},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_version_json(version_dir: Path, meta: dict) -> Path:
    version_dir.mkdir(parents=True, exist_ok=True)
    version_id = str(meta.get("id") or version_dir.name)
    path = version_dir / f"{version_id}.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_setup(target_dir: Path, mc_version: str, loader: str, loader_version: str,
                 release_time: str = "") -> Path:
    version_id = target_dir.name
    main_part, _, sub_part = mc_version.partition(".")
    lines = [
        f"VersionVanillaName:{version_id}",
        f"VersionOriginal:{mc_version}",
        f"VersionOriginalMain:{main_part}",
        f"VersionOriginalSub:{sub_part or '0'}",
        "VersionForge:",
        "VersionNeoForge:",
        "VersionFabric:",
        "VersionQuilt:",
        f"ReleaseTime:{release_time}",
        f"Info:{LOADER_LABELS.get(loader, loader)} {loader_version} · {mc_version}",
        "State:6",
        f"VersionVanilla:{main_part}.0.0",
    ]
    field = SETUP_FIELDS.get(loader)
    if field:
        for index, line in enumerate(lines):
            if line.startswith(field + ":"):
                lines[index] = f"{field}:{loader_version}"
                break

    setup_dir = target_dir / "MCHL"
    setup_dir.mkdir(parents=True, exist_ok=True)
    setup_path = setup_dir / "Setup.ini"
    setup_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return setup_path


def _find_installed_version(versions_dir: Path, mc_version: str, keyword: str) -> Path | None:
    """在 versions 下查找刚被安装器写入的版本（inheritsFrom 指向原版）。"""
    candidates: list[Path] = []
    try:
        folders = list(versions_dir.iterdir())
    except OSError:
        return None

    for folder in folders:
        if not folder.is_dir() or keyword not in folder.name.lower():
            continue
        json_path = game_launcher.find_version_json(folder)
        if json_path is None:
            continue
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and str(meta.get("inheritsFrom", "")) == mc_version:
            candidates.append(folder)

    if not candidates:
        return None
    candidates.sort(key=lambda item: item.name.lower())
    return candidates[-1]


def _library_task(entry: dict, libraries_dir: Path, default_base: str) -> dict | None:
    """把安装器内声明的库转换成下载任务（已存在则跳过）。"""
    downloads = entry.get("downloads") or {}
    artifact = downloads.get("artifact") or {}
    path = str(artifact.get("path") or "") or game_launcher.maven_path(
        str(entry.get("name", ""))
    )
    if not path:
        return None

    dest = libraries_dir / path
    if dest.is_file():
        return None

    url = str(artifact.get("url") or "")
    if not url:
        base = str(entry.get("url") or default_base).rstrip("/")
        url = f"{base}/{path}"
    return {"dest": dest, "url": url}


def _predownload_installer_files(
    installer: Path, libraries_dir: Path, mirror: bool, on_progress=None
) -> int:
    """预下载安装器需要的库，避免安装器逐文件慢速下载。

    返回预下载的任务数；任何异常都直接跳过，交给安装器自行处理。
    """
    try:
        with zipfile.ZipFile(installer) as jar:
            names = set(jar.namelist())
            profile: dict = {}
            if "install_profile.json" in names:
                profile = json.loads(jar.read("install_profile.json").decode("utf-8"))
            json_name = str(profile.get("json") or "version.json").lstrip("/")
            if json_name not in names:
                json_name = "version.json"
            if json_name not in names:
                return 0
            version = json.loads(jar.read(json_name).decode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile):
        return 0

    tasks: list[dict] = []
    for entry in version.get("libraries") or []:
        task = _library_task(entry, libraries_dir, "https://libraries.minecraft.net/")
        if task:
            tasks.append(task)

    for entry in profile.get("libraries") or []:
        task = _library_task(entry, libraries_dir, "https://maven.minecraftforge.net/")
        if task:
            tasks.append(task)

    # 安装器产物（universal / 补丁 jar）
    path = game_launcher.maven_path(str(profile.get("path") or ""))
    if path:
        dest = libraries_dir / path
        if not dest.is_file():
            tasks.append(
                {"dest": dest, "url": f"https://maven.minecraftforge.net/{path}"}
            )

    if not tasks:
        return 0

    if mirror:
        prepared: list[dict] = []
        for item in tasks:
            mirrored = game_launcher.mirror_maven_url(item["url"])
            if mirrored:
                prepared.append(dict(item, url=mirrored, fallback=item["url"]))
            else:
                prepared.append(item)
        tasks = prepared

    game_launcher._download_batch(tasks, "libraries", on_progress)
    return len(tasks)


def _run_installer(
    installer: Path, data_root: Path, java_exe: str, on_progress=None
) -> tuple[bool, str]:
    _ensure_launcher_profiles(data_root)
    if on_progress is not None:
        on_progress("loader", 0, 0, "正在运行安装器…")

    command = [java_exe, "-jar", str(installer), "--installClient", str(data_root)]
    try:
        completed = subprocess.run(
            command,
            cwd=str(data_root),
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=1800,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"安装器运行失败：{exc}"

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"退出码 {completed.returncode}"
        return False, f"安装器执行失败：{message}"
    return True, ""


def install_loader(
    loader: str,
    mc_version: str,
    loader_version: str,
    data_root: Path,
    versions_dir: Path,
    libraries_dir: Path,
    java_exe: str | None = None,
    mirror: bool = False,
    on_progress=None,
    target_name: str = "",
) -> dict:
    """安装加载器到 versions 目录，返回结果字典。"""
    loader = (loader or "").lower()
    if loader not in SETUP_FIELDS:
        return {"ok": False, "error": f"暂不支持加载器 {loader}"}
    if not mc_version or not loader_version:
        return {"ok": False, "error": "缺少游戏版本或加载器版本"}

    if not (versions_dir / mc_version).is_dir():
        return {"ok": False, "error": f"请先下载原版 {mc_version}"}

    if loader in ("fabric", "quilt"):
        return _install_from_profile(
            loader,
            mc_version,
            loader_version,
            versions_dir,
            libraries_dir,
            on_progress,
            target_name,
            mirror,
        )

    return _install_with_installer(
        loader,
        mc_version,
        loader_version,
        data_root,
        versions_dir,
        libraries_dir,
        java_exe,
        mirror,
        on_progress,
        target_name,
    )


def _install_from_profile(
    loader: str,
    mc_version: str,
    loader_version: str,
    versions_dir: Path,
    libraries_dir: Path,
    on_progress=None,
    target_name: str = "",
    mirror: bool = False,
) -> dict:
    """Fabric / Quilt 直接使用官方提供的 profile JSON。"""
    if loader == "fabric":
        url = f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json"
    else:
        url = f"{QUILT_META}/versions/loader/{mc_version}/{loader_version}/profile/json"

    try:
        meta = _read_json(url)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "无法获取加载器配置，请检查网络"}

    version_id = str(meta.get("id") or f"{loader}-loader-{loader_version}-{mc_version}")
    meta.setdefault("id", version_id)
    folder_name = (target_name or "").strip() or version_id
    target_dir = versions_dir / folder_name
    if target_dir.exists() and target_dir.name != version_id:
        return {"ok": False, "error": f"版本名 {folder_name} 已存在"}

    _write_version_json(target_dir, meta)

    merged = game_launcher.load_version_meta(target_dir, versions_dir)
    errors = []
    if isinstance(merged, dict):
        errors = game_launcher.install_libraries(
            merged, libraries_dir, target_dir / "natives", on_progress, mirror=mirror
        )

    _write_setup(target_dir, mc_version, loader, loader_version)

    if errors:
        return {
            "ok": False,
            "error": f"加载器依赖下载失败：{errors[0]}",
            "version": folder_name,
        }
    return {"ok": True, "version": folder_name, "loader": loader,
            "loader_version": loader_version, "path": str(target_dir)}


def _install_with_installer(
    loader: str,
    mc_version: str,
    loader_version: str,
    data_root: Path,
    versions_dir: Path,
    libraries_dir: Path,
    java_exe: str | None,
    mirror: bool,
    on_progress=None,
    target_name: str = "",
) -> dict:
    """Forge / NeoForge 需要运行官方安装器。"""
    if not java_exe:
        return {"ok": False, "error": "需要 Java 才能运行加载器安装器"}

    if loader == "forge":
        coord = f"{mc_version}-{loader_version}"
        relative = f"net/minecraftforge/forge/{coord}/forge-{coord}-installer.jar"
        base = f"{MIRROR_MAVEN}/maven" if mirror else FORGE_MAVEN
        version_id = f"{mc_version}-forge-{loader_version}"
    else:
        if mc_version.startswith("1.20.1"):
            coord = f"{mc_version}-{loader_version}"
            relative = f"net/neoforged/forge/{coord}/forge-{coord}-installer.jar"
        else:
            coord = loader_version
            relative = (
                f"net/neoforged/neoforge/{coord}/neoforge-{coord}-installer.jar"
            )
        base = f"{MIRROR_MAVEN}/maven" if mirror else NEOFORGE_MAVEN
        version_id = f"neoforge-{loader_version}"

    temp_dir = data_root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    installer = temp_dir / f"{loader}-installer.jar"

    if on_progress is not None:
        on_progress("loader", 0, 0, "正在下载安装器…")

    urls = [f"{base}/{relative}"]
    fallback = f"{MIRROR_MAVEN}/maven/{relative}"
    if fallback not in urls:
        urls.append(fallback)

    last_error = ""
    for url in urls:
        try:
            download_file(url, installer)
            last_error = ""
            break
        except DownloadError as exc:
            last_error = str(exc)

    if last_error:
        return {"ok": False, "error": f"安装器下载失败：{last_error}"}

    if on_progress is not None:
        on_progress("loader", 0, 0, "正在预下载安装依赖…")
    _predownload_installer_files(installer, libraries_dir, mirror, on_progress)

    ok, error = _run_installer(installer, data_root, java_exe, on_progress)
    installer.unlink(missing_ok=True)
    if not ok:
        return {"ok": False, "error": error}

    target_dir = _find_installed_version(versions_dir, mc_version, loader)
    if target_dir is None:
        target_dir = versions_dir / version_id
    if not target_dir.is_dir():
        return {"ok": False, "error": "安装器未生成版本目录，请检查安装日志"}

    wanted = (target_name or "").strip()
    if wanted and wanted != target_dir.name:
        renamed = versions_dir / wanted
        if renamed.exists():
            return {"ok": False, "error": f"版本名 {wanted} 已存在"}
        try:
            target_dir.rename(renamed)
            target_dir = renamed
        except OSError as exc:
            return {"ok": False, "error": f"版本重命名失败：{exc}"}

    _write_setup(target_dir, mc_version, loader, loader_version)

    merged = game_launcher.load_version_meta(target_dir, versions_dir)
    errors = []
    if isinstance(merged, dict):
        errors = game_launcher.install_libraries(
            merged, libraries_dir, target_dir / "natives", on_progress, mirror=mirror
        )

    if errors:
        return {
            "ok": False,
            "error": f"加载器依赖补全失败：{errors[0]}",
            "version": target_dir.name,
        }
    return {
        "ok": True,
        "version": target_dir.name,
        "loader": loader,
        "loader_version": loader_version,
        "path": str(target_dir),
    }
