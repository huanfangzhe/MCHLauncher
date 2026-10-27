"""模组搜索与文件下载（CurseForge / Modrinth）。

CurseForge 官方接口需要 API Key，这里默认使用返回结构一致的社区镜像；
若设置了环境变量 CURSEFORGE_API_KEY，则优先直连官方接口。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runtime_utils import DownloadError, SSL_CONTEXT, USER_AGENT, download_file

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_OFFICIAL = "https://api.curseforge.com/v1"
CURSEFORGE_MIRRORS = (
    "https://mod.mcimirror.top/curseforge/v1",
    "https://api.curse.tools/v1/cf",
)

CF_GAME_ID = 432
CF_CLASS_MODS = 6
CF_SORT_POPULARITY = 2
CF_LOADER_TYPES = {
    "forge": 1,
    "liteloader": 3,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}
CF_RELEASE_LABELS = {1: "正式版", 2: "Beta", 3: "Alpha"}
# CurseForge 依赖关系：3 才是必须的前置模组
CF_DEP_TYPES = {
    1: "embedded",
    2: "optional",
    3: "required",
    4: "tool",
    5: "incompatible",
    6: "embedded",
}
MODRINTH_RELEASE_LABELS = {"release": "正式版", "beta": "Beta", "alpha": "Alpha"}

SOURCES = ("curseforge", "modrinth", "all")
SOURCE_LABELS = {"curseforge": "CurseForge", "modrinth": "Modrinth", "all": "全部"}

# 下载内容类型：模组 / 整合包 / 资源包 / 光影包 / 数据包
CONTENT_TYPES = ("mod", "modpack", "resourcepack", "shaderpack", "datapack")
CONTENT_LABELS = {
    "mod": "模组",
    "modpack": "整合包",
    "resourcepack": "资源包",
    "shaderpack": "光影包",
    "datapack": "数据包",
}
# CurseForge classId；数据包在 CurseForge 没有独立分类，只走 Modrinth
CF_CLASS_IDS = {
    "mod": CF_CLASS_MODS,
    "modpack": 4471,
    "resourcepack": 12,
    "shaderpack": 6552,
}

MC_VERSION_RE = re.compile(r"^\d+\.\d+")

# CurseForge 镜像按条目串行取上游数据，拆小页并发能显著缩短等待
# 实测 30 条：单页约 25 秒，3 条一页并发 10 路约 2~3 秒
CF_PAGE_CHUNK = 3
CF_MAX_WORKERS = 10

# 搜索结果短期缓存，来回切换来源 / 重复搜索时直接复用
CACHE_TTL = 300.0
_CACHE: dict[tuple, tuple[float, object]] = {}


def _cache_read(key: tuple):
    entry = _CACHE.get(key)
    if entry is None:
        return None
    stamp, value = entry
    if time.time() - stamp > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_write(key: tuple, value) -> None:
    if len(_CACHE) > 128:
        _CACHE.clear()
    _CACHE[key] = (time.time(), value)


def _read_json(
    url: str,
    timeout: int = 25,
    headers: dict[str, str] | None = None,
    retry: int = 1,
) -> dict:
    """读取 JSON，失败后重试一次。"""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    for _ in range(retry + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(
                request, timeout=timeout, context=SSL_CONTEXT
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("读取 JSON 失败")


def _post_json(
    url: str,
    payload: dict,
    timeout: int = 25,
    headers: dict[str, str] | None = None,
) -> dict:
    """POST JSON 并返回解析结果。"""
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def _curseforge_endpoints() -> list[tuple[str, dict[str, str]]]:
    """CurseForge 可用接口：有 Key 走官方，否则依次尝试镜像。"""
    endpoints: list[tuple[str, dict[str, str]]] = []
    api_key = str(os.environ.get("CURSEFORGE_API_KEY", "") or "").strip()
    if api_key:
        endpoints.append((CURSEFORGE_OFFICIAL, {"x-api-key": api_key}))
    for base in CURSEFORGE_MIRRORS:
        endpoints.append((base, {}))
    return endpoints


def _curseforge_json(path: str, timeout: int = 25) -> dict:
    """按顺序请求 CurseForge 接口，返回第一个成功的结果。"""
    errors: list[str] = []
    for base, headers in _curseforge_endpoints():
        try:
            return _read_json(base + path, timeout=timeout, headers=headers)
        except Exception as exc:  # noqa: BLE001 - 换下一个接口
            host = urllib.parse.urlsplit(base).netloc
            errors.append(f"{host}（{exc}）")
    raise RuntimeError("；".join(errors) or "CurseForge 接口不可用")


def _curseforge_post(path: str, payload: dict, timeout: int = 25) -> dict:
    """POST 到 CurseForge 接口（官方 / 镜像依次尝试）。"""
    errors: list[str] = []
    for base, headers in _curseforge_endpoints():
        try:
            return _post_json(base + path, payload, timeout=timeout, headers=headers)
        except Exception as exc:  # noqa: BLE001 - 换下一个接口
            host = urllib.parse.urlsplit(base).netloc
            errors.append(f"{host}（{exc}）")
    raise RuntimeError("；".join(errors) or "CurseForge 接口不可用")


def _normalize_source(source: str) -> str:
    text = str(source or "all").strip().lower()
    return text if text in SOURCES else "all"


def _normalize_type(project_type: str) -> str:
    """内容类型：mod / modpack / resourcepack / datapack，未知值按模组处理。"""
    text = str(project_type or "mod").strip().lower()
    return text if text in CONTENT_TYPES else "mod"


def _clean_summary(raw, limit: int = 120) -> str:
    """压缩简介文本，卡片上只展示一小段。"""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _curseforge_search_page(
    query: str,
    mc_version: str,
    loader: str,
    limit: int,
    offset: int,
    project_type: str = "mod",
) -> tuple[list[dict], int]:
    params: dict[str, object] = {
        "gameId": CF_GAME_ID,
        "classId": CF_CLASS_IDS.get(project_type, CF_CLASS_MODS),
        "sortField": CF_SORT_POPULARITY,
        "sortOrder": "desc",
        "pageSize": limit,
        "index": offset,
    }
    if query:
        params["searchFilter"] = query
    if mc_version:
        params["gameVersion"] = mc_version
    # 整合包同样按加载器区分（Fabric / Forge / NeoForge / Quilt）
    loader_id = (
        CF_LOADER_TYPES.get(loader)
        if project_type in ("mod", "modpack")
        else None
    )
    if loader_id:
        params["modLoaderType"] = loader_id

    data = _curseforge_json("/mods/search?" + urllib.parse.urlencode(params))
    items: list[dict] = []
    for mod in data.get("data") or []:
        if not isinstance(mod, dict):
            continue
        mod_id = mod.get("id")
        if mod_id is None:
            continue
        authors = [
            str(author.get("name") or "")
            for author in mod.get("authors") or []
            if isinstance(author, dict)
        ]
        logo = mod.get("logo") if isinstance(mod.get("logo"), dict) else {}
        items.append(
            {
                "source": "curseforge",
                "id": str(mod_id),
                "slug": str(mod.get("slug") or ""),
                "name": str(mod.get("name") or ""),
                "summary": str(mod.get("summary") or ""),
                "author": "、".join([name for name in authors if name][:2]),
                "icon": str(logo.get("thumbnailUrl") or ""),
                "downloads": int(mod.get("downloadCount") or 0),
                "updated": str(mod.get("dateModified") or "")[:10],
            }
        )

    pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
    try:
        total = int(pagination.get("totalCount") or 0)
    except (TypeError, ValueError):
        total = 0
    return items, total


def _search_curseforge(
    query: str,
    mc_version: str,
    loader: str,
    limit: int,
    offset: int,
    project_type: str = "mod",
) -> tuple[list[dict], int]:
    """CurseForge 镜像每返回一条结果都要等一次上游请求，这里拆页并发取。

    实测单页 30 条约 25 秒，拆成 6 页并发后约 4~5 秒。
    """
    pages: list[tuple[int, int]] = []
    remaining = limit
    index = offset
    while remaining > 0:
        size = min(CF_PAGE_CHUNK, remaining)
        pages.append((size, index))
        remaining -= size
        index += size

    chunks: list[list[dict]] = []
    errors: list[Exception] = []
    total = 0
    if len(pages) == 1:
        size, start = pages[0]
        chunk, total = _curseforge_search_page(
            query, mc_version, loader, size, start, project_type
        )
        chunks.append(chunk)
    else:
        with ThreadPoolExecutor(max_workers=min(len(pages), CF_MAX_WORKERS)) as pool:
            futures = [
                pool.submit(
                    _curseforge_search_page,
                    query,
                    mc_version,
                    loader,
                    size,
                    start,
                    project_type,
                )
                for size, start in pages
            ]
            for future in futures:  # 按页序取回，保证顺序稳定
                try:
                    chunk, chunk_total = future.result()
                except Exception as exc:  # noqa: BLE001 - 单页失败不影响其它页
                    errors.append(exc)
                    continue
                chunks.append(chunk)
                total = max(total, chunk_total)

    items: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        for item in chunk:
            project_id = item["id"]
            if project_id in seen:
                continue
            seen.add(project_id)
            items.append(item)

    if not items and errors:
        raise errors[0]
    return items, total


def _search_modrinth(
    query: str,
    mc_version: str,
    loader: str,
    limit: int,
    offset: int,
    project_type: str = "mod",
) -> tuple[list[dict], int]:
    # Modrinth 把光影包归类为 shader
    modrinth_type = "shader" if project_type == "shaderpack" else project_type
    facets: list[list[str]] = [[f"project_type:{modrinth_type}"]]
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    # 模组与整合包都按加载器区分；资源包 / 光影包 / 数据包不参与过滤
    if loader and project_type in ("mod", "modpack"):
        facets.append([f"categories:{loader}"])

    params = {
        "query": query,
        "limit": limit,
        "offset": offset,
        "index": "relevance" if query else "downloads",
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    data = _read_json(f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params))
    items: list[dict] = []
    for hit in data.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        project_id = hit.get("project_id")
        if not project_id:
            continue
        items.append(
            {
                "source": "modrinth",
                "id": str(project_id),
                "slug": str(hit.get("slug") or ""),
                "name": str(hit.get("title") or ""),
                "summary": str(hit.get("description") or ""),
                "author": str(hit.get("author") or ""),
                "icon": str(hit.get("icon_url") or ""),
                "downloads": int(hit.get("downloads") or 0),
                "updated": str(hit.get("date_modified") or "")[:10],
            }
        )
    try:
        total = int(data.get("total_hits") or 0)
    except (TypeError, ValueError):
        total = 0
    return items, total


def _has_more(start: int, received: int, total: int, size: int) -> bool:
    """是否还能继续翻页：优先用接口给的总数判断。"""
    if total > 0:
        return start + received < total
    return received >= size


def _source_items(
    site: str,
    keyword: str,
    game_version: str,
    loader: str,
    size: int,
    start: int,
    project_type: str = "mod",
) -> tuple[list[dict], int]:
    """取单个来源的结果，命中缓存时直接返回。"""
    key = ("search", site, keyword, game_version, loader, size, start, project_type)
    cached = _cache_read(key)
    if isinstance(cached, dict):
        return list(cached.get("items") or []), int(cached.get("total") or 0)

    if site == "curseforge":
        items, total = _search_curseforge(
            keyword, game_version, loader, size, start, project_type
        )
    else:
        items, total = _search_modrinth(
            keyword, game_version, loader, size, start, project_type
        )
    _cache_write(key, {"items": items, "total": total})
    return items, total


def search(
    source: str = "all",
    query: str = "",
    mc_version: str = "",
    loader: str = "",
    limit: int = 30,
    offset: int = 0,
    project_type: str = "mod",
) -> dict:
    """按来源搜索内容；来源为 all 时合并两个站点并按下载量排序。

    project_type 取 mod / modpack / resourcepack / datapack。
    """
    source = _normalize_source(source)
    content_type = _normalize_type(project_type)
    keyword = str(query or "").strip()
    game_version = str(mc_version or "").strip()
    loader_id = str(loader or "").strip().lower()
    if loader_id not in CF_LOADER_TYPES:
        loader_id = ""
    size = max(1, min(int(limit or 30), 50))
    start = max(0, int(offset or 0))

    sites: list[str] = []
    # CurseForge 没有独立的数据包分类，数据包只搜 Modrinth
    if source in ("curseforge", "all") and content_type != "datapack":
        sites.append("curseforge")
    if source in ("modrinth", "all"):
        sites.append("modrinth")

    items: list[dict] = []
    errors: list[str] = []
    totals: dict[str, int] = {}
    more: dict[str, bool] = {}

    if len(sites) > 1:
        with ThreadPoolExecutor(max_workers=len(sites)) as pool:
            futures = [
                pool.submit(
                    _source_items,
                    site,
                    keyword,
                    game_version,
                    loader_id,
                    size,
                    start,
                    content_type,
                )
                for site in sites
            ]
            for site, future in zip(sites, futures):
                try:
                    site_items, site_total = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个来源失败不影响另一个
                    errors.append(f"{SOURCE_LABELS[site]}：{exc}")
                    continue
                items += site_items
                totals[site] = site_total
                more[site] = _has_more(start, len(site_items), site_total, size)
    else:
        for site in sites:
            try:
                site_items, site_total = _source_items(
                    site, keyword, game_version, loader_id, size, start, content_type
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{SOURCE_LABELS[site]}：{exc}")
                continue
            items += site_items
            totals[site] = site_total
            more[site] = _has_more(start, len(site_items), site_total, size)

    if not items and errors:
        return {"ok": False, "error": "；".join(errors), "items": []}

    items.sort(key=lambda item: item.get("downloads", 0), reverse=True)
    return {
        "ok": True,
        "source": source,
        "query": keyword,
        "items": items[: size * len(sites)],
        "errors": errors,
        # 各来源可继续翻页的状态，供界面显示“加载更多”
        "totals": totals,
        "has_more": any(more.values()),
        "offset": start,
        "limit": size,
        "total": sum(totals.values()),
    }


def _versions_curseforge(
    project_id: str, mc_version: str, loader: str, limit: int
) -> list[dict]:
    params: dict[str, object] = {"pageSize": limit, "index": 0}
    if mc_version:
        params["gameVersion"] = mc_version
    loader_id = CF_LOADER_TYPES.get(loader)
    if loader_id:
        params["modLoaderType"] = loader_id

    data = _curseforge_json(
        f"/mods/{urllib.parse.quote(project_id)}/files?"
        + urllib.parse.urlencode(params)
    )
    versions: list[dict] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        raw_versions = [str(value) for value in item.get("gameVersions") or []]
        release = item.get("releaseType")
        try:
            release_type = int(release or 0)
        except (TypeError, ValueError):
            release_type = 0
        versions.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("displayName") or item.get("fileName") or ""),
                "file_name": str(item.get("fileName") or ""),
                "url": str(item.get("downloadUrl") or ""),
                "size": int(item.get("fileLength") or 0),
                "date": str(item.get("fileDate") or "")[:10],
                "type": CF_RELEASE_LABELS.get(release_type, ""),
                "game_versions": [
                    value for value in raw_versions if MC_VERSION_RE.match(value)
                ][:8],
                "loaders": [
                    value
                    for value in raw_versions
                    if value.lower() in CF_LOADER_TYPES
                ][:4],
                "dependencies": _curseforge_dependencies(item),
            }
        )
    return versions


def _curseforge_dependencies(item: dict) -> list[dict]:
    """CurseForge 文件的前置关系：relationType 3 为必需依赖。"""
    deps: list[dict] = []
    for dependency in item.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        mod_id = dependency.get("modId")
        if mod_id is None:
            continue
        try:
            relation = int(dependency.get("relationType") or 0)
        except (TypeError, ValueError):
            relation = 0
        deps.append(
            {
                "id": str(mod_id),
                "type": CF_DEP_TYPES.get(relation, "required"),
            }
        )
    return deps


def _versions_modrinth(
    project_id: str, mc_version: str, loader: str, limit: int
) -> list[dict]:
    params: dict[str, str] = {}
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    if loader:
        params["loaders"] = json.dumps([loader])

    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    data = _read_json(
        f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version{query}"
    )
    if not isinstance(data, list):
        return []

    versions: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        files = [file for file in item.get("files") or [] if isinstance(file, dict)]
        primary = next(
            (file for file in files if file.get("primary")),
            files[0] if files else None,
        )
        if not isinstance(primary, dict):
            continue
        versions.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("version_number") or item.get("name") or ""),
                "file_name": str(primary.get("filename") or ""),
                "url": str(primary.get("url") or ""),
                "size": int(primary.get("size") or 0),
                "date": str(item.get("date_published") or "")[:10],
                "type": MODRINTH_RELEASE_LABELS.get(
                    str(item.get("version_type") or ""), ""
                ),
                "game_versions": [str(v) for v in item.get("game_versions") or []][:8],
                "loaders": [str(v) for v in item.get("loaders") or []][:4],
                "dependencies": _modrinth_dependencies(item),
            }
        )
        if len(versions) >= limit:
            break
    return versions


def _modrinth_dependencies(item: dict) -> list[dict]:
    """Modrinth 版本声明的依赖（只保留能对应到项目的条目）。"""
    deps: list[dict] = []
    for dependency in item.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        project_id = dependency.get("project_id")
        if not project_id:
            continue
        deps.append(
            {
                "id": str(project_id),
                "type": str(dependency.get("dependency_type") or "required"),
            }
        )
    return deps


def _resolve_modrinth_projects(ids: list[str]) -> dict[str, dict]:
    """批量查 Modrinth 项目信息（名称 / 图标 / 简介），用于前置模组卡片。"""
    if not ids:
        return {}

    key = ("mr-projects", tuple(sorted(ids)))
    cached = _cache_read(key)
    if isinstance(cached, dict):
        return dict(cached)

    projects: dict[str, dict] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        params = {"ids": json.dumps(chunk, separators=(",", ":"))}
        try:
            data = _read_json(
                f"{MODRINTH_API}/projects?" + urllib.parse.urlencode(params)
            )
        except Exception:  # noqa: BLE001 - 详情解析失败不影响主流程
            continue
        if not isinstance(data, list):
            continue
        for project in data:
            if not isinstance(project, dict) or not project.get("id"):
                continue
            projects[str(project["id"])] = {
                "name": str(project.get("title") or ""),
                "slug": str(project.get("slug") or ""),
                "icon": str(project.get("icon_url") or ""),
                "summary": _clean_summary(project.get("description")),
                "downloads": int(project.get("downloads") or 0),
            }

    _cache_write(key, projects)
    return projects


def _resolve_curseforge_projects(ids: list[str]) -> dict[str, dict]:
    """批量查 CurseForge 模组信息；批量接口不可用时逐个补查。"""
    if not ids:
        return {}

    key = ("cf-projects", tuple(sorted(ids)))
    cached = _cache_read(key)
    if isinstance(cached, dict):
        return dict(cached)

    projects: dict[str, dict] = {}

    def _store(mod: dict | None, fallback_id: str = "") -> None:
        if not isinstance(mod, dict):
            return
        raw_id = mod.get("id")
        project_id = str(raw_id) if raw_id is not None else fallback_id
        if not project_id:
            return
        logo = mod.get("logo") if isinstance(mod.get("logo"), dict) else {}
        projects[project_id] = {
            "name": str(mod.get("name") or ""),
            "slug": str(mod.get("slug") or ""),
            "icon": str(logo.get("thumbnailUrl") or ""),
            "summary": _clean_summary(mod.get("summary")),
            "downloads": int(mod.get("downloadCount") or 0),
        }

    numeric: list[int] = []
    for raw in ids:
        try:
            numeric.append(int(raw))
        except (TypeError, ValueError):
            continue

    for start in range(0, len(numeric), 50):
        chunk = numeric[start : start + 50]
        try:
            data = _curseforge_post("/mods", {"modIds": chunk})
        except Exception:  # noqa: BLE001 - 换逐个查询
            data = None
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for mod in rows:
            _store(mod)

    missing = [mod_id for mod_id in numeric if str(mod_id) not in projects]
    if missing:

        def fetch_one(mod_id: int) -> None:
            try:
                data = _curseforge_json(f"/mods/{mod_id}")
            except Exception:  # noqa: BLE001
                return
            _store(data.get("data") if isinstance(data, dict) else None, str(mod_id))

        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
            list(pool.map(fetch_one, missing))

    _cache_write(key, projects)
    return projects


def _attach_dependency_names(site: str, versions: list[dict]) -> None:
    """把依赖的项目 ID 换成名称 / 图标 / 简介，供界面显示前置模组卡片。"""
    ids: list[str] = []
    for version in versions:
        for dependency in version.get("dependencies") or []:
            dep_id = str(dependency.get("id") or "")
            if dep_id and dep_id not in ids:
                ids.append(dep_id)
    if not ids:
        for version in versions:
            version["dependencies"] = []
        return

    try:
        projects = (
            _resolve_curseforge_projects(ids)
            if site == "curseforge"
            else _resolve_modrinth_projects(ids)
        )
    except Exception:  # noqa: BLE001 - 解析失败时退回显示编号
        projects = {}

    order = {"required": 0, "optional": 1, "embedded": 2, "tool": 3, "incompatible": 4}
    for version in versions:
        resolved: list[dict] = []
        seen: set[str] = set()
        for dependency in version.get("dependencies") or []:
            dep_id = str(dependency.get("id") or "")
            if not dep_id or dep_id in seen:
                continue
            seen.add(dep_id)
            info = projects.get(dep_id) or {}
            resolved.append(
                {
                    "id": dep_id,
                    "name": info.get("name") or f"#{dep_id}",
                    "slug": info.get("slug") or "",
                    "icon": info.get("icon") or "",
                    "summary": info.get("summary") or "",
                    "downloads": int(info.get("downloads") or 0),
                    "source": site,
                    "type": str(dependency.get("type") or "required"),
                }
            )
        resolved.sort(key=lambda item: order.get(item["type"], 9))
        version["dependencies"] = resolved


def curseforge_file_info(pairs: list[tuple[int, int]]) -> dict:
    """批量查询 CurseForge 文件信息（真实文件名与下载地址）。

    整合包的 manifest 只给出 projectID / fileID，需要再查一次下载地址。
    返回 {"ok": True, "files": {"<projectID>:<fileID>": {url, file_name, size}}}。
    """
    wanted: dict[str, tuple[int, int]] = {}
    for project_id, file_id in pairs or []:
        try:
            pid = int(project_id)
            fid = int(file_id)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or fid <= 0:
            continue
        wanted.setdefault(f"{pid}:{fid}", (pid, fid))

    if not wanted:
        return {"ok": True, "files": {}}

    files: dict[str, dict] = {}

    def _store(item) -> None:
        if not isinstance(item, dict):
            return
        try:
            key = f"{int(item.get('modId') or 0)}:{int(item.get('id') or 0)}"
        except (TypeError, ValueError):
            return
        if key not in wanted or key in files:
            return
        files[key] = {
            "url": str(item.get("downloadUrl") or ""),
            "file_name": str(item.get("fileName") or ""),
            "size": int(item.get("fileLength") or 0),
        }

    ids = [fid for _pid, fid in wanted.values()]
    for start in range(0, len(ids), 200):
        try:
            data = _curseforge_post("/mods/files", {"fileIds": ids[start : start + 200]})
        except Exception:  # noqa: BLE001 - 镜像不一定支持批量接口
            data = {}
        for item in (data or {}).get("data") or []:
            _store(item)

    for key, (pid, fid) in wanted.items():
        if key in files:
            continue
        try:
            data = _curseforge_json(f"/mods/{pid}/files/{fid}")
        except Exception:  # noqa: BLE001 - 单个文件失败不影响整体
            continue
        _store((data or {}).get("data"))

    return {"ok": True, "files": files}


def fetch_versions(
    source: str,
    project_id: str,
    mc_version: str = "",
    loader: str = "",
    limit: int = 60,
    project_type: str = "mod",
) -> dict:
    """获取某个项目可下载的文件版本（新→旧）。

    project_type 用于决定是否解析前置依赖：只有模组需要，
    整合包 / 资源包 / 数据包直接展示文件即可。
    """
    site = str(source or "").strip().lower()
    if site not in ("curseforge", "modrinth"):
        return {"ok": False, "error": "请选择有效的模组来源"}

    content_type = _normalize_type(project_type)
    project = str(project_id or "").strip()
    if not project:
        return {"ok": False, "error": "缺少模组标识"}

    game_version = str(mc_version or "").strip()
    loader_id = str(loader or "").strip().lower()
    # 只有模组需要加载器条件，其他类型一律忽略
    if content_type != "mod" or loader_id not in CF_LOADER_TYPES:
        loader_id = ""
    size = max(1, min(int(limit or 60), 100))

    cache_key = ("versions", site, project, game_version, loader_id, size, content_type)
    cached = _cache_read(cache_key)
    if isinstance(cached, list):
        versions = list(cached)
    else:
        try:
            if site == "curseforge":
                versions = _versions_curseforge(project, game_version, loader_id, size)
            else:
                versions = _versions_modrinth(project, game_version, loader_id, size)
        except Exception as exc:  # noqa: BLE001 - 网络失败统一反馈
            label = SOURCE_LABELS.get(site, site)
            return {"ok": False, "error": f"{label} 版本列表获取失败：{exc}"}
        if versions:
            if content_type == "mod":
                _attach_dependency_names(site, versions)
            else:
                # 整合包 / 资源包 / 光影包 / 数据包没有“前置组件”的概念
                for version in versions:
                    version["dependencies"] = []
            _cache_write(cache_key, versions)

    if not versions:
        return {
            "ok": False,
            "error": "没有匹配的文件，试试其他游戏版本或加载器",
            "versions": [],
        }

    return {
        "ok": True,
        "source": site,
        "versions": versions,
        "recommended": versions[0]["id"],
    }


def download(
    url: str,
    dest: Path,
    on_progress=None,
    label: str = "",
    project_type: str = "mod",
) -> dict:
    """把文件下载到用户选择的路径。

    模组默认 .jar，整合包 / 资源包 / 数据包默认 .zip；
    已经是 .jar / .zip / .mrpack 的路径不再追加后缀。
    """
    link = str(url or "").strip()
    if not link:
        return {
            "ok": False,
            "error": "该文件不允许第三方下载，请到源站点手动获取",
        }

    target = Path(dest)
    known_suffixes = (".jar", ".zip", ".mrpack", ".mcpack", ".mcaddon")
    if target.suffix.lower() not in known_suffixes:
        fallback = ".jar" if _normalize_type(project_type) == "mod" else ".zip"
        target = target.with_name(target.name + fallback)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        download_file(link, target, on_progress=on_progress, label=label)
    except OSError as exc:
        return {"ok": False, "error": f"保存文件失败：{exc}"}
    except DownloadError as exc:
        return {"ok": False, "error": f"下载失败：{exc}"}

    try:
        size = target.stat().st_size
    except OSError:
        size = 0

    return {"ok": True, "path": str(target), "size": size}
