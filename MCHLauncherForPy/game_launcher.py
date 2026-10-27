"""游戏版本解析、依赖补全与启动命令构造。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from pathlib import Path

from runtime_utils import (
    CREATE_NO_WINDOW,
    DownloadError,
    NATIVE_SUFFIXES,
    download_file,
    extract_natives,
)

ASSET_BASE_URL = "https://resources.download.minecraft.net/"
CLASSPATH_SEPARATOR = ";"
MAX_DOWNLOAD_WORKERS = 8
# 原生库架构必须以实际启动游戏的 Java 为准（见 set_native_arch_from_java），
# 这里只是尚未拿到 Java 路径时的初始猜测。
_NATIVE_ARCH = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"

# BMCLAPI 的 Maven 镜像，用于加速 Forge / NeoForge / Fabric 依赖下载
MIRROR_MAVEN_BASE = "https://bmclapi2.bangbang93.com/maven"
MAVEN_MIRROR_PREFIXES = (
    "https://maven.minecraftforge.net/",
    "https://files.minecraftforge.net/maven/",
    "https://libraries.minecraft.net/",
    "https://maven.neoforged.net/releases/",
    "https://maven.fabricmc.net/",
)


def mirror_maven_url(url: str) -> str:
    """把官方 Maven 地址换成 BMCLAPI 镜像地址，无法映射时返回空串。"""
    text = (url or "").strip()
    for prefix in MAVEN_MIRROR_PREFIXES:
        if text.startswith(prefix):
            return f"{MIRROR_MAVEN_BASE}/{text[len(prefix):]}"
    return ""


def native_arch() -> str:
    """当前加载原生库使用的架构："x64" 或 "arm64"。"""
    return _NATIVE_ARCH


_PE_MACHINE_AMD64 = 0x8664
_PE_MACHINE_ARM64 = 0xAA64


def _pe_machine(path: Path) -> int | None:
    """读取 Windows PE 文件头中的机器类型。"""
    try:
        with path.open("rb") as fp:
            if fp.read(2) != b"MZ":
                return None
            fp.seek(0x3C)
            offset = int.from_bytes(fp.read(4), "little")
            if offset <= 0:
                return None
            fp.seek(offset)
            if fp.read(4) != b"PE\0\0":
                return None
            return int.from_bytes(fp.read(2), "little")
    except OSError:
        return None


def set_native_arch_from_java(java_exe: str | Path | None) -> str:
    """按 Java 可执行文件的架构决定使用哪一套原生库。

    ARM64 Windows 上也可能装的是 x64 Java（经系统转换层运行），
    此时必须用 x64 的 natives，否则 LWJGL 会直接崩溃。
    """
    global _NATIVE_ARCH
    if not java_exe:
        return _NATIVE_ARCH
    machine = _pe_machine(Path(str(java_exe)))
    if machine == _PE_MACHINE_ARM64:
        _NATIVE_ARCH = "arm64"
    elif machine == _PE_MACHINE_AMD64:
        _NATIVE_ARCH = "x64"
    return _NATIVE_ARCH


def library_native_kind(path: str) -> str:
    """判断库在启动中的角色："native" / "skip" / ""（普通 Java 库）。

    skip 表示这是其他平台或其他架构的原生库：既不能解压，
    也绝不能放进 classpath，否则 LWJGL 会误以为自己在别的架构上运行。
    """
    lowered = (path or "").lower().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    if "-natives-" not in name:
        return ""
    if "natives-windows" not in name:
        return "skip"
    if "arm64" in name or "aarch64" in name:
        return "native" if _NATIVE_ARCH == "arm64" else "skip"
    if _NATIVE_ARCH != "x64":
        return "skip"
    # natives-windows-x86 是 32 位版本，x64 JVM 解压后会覆盖正确的 dll
    if "x86" in name and "x86_64" not in name:
        return "skip"
    return "native"


def is_windows_native(path: str) -> bool:
    """判断库文件是否是当前架构可用的 Windows 原生库。"""
    return library_native_kind(path) == "native"


# ---------------------------------------------------------------- 版本元数据


def find_version_json(version_dir: Path) -> Path | None:
    """在同名 json 或包含 libraries 的 json 中定位版本描述文件。"""
    if not version_dir.is_dir():
        return None

    same_name = version_dir / f"{version_dir.name}.json"
    if same_name.is_file():
        return same_name

    for path in sorted(version_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "libraries" in data:
            return path
    return None


def _read_version_json(version_dir: Path) -> dict | None:
    path = find_version_json(version_dir)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _merge_meta(parent: dict, child: dict) -> dict:
    """把 inheritsFrom 的父版本参数合并进子版本。"""
    merged = dict(parent)
    merged.update(
        {key: value for key, value in child.items() if key not in ("libraries", "arguments")}
    )

    child_libs = [lib for lib in child.get("libraries", []) if isinstance(lib, dict)]
    child_names = {lib.get("name") for lib in child_libs}
    libraries = [
        lib
        for lib in parent.get("libraries", [])
        if isinstance(lib, dict) and lib.get("name") not in child_names
    ]
    libraries.extend(child_libs)
    merged["libraries"] = libraries

    parent_args = parent.get("arguments") or {}
    child_args = child.get("arguments") or {}
    merged["arguments"] = {
        "game": list(parent_args.get("game") or []) + list(child_args.get("game") or []),
        "jvm": list(parent_args.get("jvm") or []) + list(child_args.get("jvm") or []),
    }
    merged["id"] = child.get("id") or parent.get("id")
    return merged


def load_version_meta(
    version_dir: Path,
    versions_root: Path | None = None,
    _depth: int = 0,
) -> dict | None:
    """读取版本 JSON，并自动合并 inheritsFrom 指向的父版本。"""
    data = _read_version_json(version_dir)
    if data is None:
        return None

    parent_id = data.get("inheritsFrom")
    if not parent_id or _depth >= 5:
        return data

    root = versions_root or version_dir.parent
    parent_dir = root / str(parent_id)
    if not parent_dir.is_dir() or parent_dir == version_dir:
        return data

    parent = load_version_meta(parent_dir, root, _depth + 1)
    if parent is None:
        return data

    merged = _merge_meta(parent, data)
    merged["_jar_dir"] = str(parent_dir)
    return merged


def client_jar_path(meta: dict, version_dir: Path) -> Path:
    """定位客户端 jar，继承版本会回退到父版本目录。"""
    version_id = str(meta.get("id") or version_dir.name)
    local = version_dir / f"{version_id}.jar"
    if local.is_file():
        return local

    jar_dir_value = meta.get("_jar_dir")
    if jar_dir_value:
        jar_dir = Path(str(jar_dir_value))
        same_name = jar_dir / f"{jar_dir.name}.jar"
        if same_name.is_file():
            return same_name
        jars = sorted(jar_dir.glob("*.jar"))
        if jars:
            return jars[0]
    return local


def flatten_version(version_dir: Path, versions_root: Path, new_id: str = "") -> bool:
    """把 inheritsFrom 的父版本并入当前版本，使其能独立启动。

    合并后写入同名 json 与客户端 jar，父版本目录不再是必需依赖。
    """
    meta = load_version_meta(version_dir, versions_root)
    if meta is None:
        return False

    target_id = (new_id or "").strip() or version_dir.name
    source_jar = client_jar_path(meta, version_dir)
    if not source_jar.is_file():
        return False

    local_jar = version_dir / f"{target_id}.jar"
    if source_jar.resolve() != local_jar.resolve():
        try:
            shutil.copy2(source_jar, local_jar)
        except OSError:
            return False

    meta.pop("inheritsFrom", None)
    meta.pop("_jar_dir", None)
    meta["id"] = target_id

    path = version_dir / f"{target_id}.json"
    try:
        path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        return False

    # 移除旧的继承型描述文件，避免目录里堆两个 json
    for other in version_dir.glob("*.json"):
        if other.name == path.name:
            continue
        try:
            data = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("inheritsFrom"):
            try:
                other.unlink()
            except OSError:
                pass
    return True


# ---------------------------------------------------------------- 规则判断


def _rule_matches(rule: dict) -> bool:
    os_rule = rule.get("os") or {}
    if os_rule:
        name = os_rule.get("name")
        if name and name != "windows":
            return False
        arch = str(os_rule.get("arch") or "").lower()
        if arch:
            if arch in ("arm64", "aarch64"):
                if _NATIVE_ARCH != "arm64":
                    return False
            elif arch in ("x86_64", "amd64", "64"):
                if _NATIVE_ARCH != "x64":
                    return False
            else:
                return False
    features = rule.get("features") or {}
    # 不启用 demo、自定义分辨率等特性
    if any(features.values()):
        return False
    return True


def rules_allow(rules: list | None) -> bool:
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if not isinstance(rule, dict) or not _rule_matches(rule):
            continue
        allowed = rule.get("action", "allow") == "allow"
    return allowed


def _flatten_arguments(template: list | None) -> list[str]:
    result: list[str] = []
    for item in template or []:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            if not rules_allow(item.get("rules")):
                continue
            value = item.get("value")
            if isinstance(value, list):
                result.extend(str(entry) for entry in value)
            elif value is not None:
                result.append(str(value))
    return result


# ---------------------------------------------------------------- 依赖收集


def maven_path(coord: str) -> str:
    """把 "group:artifact:version[:classifier][:ext]" 转成 Maven 仓库路径。"""
    parts = [part for part in str(coord).split(":") if part]
    if len(parts) < 3:
        return ""

    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    ext = parts[4] if len(parts) > 4 else "jar"

    file_name = f"{artifact}-{version}"
    if classifier:
        file_name += f"-{classifier}"
    file_name += f".{ext}"
    return "/".join(group.split(".") + [artifact, version, file_name])


def collect_libraries(meta: dict, libraries_dir: Path) -> list[dict]:
    """返回需要下载的库文件列表（含 native 标记）。

    Forge / Fabric / Quilt 等加载器版本常见只给出 Maven 坐标与仓库地址，
    因此这里对缺少 downloads 字段的库做一次坐标推导。
    """
    entries: list[dict] = []
    seen: set[str] = set()

    for lib in meta.get("libraries", []):
        if not isinstance(lib, dict) or not rules_allow(lib.get("rules")):
            continue

        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact") or {}
        artifact_path = artifact.get("path")
        artifact_url = artifact.get("url", "")
        if not artifact_path and not downloads:
            # 只有完全没有 downloads 字段时才按坐标推导，
            # 否则含 natives 分类器的库会推导出不存在的 jar
            artifact_path = maven_path(str(lib.get("name", "")))
            if artifact_path:
                base = str(lib.get("url", "") or "https://libraries.minecraft.net/")
                artifact_url = base.rstrip("/") + "/" + artifact_path

        kind = library_native_kind(artifact_path) if artifact_path else ""
        if kind == "skip":
            # 其他平台 / 其他架构的原生库：下载了也用不上，更不能进 classpath
            artifact_path = ""

        if artifact_path and artifact_path not in seen:
            seen.add(artifact_path)
            entries.append(
                {
                    "dest": libraries_dir / artifact_path,
                    "url": artifact_url,
                    "fallback": artifact.get("_fallback_url", ""),
                    "native": kind == "native",
                    "path": artifact_path,
                }
            )

        natives = lib.get("natives") or {}
        classifier = natives.get("windows")
        if classifier:
            classifier = str(classifier).replace("${arch}", "64")
            classified_map = downloads.get("classifiers") or {}
            if _NATIVE_ARCH == "arm64" and f"{classifier}-arm64" in classified_map:
                classifier = f"{classifier}-arm64"
            classified = classified_map.get(classifier) or {}
            classified_path = classified.get("path")
            if classified_path and library_native_kind(classified_path) == "skip":
                classified_path = None
            if classified_path and classified_path not in seen:
                seen.add(classified_path)
                entries.append(
                    {
                        "dest": libraries_dir / classified_path,
                        "url": classified.get("url", ""),
                        "fallback": classified.get("_fallback_url", ""),
                        "native": True,
                        "path": classified_path,
                    }
                )

    return entries


def asset_tasks(meta: dict, assets_dir: Path) -> tuple[list[dict], Path]:
    """返回资源下载任务与索引文件路径。"""
    index_info = meta.get("assetIndex") or {}
    index_id = str(index_info.get("id") or meta.get("assets") or "legacy")
    index_path = assets_dir / "indexes" / f"{index_id}.json"

    tasks: list[dict] = []
    if not index_path.is_file() and index_info.get("url"):
        tasks.append(
            {
                "dest": index_path,
                "url": index_info["url"],
                "fallback": index_info.get("_fallback_url", ""),
            }
        )

    return tasks, index_path


def object_tasks(
    index_path: Path, assets_dir: Path, asset_base: str = ASSET_BASE_URL
) -> list[dict]:
    """根据资源索引生成缺失对象的下载任务。"""
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    base = (asset_base or ASSET_BASE_URL).rstrip("/") + "/"
    official = ASSET_BASE_URL.rstrip("/") + "/"
    use_fallback = base != official
    tasks: list[dict] = []
    for obj in (data.get("objects") or {}).values():
        if not isinstance(obj, dict):
            continue
        digest = str(obj.get("hash", ""))
        if len(digest) < 2:
            continue
        target = assets_dir / "objects" / digest[:2] / digest
        if target.is_file():
            continue
        relative = f"{digest[:2]}/{digest}"
        tasks.append(
            {
                "dest": target,
                "url": f"{base}{relative}",
                # 镜像缺文件时回退到官方资源地址
                "fallback": f"{official}{relative}" if use_fallback else "",
            }
        )
    return tasks


def _download_batch(items: list[dict], kind: str, on_progress=None) -> list[str]:
    pending = [item for item in items if item.get("url")]
    if not pending:
        return []

    errors: list[str] = []
    total = len(pending)
    done = 0

    def _worker(item: dict) -> None:
        try:
            download_file(item["url"], item["dest"])
        except DownloadError:
            # 镜像缺少某些文件时回退到原始地址
            fallback = item.get("fallback")
            if not fallback or fallback == item["url"]:
                raise
            download_file(fallback, item["dest"])

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(_worker, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
            except DownloadError as exc:
                errors.append(f"{item['dest'].name}: {exc}")
            done += 1
            if on_progress is not None:
                on_progress(kind, done, total)

    return errors


def missing_forge_files(meta: dict, libraries_dir: Path) -> list[str]:
    """检查 Forge 安装产物（SRG 客户端库）是否齐全。

    通过官方安装器或 PCL 安装的版本会在 libraries 下生成这些文件；
    仅导出游戏目录的版本通常缺少它们，需要重新安装才能启动。
    """
    game_args = _flatten_arguments((meta.get("arguments") or {}).get("game"))

    def arg_value(key: str) -> str:
        for index, value in enumerate(game_args[:-1]):
            if value == key:
                return game_args[index + 1]
        return ""

    mcp_version = arg_value("--fml.mcpVersion")
    if not mcp_version:
        return []

    mc_version = arg_value("--fml.mcVersion") or str(meta.get("clientVersion") or "")
    forge_version = arg_value("--fml.forgeVersion")
    coord = f"{mc_version}-{mcp_version}"

    missing: list[str] = []
    client_dir = libraries_dir / "net" / "minecraft" / "client" / coord
    for name in (f"client-{coord}-srg.jar", f"client-{coord}-extra.jar"):
        if not (client_dir / name).is_file():
            missing.append(str(client_dir / name))

    if forge_version:
        forge_jar = (
            libraries_dir
            / "net"
            / "minecraftforge"
            / "forge"
            / f"{mc_version}-{forge_version}"
            / f"forge-{mc_version}-{forge_version}-client.jar"
        )
        if not forge_jar.is_file():
            missing.append(str(forge_jar))

    return missing


def prepare_dependencies(
    meta: dict,
    version_dir: Path,
    libraries_dir: Path,
    assets_dir: Path,
    natives_dir: Path,
    on_progress=None,
    asset_base: str = ASSET_BASE_URL,
    mirror: bool = False,
) -> list[str]:
    """补全库文件、客户端 jar、natives 与资源，返回错误信息列表。"""
    errors: list[str] = []
    version_id = str(meta.get("id") or version_dir.name)

    # 客户端 jar（继承版本若父目录已有则跳过）
    client_jar = client_jar_path(meta, version_dir)
    if not client_jar.is_file():
        client_info = (meta.get("downloads") or {}).get("client") or {}
        if client_info.get("url"):
            try:
                download_file(client_info["url"], client_jar)
            except DownloadError:
                fallback = client_info.get("_fallback_url", "")
                try:
                    if not fallback:
                        raise
                    download_file(fallback, client_jar)
                except DownloadError as exc:
                    errors.append(f"客户端 jar 下载失败：{exc}")

    errors.extend(
        install_libraries(meta, libraries_dir, natives_dir, on_progress, mirror=mirror)
    )

    # 资源索引
    index_tasks, index_path = asset_tasks(meta, assets_dir)
    if index_tasks:
        errors.extend(_download_batch(index_tasks, "assets", on_progress))

    # 资源对象
    if index_path.is_file():
        objects = object_tasks(index_path, assets_dir, asset_base)
        errors.extend(_download_batch(objects, "assets", on_progress))

    return errors


def native_target_dir(meta: dict, natives_dir: Path) -> Path:
    """定位原生库应放置的目录。

    新版版本 JSON 会把 -Djava.library.path 指向 ${natives_directory}/java，
    因此需要按实际的 JVM 参数决定解压目标。
    """
    for arg in _flatten_arguments((meta.get("arguments") or {}).get("jvm")):
        if not arg.startswith("-Djava.library.path="):
            continue
        value = arg.split("=", 1)[1].replace("${natives_directory}", str(natives_dir))
        if "${" in value:
            break
        try:
            return Path(value)
        except (OSError, ValueError):
            break
    return natives_dir


def clean_natives(target: Path) -> None:
    """删除 target 里旧的库文件，避免残留其它架构的 dll。"""
    try:
        for stale in target.iterdir():
            if stale.is_file() and stale.suffix.lower() in NATIVE_SUFFIXES:
                stale.unlink(missing_ok=True)
    except OSError:
        pass


def install_libraries(
    meta: dict,
    libraries_dir: Path,
    natives_dir: Path,
    on_progress=None,
    mirror: bool = False,
) -> list[str]:
    """下载缺失的库文件并解压 natives，返回错误信息列表。

    mirror 为真时优先走 BMCLAPI 的 Maven 镜像，失败再回退官方地址。
    """
    errors: list[str] = []
    libraries = collect_libraries(meta, libraries_dir)
    pending: list[dict] = []
    for item in libraries:
        if item["dest"].is_file():
            continue
        if mirror:
            mirrored = mirror_maven_url(str(item.get("url", "")))
            if mirrored:
                pending.append(
                    dict(item, url=mirrored, fallback=item.get("url", ""))
                )
                continue
        pending.append(item)

    errors.extend(_download_batch(pending, "libraries", on_progress))

    target = native_target_dir(meta, natives_dir)
    try:
        natives_dir.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # 切换 Java 架构后旧 dll 还在会让 LWJGL 加载失败，解压前先清空
    clean_natives(target)
    if target != natives_dir:
        clean_natives(natives_dir)

    for item in libraries:
        if not item["native"] or not item["dest"].is_file():
            continue
        try:
            extract_natives(item["dest"], target, _NATIVE_ARCH)
        except Exception:  # noqa: BLE001 - 单个 native 失败不应中断启动
            continue

    return errors


# ---------------------------------------------------------------- 启动命令


def offline_uuid(username: str) -> str:
    digest = hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def classpath_entries(meta: dict, libraries_dir: Path, client_jar: Path) -> list[str]:
    entries: list[str] = []
    for item in collect_libraries(meta, libraries_dir):
        if item["native"]:
            continue
        # 缺失的库不进入 classpath，否则 Forge 的 SecureJar 会直接中断启动
        if not item["dest"].is_file():
            continue
        entries.append(str(item["dest"]))
    if client_jar.is_file():
        entries.append(str(client_jar))
    return entries


def build_command(
    meta: dict,
    java_exe: str,
    version_dir: Path,
    libraries_dir: Path,
    assets_dir: Path,
    natives_dir: Path,
    username: str,
    memory_mb: int,
    extra_jvm: list[str] | None = None,
    extra_game: list[str] | None = None,
    resolution: tuple[int, int] | None = None,
    account: dict | None = None,
    game_dir: Path | None = None,
) -> list[str]:
    version_id = str(meta.get("id") or version_dir.name)
    client_jar = client_jar_path(meta, version_dir)
    classpath = CLASSPATH_SEPARATOR.join(
        classpath_entries(meta, libraries_dir, client_jar)
    )

    asset_index = str((meta.get("assetIndex") or {}).get("id") or meta.get("assets") or "legacy")
    arguments = meta.get("arguments") or {}
    # Forge 等 json 常带空的 arguments 字段，空列表也要退回默认模板，
    # 否则会丢掉 -cp 与 minecraftArguments 里的 tweakClass
    jvm_template = arguments.get("jvm") or None
    game_template = arguments.get("game") or None

    if game_template is None:
        legacy = str(meta.get("minecraftArguments") or "").split()
        game_template = legacy or [
            "--username", "${auth_player_name}",
            "--version", "${version_name}",
            "--gameDir", "${game_directory}",
            "--assetsDir", "${assets_root}",
            "--assetIndex", "${assets_index_name}",
            "--uuid", "${auth_uuid}",
            "--accessToken", "${auth_access_token}",
            "--userType", "legacy",
            "--versionType", "${version_type}",
        ]

    if jvm_template is None:
        jvm_template = ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"]
    elif not any("${classpath}" in str(item) for item in jvm_template):
        # 部分 json 的 jvm 段不带 -cp，由启动器补齐，否则主类必然找不到
        jvm_template = ["-cp", "${classpath}", *jvm_template]

    # 有正版令牌就用正版身份，否则退回离线身份
    auth = account or {}
    if str(auth.get("access_token") or ""):
        player_name = str(auth.get("name") or username)
        auth_uuid = str(auth.get("uuid") or "").replace("-", "")
        auth_token = str(auth["access_token"])
        # 微软正版为 msa，外置登录（authlib-injector）为 mojang
        user_type = str(auth.get("user_type") or "msa")
        auth_xuid = str(auth.get("xuid") or "")
    else:
        player_name = username
        auth_uuid = offline_uuid(username).replace("-", "")
        auth_token = "0"
        user_type = "legacy"
        auth_xuid = ""

    placeholders = {
        "natives_directory": str(natives_dir),
        "launcher_name": "MCHLauncherForPy",
        "launcher_version": "0.1.0",
        "classpath": classpath,
        "classpath_separator": CLASSPATH_SEPARATOR,
        "library_directory": str(libraries_dir),
        "version_name": version_id,
        "version_type": str(meta.get("type") or "release"),
        # 版本隔离关闭时由调用方传入数据根目录，所有版本共用存档与配置
        "game_directory": str(game_dir or version_dir),
        "assets_root": str(assets_dir),
        "assets_index_name": asset_index,
        "auth_player_name": player_name,
        "auth_uuid": auth_uuid,
        "auth_access_token": auth_token,
        "clientid": "",
        "auth_xuid": auth_xuid,
        "user_type": user_type,
        "resolution_width": str(resolution[0]) if resolution else "854",
        "resolution_height": str(resolution[1]) if resolution else "480",
    }

    def substitute(value: str) -> str:
        for key, replacement in placeholders.items():
            value = value.replace("${" + key + "}", str(replacement))
        return value

    command: list[str] = [java_exe]
    command.append(f"-Xmx{max(int(memory_mb), 512)}m")
    command.append(f"-Xms{min(int(memory_mb), 2048)}m")
    command.extend(extra_jvm or [])

    for arg in _flatten_arguments(jvm_template):
        command.append(substitute(arg))

    main_class = str(meta.get("mainClass") or "net.minecraft.client.main.Main")
    command.append(main_class)

    game_args = _flatten_arguments(game_template)
    for arg in game_args:
        command.append(substitute(arg))

    # 极少数版本 JSON 没有 --width/--height，这里补上
    if resolution and not any("--width" in arg for arg in game_args):
        command.extend(["--width", str(resolution[0]), "--height", str(resolution[1])])

    command.extend(extra_game or [])

    return command


def resolve_javaw(java_exe: str) -> str:
    """优先使用 javaw.exe，避免出现控制台黑框。"""
    path = Path(java_exe)
    javaw = path.with_name("javaw.exe")
    return str(javaw) if javaw.is_file() else str(path)


def apply_auto_language(game_dir: Path) -> bool:
    """首次启动前把游戏语言设为简体中文。

    只在该版本的 options.txt 不存在、或其中还没有 lang 行时写入，
    不会覆盖玩家在游戏内自己选择的语言。
    """
    path = game_dir / "options.txt"
    try:
        if not path.is_file():
            game_dir.mkdir(parents=True, exist_ok=True)
            path.write_text("lang:zh_cn\n", encoding="utf-8")
            return True
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    for line in text.splitlines():
        if line.strip().startswith("lang:"):
            return False

    if text and not text.endswith("\n"):
        text += "\n"
    try:
        path.write_text(text + "lang:zh_cn\n", encoding="utf-8")
    except OSError:
        return False
    return True


def start_process(
    command: list[str],
    version_dir: Path,
    log_path: Path,
    work_dir: Path | None = None,
) -> subprocess.Popen:
    """启动游戏进程并返回 Popen 对象，便于跟踪启动状态。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8", errors="ignore")
    return subprocess.Popen(
        command,
        cwd=str(work_dir or version_dir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )


def read_log_tail(log_path: Path, max_bytes: int = 20000) -> str:
    """读取日志文件末尾内容，用于判断启动阶段。"""
    try:
        with log_path.open("rb") as fp:
            fp.seek(0, 2)
            size = fp.tell()
            fp.seek(max(0, size - max_bytes))
            return fp.read().decode("utf-8", "ignore")
    except OSError:
        return ""


def launch_stage(text: str) -> tuple[int, str]:
    """根据日志内容推断启动阶段，返回（百分比, 描述）。"""
    if "Sound engine started" in text or "OpenAL initialized" in text:
        return 92, "正在初始化音频…"
    if "Created:" in text and "atlas" in text:
        return 82, "正在加载资源…"
    if "Backend library: LWJGL" in text:
        return 68, "正在初始化渲染…"
    if "Datafixer Bootstrap" in text:
        return 44, "正在修复游戏数据…"
    if "Setting user:" in text:
        return 56, "正在初始化游戏…"
    if "Environment:" in text or "Loading for game" in text:
        return 48, "游戏主类已加载…"
    return 38, "正在启动游戏进程…"


def find_game_window(pid: int, markers: tuple[str, ...] = ("Minecraft",)) -> int | None:
    """查找该进程下标题包含指定标记的可见窗口。"""
    if os.name != "nt" or pid <= 0:
        return None

    user32 = ctypes.WinDLL("user32")
    found: list[int] = []
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):  # noqa: ANN001
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid or not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if title and any(marker in title for marker in markers):
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(enum_proc_type(_callback), 0)
    return found[0] if found else None


def window_ready(pid: int, markers: tuple[str, ...] = ("Minecraft",)) -> bool:
    """检测该进程是否已经出现可见窗口（视为启动完成）。"""
    return find_game_window(pid, markers) is not None


def apply_window_mode(hwnd: int, mode: str) -> None:
    """按设置调整游戏窗口状态（最大化 / 最小化）。"""
    if os.name != "nt" or not hwnd:
        return
    user32 = ctypes.WinDLL("user32")
    if mode == "maximized":
        user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
    elif mode == "minimized":
        user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE


def window_client_size(hwnd: int) -> tuple[int, int] | None:
    """读取窗口客户区尺寸（用于“与启动器同样大小”）。"""
    if os.name != "nt" or not hwnd:
        return None
    user32 = ctypes.WinDLL("user32")
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 320 or height < 240:
        return None
    return width, height
