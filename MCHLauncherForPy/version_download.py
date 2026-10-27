"""原版游戏版本清单获取与安装。"""

from __future__ import annotations

import copy
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

import game_launcher
from runtime_utils import DownloadError, SSL_CONTEXT, USER_AGENT, download_file

OFFICIAL_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
MIRROR_BASE = "https://bmclapi2.bangbang93.com"
MIRROR_MANIFEST = f"{MIRROR_BASE}/mc/game/version_manifest_v2.json"
OFFICIAL_ASSET_BASE = "https://resources.download.minecraft.net/"
MIRROR_ASSET_BASE = f"{MIRROR_BASE}/assets/"
MODRINTH_API = "https://api.modrinth.com/v2"
FABRIC_API_PROJECT = "fabric-api"

TYPE_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "远古 Beta",
    "old_alpha": "远古 Alpha",
}


def _read_json(url: str, timeout: int = 30, retry: int = 1) -> dict:
    """读取 JSON；镜像会 302 跳转到不同 CDN 节点，偶发超时因此允许重试。"""
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


def asset_base(mirror: bool) -> str:
    return MIRROR_ASSET_BASE if mirror else OFFICIAL_ASSET_BASE


def _json_from_sources(urls: list[str]) -> tuple[dict | None, str]:
    """按顺序尝试多个地址，返回（数据, 最后错误）。"""
    error = ""
    for url in urls:
        if not url:
            continue
        try:
            return _read_json(url), ""
        except Exception as exc:  # noqa: BLE001 - 依次尝试可用源
            error = str(exc)
    return None, error


def fetch_manifest(mirror: bool = False) -> dict:
    """获取版本清单；首选源不可用时自动切换到另一个源。"""
    primary = MIRROR_MANIFEST if mirror else OFFICIAL_MANIFEST
    secondary = OFFICIAL_MANIFEST if mirror else MIRROR_MANIFEST
    source = "mirror" if mirror else "official"

    data, error = _json_from_sources([primary])
    used = source
    if data is None:
        data, error = _json_from_sources([secondary])
        used = "official" if source == "mirror" else "mirror"
    if data is None:
        return {"ok": False, "error": "无法获取版本清单，请检查网络或切换下载源"}

    versions = []
    for item in data.get("versions", []):
        version_id = item.get("id")
        if not version_id:
            continue
        versions.append(
            {
                "id": version_id,
                "type": item.get("type", ""),
                "type_label": TYPE_LABELS.get(item.get("type", ""), item.get("type", "")),
                "release_time": item.get("releaseTime", ""),
                "url": item.get("url", ""),
            }
        )

    return {
        "ok": True,
        "latest": data.get("latest", {}),
        "versions": versions,
        "source": used,
        "fallback": used != source,
    }


def _redirect(entry: dict, new_url: str) -> None:
    """替换下载地址，同时保留原始地址用于镜像缺文件时回退。"""
    original = entry.get("url", "")
    if original and original != new_url:
        entry["_fallback_url"] = original
    entry["url"] = new_url


def apply_mirror(meta: dict) -> None:
    """就地替换版本 JSON 中体积较大的下载地址为镜像地址。"""
    version_id = str(meta.get("id", ""))
    downloads = meta.get("downloads") or {}
    client = downloads.get("client")
    if isinstance(client, dict) and version_id:
        _redirect(client, f"{MIRROR_BASE}/version/{version_id}/client")

    for lib in meta.get("libraries", []):
        if not isinstance(lib, dict):
            continue
        lib_downloads = lib.get("downloads") or {}
        artifact = lib_downloads.get("artifact")
        if isinstance(artifact, dict) and artifact.get("path"):
            _redirect(artifact, f"{MIRROR_BASE}/maven/{artifact['path']}")
        for classified in (lib_downloads.get("classifiers") or {}).values():
            if isinstance(classified, dict) and classified.get("path"):
                _redirect(classified, f"{MIRROR_BASE}/maven/{classified['path']}")


def _format_time(raw: str) -> str:
    """把 ISO 时间转成 Setup.ini 使用的 "YYYY-MM-DD HH:MM"。"""
    text = (raw or "").replace("T", " ").replace("Z", "")
    if len(text) >= 16:
        return text[:16]
    return text


def write_setup_ini(
    target_dir: Path,
    meta: dict,
    version_type: str,
    version_name: str = "",
    hidden: bool = False,
) -> Path:
    """为下载的版本生成 MCHL/Setup.ini，使启动器能够识别。

    hidden 为真时写入 Hidden:1，仅作为加载器依赖使用，不在版本列表中显示。
    """
    setup_dir = target_dir / "MCHL"
    setup_dir.mkdir(parents=True, exist_ok=True)
    setup_path = setup_dir / "Setup.ini"

    version_id = str(meta.get("id", ""))
    display_name = (version_name or "").strip() or version_id or target_dir.name
    main_part, _, sub_part = version_id.partition(".")
    type_label = TYPE_LABELS.get(version_type, version_type or "正式版")

    lines = [
        f"VersionVanillaName:{display_name}",
        f"VersionOriginal:{version_id}",
        f"VersionOriginalMain:{main_part}",
        f"VersionOriginalSub:{sub_part or '0'}",
        "VersionForge:",
        "VersionNeoForge:",
        "VersionFabric:",
        "VersionQuilt:",
        f"ReleaseTime:{_format_time(meta.get('releaseTime', ''))}",
        f"Info:{type_label} {version_id}",
        "State:6",
        f"VersionVanilla:{main_part}.0.0",
    ]
    if hidden:
        lines.append("Hidden:1")
    setup_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return setup_path


def install_version(
    version_id: str,
    mirror: bool,
    versions_dir: Path,
    libraries_dir: Path,
    assets_dir: Path,
    on_progress=None,
    target_name: str = "",
    hidden: bool = False,
) -> dict:
    """下载并安装一个原版版本，返回结果字典。

    target_name 用于把版本装进自定义名称的目录，留空则使用版本号。
    hidden 为真表示该原版只作为加载器依赖，不在版本列表中显示。
    """
    manifest = fetch_manifest(mirror)
    if not manifest.get("ok"):
        return {"ok": False, "error": manifest.get("error", "获取版本清单失败")}

    entry = next(
        (item for item in manifest["versions"] if item["id"] == version_id), None
    )
    if entry is None:
        return {"ok": False, "error": f"未找到版本 {version_id}"}

    mirror_json = f"{MIRROR_BASE}/version/{version_id}/json"
    primary_json = mirror_json if mirror else entry["url"]
    secondary_json = entry["url"] if mirror else mirror_json
    meta, _ = _json_from_sources([primary_json, secondary_json])
    if meta is None:
        return {"ok": False, "error": "下载版本 JSON 失败，请切换下载源后重试"}

    original_meta = copy.deepcopy(meta)
    if mirror:
        apply_mirror(meta)

    folder_name = (target_name or "").strip() or version_id
    target_dir = versions_dir / folder_name
    created_dir = not target_dir.exists()
    (target_dir / "MCHL").mkdir(parents=True, exist_ok=True)

    (target_dir / f"{version_id}.json").write_text(
        json.dumps(original_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if on_progress is not None:
        on_progress("version", 0, 0)

    errors = game_launcher.prepare_dependencies(
        meta,
        target_dir,
        libraries_dir,
        assets_dir,
        target_dir / "natives",
        on_progress,
        asset_base=asset_base(mirror),
        mirror=mirror,
    )

    if errors:
        # 下载失败时清掉本次新建的目录，避免留下没有 Setup.ini 的残留版本
        if created_dir:
            shutil.rmtree(target_dir, ignore_errors=True)
        return {
            "ok": False,
            "error": f"有 {len(errors)} 个文件下载失败：{errors[0]}",
            "version": folder_name,
        }

    write_setup_ini(
        target_dir,
        original_meta,
        str(meta.get("type", "")),
        version_name=folder_name,
        hidden=hidden,
    )

    return {
        "ok": True,
        "version": folder_name,
        "path": str(target_dir),
        "type_label": entry.get("type_label", ""),
    }


# ---------------------------------------------------------------- Fabric API


def fetch_fabric_api(mc_version: str) -> dict:
    """查询与指定游戏版本匹配的 Fabric API 版本列表（最新在最前）。"""
    version = str(mc_version or "").strip()
    if not version:
        return {"ok": False, "error": "缺少游戏版本"}

    query = urllib.parse.urlencode(
        {
            "game_versions": json.dumps([version]),
            "loaders": json.dumps(["fabric"]),
        }
    )
    url = f"{MODRINTH_API}/project/{FABRIC_API_PROJECT}/version?{query}"
    try:
        data = _read_json(url)
    except Exception:  # noqa: BLE001 - 网络失败统一反馈
        return {"ok": False, "error": "无法获取 Fabric API 版本信息，请检查网络"}

    if not isinstance(data, list) or not data:
        return {"ok": False, "error": f"未找到适配 {version} 的 Fabric API"}

    versions: list[dict] = []
    for item in data[:20]:
        if not isinstance(item, dict):
            continue
        files = item.get("files") or []
        primary = next(
            (file for file in files if isinstance(file, dict) and file.get("primary")),
            files[0] if files else None,
        )
        if not isinstance(primary, dict) or not primary.get("url"):
            continue
        version_number = str(item.get("version_number", ""))
        if not version_number:
            continue
        versions.append(
            {
                "version": version_number,
                "name": str(item.get("name", "")),
                "file_name": str(primary.get("filename", "")),
                "url": str(primary.get("url", "")),
                "size": int(primary.get("size") or 0),
            }
        )

    if not versions:
        return {"ok": False, "error": f"未找到适配 {version} 的 Fabric API"}

    return {
        "ok": True,
        "mc_version": version,
        "versions": versions,
        # 默认选中最新（自动匹配）的版本
        "recommended": versions[0]["version"],
    }


def install_fabric_api(
    target_dir: Path, mc_version: str, version: str = "", on_progress=None
) -> dict:
    """下载 Fabric API 到版本目录的 mods 文件夹，version 为空时取最新匹配。"""
    info = fetch_fabric_api(mc_version)
    if not info.get("ok"):
        return info

    wanted = str(version or "").strip()
    versions = info.get("versions") or []
    entry = None
    if wanted:
        entry = next((item for item in versions if item["version"] == wanted), None)
    if entry is None:
        entry = versions[0] if versions else None
    if entry is None:
        return {"ok": False, "error": "Fabric API 版本信息为空"}

    file_name = entry.get("file_name") or "fabric-api.jar"
    mods_dir = target_dir / "mods"
    dest = mods_dir / file_name
    expected = int(entry.get("size") or 0)

    def _progress(done: int, total: int, label: str = "") -> None:
        if on_progress is not None:
            on_progress("fabricapi", done, total)

    try:
        mods_dir.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or (expected and dest.stat().st_size != expected):
            download_file(entry["url"], dest, on_progress=_progress, label="Fabric API")
    except OSError as exc:
        return {"ok": False, "error": f"保存 Fabric API 失败：{exc}"}
    except DownloadError as exc:
        return {"ok": False, "error": f"Fabric API 下载失败：{exc}"}

    return {
        "ok": True,
        "version": entry.get("version", ""),
        "file": str(dest),
        "size": expected,
    }
