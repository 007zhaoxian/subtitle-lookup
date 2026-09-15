"""播放器控制层 —— 让「空格」顺手把画面停住 / 继续播。

背景
----
老做法是"不吞键，让真实的空格自己送到播放器"。这依赖一个脆弱的前提：
**我们绝不能抢走前台 App**。一旦抢走（历史 bug：Qt 的 raise_() 会顺带激活应用），
空格就再也到不了播放器，用户必须先鼠标点一下播放器才能继续播 —— 极其烦人，
而且极难描述和复现。

现在改成**我们主动去控制播放器**，于是这件事和"焦点在谁手里"彻底解耦。

两条通道（精确 → 万能）
------------------------
① IINA —— mpv 的 JSON IPC（Unix domain socket）
   能读到**真实**的 `pause` 属性，也能精确 `set_property pause true/false`。
   前提：IINA 偏好里加过 `input-ipc-server = /tmp/iina.sock`。
   ⚠️ IINA 1.4.3 **没有** AppleScript 播放控制（实测：bundle 里没有 sdef、
      也没有 Scripting 资源），所以 IPC 是它唯一能被精确控制的入口。
      面板上有「🛠 配置 IINA 高速通道」一键写入。

② 兜底 —— 合成系统媒体键（NX_KEYTYPE_PLAY）
   macOS 的媒体键**不按焦点投递**，而是送给当前占据 "Now Playing" 的那个 App。
   零配置、全播放器通吃。代价：只能"切换"、读不到状态。
   ⚠️ 实测（2026-09-15，IINA 1.4.3）：**暂停中的 IINA 收不到媒体键**
      （CPU 采样证实它没被唤醒）—— 也就是"能停、多半叫不醒"。
      所以它只配当最后的兜底，别指望它做完"关掉浮窗继续播"。

★ v1.2.0 起：浏览器的在线视频通道（网页视频桥 + AppleScript 执行 JS）
  已**整体删除** —— 用户只用 IINA 看片，那两条通道纯属维护负担，
  留着还会在"前台是浏览器"时把空格吃掉却控不动任何东西。

所以对外接口是 set_paused(bool)，内部按"能不能精确设置"决定走哪条路。
"""
from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path

import config
from utils import log

# NSEvent / hidsystem/ev_keymap.h
NSSYSTEM_DEFINED = 14
NX_KEYTYPE_PLAY = 16
# NSEventSubtype 里"辅助功能键"那一档
SUBTYPE_AUX = 8


# ==================================================================== ② 媒体键
def post_media_key(key: int = NX_KEYTYPE_PLAY, dry_run: bool = False) -> bool:
    """合成一次系统媒体键。

    这是唯一"不受焦点影响"的通道：macOS 会把媒体键路由到当前占据
    Now Playing 的那个 App，而不是当前 key window 的那个。
    所以即使我们的浮窗挡在最前面，播放器照样能收到。

    需要辅助功能权限（本程序本来就要，见面板的权限检查）。

    dry_run=True 时只构造事件、**不真正派发** —— 给单测用。
    （真实派发会切换用户正在播的东西，测试里绝不能误触发。）
    """
    try:
        from AppKit import NSEvent
        from Quartz import CGEventPost
    except Exception as exc:
        log.debug("媒体键不可用（pyobjc 缺失？）: %s", exc)
        return False
    try:
        def _post(down: bool) -> None:
            flags = 0xA00 if down else 0xB00
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501
                NSSYSTEM_DEFINED,
                (0.0, 0.0),
                flags,
                0.0,
                0,
                None,
                SUBTYPE_AUX,
                (key << 16) | flags,
                -1,
            )
            if dry_run:
                return
            CGEventPost(0, ev.CGEvent())

        _post(True)
        _post(False)
        return True
    except Exception as exc:
        log.warning("合成媒体键失败: %s", exc)
        return False


# ==================================================================== ① IINA
def _iina_cmd(command: list, timeout: float | None = None) -> dict | None:
    """往 IINA 的 mpv IPC socket 发一条 JSON 命令，返回解析后的响应。

    失败一律返回 None（"用不了"），**不抛异常** —— 调用方要的是
    "这条通道行不行"，不是异常栈。

    ★ 带 `request_id` 发、并且**只认回带同一个 id 的那一行**。
      原因：同一条连接上可能混进事件推送（`{"event": ...}`，**没有 error 字段**），
      而老实现是"取第一行 + 没有 error 就当成功"，一旦第一行是事件行，
      就会被误判成成功、取出 `data=None`。实测 IINA 1.4.3 的 mpv 会正常回带
      request_id；同时留个兜底：对面不回 id 时，接受第一条**带 error 字段**的行。
    """
    path = Path(config.IINA_SOCKET_PATH)
    if not path.exists():
        return None
    to = float(timeout if timeout is not None else config.PLAYER_CMD_TIMEOUT)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(to)
    rid = 1
    fallback = None
    try:
        sock.connect(str(path))
        sock.sendall(
            (json.dumps({"command": command, "request_id": rid}) + "\n").encode("utf-8"))
        buf = b""
        deadline = time.time() + to
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("request_id") == rid:
                    return msg
                if fallback is None and "error" in msg:
                    fallback = msg          # 对面不回 id 时的兜底
        return fallback
    except Exception as exc:
        log.debug("IINA IPC 失败: %s", exc)
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _iina_playing() -> bool | None:
    """IINA 当前在播吗。返回 None = 问不到（没开 socket / 没在放东西）。"""
    idle = _iina_cmd(["get_property", "idle-active"])
    if not idle or idle.get("error") not in (None, "success"):
        return None
    if idle.get("data") is True:
        return None                     # 没加载任何文件，别去控制它
    resp = _iina_cmd(["get_property", "pause"])
    if not resp or resp.get("error") not in (None, "success"):
        return None
    data = resp.get("data")
    if isinstance(data, bool):
        return not data
    return None


def _iina_set_paused(paused: bool) -> bool:
    resp = _iina_cmd(["set_property", "pause", bool(paused)])
    return bool(resp) and resp.get("error") in (None, "success")


# ================================================= ③ 不依赖 IPC 的两条旁路
# 为什么必须要有这一段（2026-09-15 实测，IINA 1.4.3）：
#
#   IINA 进程内可能有**多个 mpv core**，而它们共用同一个 `input-ipc-server`
#   路径（/tmp/iina.sock）。后创建的那个 core 会 unlink 旧文件再 bind，
#   **把路径抢走** —— 于是我们 `connect()` 到的可能是个**空转的 core**：
#   `idle-active=true`、`playlist-count=0`、`filename` 一律 `property unavailable`，
#   而**真正在播的那个 core 的 socket 已被 unlink，从文件路径上完全不可达**。
#   （判据：`pmset -g assertions` 里明明有 "IINA playback is in progress"，IPC 却说空闲。）
#
# 后果不是"少读一个值"这么轻：`status()` 会返回 playing=None，而调用方
# 把 None 当成"按在播处理"，媒体键又是**切换**语义 —— 一旦实际是暂停状态，
# 这一下就做出**反向动作**（用户想暂停，结果视频开始播了）。
#
# 所以下面两条旁路**都不碰 IPC**，专门用来回答两件事：
#   · 到底在不在播  → 电源断言（准、快、无需权限）
#   · 放的是哪个片  → 屏幕上可见的 IINA 窗口标题（就是磁盘上的原始文件名）

# 窗口归属名（CGWindowList 里的 owner name，不是 bundle id）
IINA_OWNER_NAMES = ("IINA",)
# "看起来像磁盘上的媒体文件" —— 用来排除 IINA 的设置/脚本窗口标题
_MEDIA_SUFFIX = re.compile(
    r"(?i)\.(mkv|mp4|m4v|avi|mov|flv|wmv|webm|rmvb|rm|mpg|mpeg|m2ts|mts|ts|"
    r"vob|iso)$")

_assert_cache: dict = {"at": 0.0, "active": None}
_ASSERT_TTL = 0.5


def _looks_like_media(title: str) -> bool:
    """标题像不像"磁盘上的媒体文件名"。**纯函数，离线可测。**"""
    return bool(_MEDIA_SUFFIX.search((title or "").strip()))


def _iina_running() -> bool:
    """IINA 进程在不在。

    用 `pgrep` 而不是 `ps`（沙箱里 ps 会被拒；pgrep 实测可用）。
    用来区分"暂停中"和"压根没开 IINA"这两件不同的事。
    """
    import subprocess

    try:
        return subprocess.run(["pgrep", "-x", "IINA"],
                              capture_output=True, timeout=1.0).returncode == 0
    except Exception:
        return False


def iina_playback_active(force: bool = False) -> bool | None:
    """IINA 现在在播放吗？**完全不依赖 IPC**，读系统电源断言。

    实测（2026-09-15）：IINA 播放时进程持有
    `PreventUserIdleDisplaySleep named: "IINA playback is in progress"`，
    暂停后立即释放 —— 所以"有这条断言"≈"正在播"。

    返回 True/False/None（None = 问不到：没装 pmset、命令失败、IINA 没开）。
    带 0.5 秒缓存：面板会 600ms 轮询一次状态，别每次都 fork 一个 pmset。
    """
    import subprocess

    now = time.time()
    if (not force and _assert_cache["active"] is not None
            and (now - _assert_cache["at"]) < _ASSERT_TTL):
        return _assert_cache["active"]
    try:
        out = subprocess.run(["pmset", "-g", "assertions"],
                             capture_output=True, text=True, timeout=2.0).stdout
    except Exception as exc:
        log.debug("读电源断言失败: %s", exc)
        return None
    active = True if _assert_says_playing(out) else None
    if active is None:
        # 没看到播放断言：可能"暂停着"，也可能"IINA 没开" —— 用进程存在性区分，
        # 免得把"没开 IINA"说成"已暂停"（那会导致按钮文案和安全判断都错）。
        active = False if _iina_running() else None
    _assert_cache.update(at=now, active=active)
    return active


def _assert_says_playing(pmset_text: str) -> bool:
    """`pmset -g assertions` 的输出里有没有 IINA 的**播放中**断言。

    **纯函数，离线可测。** 实测那一行长这样：
        pid 52908(IINA): [0x...] 00:01:03 PreventUserIdleDisplaySleep \
named: "IINA playback is in progress"
    匹配得松一点（只要同一行同时出现 `(IINA)` 和 `playback is in progress`），
    免得 IINA 换版本改了断言前缀就失效。
    """
    for line in (pmset_text or "").splitlines():
        if "(IINA)" in line and "playback is in progress" in line:
            return True
    return False


def window_media_title() -> str:
    """屏幕上**可见的** IINA 窗口标题 —— 通常就是正在播的文件名。拿不到返回空串。

    ★ 为什么只取「屏幕上可见」的：完整窗口列表（`kCGWindowListOptionAll`）里
      连**隐藏窗口**都带着完整文件名（实测：层级第一位就是个隐藏窗口），
      那属于用户的私人内容 —— **遍历所有窗口在隐私上不可接受**。
      所以这里固定用 OnScreenOnly，绝不枚举全部窗口，**也绝不把标题写进日志**。

    ★ 为什么信它比信 IPC 靠谱：窗口标题天然对应"用户此刻正在看的那个窗口"，
      不受 IINA 内部 core 混乱的影响（IPC 会连到空转的 core，见上面那段注释）。
      ⚠️ 但**不能拿窗口面积当"主窗口"判据** —— 用户会切 IINA 的迷你播放器模式
      （实测同一个 window# 从 1440×600 缩成 285×503，编号都没变），
      小窗口才是他正在看的那个。所以这里取**层级最前的那个**（列表是前→后）。
    """
    try:
        import Quartz
    except Exception as exc:                        # pragma: no cover
        log.debug("窗口标题通道不可用（pyobjc/Quartz 缺失）: %s", exc)
        return ""
    try:
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
    except Exception as exc:                        # pragma: no cover
        log.debug("读窗口列表失败: %s", exc)
        return ""
    for w in (wins or []):
        if (w.get("kCGWindowOwnerName") or "") not in IINA_OWNER_NAMES:
            continue
        if not w.get("kCGWindowIsOnscreen"):
            continue
        title = (w.get("kCGWindowName") or "").strip()
        if not title:
            continue
        # ⚠️ IINA 里还常开着「OpenSubtitles」「User Scripts」「用户界面」这类
        #    设置/脚本窗口，标题**不是**媒体文件名 —— 必须筛掉，
        #    否则「Window — OpenSubtitles」会变成一本记录的名字。
        if _looks_like_media(title):
            return title
    return ""


# ================================================================= 控制器
class Controller:
    """统一的播放器控制入口。

    对外的语义很简单：
        status()        —— 现在在播吗？（拿不准就返回 None）
        set_paused(b)   —— 尽量把它设置成"停"或"播"
        toggle()        —— 切换（只在拿不到精确通道时才用）
    """

    def __init__(self) -> None:
        # 记住"这条通道上次为什么不可用"，避免每次按键都白试一遍
        self._iina_missing_logged = False
        self.last_backend = ""
        # 面板每 600ms 刷一次状态，而探测 IINA 要连一次 UDS —— 虽然很快，
        # 也没必要每刷一次就真探一次。给"给人看的说明"加一层短路缓存。
        self._describe_cache = ""
        self._describe_at = 0.0

    # ------------------------------------------------------------ 探活
    def iina_available(self) -> bool:
        # IPC 读不到时，电源断言也算"IINA 在跑"（见 status() 里的旁路说明）
        return _iina_playing() is not None or iina_playback_active() is not None

    def media_key_available(self) -> bool:
        if not config.MEDIA_KEY_FALLBACK:
            return False
        try:
            from Quartz import CGEventPost  # noqa: F401
        except Exception:
            return False
        return True

    # ------------------------------------------------------------ 读状态
    def status(self) -> dict:
        """返回 {"backend", "playing", "exact", "detail"}。

        只剩两条通道：IINA IPC（精确）→ 系统媒体键（读不到状态）。
        """
        if not config.PLAYER_CONTROL:
            return {"backend": "off", "playing": None, "exact": False,
                    "detail": "播放器控制已关闭"}

        if Path(config.IINA_SOCKET_PATH).exists():
            playing = _iina_playing()
            if playing is not None:
                return {"backend": "iina", "playing": playing, "exact": True,
                        "detail": "IINA（mpv IPC，可精确读写）"}
        elif not self._iina_missing_logged:
            self._iina_missing_logged = True
            log.info("IINA IPC socket 不存在（%s）—— 没在 IINA 里加过 "
                     "input-ipc-server 选项？跳过这条通道",
                     config.IINA_SOCKET_PATH)

        # ★ IPC 读不到状态（socket 不在 / 连到了**空转的 core**）→ 别急着说"不知道"。
        #   走不依赖 IPC 的旁路：电源断言。它给的 playing 是**可信的**，
        #   于是 set_paused 里"状态已符合就别动"的短路才能生效 ——
        #   而那个短路正是"按空格做出反向动作"的根治点。
        active = iina_playback_active()
        if active is not None:
            return {"backend": "iina-side", "playing": active, "exact": False,
                    "detail": "IINA（IPC 连不到正在播的 core，用电源断言读状态）"}

        if self.media_key_available():
            return {"backend": "mediakey", "playing": None, "exact": False,
                    "detail": "系统媒体键（万能，但只能切换、读不到状态）"}
        return {"backend": "none", "playing": None, "exact": False,
                "detail": "没有可用的播放器通道"}

    def is_playing(self) -> bool | None:
        return self.status().get("playing")

    # ------------------------------------------------------------ 写状态
    def set_paused(self, paused: bool) -> dict:
        """尽量把播放器设成「停」/「播」。

        返回 {"ok", "backend", "exact", "detail"}。
        exact=False 表示走的是媒体键"切换"—— 也就是**我们不能保证**结果状态，
        只是发了一次切换。调用方需要知道这个区别，才能决定要不要相信状态。
        """
        if not config.PLAYER_CONTROL:
            return {"ok": False, "backend": "off", "exact": False,
                    "detail": "播放器控制已关闭"}

        st = self.status()
        backend = st["backend"]

        # ★ 读得到状态、而且已经和目标一致 → 一次都不要发。
        #   对媒体键尤其关键（它是"切换"，多发一次就把刚停的又播起来）；
        #   对 IINA 也省一次无谓的 IPC。
        cur_playing = st.get("playing")
        if cur_playing is not None and cur_playing == (not paused):
            return {"ok": True, "backend": backend, "exact": True,
                    "detail": "状态已符合，无需操作"}

        # ★ 只有"IPC 精确可用"才用 IPC 去设置。iina-side 虽然名字里有 iina，
        #   但它的状态是从电源断言读来的 —— IPC 根本写不进去（连的是空转的 core），
        #   这种情况必须走媒体键。
        if backend == "iina" and st.get("exact"):
            ok = _iina_set_paused(paused)
            self.last_backend = "iina"
            if ok:
                log.info("播放器控制：IINA → %s", "暂停" if paused else "继续播放")
                return {"ok": True, "backend": "iina", "exact": True,
                        "detail": "IINA mpv IPC"}
            log.warning("IINA 设置 pause=%s 失败，降级到媒体键", paused)
            backend = "mediakey"

        if backend in ("iina-side", "mediakey", "none") and self.media_key_available():
            # 走到这里有两种情况：
            #  · iina-side：状态**读得到**（电源断言可信），只是 IPC 写不进去
            #    → 媒体键的方向一定对（"状态已符合"那道短路在上面拦过了）；
            #  · mediakey / none：状态读不到 → 只能盲发一次切换。
            if post_media_key():
                self.last_backend = "mediakey"
                log.info("播放器控制：系统媒体键切换（目标=%s，状态未知）",
                         "暂停" if paused else "继续播放")
                return {"ok": True, "backend": "mediakey", "exact": False,
                        "detail": "系统媒体键（切换，状态未知）"}
            return {"ok": False, "backend": "mediakey", "exact": False,
                    "detail": "合成媒体键失败（检查辅助功能权限）"}

        return {"ok": False, "backend": "none", "exact": False,
                "detail": "没有可用的播放器通道：" + str(st.get("detail", ""))}

    def toggle(self) -> dict:
        """切换播放/暂停。只有在读不到状态时才该用它。"""
        if not config.PLAYER_CONTROL:
            return {"ok": False, "backend": "off", "exact": False,
                    "detail": "播放器控制已关闭"}
        st = self.status()
        if st.get("playing") is not None:
            return self.set_paused(not st["playing"])
        if self.media_key_available() and post_media_key():
            self.last_backend = "mediakey"
            log.info("播放器控制：媒体键切换（状态未知）")
            return {"ok": True, "backend": "mediakey", "exact": False,
                    "detail": "系统媒体键（切换）"}
        return {"ok": False, "backend": st.get("backend", "none"), "exact": False,
                "detail": "拿不到状态，也没有媒体键可用"}

    # ------------------------------------------------------------ 给人看的
    def describe(self) -> str:
        """一句话说明当前能用哪条通道（面板上显示用）。"""
        if not config.PLAYER_CONTROL:
            return "⏸ 已关闭（按空格只查词，不动播放）"
        st = self.status()
        b = st["backend"]
        if b == "iina":
            return "✅ IINA（可精确读写在播/已停）"
        if b == "iina-side":
            return ("🟡 IINA 在跑，但 mpv IPC **连不到正在播的那个 core**"
                    "（IINA 内部多 core 抢同一个 socket）—— 在播/已停靠系统电源断言判断，"
                    "暂停与继续靠系统媒体键。**退出 IINA 再重开**即可恢复精确控制。")
        if b == "mediakey":
            # ★ 别把这条通道说得太乐观。实测（2026-09-15，本机 IINA 1.4.3）：
            #   系统媒体键会被 macOS 送给"当前占用 Now Playing"的那个 App，
            #   而**暂停状态的 IINA 收不到它**（CPU 采样证实没被唤醒）。
            #   也就是：媒体键能停、多半叫不醒 —— 对"关掉浮窗继续播"是致命的。
            #   所以这里必须明确告诉用户"配 IPC 才是正解"，不能只写"能切换"。
            hint = ("🟡 只能靠系统媒体键切换（读不到状态），"
                    "**可能无法恢复播放**")
            if Path(config.IINA_SOCKET_PATH).exists():
                hint += "｜IINA 的 socket 在，但读不到状态（没加载文件？）"
            else:
                hint += ("｜IINA 用户：退出 IINA 后点面板上的"
                         "「🛠 配置 IINA 高速通道」即可变成精确控制")
            return hint
        if not Path(config.IINA_SOCKET_PATH).exists():
            return ("⚠️ 没检测到播放器通道。IINA 用户请退出 IINA，"
                    "再点面板上的「🛠 配置 IINA 高速通道」（等价于在 "
                    "设置 → 高级 → 附加 mpv 选项 里加 input-ipc-server = "
                    f"{config.IINA_SOCKET_PATH}），然后重开 IINA。")
        return "⚠️ 没检测到可控制的播放器"

    def describe_cached(self) -> str:
        """和 describe() 一样，但**最多每 5 秒真探一次**。

        面板是每 600ms 刷一次的状态轮询，真探一次要连一次 IINA 的 UDS socket。
        不缓存的话，光开着设置面板就会每秒连两次，纯属白烧 CPU。
        诊断按钮走的是不带缓存的 describe()/diagnose()，不受影响。
        """
        now = time.time()
        if self._describe_cache and (now - self._describe_at) < 5.0:
            return self._describe_cache
        try:
            self._describe_cache = self.describe()
            self._describe_at = now
        except Exception:
            log.debug("刷新播放器说明失败", exc_info=True)
        return self._describe_cache or "⚠️ 播放器状态未知"

    def diagnose(self) -> str:
        """多行诊断报告，排查用（面板上的「检测播放器」按钮）。"""
        has_socket = Path(config.IINA_SOCKET_PATH).exists()
        lines = [
            f"播放器控制总开关：{'开' if config.PLAYER_CONTROL else '关'}",
            f"IINA IPC socket（{config.IINA_SOCKET_PATH}）："
            f"{'存在' if has_socket else '不存在'}",
        ]
        if has_socket:
            p = _iina_playing()
            lines.append(f"  · IPC 读到 IINA 状态："
                         f"{'在播' if p else ('已停' if p is False else '读不到（可能连到了空转的 core）')}")
        act = iina_playback_active(force=True)
        lines.append(f"  · 电源断言（不依赖 IPC）："
                     f"{'正在播放' if act else ('已暂停' if act is False else '问不到')}")
        # 注意：这里**只报通道可用性，不打印标题内容** —— 标题就是文件名，
        # 可能包含用户私人视频的名字，不进日志（隐私红线）。
        lines.append("  · 窗口标题通道："
                     f"{'可用' if window_media_title() else '没读到正在播的窗口标题'}")
        lines.append(f"媒体键兜底：{'可用' if self.media_key_available() else '不可用'}")
        lines.append(f"当前生效通道：{self.describe()}")
        return "\n".join(lines)


# ------------------------------------------------------------------ 便捷函数
_CTL: Controller | None = None


def controller() -> Controller:
    """进程内单例（它没有可变状态需要保护，只是省得重复探测）。"""
    global _CTL
    if _CTL is None:
        _CTL = Controller()
    return _CTL


# ------------------------------------------------------------------ 前台应用
def frontmost_bundle_id() -> str:
    """当前前台应用的 bundle id（读不到就空串）。

    为什么要问它：空格这一下只跟"用户此刻在看谁"有关 —— 只有当用户真的
    对着播放器按空格时，我们才接管它。别的时候（在 Word / Slack 里打字）
    空格必须原样是空格。

    ⚠️ 会被事件识别回调调用，必须快 —— NSWorkspace 这个调用是 AppKit
    内部缓存过的，正常在微秒级。
    """
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.bundleIdentifier() or "") if app is not None else ""
    except Exception:
        return ""


def frontmost_backend() -> str:
    """当前前台应用对应哪条播放器通道（"iina" / "generic" / ""）。

    空串 = 前台不是已知播放器（比如用户切到了 Finder / Word / 浏览器）。
    """
    try:
        return config.PLAYER_BUNDLE_IDS.get(frontmost_bundle_id(), "")
    except Exception:
        return ""
