"""基岩版（Minecraft for Windows）管理：检测、启动、下载本体、安装。

走的是微软 Store 的匿名链路：

    displaycatalog（拿 WuCategoryId）
        → FE3 GetCookie（拿匿名 cookie）
        → FE3 SyncUpdates（拿 UpdateID + 文件摘要）
        → FE3 GetExtendedUpdateInfo2（拿带签名的 CDN 直链）

这几步都是微软未公开的接口，字段形状可能被随时调整，
所以所有失败都必须能落到「打开 Microsoft Store 安装」这条兜底路径上。
"""

from __future__ import annotations

import ctypes
import html
import json
import os
import platform
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

PRODUCT_ID = "9NBLGGH2JHXJ"  # Minecraft for Windows
PACKAGE_NAME = "Microsoft.MinecraftUWP"
PACKAGE_FAMILY = "Microsoft.MinecraftUWP_8wekyb3d8bbwe"
APP_USER_MODEL_ID = f"{PACKAGE_FAMILY}!App"

# 商店里分成两个商品的基岩版渠道：正式版与预览版（各自的包名与 PFN 都不同）
CHANNELS = {
    "release": {
        "product_id": PRODUCT_ID,
        "name": "Minecraft for Windows",
        "label": "正式版",
        "package": PACKAGE_NAME,
        "family": PACKAGE_FAMILY,
    },
    "preview": {
        "product_id": "9P5X4QVLC2XR",
        "name": "Minecraft Preview",
        "label": "预览版",
        "package": "Microsoft.MinecraftWindowsBeta",
        "family": "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe",
    },
}
DEFAULT_CHANNEL = "release"


def channel_meta(channel: str = DEFAULT_CHANNEL) -> dict:
    """按渠道名取元数据；未知渠道回落到正式版。"""
    key = str(channel or "").strip().lower()
    return CHANNELS.get(key) or CHANNELS[DEFAULT_CHANNEL]

CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products/{product}"
    "?market=US&languages=en-US,en&fieldsTemplate=Details"
)
FE3_BASE = "https://fe3cr.delivery.mp.microsoft.com/ClientWebService/client.asmx"
FE3_SECURED = FE3_BASE + "/secured"
FE3_SOAP_TARGET = "https://fe3.delivery.mp.microsoft.com/ClientWebService/client.asmx"
FE3_USER_AGENT = "Windows-Update-Agent/10.0.10011.16384 Client-Protocol/2.1"

STORE_PAGE = f"ms-windows-store://pdp/?PFN={PACKAGE_FAMILY}"

# WU 协议里必须带上的基础设施分类 ID，少一个就会静默返回空结果
BASELINE_UPDATE_IDS = (
    1, 2, 3, 10, 11, 17, 19, 2359974, 2359977, 5143990, 5169043, 5169044, 5169047,
    8788830, 8806526, 9125350, 9154769, 10809856, 23110993, 23110994, 23110995,
    23110996, 23110999, 23111000, 23111001, 23111002, 23111003, 23111004,
    24513870, 28880263, 30077688, 30486944, 59830006, 59830007, 59830008,
    60484010, 62450018, 62450019, 62450020, 98959022, 98959023, 98959024,
    98959025, 98959026, 105939029, 105995585, 106017178, 107825194, 117765322,
    129905029, 130040030, 130040031, 130040032, 130040033, 133399034, 138372035,
    138372036, 139536037, 139536038, 139536039, 139536040, 142045136, 158941041,
    158941042, 158941043, 158941044, 159776047, 160733048, 160733049, 160733050,
    160733051, 160733055, 160733056, 161870057, 161870058, 161870059,
)

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class BedrockError(RuntimeError):
    """基岩版操作失败。"""


# ---- 系统信息 -----------------------------------------------------
def os_version_string() -> str:
    """WU 协议里的 OSVersion，形如 10.0.22621.1。"""
    try:
        parts = platform.version().split(".")
        numbers = [int(item) for item in parts if item.isdigit()]
    except (ValueError, OSError):
        numbers = []
    while len(numbers) < 3:
        numbers.append(0)
    return ".".join(str(item) for item in numbers[:3] + [numbers[3] if len(numbers) > 3 else 0])


def is_supported() -> bool:
    """基岩版需要 Windows 10 1809（build 17763）及以上。"""
    try:
        return sys_build() >= 17763
    except OSError:
        return False


def sys_build() -> int:
    try:
        return int(platform.version().split(".")[2])
    except (IndexError, ValueError):
        return 0


def _device_attributes() -> str:
    os_version = os_version_string()
    return (
        "BranchReadinessLevel=CB;CurrentBranch=;OEMModel=Virtual Machine;FlightRing=Retail;"
        "AttrDataVer=21;SystemManufacturer=Microsoft Corporation;InstallLanguage=en-US;"
        f"OSUILocale=en-US;InstallationType=Client;FlightingBranchName=;FirmwareVersion=Unknown;"
        "SystemProductName=Virtual Machine;OSSkuId=48;FlightContent=Mainline;App=WU_STORE;"
        f"OEMName_Uncleaned=Microsoft Corporation;AppVer=0.0.0.0;OSArchitecture=AMD64;"
        f"SystemSKU=None;UpdateManagementGroup=2;IsFlightingEnabled=0;IsDeviceRetailDemo=0;"
        f"TelemetryLevel=3;OSVersion={os_version};DeviceFamily=Windows.Desktop;"
    )


# ---- 已安装检测 ---------------------------------------------------
def _run_powershell(script: str, timeout: int = 40) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BedrockError(f"无法调用 PowerShell：{exc}") from exc
    text = result.stdout.decode("utf-8", errors="ignore").strip()
    if not text:
        error = result.stderr.decode("utf-8", errors="ignore").strip()
        if error:
            raise BedrockError(error.splitlines()[0])
    return text


def installed_info(channel: str = DEFAULT_CHANNEL) -> dict | None:
    """查询某个渠道已安装的基岩版；未安装返回 None。"""
    if not is_supported():
        return None
    meta = channel_meta(channel)
    script = (
        f"$p = Get-AppxPackage -Name {meta['package']} | "
        "Select-Object -First 1 Name,Version,InstallLocation,PackageFullName,Status; "
        "if ($p) { $p | ConvertTo-Json -Compress }"
    )
    try:
        text = _run_powershell(script)
    except BedrockError:
        return None
    if not text or not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("Name"):
        return None
    return {
        "name": str(data.get("Name") or meta["package"]),
        "label": meta["label"],
        "channel": str(channel or DEFAULT_CHANNEL).strip().lower(),
        "version": str(data.get("Version") or ""),
        "location": str(data.get("InstallLocation") or ""),
        "full_name": str(data.get("PackageFullName") or ""),
        "status": _status_text(data.get("Status")),
    }


def _status_text(raw) -> str:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return "正常"
    mapping = {0: "正常", 1: "已修改", 2: "不可用", 4: "部分安装", 8: "已损坏"}
    return mapping.get(value, "正常")


# ---- 启动 / 商店 ---------------------------------------------------
def launch(channel: str = DEFAULT_CHANNEL) -> dict:
    """启动某个渠道已安装的基岩版。"""
    meta = channel_meta(channel)
    if installed_info(channel) is None:
        return {"ok": False, "error": f"尚未安装{meta['label']}"}
    aumid = f"{meta['family']}!App"
    try:
        os.startfile(f"shell:appsFolder\\{aumid}")  # noqa: S606
        return {"ok": True}
    except OSError:
        pass
    try:
        subprocess.Popen(["explorer.exe", f"shell:appsFolder\\{aumid}"])
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": f"启动失败：{exc}"}


def open_store(channel: str = DEFAULT_CHANNEL) -> dict:
    """打开 Microsoft Store 里对应渠道的页面。"""
    meta = channel_meta(channel)
    page = f"ms-windows-store://pdp/?PFN={meta['family']}"
    try:
        os.startfile(page)  # noqa: S606
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": f"无法打开商店：{exc}"}


def open_xbox_app() -> dict:
    """打开 Xbox 应用（基岩版账号在游戏内用 Xbox 登录）。"""
    try:
        os.startfile("xbox://")  # noqa: S606
        return {"ok": True}
    except OSError:
        pass
    try:
        os.startfile("ms-windows-store://pdp/?productid=9MV0B5HZVK9Z")  # noqa: S606
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": f"无法打开 Xbox 应用：{exc}"}


# ---- FE3 链路 -----------------------------------------------------
def _now() -> str:
    """WU 协议要求的时间格式：2026-10-25T12:34:56.789Z。"""
    current = datetime.now(timezone.utc)
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{current.microsecond // 1000:03d}Z"


def _soap_envelope(action: str, body: str, secured: bool = False) -> tuple[str, str]:
    url = FE3_SECURED if secured else FE3_BASE
    target = FE3_SOAP_TARGET + ("/secured" if secured else "")
    envelope = (
        '<s:Envelope xmlns:a="http://www.w3.org/2005/08/addressing"'
        ' xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        "<s:Header>"
        f'<a:Action s:mustUnderstand="1">{action}</a:Action>'
        f"<a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>"
        f'<a:To s:mustUnderstand="1">{target}</a:To>'
        '<o:Security s:mustUnderstand="1"'
        ' xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        '<Timestamp xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"<Created>{_now()}</Created><Expires>2044-08-02T20:09:03Z</Expires></Timestamp>"
        '<wuws:WindowsUpdateTicketsToken wsu:id="ClientMSA"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"'
        ' xmlns:wuws="http://schemas.microsoft.com/msus/2014/10/WindowsUpdateAuthorization">'
        "</wuws:WindowsUpdateTicketsToken></o:Security></s:Header>"
        f'<s:Body>{body}</s:Body></s:Envelope>'
    )
    return url, envelope


def _http_request(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 40,
    attempts: int = 3,
) -> bytes:
    """发起请求并读回全部内容。

    这里刻意用标准库 urllib 而不是 requests：fe3cr 端点下发的是不完整证书链，
    requests 用的 certifi 不会补中间证书，而 urllib 走 Windows 证书库可以正常校验。

    fe3cr 在国内网络下偶发连接超时（WinError 10060），所以网络层错误重试几次；
    HTTP 状态码错误不重试，直接报出来。
    """
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read()[:180].decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001 - 读错误体失败就忽略
                detail = ""
            raise BedrockError(f"微软服务返回 {exc.code}：{detail}".strip()) from exc
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(1.2 * attempt)
    reason = getattr(last_error, "reason", last_error)
    raise BedrockError(f"无法连接微软服务：{reason}") from last_error


def _post_soap(action: str, body: str, secured: bool = False, timeout: int = 40) -> str:
    url, envelope = _soap_envelope(action, body, secured)
    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8",
        "User-Agent": FE3_USER_AGENT,
    }
    payload = _http_request(url, envelope.encode("utf-8"), headers, timeout)
    return payload.decode("utf-8", errors="ignore")


def _xml_value(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return match.group(1).strip() if match else ""


def _category_id(product_id: str = PRODUCT_ID) -> str:
    raw = _http_request(CATALOG_URL.format(product=product_id), timeout=30)
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        fulfillment = data["Product"]["DisplaySkuAvailabilities"][0]["Sku"]["Properties"][
            "Packages"
        ][0]["FulfillmentData"]
        category = str(fulfillment.get("WuCategoryId") or "").strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise BedrockError(f"读取商店目录失败：{exc}") from exc
    if not category:
        raise BedrockError("该商品没有 Store 包分类，可能不以 MSIX 分发")
    return category


def _get_cookie() -> tuple[str, str]:
    body = (
        '<GetCookie xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        "<oldCookie><Expiration>2016-07-27T07:18:09Z</Expiration></oldCookie>"
        "<lastChange>2015-10-21T17:01:07.1472913Z</lastChange>"
        f"<currentTime>{_now()}</currentTime>"
        "<protocolVersion>2.50</protocolVersion></GetCookie>"
    )
    text = _post_soap(
        "http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetCookie", body
    )
    encrypted = _xml_value(text, "EncryptedData")
    expiration = _xml_value(text, "Expiration") or "2044-08-02T20:09:03Z"
    if not encrypted:
        raise BedrockError("微软更新服务未返回会话凭据")
    return encrypted, expiration


def _sync_updates(cookie: str, expiration: str, category: str) -> str:
    ids = "".join(f"<int>{value}</int>" for value in BASELINE_UPDATE_IDS)
    body = (
        '<SyncUpdates xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        f"<cookie><Expiration>{expiration}</Expiration>"
        f"<EncryptedData>{cookie}</EncryptedData></cookie>"
        "<parameters><ExpressQuery>false</ExpressQuery>"
        f"<InstalledNonLeafUpdateIDs>{ids}</InstalledNonLeafUpdateIDs>"
        "<OtherCachedUpdateIDs></OtherCachedUpdateIDs>"
        "<SkipSoftwareSync>false</SkipSoftwareSync>"
        "<NeedTwoGroupOutOfScopeUpdates>true</NeedTwoGroupOutOfScopeUpdates>"
        f"<FilterAppCategoryIds><CategoryIdentifier><Id>{category}</Id>"
        "</CategoryIdentifier></FilterAppCategoryIds>"
        "<TreatAppCategoryIdsAsInstalled>true</TreatAppCategoryIdsAsInstalled>"
        "<AlsoPerformRegularSync>false</AlsoPerformRegularSync>"
        "<ComputerSpec /><ExtendedUpdateInfoParameters><XmlUpdateFragmentTypes>"
        "<XmlUpdateFragmentType>Extended</XmlUpdateFragmentType>"
        "<XmlUpdateFragmentType>Published</XmlUpdateFragmentType>"
        "<XmlUpdateFragmentType>Core</XmlUpdateFragmentType>"
        "</XmlUpdateFragmentTypes><Locales><string>en-US</string><string>en</string></Locales>"
        "</ExtendedUpdateInfoParameters><ClientPreferredLanguages><string>en-US</string>"
        "</ClientPreferredLanguages><ProductsParameters><SyncCurrentVersionOnly>false</SyncCurrentVersionOnly>"
        f"<DeviceAttributes>{_device_attributes()}</DeviceAttributes>"
        "<CallerAttributes>Interactive=1;IsSeeker=0;</CallerAttributes><Products />"
        "</ProductsParameters></parameters></SyncUpdates>"
    )
    return _post_soap(
        "http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/SyncUpdates", body
    )


def _parse_sync(text: str) -> tuple[list[dict], tuple[str, str] | None]:
    """从 SyncUpdates 里解析出「UpdateID + 修订号 + 文件列表」。"""
    core_map: dict[str, str] = {}
    extended_map: dict[str, str] = {}

    for block in re.findall(r"<UpdateInfo>(.*?)</UpdateInfo>", text, re.S):
        update_id = _xml_value(block, "ID")
        xml = _xml_value(block, "Xml")
        if update_id and xml:
            core_map[update_id] = xml

    extended_block = re.search(r"<ExtendedUpdateInfo>(.*?)</ExtendedUpdateInfo>", text, re.S)
    if extended_block:
        for block in re.findall(r"<Update>(.*?)</Update>", extended_block.group(1), re.S):
            update_id = _xml_value(block, "ID")
            xml = _xml_value(block, "Xml")
            if update_id and xml:
                extended_map[update_id] = xml

    new_cookie = None
    new_cookie_block = re.search(r"<NewCookie>(.*?)</NewCookie>", text, re.S)
    if new_cookie_block:
        encrypted = _xml_value(new_cookie_block.group(1), "EncryptedData")
        expiration = _xml_value(new_cookie_block.group(1), "Expiration")
        if encrypted:
            new_cookie = (encrypted, expiration or "2044-08-02T20:09:03Z")

    entries: list[dict] = []
    for session_id, core_raw in core_map.items():
        core = html.unescape(core_raw)
        if "SecuredFragment" not in core:
            continue
        extended_raw = extended_map.get(session_id)
        if not extended_raw:
            continue
        extended = html.unescape(extended_raw)

        # 真正用于后续查询的是 Core 片段里第一个 UpdateIdentity 的属性
        identity_match = re.search(r"<UpdateIdentity\b([^>]*)", core)
        identity = _attributes(identity_match.group(1)) if identity_match else {}
        internal_id = (identity.get("UpdateID") or "").strip()
        revision = (identity.get("RevisionNumber") or "1").strip()
        if not internal_id:
            continue

        package_name = ""
        package_match = re.search(r'PackageIdentityName="([^"]*)"', extended)
        if package_match:
            package_name = package_match.group(1)

        files: list[dict] = []
        for attributes_text in re.findall(r"<File\b([^>]*)>", extended):
            attributes = _attributes(attributes_text)
            name = attributes.get("FileName", "")
            digest = attributes.get("Digest", "")
            if not name or not digest:
                continue
            if name.lower().endswith(".cab"):
                continue
            files.append(
                {
                    "name": name,
                    "digest": digest,
                    "size": _safe_int(attributes.get("Size", "0")),
                    "identifier": attributes.get("InstallerSpecificIdentifier", "")
                    or package_name,
                    "patching": attributes.get("PatchingType", ""),
                }
            )
        if files:
            entries.append({"update_id": internal_id, "revision": revision, "files": files})
    return entries, new_cookie


def _attributes(text: str) -> dict[str, str]:
    return {key: value for key, value in re.findall(r'([\w:]+)\s*=\s*"([^"]*)"', text)}


def _safe_int(text: str) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return 0


def _file_urls(cookie: str, expiration: str, entry: dict) -> dict[str, str]:
    body = (
        '<GetExtendedUpdateInfo2 xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        f"<cookie><Expiration>{expiration}</Expiration><EncryptedData>{cookie}</EncryptedData></cookie>"
        "<updateIDs><UpdateIdentity>"
        f"<UpdateID>{entry['update_id']}</UpdateID><RevisionNumber>{entry['revision']}</RevisionNumber>"
        "</UpdateIdentity></updateIDs><infoTypes>"
        "<XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>"
        "<XmlUpdateFragmentType>FileDecryption</XmlUpdateFragmentType>"
        "</infoTypes>"
        f"<deviceAttributes>{_device_attributes()}</deviceAttributes>"
        "</GetExtendedUpdateInfo2>"
    )
    text = _post_soap(
        "http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetExtendedUpdateInfo2",
        body,
        secured=True,
    )
    mapping: dict[str, str] = {}
    for block in re.findall(r"<FileLocation>(.*?)</FileLocation>", text, re.S):
        digest = _xml_value(block, "FileDigest")
        # 直链带签名参数，XML 里的 & 会被转义成 &amp;，不还原就会 403
        url = html.unescape(_xml_value(block, "Url"))
        if digest and url:
            mapping[digest] = url
    return mapping


def _pick_arch(name: str) -> str:
    """从包标识里取架构；arm64 必须排在 arm 之前。"""
    match = re.search(r"_(arm64|arm|x64|x86|neutral)__", name, re.I)
    return match.group(1).lower() if match else "neutral"


def _collect_packages(entries: list[dict], package: str) -> list[dict]:
    """整理 SyncUpdates 里的待安装包：按摘要去重、标出主包，暂不含下载直链。"""
    marker = package.lower()
    seen: set[str] = set()
    packages: list[dict] = []
    for entry in entries:
        for item in entry["files"]:
            digest = item["digest"]
            if not digest or digest in seen:
                continue
            seen.add(digest)

            identity = item["identifier"] or "package"
            arch = _pick_arch(identity) or _pick_arch(item["name"])
            suffix = Path(item["name"]).suffix or ".appx"
            packages.append(
                {
                    "name": f"{identity}{suffix}",
                    "digest": digest,
                    "size": item["size"],
                    "arch": arch,
                    "identifier": identity,
                    "main": marker in identity.lower(),
                    "update": entry,
                }
            )
    return packages


def _pick_local(items: list[dict]) -> list[dict]:
    """微软会同时下发多个架构的条目（含更旧的 arm 版本），按本机架构筛一遍。"""
    wanted = [item for item in items if item["arch"] in ("x64", "neutral")]
    return wanted or list(items)


def _version_of(items: list[dict]) -> str:
    """从包标识里取版本号，优先取主包。"""
    mains = [item for item in items if item["main"]]
    version = _version_from_identity(mains[0]["identifier"]) if mains else ""
    if not version and items:
        biggest = max(items, key=lambda item: item.get("size") or 0)
        version = _version_from_identity(biggest["identifier"])
    return version


def version_brief(channel: str = DEFAULT_CHANNEL, progress=None) -> dict:
    """只查某个渠道最新版的版本号与体积（不解析下载直链），用于版本列表。"""
    meta = channel_meta(channel)
    reporter = progress or (lambda _text: None)

    reporter("正在查询商店目录…")
    category = _category_id(meta["product_id"])

    reporter("正在获取版本清单…")
    cookie, expiration = _get_cookie()
    sync_text = _sync_updates(cookie, expiration, category)
    entries, _ = _parse_sync(sync_text)
    if not entries:
        raise BedrockError("未能从微软更新服务获取到包信息")

    packages = _pick_local(_collect_packages(entries, meta["package"]))
    if not packages:
        raise BedrockError("未能解析出任何安装包")
    return {
        "channel": str(channel or DEFAULT_CHANNEL).strip().lower(),
        "label": meta["label"],
        "name": meta["name"],
        "package": meta["package"],
        "version": _version_of(packages),
        "size": sum(int(item.get("size") or 0) for item in packages),
        "files": len(packages),
    }


def latest_files(progress=None, channel: str = DEFAULT_CHANNEL) -> dict:
    """查询某个渠道最新版的安装包列表（含下载直链，不下载）。"""
    meta = channel_meta(channel)
    reporter = progress or (lambda _text: None)

    reporter("正在查询商店目录…")
    category = _category_id(meta["product_id"])

    reporter("正在建立更新会话…")
    cookie, expiration = _get_cookie()

    reporter("正在获取版本清单…")
    sync_text = _sync_updates(cookie, expiration, category)
    entries, new_cookie = _parse_sync(sync_text)
    if not entries:
        raise BedrockError("未能从微软更新服务获取到包信息")
    if new_cookie:
        cookie, expiration = new_cookie

    packages = _collect_packages(entries, meta["package"])
    if not packages:
        raise BedrockError("未能解析出任何安装包")

    reporter("正在解析下载地址…")
    url_cache: dict[str, dict[str, str]] = {}
    results: list[dict] = []
    for item in packages:
        entry = item.pop("update")
        key = entry["update_id"]
        if key not in url_cache:
            url_cache[key] = _file_urls(cookie, expiration, entry)
        url = url_cache[key].get(item["digest"])
        if not url:
            continue
        item["url"] = url
        results.append(item)

    if not results:
        raise BedrockError("未能解析出任何下载地址")

    wanted = _pick_local(results)
    version = _version_of(wanted)

    # 依赖包先装、主包最后装
    wanted.sort(key=lambda item: 1 if item["main"] else 0)
    return {"version": version, "files": wanted, "all_files": results}


def _version_from_identity(identity: str) -> str:
    match = re.search(r"_(\d+\.\d+\.\d+(?:\.\d+)?)_", identity or "")
    return match.group(1) if match else ""


def package_version(package_name: str) -> str:
    """从安装包文件名里取完整版本号（如 1.21.7301.0）。"""
    return _version_from_identity(str(package_name or ""))


# ---- 下载与安装 ---------------------------------------------------
DOWNLOAD_CHUNK = 1024 * 1024
DOWNLOAD_ATTEMPTS = 4


def human_speed(value: float) -> str:
    return f"{human_size(int(value or 0))}/s"


def human_eta(seconds: float) -> str:
    total = int(seconds or 0)
    if total <= 0:
        return "未知"
    if total >= 3600:
        return f"{total // 3600}小时{total % 3600 // 60}分"
    if total >= 60:
        return f"{total // 60}分{total % 60}秒"
    return f"{total}秒"


def _range_total(response, already: int) -> int:
    """取整个文件的大小（206 时从 Content-Range 里拿）。"""
    match = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range") or "")
    if match:
        return _safe_int(match.group(1))
    length = _safe_int(response.headers.get("Content-Length"))
    return already + length if length else 0


def _short_name(name: str) -> str:
    """把长包名压成「1.21.7301.0_x64」这种短标签，避免进度文本被截断。"""
    text = str(name or "")
    text = re.sub(r"^Microsoft\.Minecraft(?:UWP|WindowsBeta)_", "", text)
    text = re.sub(r"__8wekyb3d8bbwe\..*$", "", text)
    return text or str(name or "")


def _download_text(name: str, done: int, total: int, speed: float) -> str:
    parts = []
    if speed > 0:
        parts.append(human_speed(speed))
    if total:
        parts.append(f"{human_size(done)}/{human_size(total)}")
    if speed > 0 and total and done < total:
        parts.append("剩余 " + human_eta((total - done) / speed))
    head = f"正在下载 {_short_name(name)}"
    return head + (" · " + " · ".join(parts) if parts else "")


def _fetch_file(
    url: str,
    destination: Path,
    progress=None,
    cancel_check=None,
    attempts: int = DOWNLOAD_ATTEMPTS,
    timeout: int = 60,
) -> Path:
    """单文件下载：断点续传 + 失败自动重试 + 实时速度/剩余时间。"""
    reporter = progress or (lambda _done, _total, _text: None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")

    last_error: Exception | None = None
    done = 0
    total = 0
    for attempt in range(1, attempts + 1):
        already = temp.stat().st_size if temp.is_file() else 0
        headers = {
            "User-Agent": "Microsoft-Delivery-Optimization/10.0",
            "Accept-Encoding": "identity",
        }
        if already:
            headers["Range"] = f"bytes={already}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if already and response.status != 206:
                    # 服务端没按 Range 返回，只能从头下
                    already = 0
                total = _range_total(response, already)
                with temp.open("ab" if already else "wb") as handle:
                    done = already
                    started = time.monotonic()
                    while True:
                        if cancel_check and cancel_check():
                            raise BedrockError("已取消下载")
                        chunk = response.read(DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        elapsed = max(time.monotonic() - started, 0.001)
                        reporter(
                            done,
                            total or done,
                            _download_text(
                                destination.name, done, total, (done - already) / elapsed
                            ),
                        )
            if total and done < total:
                raise BedrockError(f"连接提前结束（{human_size(done)}/{human_size(total)}）")
            temp.replace(destination)
            return destination
        except BedrockError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and temp.is_file():
                # 已经下完（或超出范围），删掉残片重来
                temp.unlink(missing_ok=True)
                continue
            last_error = exc
            if attempt >= attempts:
                break
            reporter(done, total or done, f"服务器返回 {exc.code}，正在重试（{attempt}/{attempts}）…")
            time.sleep(min(1.5 * attempt, 5.0))
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            last_error = exc
            if attempt >= attempts:
                break
            reporter(done, total or done, f"连接中断，正在重试（{attempt}/{attempts}）…")
            time.sleep(min(1.5 * attempt, 5.0))

    raise BedrockError(f"下载失败：{last_error}") from last_error


def download_packages(
    payload: dict,
    target_dir: Path,
    progress=None,
    cancel_check=None,
) -> list[Path]:
    """下载安装包，返回本地文件列表（共用断点续传下载器）。"""
    reporter = progress or (lambda _done, _total, _text: None)
    target_dir.mkdir(parents=True, exist_ok=True)

    files = payload.get("files") or []
    declared_total = sum(int(item.get("size") or 0) for item in files)
    finished = 0
    paths: list[Path] = []

    for item in files:
        if cancel_check and cancel_check():
            raise BedrockError("已取消下载")

        destination = target_dir / safe_filename(item["name"])
        declared = int(item.get("size") or 0)
        if declared and destination.is_file() and destination.stat().st_size == declared:
            # 上一次已经下完整了，直接复用
            finished += declared
            paths.append(destination)
            continue

        base = finished

        def file_progress(done: int, total: int, text: str, _base: int = base) -> None:
            overall_total = declared_total or (_base + total)
            reporter(_base + done, overall_total, text)

        paths.append(
            _fetch_file(
                item["url"],
                destination,
                progress=file_progress,
                cancel_check=cancel_check,
            )
        )
        try:
            finished += destination.stat().st_size
        except OSError:
            finished += declared

    return paths


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    return cleaned or "package.bin"


def install_packages(
    paths: list[Path], progress=None, channel: str = DEFAULT_CHANNEL
) -> dict:
    """用 Add-AppxPackage 安装；依赖包先装、主包最后装。"""
    reporter = progress or (lambda _text: None)
    marker = channel_meta(channel)["package"].lower()
    ordered = sorted(paths, key=lambda item: 1 if marker in item.name.lower() else 0)
    for path in ordered:
        reporter(f"正在安装 {path.name}…")
        script = (
            f"$ErrorActionPreference='Stop'; "
            f"Add-AppxPackage -Path '{str(path).replace(chr(39), chr(39) * 2)}' "
            "-ForceApplicationShutdown -ErrorAction Stop"
        )
        try:
            _run_powershell(script, timeout=900)
        except BedrockError as exc:
            return {"ok": False, "error": f"安装 {path.name} 失败：{exc}"}
    reporter("安装完成")
    return {"ok": True}


def install_local(path: str, progress=None) -> dict:
    """安装本地已有的 .msix / .msixbundle / .appx。"""
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "error": "文件不存在"}
    if target.suffix.lower() not in (".msix", ".msixbundle", ".appx", ".appxbundle"):
        return {"ok": False, "error": "请选择 .msix / .msixbundle / .appx 文件"}
    return install_packages([target], progress)


def human_size(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def hide_console() -> None:
    """打包成无控制台程序时，避免 PowerShell 弹黑框（预留）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.FreeConsole()
    except Exception:  # noqa: BLE001 - 没有控制台时忽略
        pass


# ---- 历史版本（third-party 版本库 mcappx.com） ---------------------------
MIRROR_URL = "https://data.mcappx.com/v2/bedrock.json"
MIRROR_SOURCE = "mcappx.com"
MIRROR_TTL = 1800

_MIRROR_CACHE: dict = {"time": 0.0, "versions": None, "updated": ""}


def _version_key(version: str) -> tuple:
    return tuple(_safe_int(chunk) for chunk in str(version or "").split("."))


def mirror_versions(force: bool = False) -> dict:
    """读取第三方版本库里的全部基岩版版本（UWP + GDK，含 26.x 等新版本号体系）。

    版本库本身只是一份目录：UWP 给 UpdateID（再由 FE3 换直链），GDK 直接给 msixvc 链接。
    """
    cached = _MIRROR_CACHE.get("versions")
    if cached is not None and not force and time.time() - _MIRROR_CACHE["time"] < MIRROR_TTL:
        return {"versions": cached, "updated": _MIRROR_CACHE.get("updated", "")}

    raw = _http_request(
        MIRROR_URL,
        headers={"User-Agent": FE3_USER_AGENT, "Accept": "application/json"},
        timeout=45,
    )
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    except ValueError as exc:
        raise BedrockError(f"版本库数据格式异常：{exc}") from exc

    table = data.get("From_mcappx.com") or {}
    collected: list[dict] = []
    for version, meta in table.items():
        if not isinstance(meta, dict):
            continue
        build_type = str(meta.get("BuildType") or "").upper()
        if build_type not in ("UWP", "GDK"):
            continue

        variations = meta.get("Variations") or []
        variation = None
        for arch in ("x64", "x86", "arm64", "arm"):
            variation = next(
                (item for item in variations if str(item.get("Arch") or "").lower() == arch),
                None,
            )
            if variation is not None:
                break
        if variation is None:
            continue

        status = _safe_int(variation.get("ArchivalStatus"))
        metadata = [str(item) for item in (variation.get("MetaData") or []) if item]
        first = metadata[0] if metadata else ""
        update_id = first if build_type == "UWP" else ""
        direct_url = first if build_type == "GDK" else ""
        # ArchivalStatus：3 已收录且有官方记录、2 已收录但无记录（这两类可下载）；
        # 1 未发布/无法证明发布过、0 已丢失，都拿不到包
        downloadable = status in (2, 3) and bool(update_id or direct_url)

        collected.append(
            {
                "version": str(version),
                "release_type": str(meta.get("Type") or ""),
                "build_type": build_type,
                "package_id": str(meta.get("ID") or ""),
                "date": str(meta.get("Date") or ""),
                "arch": str(variation.get("Arch") or "").lower(),
                "status": status,
                "update_id": update_id,
                "url": direct_url,
                "available": downloadable,
                # 只有 UWP 的 appx 能用 Add-AppxPackage 安装，GDK 的 msixvc 只能下载
                "installable": build_type == "UWP" and downloadable,
            }
        )

    collected.sort(key=lambda item: _version_key(item["version"]), reverse=True)
    updated = str(data.get("CreationTime") or "")
    _MIRROR_CACHE.update({"time": time.time(), "versions": collected, "updated": updated})
    return {"versions": collected, "updated": updated}


def resolve_update_url(update_id: str, revision: int = 1) -> dict:
    """用 UpdateID 向微软更新服务换取签名直链（历史版本同样走这条链路）。"""
    target = str(update_id or "").strip()
    if not target:
        raise BedrockError("该版本没有可用的更新标识")

    body = (
        '<GetExtendedUpdateInfo2 xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        "<cookie></cookie><updateIDs><UpdateIdentity>"
        f"<UpdateID>{target}</UpdateID><RevisionNumber>{max(1, _safe_int(revision))}</RevisionNumber>"
        "</UpdateIdentity></updateIDs><infoTypes>"
        "<XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>"
        "</infoTypes>"
        "<deviceAttributes>FlightRing=RP;</deviceAttributes>"
        "</GetExtendedUpdateInfo2>"
    )
    text = _post_soap(
        "http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetExtendedUpdateInfo2",
        body,
        secured=True,
    )
    urls = [html.unescape(value) for value in re.findall(r"<Url>(.*?)</Url>", text, re.S)]
    # blockmap 的地址长度固定是 99，剔除后剩下的才是安装包本体
    real = [value for value in urls if len(value) != 99]
    if not real:
        raise BedrockError("微软服务没有返回下载地址，该版本的更新记录可能已失效")
    return {"url": real[0]}


def probe_package(url: str) -> dict:
    """HEAD 一下直链，拿真实文件名与体积。"""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": FE3_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            disposition = response.headers.get("Content-Disposition") or ""
            match = re.search(r'filename="?([^";]+)"?', disposition)
            return {
                "filename": match.group(1).strip() if match else "",
                "size": _safe_int(response.headers.get("Content-Length")),
            }
    except (urllib.error.URLError, OSError) as exc:
        return {"filename": "", "size": 0, "error": str(exc)}


def download_update(
    url: str, target_dir: Path, filename: str = "", progress=None, cancel_check=None
) -> Path:
    """把签名直链下载到目标目录，返回落地文件（断点续传 + 重试）。"""
    name = safe_filename(filename) if filename else f"package{int(time.time())}.appx"
    return _fetch_file(
        url,
        target_dir / name,
        progress=progress,
        cancel_check=cancel_check,
    )


# Xbox CDN 支持这些域名，内容相同但各地区速度不同（.cn 在中国大陆更快）
GDK_CDN_HOSTS = (
    "assets1.xboxlive.cn",
    "d1.xboxlive.cn",
    "assets2.xboxlive.cn",
    "d2.xboxlive.cn",
    "assets1.xboxlive.com",
    "d1.xboxlive.com",
    "assets2.xboxlive.com",
    "d2.xboxlive.com",
)


def prefer_cdn_host(url: str) -> str:
    """把 Xbox CDN 链接换成本机更快的节点（默认国内 .cn 优先）。"""
    parsed = urllib.parse.urlsplit(str(url or ""))
    host = parsed.netloc.lower()
    if not host or not host.startswith(("assets", "d1.", "d2.")):
        return str(url or "")
    if host in GDK_CDN_HOSTS[:4]:
        return str(url or "")
    return urllib.parse.urlunsplit(
        (parsed.scheme or "http", GDK_CDN_HOSTS[0], parsed.path, parsed.query, parsed.fragment)
    )


def gdk_filename(url: str, version: str = "") -> str:
    """从 msixvc 链接里取文件名，取不到就用版本号拼一个。"""
    name = urllib.parse.unquote(Path(urllib.parse.urlsplit(str(url or "")).path).name)
    if name.lower().endswith(".msixvc"):
        return name
    ident = str(version or "").replace(".", "")
    return f"Microsoft.MinecraftUWP_{ident}_x64__8wekyb3d8bbwe.msixvc"


def download_msixvc(
    url: str, target_dir: Path, version: str = "", progress=None, cancel_check=None
) -> Path:
    """下载 GDK 的 msixvc 包（只下载；安装必须交给商店的部署服务）。"""
    name = safe_filename(gdk_filename(url, version))
    candidates: list[str] = []
    for candidate in (prefer_cdn_host(url), str(url or "")):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return _fetch_file(
                candidate,
                target_dir / name,
                progress=progress,
                cancel_check=cancel_check,
            )
        except BedrockError as exc:
            if "已取消" in str(exc) or (cancel_check and cancel_check()):
                raise
            last_error = exc
    raise BedrockError(f"下载失败：{last_error}")


def uninstall(channel: str = DEFAULT_CHANNEL, progress=None) -> dict:
    """卸载某个渠道已安装的包（降级安装前需要）。"""
    reporter = progress or (lambda _text: None)
    info = installed_info(channel)
    if info is None:
        return {"ok": True, "removed": False}

    full_name = info.get("full_name") or channel_meta(channel)["package"]
    reporter(f"正在卸载 {full_name}…")
    script = (
        f"$ErrorActionPreference='Stop'; "
        f"Remove-AppxPackage -Package '{full_name.replace(chr(39), chr(39) * 2)}' "
        "-ErrorAction Stop"
    )
    try:
        _run_powershell(script, timeout=900)
    except BedrockError as exc:
        return {"ok": False, "error": f"卸载失败：{exc}"}
    return {"ok": True, "removed": True}


def package_channel(package_name: str) -> str:
    """按安装包文件名判断它属于哪个渠道。"""
    name = str(package_name or "").lower()
    if "minecraftwindowsbeta" in name:
        return "preview"
    return DEFAULT_CHANNEL
