"""整合包解析：Modrinth（.mrpack）与 CurseForge（.zip）。

只做“读”的工作：拿到整合包声明的游戏版本、加载器与文件清单，
真正的下载 / 安装交给 main.LauncherApi，避免这里依赖窗口与进度对象。
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

MODRINTH_INDEX = "modrinth.index.json"
CURSEFORGE_MANIFEST = "manifest.json"

# 整合包（.zip）相关：MCHL 与 PCL 的版本标识目录名
PACK_SUBDIR = "MCHL"
PCL_SUBDIR = "PCL"
SETUP_FILE = "Setup.ini"
PACK_FORMAT = "MCHL"

# 版本目录之外的“公共”游戏文件，导入时并入主版本目录
SHARED_DIRS = (
    "mods",
    "config",
    "defaultconfigs",
    "resourcepacks",
    "shaderpacks",
    "scripts",
    "kubejs",
)
SHARED_FILES = ("options.txt", "servers.dat", "optionsof.txt", "optionsshaders.txt")

# 导出时跳过的目录（日志、存档、崩溃报告等无意义或体积过大）
EXPORT_SKIP_DIRS = ("logs", "crash-reports", "saves", "natives", "screenshots", ".cache")

# 整合包里的加载器依赖键 → 启动器内部加载器 id
MODRINTH_LOADERS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}

# 匹配 "fabric-0.15.11" 这类加载器标识，长名字放前面避免误匹配
LOADER_NAMES = ("neoforge", "fabric", "quilt", "forge")

DEFAULT_OVERRIDES = ("overrides", "client-overrides")


def read_pack(archive: Path) -> dict:
    """解析整合包，返回游戏版本、加载器、文件清单与覆盖目录。"""
    path = Path(archive)
    if not path.is_file():
        return {"ok": False, "error": "整合包文件不存在"}

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MODRINTH_INDEX in names:
                return _read_modrinth(zf)
            if CURSEFORGE_MANIFEST in names:
                return _read_curseforge(zf)
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是有效的整合包压缩文件"}
    except OSError as exc:
        return {"ok": False, "error": f"读取整合包失败：{exc}"}

    return {
        "ok": False,
        "error": "无法识别的整合包格式，仅支持 Modrinth / CurseForge 整合包",
    }


def _read_json_entry(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        data = json.loads(zf.read(name).decode("utf-8-sig", "ignore"))
    except (KeyError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _split_loader(raw: str) -> tuple[str, str]:
    """把 "fabric-0.15.11" 拆成 ("fabric", "0.15.11")。"""
    text = str(raw or "").strip()
    lowered = text.lower()
    for name in LOADER_NAMES:
        if lowered.startswith(name + "-"):
            return name, text[len(name) + 1 :]
    return "", ""


def _override_dirs(zf: zipfile.ZipFile, extra: str = "") -> list[str]:
    """找出整合包里实际存在的覆盖目录。"""
    names = zf.namelist()
    found: list[str] = []
    for prefix in DEFAULT_OVERRIDES + ((extra,) if extra else ()):
        cleaned = str(prefix or "").strip("/")
        if not cleaned or cleaned in found:
            continue
        marker = cleaned + "/"
        if any(name.startswith(marker) for name in names):
            found.append(cleaned)
    return found


def _read_modrinth(zf: zipfile.ZipFile) -> dict:
    data = _read_json_entry(zf, MODRINTH_INDEX)
    deps = data.get("dependencies") or {}
    mc_version = str(deps.get("minecraft") or "").strip()

    loader = ""
    loader_version = ""
    for key, name in MODRINTH_LOADERS.items():
        value = str(deps.get(key) or "").strip()
        if value:
            loader, loader_version = name, value
            break

    if not mc_version:
        return {"ok": False, "error": "整合包没有声明 Minecraft 版本"}

    files: list[dict] = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        env = item.get("env") or {}
        # 仅服务端使用的文件不用装到客户端
        if str(env.get("client") or "").lower() == "unsupported":
            continue
        rel = str(item.get("path") or "").replace("\\", "/").strip().lstrip("/")
        links = item.get("downloads") or []
        url = str(links[0]) if links else ""
        if not rel or not url:
            continue
        files.append(
            {
                "path": rel,
                "url": url,
                "name": Path(rel).name or "file",
                "required": str(env.get("client") or "required") != "optional",
            }
        )

    return {
        "ok": True,
        "format": "modrinth",
        "name": str(data.get("name") or "").strip(),
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "files": files,
        "override_dirs": _override_dirs(zf),
    }


def _read_curseforge(zf: zipfile.ZipFile) -> dict:
    data = _read_json_entry(zf, CURSEFORGE_MANIFEST)
    minecraft = data.get("minecraft") or {}
    mc_version = str(minecraft.get("version") or "").strip()

    raw_loader = ""
    entries = minecraft.get("modLoaders") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("primary") and entry.get("id"):
            raw_loader = str(entry["id"])
            break
    if not raw_loader:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                raw_loader = str(entry["id"])
                break

    loader, loader_version = _split_loader(raw_loader)

    if not mc_version:
        return {"ok": False, "error": "整合包没有声明 Minecraft 版本"}

    files: list[dict] = []
    for entry in data.get("files") or []:
        if not isinstance(entry, dict):
            continue
        try:
            project_id = int(entry.get("projectID") or 0)
            file_id = int(entry.get("fileID") or 0)
        except (TypeError, ValueError):
            continue
        if not project_id or not file_id:
            continue
        files.append(
            {
                "project_id": project_id,
                "file_id": file_id,
                "required": bool(entry.get("required", True)),
            }
        )

    overrides = str(data.get("overrides") or "overrides").strip("/")
    return {
        "ok": True,
        "format": "curseforge",
        "name": str(data.get("name") or "").strip(),
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "files": files,
        "override_dirs": _override_dirs(zf, overrides),
    }


def apply_overrides(archive: Path, target: Path, prefixes: list[str]) -> int:
    """把整合包的覆盖文件解压到版本目录，返回写入的文件数。"""
    root = Path(target)
    if not prefixes:
        return 0

    written = 0
    base = root.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                relative = ""
                for prefix in prefixes:
                    folder = str(prefix or "").strip("/")
                    if folder and name.startswith(folder + "/"):
                        relative = name[len(folder) + 1 :]
                        break
                if not relative:
                    continue

                dest = (root / relative).resolve()
                try:
                    # 防 zip slip：解压结果必须仍在版本目录内
                    dest.relative_to(base)
                except ValueError:
                    continue

                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info))
                    written += 1
                except OSError:
                    continue
    except (zipfile.BadZipFile, OSError):
        return written

    return written


# ---- 整合包导入 / 导出 ------------------------------------------------


def extract_zip(archive: Path, target: Path) -> int:
    """把整合包解压到 target，返回解压的文件数（带 zip slip 防护）。"""
    root = Path(target)
    base = root.resolve()
    count = 0

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            # 忽略 macOS 压缩时附带的元数据
            if name.startswith("__MACOSX/") or name.startswith("._"):
                continue

            dest = (root / name).resolve()
            try:
                dest.relative_to(base)
            except ValueError:
                continue

            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
            except (KeyError, OSError):
                continue
            count += 1

    return count


def find_pack_root(folder: Path) -> Path | None:
    """在解压目录里找到含 versions 的游戏根目录（兼容多包一层文件夹）。"""
    root = Path(folder)
    candidates = [root]
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            candidates.append(child)
            for sub in sorted(child.iterdir()):
                if sub.is_dir():
                    candidates.append(sub)
    except OSError:
        return None

    for candidate in candidates:
        if (candidate / "versions").is_dir():
            return candidate
    return None


def find_pack_versions(root: Path) -> list[Path]:
    """列出整合包里 versions 下可识别的版本目录。"""
    versions_dir = Path(root) / "versions"
    if not versions_dir.is_dir():
        return []

    result: list[Path] = []
    try:
        for folder in sorted(versions_dir.iterdir()):
            if not folder.is_dir():
                continue
            if any(folder.glob("*.json")):
                result.append(folder)
    except OSError:
        return []
    return result


def _detect_loader(meta: dict) -> tuple[str, str]:
    """从 version json 的 libraries 推断加载器字段与版本号。"""
    markers = (
        ("fabric-loader", "VersionFabric"),
        ("quilt-loader", "VersionQuilt"),
        ("neoforge", "VersionNeoForge"),
        ("forge", "VersionForge"),
    )
    for lib in meta.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        name = str(lib.get("name") or "")
        lowered = name.lower()
        for marker, key in markers:
            if marker not in lowered:
                continue
            parts = name.split(":")
            return key, parts[2] if len(parts) > 2 else ""
    return "", ""


def _read_version_json(folder: Path, name: str) -> dict:
    path = folder / f"{name}.json"
    if not path.is_file():
        found = sorted(folder.glob("*.json"))
        path = found[0] if found else None
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_version_dir(folder: Path) -> str:
    """把 PCL 等启动器的版本标识统一成 MCHL/Setup.ini。

    返回 "mchl"（本来就有）、"pcl"（从 PCL 转换）、"generated"（按 json 生成）或 ""。
    """
    target = Path(folder)
    mchl_dir = target / PACK_SUBDIR
    mchl_setup = mchl_dir / SETUP_FILE
    if mchl_setup.is_file():
        return "mchl"

    pcl_setup = target / PCL_SUBDIR / SETUP_FILE
    if pcl_setup.is_file():
        try:
            mchl_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pcl_setup, mchl_setup)
            for icon in ("icon.png", "icon.jpg", "icon.gif", "icon.webp"):
                source = target / PCL_SUBDIR / icon
                if source.is_file():
                    shutil.copyfile(source, mchl_dir / icon)
                    break
        except OSError:
            return ""
        return "pcl"

    meta = _read_version_json(target, target.name)
    if not meta:
        return ""

    mc_version = str(meta.get("inheritsFrom") or meta.get("id") or target.name)
    loader_key, loader_version = _detect_loader(meta)
    fields = {
        "VersionVanillaName": target.name,
        "VersionOriginal": mc_version,
        "VersionForge": "",
        "VersionNeoForge": "",
        "VersionFabric": "",
        "VersionQuilt": "",
        "Info": f"{mc_version} {loader_version}".strip(),
        "State": "6",
    }
    if loader_key:
        fields[loader_key] = loader_version

    try:
        mchl_dir.mkdir(parents=True, exist_ok=True)
        text = "\n".join(f"{key}:{value}" for key, value in fields.items()) + "\n"
        mchl_setup.write_text(text, encoding="utf-8")
    except OSError:
        return ""
    return "generated"


def merge_shared_files(root: Path, target: Path) -> int:
    """把整合包根目录的公共游戏文件并入版本目录（不覆盖已有文件）。"""
    source_root = Path(root)
    dest_root = Path(target)
    count = 0

    for name in SHARED_DIRS:
        source = source_root / name
        if not source.is_dir():
            continue
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            dest = dest_root / path.relative_to(source_root)
            if dest.exists():
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            except OSError:
                continue
            count += 1

    for name in SHARED_FILES:
        source = source_root / name
        if not source.is_file():
            continue
        dest = dest_root / name
        if dest.exists():
            continue
        try:
            shutil.copy2(source, dest)
        except OSError:
            continue
        count += 1

    return count


def build_pack_setup(name: str, mc_version: str, loader: str) -> str:
    """生成整合包根目录的 MCHL/Setup.ini 内容。"""
    lines = [
        "PackFormat:MCHL",
        f"PackName:{name}",
        f"PackVersion:{mc_version}",
        f"PackLoader:{loader}",
        f"PackVersionDir:{name}",
    ]
    return "\n".join(lines) + "\n"
