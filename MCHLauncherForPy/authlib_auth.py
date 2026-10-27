"""第三方外置登录（Yggdrasil / authlib-injector）。

支持的服务器：LittleSkin、统一通行证等任何实现了 Yggdrasil 协议的外置登录站点，
地址形如 https://littleskin.cn/api/yggdrasil。

启动外置登录的版本时还需要 authlib-injector 把游戏的认证地址重定向到该服务器，
所以这里一并负责下载并缓存 authlib-injector.jar。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from runtime_utils import SSL_CONTEXT, USER_AGENT, download_file

DEFAULT_SERVER = "https://littleskin.cn/api/yggdrasil"
TIMEOUT = 30

INJECTOR_META_URLS = (
    "https://authlib-injector.yushi.moe/artifact/latest.json",
    "https://bmclapi2.bangbang93.com/mirrors/authlib-injector/artifact/latest.json",
)
INJECTOR_MIN_BYTES = 100 * 1024


class AuthlibError(RuntimeError):
    """外置登录失败，异常信息可直接展示给用户。"""


def normalize_server(server: str) -> str:
    """把用户填的地址整理成 Yggdrasil API 根地址。"""
    text = (server or "").strip()
    if not text:
        text = DEFAULT_SERVER
    if "://" not in text:
        text = "https://" + text
    text = text.rstrip("/")
    for suffix in ("/authserver", "/sessionserver"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def new_client_token() -> str:
    """生成新的 clientToken。"""
    return uuid.uuid4().hex


def _send(url: str, payload: dict | None = None) -> tuple[int, dict]:
    """发请求，返回 (状态码, JSON)。网络错误抛 AuthlibError。"""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise AuthlibError(f"无法连接外置登录服务器：{exc.reason}") from None
    except OSError as exc:
        raise AuthlibError(f"网络请求失败：{exc}") from None

    if not raw.strip():
        return status, {}
    try:
        data = json.loads(raw)
    except ValueError:
        if status >= 400:
            raise AuthlibError(f"服务器返回异常（HTTP {status}）") from None
        return status, {}
    return status, data if isinstance(data, dict) else {}


def _error_message(status: int, data: dict, fallback: str) -> str:
    message = str(data.get("errorMessage") or data.get("error") or "").strip()
    if message:
        return message[:200]
    if status == 403:
        return "账号或密码错误"
    return f"{fallback}（HTTP {status}）"


def authenticate(server: str, username: str, password: str, client_token: str) -> dict:
    """用账号密码换取 accessToken。"""
    root = normalize_server(server)
    payload = {
        "agent": {"name": "Minecraft", "version": 1},
        "username": username,
        "password": password,
        "clientToken": client_token,
        "requestUser": False,
    }
    status, data = _send(f"{root}/authserver/authenticate", payload)
    token = str(data.get("accessToken") or "")
    if status >= 400 or not token:
        raise AuthlibError(_error_message(status, data, "登录失败"))

    profile = data.get("selectedProfile") or {}
    if not isinstance(profile, dict) or not profile.get("id"):
        profiles = data.get("availableProfiles") or []
        profile = profiles[0] if profiles else {}
    if not isinstance(profile, dict) or not profile.get("id"):
        raise AuthlibError("该账号还没有创建角色，请先在服务器网站创建角色名")

    return {
        "server": root,
        "access_token": token,
        "client_token": str(data.get("clientToken") or client_token),
        "uuid": str(profile.get("id") or ""),
        "name": str(profile.get("name") or ""),
    }


def validate(server: str, access_token: str, client_token: str) -> bool:
    """校验令牌是否仍然有效。"""
    root = normalize_server(server)
    payload = {"accessToken": access_token, "clientToken": client_token}
    status, _ = _send(f"{root}/authserver/validate", payload)
    return status < 400


def refresh(server: str, access_token: str, client_token: str) -> dict:
    """令牌失效前刷新，返回新的令牌信息。"""
    root = normalize_server(server)
    payload = {"accessToken": access_token, "clientToken": client_token}
    status, data = _send(f"{root}/authserver/refresh", payload)
    token = str(data.get("accessToken") or "")
    if status >= 400 or not token:
        raise AuthlibError(_error_message(status, data, "登录状态已失效，请重新登录"))
    profile = data.get("selectedProfile") or {}
    return {
        "server": root,
        "access_token": token,
        "client_token": str(data.get("clientToken") or client_token),
        "uuid": str(profile.get("id") or ""),
        "name": str(profile.get("name") or ""),
    }


def invalidate(server: str, access_token: str, client_token: str) -> None:
    """让服务器作废当前令牌（退出登录）。"""
    root = normalize_server(server)
    try:
        _send(
            f"{root}/authserver/invalidate",
            {"accessToken": access_token, "clientToken": client_token},
        )
    except AuthlibError:
        pass


def fetch_skin_url(server: str, account_uuid: str) -> str:
    """读取角色皮肤地址（拿不到就返回空字符串）。"""
    if not account_uuid:
        return ""
    root = normalize_server(server)
    url = f"{root}/sessionserver/session/minecraft/profile/{account_uuid}"
    try:
        status, data = _send(url)
    except AuthlibError:
        return ""
    if status >= 400:
        return ""

    for item in data.get("properties") or []:
        if not isinstance(item, dict) or item.get("name") != "textures":
            continue
        try:
            decoded = base64.b64decode(str(item.get("value") or "")).decode("utf-8")
            textures = json.loads(decoded).get("textures") or {}
        except (ValueError, TypeError):
            continue
        skin = textures.get("SKIN") or {}
        if isinstance(skin, dict) and skin.get("url"):
            return str(skin["url"])
    return ""


def ensure_injector(dest: Path) -> Path | None:
    """确保 authlib-injector.jar 存在，缺失时下载；失败返回 None。"""
    try:
        if dest.is_file() and dest.stat().st_size > INJECTOR_MIN_BYTES:
            return dest
    except OSError:
        pass

    for meta_url in INJECTOR_META_URLS:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(meta_url, headers={"User-Agent": USER_AGENT}),
                timeout=TIMEOUT,
                context=SSL_CONTEXT,
            ) as resp:
                meta = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError):
            continue

        download_url = ""
        if isinstance(meta, dict):
            download_url = str(meta.get("download_url") or "")
        if not download_url:
            continue

        try:
            download_file(download_url, dest, label="authlib-injector")
        except Exception:  # noqa: BLE001 - 换下一个镜像重试
            continue
        if dest.is_file() and dest.stat().st_size > INJECTOR_MIN_BYTES:
            return dest
    return None
