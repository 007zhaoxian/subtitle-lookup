"""给 IINA 打开 mpv IPC 通道（`input-ipc-server`）—— 一键配置。

【为什么需要这个模块】
IINA 是看剧场景的主力播放器，但它**只**能通过 mpv 的 JSON IPC 被精确控制：

  · IINA 1.4.3 **没有** AppleScript 播放控制。实测（2026-09-15，本机）：
    它的 bundle 里没有 `.sdef`、没有 `Scripting` 资源、Info.plist 里也没有
    `NSAppleScriptEnabled`；`osascript -e 'tell application "IINA" to get playing'`
    直接报「变量"playing"没有定义 (-2753)」，只有通用的 `version` 能读。
  · 唯一的兜底"系统媒体键"**不可靠**：macOS 把媒体键路由给当前占用
    "Now Playing" 的那个 App。实测**暂停状态的 IINA 拿不到媒体键**
    （CPU 采样证明它没被动过）—— 也就是说媒体键能停、但**叫不醒**。
    对"关掉浮窗继续播"这种场景是致命的。

所以 IPC 是 IINA 用户体验的关键：配上之后能**读**真实的 pause 属性、
也能**精确设置**成停/播，不依赖焦点、不依赖 Now Playing。

【怎么配】
IINA → 设置 → 高级（需先勾「启用高级设置」）→ 附加 mpv 选项，
加一行：   input-ipc-server = /tmp/iina.sock
然后重启 IINA。

这个"加一行"在 IINA 里是纯手工操作，很容易配错（少个斜杠、写成
`--input-ipc-server`），所以这里提供程序化写入。

【为什么必须先退出 IINA】
IINA 在运行期间持有自己的偏好内存副本，退出/改设置时会整体写回 plist ——
**在线改会被它覆盖掉**。所以这里先检查 IINA 是否在跑，在跑就拒绝并让用户
先退出，避免"看着写成功了，一重启又没了"这种鬼问题。

【写 plist 的正确姿势】
不能直接 `plistlib.dump` 覆盖文件：偏好是由 `cfprefsd` 这个守护进程缓存的，
直接写文件大概率被缓存值盖住。走 `defaults export/import` 才落到 cfprefsd
的正规路径上（实测读回一致）。
"""
from __future__ import annotations

import plistlib
import subprocess
import tempfile
from pathlib import Path

import config
from utils import log

IINA_BUNDLE_ID = "com.colliderli.iina"
IINA_APP_PATHS = ("/Applications/IINA.app",)
# IINA 里存 mpv 附加选项的偏好键，形状是 [[名称, 值], …]
USER_OPTIONS_KEY = "userOptions"
IPC_OPTION_NAME = "input-ipc-server"


def iina_installed() -> bool:
    return any(Path(p).exists() for p in IINA_APP_PATHS)


def iina_running() -> bool:
    """IINA 现在开着吗（开着就别改偏好，会被它覆盖）。"""
    try:
        from AppKit import NSRunningApplication

        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            IINA_BUNDLE_ID
        )
        return bool(apps)
    except Exception:
        # 拿不到就按"在跑"处理 —— 宁可让用户多退一次，也不要写入被覆盖
        log.debug("查 IINA 是否在运行失败，保守当作在运行")
        return True


def current_options() -> list[list[str]]:
    """读 IINA 现有的「附加 mpv 选项」（读不到就空列表）。"""
    try:
        r = subprocess.run(["defaults", "export", IINA_BUNDLE_ID, "-"],
                           capture_output=True, timeout=6)
        if r.returncode != 0 or not r.stdout:
            return []
        data = plistlib.loads(r.stdout)
    except Exception:
        log.debug("读 IINA 偏好失败", exc_info=True)
        return []
    raw = data.get(USER_OPTIONS_KEY) or []
    out: list[list[str]] = []
    for item in raw:
        try:
            out.append([str(x) for x in item])
        except TypeError:
            continue
    return out


def _norm(name: str) -> str:
    """把选项名规范化：容忍 `--foo-bar` / `foo_bar` / `-foo-bar` 这些写法。"""
    return (name or "").strip().lstrip("-").replace("_", "-")


def ipc_configured(sock_path: str | None = None) -> bool:
    """IINA 偏好里是不是已经配了 input-ipc-server（名字对上就算）。"""
    for item in current_options():
        if item and _norm(item[0]) == IPC_OPTION_NAME:
            return True
    return False


def current_ipc_value() -> str:
    """当前配的 socket 路径（没配就空串）。"""
    for item in current_options():
        if item and _norm(item[0]) == IPC_OPTION_NAME:
            return item[1] if len(item) > 1 else ""
    return ""


def _merge_option(options: list[list[str]], sock_path: str) -> list[list[str]]:
    """把 input-ipc-server 加进去（已有同名项就改值，不重复加）。"""
    out: list[list[str]] = []
    replaced = False
    for item in options:
        if item and _norm(item[0]) == IPC_OPTION_NAME:
            out.append([IPC_OPTION_NAME, sock_path])
            replaced = True
        else:
            out.append(list(item))
    if not replaced:
        out.append([IPC_OPTION_NAME, sock_path])
    return out


def configure(sock_path: str | None = None, dry_run: bool = False
              ) -> tuple[bool, str]:
    """给 IINA 写入 `input-ipc-server`。返回 (成功?, 给用户看的一句话)。

    dry_run=True 只算结果、不落盘（给测试用，也方便先给用户看一眼会改成什么）。
    """
    path = str(sock_path or config.IINA_SOCKET_PATH)
    if not iina_installed():
        return False, "没找到 IINA（/Applications/IINA.app）。你用的是别的播放器就不用配它。"
    if ipc_configured(path):
        return True, (f"IINA 里已经配过 input-ipc-server = {path}，不用重复配置。\n"
                      "如果状态栏仍显示读不到，检查 IINA 是不是没重启过。")
    if iina_running() and not dry_run:
        return False, ("请先**完全退出 IINA**（⌘Q），再点这个按钮。\n"
                       "原因：IINA 运行期间持有自己的偏好副本，退出时会整体写回，"
                       "在线改会被它覆盖掉。")

    options = _merge_option(current_options(), path)
    if dry_run:
        return True, f"将写入：{options}"

    # 走 defaults export/import，落到 cfprefsd 的正规路径
    try:
        r = subprocess.run(["defaults", "export", IINA_BUNDLE_ID, "-"],
                           capture_output=True, timeout=6)
        data = plistlib.loads(r.stdout) if r.returncode == 0 and r.stdout else {}
        data[USER_OPTIONS_KEY] = options
        with tempfile.NamedTemporaryFile("wb", suffix=".plist",
                                         delete=False) as fh:
            plistlib.dump(data, fh)
            tmp = fh.name
        r2 = subprocess.run(["defaults", "import", IINA_BUNDLE_ID, tmp],
                            capture_output=True, text=True, timeout=8)
        Path(tmp).unlink(missing_ok=True)
        if r2.returncode != 0:
            return False, f"写入失败：{(r2.stderr or '').strip()[:200]}"
    except Exception as exc:
        return False, f"写入失败：{type(exc).__name__}: {exc}"

    # 立刻读回确认（不确认就等于没配）
    if not ipc_configured(path):
        return False, "写进去又读不回来（可能被 cfprefsd 缓存挡住了）。请改用手工配置。"
    log.info("已给 IINA 写入 %s = %s", IPC_OPTION_NAME, path)
    return True, (f"✅ 已给 IINA 写入 {IPC_OPTION_NAME} = {path}\n\n"
                  "现在请**打开 IINA**，然后回面板点「🎬 检测播放器」——"
                  "应该会看到「✅ IINA（可精确读写在播/已停）」。\n"
                  "以后就不用再配了（升级 IINA 也不会丢）。")


def remove(sock_path: str | None = None, dry_run: bool = False
           ) -> tuple[bool, str]:
    """把配置撤掉（万一用户想恢复原样）。"""
    path = str(sock_path or config.IINA_SOCKET_PATH)
    options = [o for o in current_options()
               if (o[0] if o else "").strip().lstrip("-").replace("_", "-")
               != IPC_OPTION_NAME]
    if len(options) == len(current_options()):
        return True, "本来就没有配置，无需撤销。"
    if iina_running() and not dry_run:
        return False, "请先完全退出 IINA（⌘Q）再撤销。"
    if dry_run:
        return True, f"将写入：{options}"
    try:
        r = subprocess.run(["defaults", "export", IINA_BUNDLE_ID, "-"],
                           capture_output=True, timeout=6)
        data = plistlib.loads(r.stdout) if r.returncode == 0 and r.stdout else {}
        data[USER_OPTIONS_KEY] = options
        with tempfile.NamedTemporaryFile("wb", suffix=".plist",
                                         delete=False) as fh:
            plistlib.dump(data, fh)
            tmp = fh.name
        subprocess.run(["defaults", "import", IINA_BUNDLE_ID, tmp],
                       capture_output=True, timeout=8)
        Path(tmp).unlink(missing_ok=True)
    except Exception as exc:
        return False, f"撤销失败：{exc}"
    log.info("已从 IINA 移除 %s（target=%s）", IPC_OPTION_NAME, path)
    return True, "已撤销 IINA 的 IPC 配置，下次启动 IINA 后生效。"
