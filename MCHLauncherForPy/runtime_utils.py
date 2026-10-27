"""通用运行时工具：文件下载与解压。"""

from __future__ import annotations

import os
import platform
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

USER_AGENT = "MCHLauncherForPy/0.1 (pywebview)"
CHUNK_SIZE = 1 << 16
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _build_ssl_context() -> ssl.SSLContext:
    """保留证书链校验，但关闭 OpenSSL 严格模式。

    部分代理/镜像站点的证书缺少 Authority Key Identifier 扩展，
    Python 3.13+ 默认的 X509_STRICT 会直接判定失败。
    """
    context = ssl.create_default_context()
    try:
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    except AttributeError:
        pass
    return context


SSL_CONTEXT = _build_ssl_context()

ProgressCallback = Callable[[int, int, str], None]


class DownloadError(RuntimeError):
    """下载失败。"""


def _download_once(
    url: str,
    dest: Path,
    on_progress: ProgressCallback | None = None,
    label: str = "",
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with part.open("wb") as fp:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fp.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total, label)
    except Exception:  # 网络错误种类很多，交由上层统一转换
        part.unlink(missing_ok=True)
        raise

    part.replace(dest)
    return dest


def download_file(
    url: str,
    dest: Path,
    on_progress: ProgressCallback | None = None,
    label: str = "",
    retries: int = 2,
) -> Path:
    """下载单个文件到 dest，失败时自动重试。"""
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            return _download_once(url, dest, on_progress, label)
        except Exception as exc:  # noqa: BLE001 - 统一转换为 DownloadError
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))

    raise DownloadError(str(last_error) if last_error else "未知错误")


NATIVE_SUFFIXES = (".dll", ".so", ".dylib")


def extract_natives(archive: Path, target: Path, arch: str | None = None) -> int:
    """把 native 库扁平解压到 target 根目录。

    新版（LWJGL 3.4+）的 natives jar 内部按 windows/x64、windows/arm64
    分目录存放 dll，直接解压会导致 java.library.path 找不到库，
    因此这里只取目标架构的库文件并统一放到 target 根目录。

    arch 由调用方按实际启动的 Java 传入（"x64" / "arm64"），
    不传时退回系统架构推断。
    """
    target.mkdir(parents=True, exist_ok=True)
    if arch:
        is_arm = str(arch).lower() == "arm64"
    else:
        is_arm = platform.machine().lower() in ("arm64", "aarch64")
    written = 0

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.startswith("META-INF/"):
                continue
            if not name.lower().endswith(NATIVE_SUFFIXES):
                continue

            parts = [part.lower() for part in name.split("/")]
            if "arm64" in parts and not is_arm:
                continue
            if is_arm and ("x64" in parts or "x86" in parts):
                continue

            dest = target / Path(name).name
            try:
                dest.write_bytes(zf.read(info))
                written += 1
            except OSError:
                continue

    return written


def extract_zip(archive: Path, target: Path, skip_prefixes: tuple[str, ...] = ()) -> None:
    """解压 zip，可跳过 META-INF 之类的无用条目。"""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if skip_prefixes and name.startswith(skip_prefixes):
                continue
            zf.extract(info, target)
