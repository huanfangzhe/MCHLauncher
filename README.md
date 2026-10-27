# MCHLauncherForPy

基于 **Python + pywebview（WebView2）** 的《我的世界》启动器，界面用原生 HTML/CSS/JS 编写，业务逻辑全部在 Python 侧，可打包为单个 exe 分发。

- 开发者：**幻方者**
- 哔哩哔哩：<https://space.bilibili.com/3546815194467241>
- 当前版本：`1.1.1`

## 功能

### 版本与游戏文件夹
- 自动扫描 `.minecraft/versions`，也可把任意位置的 `versions` 目录加入列表（支持选择游戏根目录自动识别）
- 游戏文件夹可从列表中移除（只改列表，**不会删除磁盘文件**）
- 版本重命名、删除、打开目录、查看详情、自定义版本图标
- 每个版本独立保存 Java、内存、附加启动参数

### 启动
- 自动校验并补全版本依赖（libraries / assets / natives）
- 启动进度实时反馈、启动日志查看、一键终止游戏
- 可选“游戏启动后关闭启动器”

### 账号
- 微软正版登录（设备码流程，无需内嵌浏览器或本地回调端口）
- 外置登录（Yggdrasil / authlib-injector，如 LittleSkin 等），自动下载并缓存 `authlib-injector.jar`
- 离线账号、自定义皮肤、皮肤头像裁剪（纯标准库实现，不依赖 Pillow）

### 下载
- 原版游戏版本下载
- 模组加载器：Fabric / Quilt / Forge / NeoForge，附 Fabric API 一键安装
- 模组搜索与下载：Modrinth、CurseForge（默认走社区镜像，设置 `CURSEFORGE_API_KEY` 可走官方接口）

### 模组与整合包
- 模组启用 / 禁用 / 删除（通过 `.disabled` 后缀实现，不改文件内容）
- 拖拽 jar / zip 直接导入
- 整合包导入：`.mrpack`（Modrinth）、CurseForge zip、MCHL / PCL 导出的整合包
- 整合包导出为 zip

### Java 运行时
- 自动探测常见安装目录、环境变量与注册表
- 手动指定 java 可执行文件
- 按版本要求自动下载对应 Java

### 基岩版
- 检测本地已安装的《Minecraft for Windows》并启动
- 通过 Store 渠道 / 镜像源下载安装包，或安装本地安装包
- 打开存档目录

### 界面
- 主题色、自定义背景图与模糊度、默认板块
- 首页自定义小组件
- 窗口圆角、自适应窗口尺寸

## 环境要求

| 项目 | 说明 |
| --- | --- |
| 系统 | Windows 10 / 11（x64） |
| Python | 3.10+（开发环境为 3.14） |
| 运行时 | Microsoft Edge WebView2 Runtime（Win11 自带，缺失时启动器会提示下载） |
| 游戏 | 需自备正版账号或可用的外置登录 / 离线账号 |

## 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

依赖只有 `pywebview`（`requirements.txt`）：

```
pywebview>=6.0,<7.0
```

## 打包为 exe

双击 `build.bat`，或手动执行：

```bash
python -m PyInstaller MCHLauncherForPy.spec --noconfirm --clean --distpath exe --workpath build
```

产物：`exe/MCHLauncherForPy_<版本>.exe`（版本号取自 `main.py` 的 `APP_VERSION`）。

构建开关（环境变量）：

| 变量 | 作用 |
| --- | --- |
| `MCHL_ONEDIR=1` | 产出文件夹版，启动更快（单文件版每次启动需解包到临时目录） |
| `MCHL_CONSOLE=1` | 产出带控制台的调试版，方便看报错 |
| `MCHL_FULL_DOTNET=1` | 保留 pythonnet 的 CoreCLR 程序集（默认裁掉以缩小体积） |

体积说明：单文件 exe 约 10.7 MB。构建时会剔除未使用的标准库与 pythonnet 的 CoreCLR 程序集；**不要删除** `webview/lib/runtimes` 下的 `win-x64` / `win-x86` / `win-arm64`，pywebview 依赖它们探测 WebView2Loader。

## 目录结构

```
main.py              启动器 API 与业务编排（版本、账号、下载、模组、设置…）
game_launcher.py     版本解析、依赖补全、启动命令构造
version_download.py  原版版本清单与安装
loader_install.py    加载器（Fabric / Quilt / Forge / NeoForge）安装
mod_download.py      模组搜索与下载（Modrinth / CurseForge）
mod_manage.py        已安装模组扫描与启停
modpack_install.py   整合包解析（.mrpack / CurseForge zip）
ms_auth.py           微软设备码登录
authlib_auth.py      外置登录（Yggdrasil / authlib-injector）
skin_avatar.py       皮肤头像裁剪（纯标准库）
java_runtime.py      Java 探测与下载
bedrock_manage.py    基岩版检测、启动与安装
runtime_utils.py     下载与解压等通用工具
web/                 前端页面（index.html + assets/css + assets/js）
MCHLauncherForPy.spec PyInstaller 配置
build.bat            一键打包脚本
```

## 数据目录

启动器在**自身所在目录**创建 `.minecraft`（旧版 `MCHL` 目录会自动迁移）：

| 路径 | 内容 |
| --- | --- |
| `.minecraft/versions/` | 游戏版本 |
| `.minecraft/libraries/`、`assets/` | 依赖库与资源文件 |
| `.minecraft/launcher.json` | 启动器状态（选中版本、窗口尺寸等） |
| `.minecraft/home.json` | 首页自定义小组件 |
| `.minecraft/logs/` | 启动日志 |
| `.minecraft/MCHL/Setup.ini` | 全局设置（主题、背景、Java、内存等） |
| `.minecraft/java/` | 启动器下载的 Java 运行时 |
| `.minecraft/bedrock/` | 基岩版安装包缓存 |

设置页“启动器 → 数据目录”可以直接打开该目录。

## 可选环境变量

| 变量 | 作用 |
| --- | --- |
| `MCHL_MS_CLIENT_ID` | 替换微软登录使用的 Azure 客户端 ID（需允许设备码流程） |
| `CURSEFORGE_API_KEY` | 使用 CurseForge 官方 API，而不是社区镜像 |

## 常见问题

**启动器打不开，提示 WebView2 / `Cannot find win-arm64`？**
安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。若是自行打包遇到 `Cannot find win-*`，说明打包时删掉了 `webview/lib/runtimes` 下的架构目录，请恢复。

**提示找不到 Java？**
在设置页或版本设置里指定 java 路径，或使用“下载 Java”。游戏版本要求的 Java 大版本不匹配时，启动器会给出提示。

**单文件 exe 首次启动较慢？**
单文件模式每次启动都要解包到临时目录。可用 `MCHL_ONEDIR=1` 生成文件夹版（需整个文件夹一起分发）。

**杀毒软件报毒？**
PyInstaller 打包的程序被误报较常见，可添加信任或用文件夹版自行打包。

## 免责声明

本项目为个人开发的第三方启动器，与 Mojang Studios、Microsoft 无隶属关系。《Minecraft》相关名称、素材与版权归其各自所有者所有。请使用正版账号登录官方服务器。

---

开发者：幻方者 · <https://space.bilibili.com/3546815194467241>
