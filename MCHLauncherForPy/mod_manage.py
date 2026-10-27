"""已安装模组的扫描、元数据读取与启停。

启用 / 禁用通过文件名后缀实现：载入器只加载 `xxx.jar`，
把文件改名为 `xxx.jar.disabled` 即可让游戏忽略它。
"""

from __future__ import annotations

import base64
import json
import re
import zipfile
from pathlib import Path

DISABLED_SUFFIX = ".disabled"
MANIFEST_CANDIDATES = (
    "fabric.mod.json",
    "META-INF/neoforge.mods.toml",
    "META-INF/mods.toml",
    "mcmod.info",
)
TAG_RE = re.compile(r"<[^>]+>")

# 元数据按（路径, 大小, 修改时间）缓存，避免每次打开页面都解压 jar
_META_CACHE: dict[tuple[str, int, int], dict] = {}
# 图标转成 data URI 后缓存，避免反复读 jar
_ICON_CACHE: dict[tuple[str, int, int], str] = {}
MAX_ICON_BYTES = 300 * 1024


def _clean_text(raw: str, limit: int = 600) -> str:
    text = TAG_RE.sub(" ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _base_stem(file_name: str) -> str:
    name = file_name
    if name.lower().endswith(DISABLED_SUFFIX):
        name = name[: -len(DISABLED_SUFFIX)]
    if name.lower().endswith(".jar"):
        name = name[: -4]
    return name


def _toml_scalar(section: str, keys: tuple[str, ...]) -> str:
    """从 TOML 片段里取一个标量（支持三引号多行与数组）。"""
    for key in keys:
        multi = re.search(
            rf"{key}\s*=\s*(?P<q>'''|\"\"\")(?P<v>.*?)(?P=q)", section, re.S
        )
        if multi:
            return multi.group("v").strip()

        single = re.search(rf"(?m)^\s*{key}\s*=\s*(?P<v>.+)$", section)
        if not single:
            continue
        raw = single.group("v").strip()

        # 单行带引号的值：只取引号内的内容，忽略行尾注释
        if raw[:1] in ("'", '"'):
            end = raw.find(raw[0], 1)
            if end > 0:
                return raw[1:end]

        raw = re.sub(r"\s+#.*$", "", raw).strip()
        if raw.startswith("[") and raw.endswith("]"):
            parts = [
                part.strip().strip('"').strip("'")
                for part in raw[1:-1].split(",")
                if part.strip()
            ]
            return "、".join(parts)
        return raw.strip('"').strip("'")
    return ""


def _from_fabric(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    authors: list[str] = []
    for author in data.get("authors") or []:
        if isinstance(author, str):
            authors.append(author)
        elif isinstance(author, dict) and author.get("name"):
            authors.append(str(author["name"]))

    dependencies: list[str] = []
    depends = data.get("depends")
    if isinstance(depends, dict):
        dependencies = [
            str(name)
            for name in depends
            if str(name) not in ("minecraft", "java", "fabricloader", "quilt_loader")
        ]

    description = data.get("description")
    if isinstance(description, dict):
        description = description.get("text") or description.get("translate") or ""

    return {
        "modid": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "version": str(data.get("version") or ""),
        "authors": "、".join(authors[:3]),
        "description": _clean_text(str(description or "")),
        "dependencies": dependencies[:8],
    }


def _from_toml(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace")
    section = ""
    for block in re.split(r"(?m)^\s*\[\[", text):
        if block.startswith("mods]]"):
            section = block
            break
    if not section:
        return {}

    return {
        "modid": _toml_scalar(section, ("modId", "modid")),
        "name": _toml_scalar(section, ("displayName", "name")),
        "version": _toml_scalar(section, ("version",)),
        "authors": _toml_scalar(section, ("authors", "author")),
        "description": _clean_text(_toml_scalar(section, ("description",))),
        "dependencies": [],
    }


def _from_mcmod(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if isinstance(data, dict):
        data = data.get("modList") or []
    if not isinstance(data, list) or not data:
        return {}

    item = next((entry for entry in data if isinstance(entry, dict)), {})
    authors = item.get("authorList") or item.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]

    return {
        "modid": str(item.get("modid") or ""),
        "name": str(item.get("name") or ""),
        "version": str(item.get("version") or ""),
        "authors": "、".join([str(name) for name in authors if name][:3]),
        "description": _clean_text(str(item.get("description") or "")),
        "dependencies": [],
    }


def read_metadata(path: Path) -> dict:
    """读取 jar 内的模组元数据，识别失败时返回空字段。"""
    meta = {
        "modid": "",
        "name": "",
        "version": "",
        "authors": "",
        "description": "",
        "loader": "",
        "dependencies": [],
    }

    try:
        with zipfile.ZipFile(path) as jar:
            names = set(jar.namelist())
            if "fabric.mod.json" in names:
                meta.update(_from_fabric(jar.read("fabric.mod.json")))
                meta["loader"] = "fabric"
            elif "META-INF/neoforge.mods.toml" in names:
                meta.update(_from_toml(jar.read("META-INF/neoforge.mods.toml")))
                meta["loader"] = "neoforge"
            elif "META-INF/mods.toml" in names:
                meta.update(_from_toml(jar.read("META-INF/mods.toml")))
                meta["loader"] = "forge"
            elif "mcmod.info" in names:
                meta.update(_from_mcmod(jar.read("mcmod.info")))
                meta["loader"] = "forge"
    except (OSError, zipfile.BadZipFile, KeyError):
        pass

    if not meta["name"]:
        meta["name"] = _base_stem(path.name)
    return meta


def _icon_mime(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _icon_candidates(jar: zipfile.ZipFile, names: set[str], modid: str) -> list[str]:
    """按加载器约定收集模组可能使用的图标路径。"""
    candidates: list[str] = []

    manifest = "fabric.mod.json" if "fabric.mod.json" in names else ""
    if not manifest and "quilt.mod.json" in names:
        manifest = "quilt.mod.json"
    if manifest:
        try:
            data = json.loads(jar.read(manifest).decode("utf-8", "replace"))
        except (KeyError, ValueError, UnicodeDecodeError):
            data = {}
        icon = data.get("icon") if isinstance(data, dict) else None
        if isinstance(icon, str):
            candidates.append(icon)
        elif isinstance(icon, dict):
            candidates.extend(str(value) for value in icon.values())

    for entry in ("META-INF/mods.toml", "META-INF/neoforge.mods.toml"):
        if entry not in names:
            continue
        try:
            text = jar.read(entry).decode("utf-8", "replace")
        except (KeyError, UnicodeDecodeError):
            continue
        logo = _toml_scalar(text, ("logoFile", "logo_file"))
        if logo:
            candidates.append(logo)

    if modid:
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            candidates.append(f"assets/{modid}/icon.{ext}")
            candidates.append(f"assets/{modid}/textures/icon.{ext}")

    candidates.extend(("icon.png", "logo.png", "icon.jpg", "logo.jpg"))
    return candidates


def read_mod_icon(path: Path, meta: dict | None = None) -> str:
    """读取模组图标并转成 data URI，找不到时返回空字符串。"""
    modid = str((meta or {}).get("modid") or "").strip()
    try:
        with zipfile.ZipFile(path) as jar:
            names = set(jar.namelist())
            for candidate in _icon_candidates(jar, names, modid):
                name = str(candidate).replace("\\", "/").strip().lstrip("/")
                if not name or name not in names:
                    continue
                try:
                    raw = jar.read(name)
                except (KeyError, OSError):
                    continue
                if not raw or len(raw) > MAX_ICON_BYTES:
                    continue
                return "data:{};base64,{}".format(
                    _icon_mime(name), base64.b64encode(raw).decode("ascii")
                )
    except (OSError, zipfile.BadZipFile):
        return ""
    return ""


def describe_mod(path: Path) -> dict | None:
    """返回单个模组的展示信息；非 jar 文件返回 None。"""
    if not path.is_file():
        return None

    name = path.name
    enabled = True
    if name.lower().endswith(DISABLED_SUFFIX):
        # 只有 xxx.jar.disabled 才算被禁用的模组
        if not name[: -len(DISABLED_SUFFIX)].lower().endswith(".jar"):
            return None
        enabled = False
    elif not name.lower().endswith(".jar"):
        return None

    try:
        stat = path.stat()
    except OSError:
        return None

    key = (str(path), stat.st_size, int(stat.st_mtime))
    meta = _META_CACHE.get(key)
    if meta is None:
        meta = read_metadata(path)
        if len(_META_CACHE) > 512:
            _META_CACHE.clear()
        _META_CACHE[key] = meta

    info = dict(meta)
    info.update(
        {
            "file": name,
            "path": str(path),
            "enabled": enabled,
            "size": stat.st_size,
        }
    )

    icon = _ICON_CACHE.get(key)
    if icon is None:
        icon = read_mod_icon(path, info)
        if len(_ICON_CACHE) > 256:
            _ICON_CACHE.clear()
        _ICON_CACHE[key] = icon
    info["icon"] = icon
    return info


def scan_mods(mods_dir: Path) -> list[dict]:
    """列出 mods 目录内的模组（含已禁用），按名称排序。"""
    if not mods_dir.is_dir():
        return []

    mods: list[dict] = []
    try:
        entries = list(mods_dir.iterdir())
    except OSError:
        return []

    for entry in entries:
        info = describe_mod(entry)
        if info is not None:
            mods.append(info)

    mods.sort(key=lambda item: (item.get("name") or item["file"]).lower())
    return mods


def resolve_mod_file(mods_dir: Path, file_name: str) -> Path | None:
    """把前端传来的文件名解析成 mods 目录下的真实文件（防路径穿越）。"""
    text = str(file_name or "").strip()
    if not text or Path(text).name != text:
        return None
    path = mods_dir / text
    return path if path.is_file() else None


def set_enabled(path: Path, enabled: bool) -> Path:
    """启用 / 禁用模组，返回改名后的路径。"""
    name = path.name
    disabled = name.lower().endswith(DISABLED_SUFFIX)

    if enabled and disabled:
        target = path.with_name(name[: -len(DISABLED_SUFFIX)])
    elif not enabled and not disabled:
        target = path.with_name(name + DISABLED_SUFFIX)
    else:
        return path

    if target.exists():
        raise FileExistsError(target.name)
    path.rename(target)
    return target
