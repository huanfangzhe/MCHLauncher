"""微软正版登录：Microsoft 账户 → Xbox Live → Minecraft Services。

采用「设备码（device code）」流程：程序先向微软申请一组一次性的 user_code
与验证网址，用户在任意浏览器里输入该码完成授权；程序只轮询令牌接口取结果，
因此不需要内嵌浏览器、本地回调端口或客户端密钥。

默认 client_id 为微软官方启动器的公共应用 ID，也可以通过环境变量
MCHL_MS_CLIENT_ID 换成自己在 Azure 注册的公共客户端应用（需允许设备码流程）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from runtime_utils import SSL_CONTEXT, USER_AGENT

DEFAULT_CLIENT_ID = "00000000402b5328"
SCOPE = "XboxLive.signin offline_access"
TIMEOUT = 30
# 令牌剩余有效期不足该值就视为过期，提前续期避免启动游戏时才失败
TOKEN_MARGIN = 300

# 官方启动器应用走 Live Connect 端点，自建 Azure 应用走 Microsoft 身份平台
LIVE_DEVICE_CODE_URL = "https://login.live.com/oauth20_connect.srf"
LIVE_TOKEN_URL = "https://login.live.com/oauth20_token.srf"
AAD_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
AAD_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
SKIN_UPLOAD_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
SKIN_RESET_URL = "https://api.minecraftservices.com/minecraft/profile/skins/active"

# XSTS 拒绝登录时的错误码，含义来自微软文档
XSTS_ERRORS = {
    2148916227: "该 Microsoft 账户已被 Xbox Live 封禁",
    2148916233: "该 Microsoft 账户还没有 Xbox 档案，请先在 xbox.com 创建",
    2148916235: "所在国家/地区不支持 Xbox Live",
    2148916236: "该账户需要进行成人身份验证",
    2148916237: "该账户需要进行成人身份验证",
    2148916238: "未成年账户需要先加入 Microsoft 家庭组",
}


class AuthError(RuntimeError):
    """登录流程失败，异常信息可直接展示给用户。"""


def client_id() -> str:
    """当前使用的 OAuth 客户端 ID。"""
    return (os.environ.get("MCHL_MS_CLIENT_ID") or DEFAULT_CLIENT_ID).strip()


def _endpoints(cid: str) -> tuple[str, str]:
    """返回 (设备码地址, 令牌地址)。"""
    if cid == DEFAULT_CLIENT_ID:
        return LIVE_DEVICE_CODE_URL, LIVE_TOKEN_URL
    return AAD_DEVICE_CODE_URL, AAD_TOKEN_URL


def _send(
    url: str,
    form: dict | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    """发送请求，返回 (状态码, JSON 字典)；网络层失败抛 AuthError。"""
    body: bytes | None = None
    final_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        final_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    final_headers.update(headers or {})

    request = urllib.request.Request(url, data=body, headers=final_headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=SSL_CONTEXT) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise AuthError(f"无法连接登录服务：{exc.reason}") from None
    except OSError as exc:
        raise AuthError(f"网络请求失败：{exc}") from None

    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
        if status >= 400:
            raise AuthError(f"登录服务返回异常（HTTP {status}）") from None
    if not isinstance(data, dict):
        data = {}
    return status, data


def _http_error(status: int, data: dict, fallback: str) -> str:
    """把接口返回的错误整理成一句中文提示。"""
    xerr = data.get("XErr")
    if isinstance(xerr, int) and xerr in XSTS_ERRORS:
        return XSTS_ERRORS[xerr]
    for key in ("error_description", "Message", "message", "error"):
        value = str(data.get(key) or "").strip()
        if value:
            return value.splitlines()[0][:200]
    return f"{fallback}（HTTP {status}）"


def request_device_code(cid: str | None = None) -> dict:
    """申请一次登录会话，返回 user_code / 验证网址等。"""
    cid = (cid or client_id()).strip()
    device_url, _ = _endpoints(cid)
    form = {"client_id": cid, "scope": SCOPE}
    if device_url == LIVE_DEVICE_CODE_URL:
        # Live Connect 的设备码端点要求显式声明 response_type
        form["response_type"] = "device_code"
    status, data = _send(device_url, form=form)
    if status >= 400 or not data.get("user_code"):
        raise AuthError(_http_error(status, data, "无法获取登录码"))

    try:
        interval = int(data.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5

    return {
        "client_id": cid,
        "device_code": str(data.get("device_code") or ""),
        "user_code": str(data.get("user_code") or ""),
        "verification_uri": str(
            data.get("verification_uri") or data.get("verification_url") or "https://www.microsoft.com/link"
        ),
        "expires_in": int(data.get("expires_in") or 900),
        "interval": max(2, interval),
        "message": str(data.get("message") or ""),
    }


def poll_device_token(device_code: str, cid: str | None = None) -> dict:
    """轮询一次登录结果。

    返回 {"status": "ok", "token": {...}} 或 {"status": "pending"|"slow_down"}；
    用户取消 / 登录码过期等终端错误抛 AuthError。
    """
    cid = (cid or client_id()).strip()
    _, token_url = _endpoints(cid)
    form = {
        "client_id": cid,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "scope": SCOPE,
    }
    status, data = _send(token_url, form=form)
    if status < 400 and data.get("access_token"):
        return {"status": "ok", "token": data}

    error = str(data.get("error") or "").strip()
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "slow_down":
        return {"status": "slow_down"}
    if error in ("expired_token", "code_expired"):
        raise AuthError("登录码已过期，请重新登录")
    if error in ("authorization_declined", "access_denied", "consent_required"):
        raise AuthError("已取消授权")
    if error == "bad_verification_code":
        raise AuthError("登录码已失效，请重新登录")
    raise AuthError(_http_error(status, data, "微软登录失败"))


def refresh_ms_token(refresh_token: str, cid: str | None = None) -> dict:
    """用 refresh token 换取新的微软令牌。"""
    if not refresh_token:
        raise AuthError("登录状态已失效，请重新登录")
    cid = (cid or client_id()).strip()
    _, token_url = _endpoints(cid)
    form = {
        "client_id": cid,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if cid != DEFAULT_CLIENT_ID:
        form["scope"] = SCOPE
    status, data = _send(token_url, form=form)
    if status >= 400 or not data.get("access_token"):
        error = str(data.get("error") or "")
        if error in ("invalid_grant", "invalid_request", "expired_token"):
            raise AuthError("登录状态已过期，请重新登录")
        raise AuthError(_http_error(status, data, "刷新登录状态失败"))
    return data


def authenticate_xbox(ms_access_token: str) -> dict:
    """用微软令牌换取 Xbox Live 令牌。"""
    payload = {
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": f"d={ms_access_token}",
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }
    status, data = _send(
        XBL_AUTH_URL, payload=payload, headers={"x-xbl-contract-version": "1"}
    )
    token = str(data.get("Token") or "")
    if status >= 400 or not token:
        raise AuthError(_http_error(status, data, "Xbox Live 认证失败"))

    claims = (data.get("DisplayClaims") or {}).get("xui") or [{}]
    first = claims[0] if isinstance(claims, list) and claims else {}
    return {
        "token": token,
        "xuid": str(first.get("xid") or ""),
        "user_hash": str(first.get("uhs") or ""),
    }


def authorize_xsts(xbl_token: str) -> dict:
    """用 Xbox Live 令牌换取决赛授权（XSTS）。"""
    payload = {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    }
    status, data = _send(
        XSTS_AUTH_URL, payload=payload, headers={"x-xbl-contract-version": "1"}
    )
    token = str(data.get("Token") or "")
    if status >= 400 or not token:
        raise AuthError(_http_error(status, data, "Xbox Live 授权失败"))

    claims = (data.get("DisplayClaims") or {}).get("xui") or [{}]
    first = claims[0] if isinstance(claims, list) and claims else {}
    return {
        "token": token,
        "user_hash": str(first.get("uhs") or ""),
        "xuid": str(first.get("xid") or ""),
    }


def login_minecraft(user_hash: str, xsts_token: str) -> dict:
    """用 XSTS 令牌换取 Minecraft 访问令牌。"""
    payload = {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"}
    status, data = _send(MC_LOGIN_URL, payload=payload)
    token = str(data.get("access_token") or "")
    if status >= 400 or not token:
        raise AuthError(_http_error(status, data, "Minecraft 认证失败"))
    return {"access_token": token, "expires_in": int(data.get("expires_in") or 0)}


def fetch_profile(mc_access_token: str) -> dict:
    """读取正版档案（角色名与 UUID），同时确认账号拥有 Java 版。"""
    status, data = _send(
        MC_PROFILE_URL,
        headers={"Authorization": f"Bearer {mc_access_token}"},
    )
    if status == 401:
        raise AuthError("该账号没有《我的世界》Java 版，或登录状态已失效")
    if status >= 400:
        raise AuthError(_http_error(status, data, "读取游戏档案失败"))
    name = str(data.get("name") or "")
    if not name:
        raise AuthError("该账号还没有创建 Minecraft 角色名，请先在官网创建")

    # 皮肤地址带哈希，变化后可以用来判断是否需要重新生成头像
    skin_url = ""
    skins = data.get("skins")
    if isinstance(skins, list) and skins and isinstance(skins[0], dict):
        skin_url = str(skins[0].get("url") or "")
    return {"name": name, "uuid": str(data.get("id") or ""), "skin_url": skin_url}


def upload_skin(access_token: str, skin_png: bytes, variant: str = "classic") -> dict:
    """把本地皮肤 PNG 上传到正版账号（multipart 直传文件）。

    variant 取 classic（经典）或 slim（纤细），返回接口的 JSON（皮肤部件列表）。
    """
    if not access_token:
        raise AuthError("登录状态已失效，请重新登录")
    if not skin_png:
        raise AuthError("皮肤文件为空")

    model = "slim" if str(variant or "").strip().lower() == "slim" else "classic"
    boundary = f"----MCHLSkin{uuid.uuid4().hex}"
    body = b"".join(
        (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="variant"\r\n\r\n'
                f"{model}\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="skin.png"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode("utf-8"),
            skin_png,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        )
    )
    request = urllib.request.Request(
        SKIN_UPLOAD_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise AuthError(f"无法连接皮肤服务：{exc.reason}") from None
    except OSError as exc:
        raise AuthError(f"上传皮肤失败：{exc}") from None

    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if status >= 400:
        raise AuthError(_http_error(status, data, "上传皮肤失败"))
    return data


def reset_skin(access_token: str) -> None:
    """删除当前正版皮肤，恢复成默认皮肤（Steve / Alex）。"""
    if not access_token:
        raise AuthError("登录状态已失效，请重新登录")
    request = urllib.request.Request(
        SKIN_RESET_URL,
        method="DELETE",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise AuthError(f"无法连接皮肤服务：{exc.reason}") from None
    except OSError as exc:
        raise AuthError(f"重置皮肤失败：{exc}") from None

    if status >= 400:
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            data = {}
        raise AuthError(_http_error(status, data if isinstance(data, dict) else {}, "重置皮肤失败"))


def _notify(on_stage, text: str) -> None:
    """安全地回调阶段文字（异常不影响登录本身）。"""
    if on_stage is None:
        return
    try:
        on_stage(text)
    except Exception:  # noqa: BLE001 - 回调只是提示用
        pass


def complete_login(
    ms_token: dict,
    cid: str | None = None,
    on_stage=None,
) -> dict:
    """把微软令牌逐级换成可直接启动游戏的账号信息。

    on_stage 会在每个阶段被调用一次，用来给界面显示实时进度。
    """
    _notify(on_stage, "正在验证 Xbox 账户")
    xbl = authenticate_xbox(str(ms_token.get("access_token") or ""))
    xsts = authorize_xsts(xbl["token"])
    _notify(on_stage, "正在获取游戏令牌")
    mc = login_minecraft(xsts["user_hash"] or xbl["user_hash"], xsts["token"])
    _notify(on_stage, "正在读取游戏档案")
    profile = fetch_profile(mc["access_token"])

    lifetime = mc["expires_in"] or int(ms_token.get("expires_in") or 0)
    return {
        "name": profile["name"],
        "uuid": profile["uuid"],
        "skin_url": profile.get("skin_url", ""),
        "access_token": mc["access_token"],
        "refresh_token": str(ms_token.get("refresh_token") or ""),
        "client_id": (cid or client_id()).strip(),
        "expires_at": int(time.time() + lifetime),
        "xuid": xsts["xuid"] or xbl["xuid"],
        "user_type": "msa",
    }


def refresh_account(account: dict, on_stage=None) -> dict:
    """用保存的 refresh token 重新走一遍认证链。"""
    token = refresh_ms_token(
        str(account.get("refresh_token") or ""),
        str(account.get("client_id") or "") or None,
    )
    return complete_login(token, str(account.get("client_id") or "") or None, on_stage)


def is_expired(account: dict | None, margin: int = TOKEN_MARGIN) -> bool:
    """本地令牌是否已过期（无法判断时按未过期处理）。"""
    if not account:
        return True
    try:
        expires_at = int(account.get("expires_at") or 0)
    except (TypeError, ValueError):
        return False
    if expires_at <= 0:
        return False
    return expires_at - margin <= time.time()


def load_account(path: Path) -> dict | None:
    """读取本地保存的账号，文件缺失或损坏时返回 None。"""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not str(data.get("refresh_token") or ""):
        return None
    return data


def save_account(path: Path, account: dict) -> None:
    """把账号写入本地文件（含 refresh token，注意保密）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:  # 尽量收紧权限（Windows 上只影响只读位，Linux 上生效）
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_account(path: Path) -> None:
    """删除本地保存的账号。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
