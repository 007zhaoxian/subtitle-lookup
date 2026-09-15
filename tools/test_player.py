"""播放器控制层测试 —— **完全离线**，不需要真的开着 IINA。

覆盖：
  1. IINA mpv IPC 协议：用假 socket 起一个迷你 mpv，跑真的收发
  2. IINA socket 不存在时要干净地返回 None，不能抛异常
  3. Controller 的通道选择与"已知状态就不重复切换"的保护
  4. 总开关关掉时的行为
  5. 媒体键事件构造（dry_run，绝不真派发）
  6. describe_cached 的短路缓存

★ v1.2.0：浏览器的在线视频通道（网页视频桥 + AppleScript 执行 JS）
  已整体删除，所以本测试不再覆盖 JS 常量与 osascript 错误分类。

跑法（必须用带 pyobjc/PyQt6 的那个解释器）：
    python3 tools/test_player.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config                                                    # noqa: E402
import player                                                    # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: 实际 {got!r}，期望 {want!r}")
        FAILED.append(name)


def check_true(name: str, got) -> None:
    check(name, bool(got), True)


# ================================================= 1. IINA mpv IPC
print("\n== 1. IINA mpv IPC（假 socket 模拟 mpv）==")


class FakeMpv:
    """极简 mpv JSON IPC 服务器：够用来验协议解析与状态机。

    ⚠️ 千万别写成 `threading.Thread` 的子类。Python 3.13 的
    `Thread.__init__` 会往实例上塞 `self._handle`（一个 _ThreadHandle）
    和一个 `self._stop` 方法；而我们刚好也需要叫 `_handle` / `_stop`。
    子类里定义的同名方法会被这两个**实例属性**盖掉，于是服务端在处理请求时
    抛 `TypeError: '_thread._ThreadHandle' object is not callable`，
    连接被 `except: continue` 吞掉，客户端一个字节都收不到 ——
    现象是"客户端 recv 返回 b''，整条通道像是坏的"，极难定位。
    所以改成"组合一个线程"，并且方法名避开 Thread 的保留名。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.pause = False              # mpv 语义：pause=False 表示"正在播"
        self.idle = False
        self.unknown_prop = False       # 把 pause 变成"读不到"，用来测降级
        self.seen_writes: list = []
        self.ready = threading.Event()
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    # ----------------------------------------------------------- 生命周期
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._thread.join(timeout=2.0)

    # ----------------------------------------------------------- 服务端
    def _serve(self) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(self.path)
            srv.listen(8)
            srv.settimeout(0.3)
            self.ready.set()
            while not self._stop_evt.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with conn:
                    conn.settimeout(1.0)
                    buf = b""
                    try:
                        while True:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            buf += chunk
                            while b"\n" in buf:
                                line, buf = buf.split(b"\n", 1)
                                if not line.strip():
                                    continue
                                resp = self.reply(json.loads(line.decode()))
                                conn.sendall((json.dumps(resp) + "\n").encode())
                    except Exception:
                        continue
        finally:
            try:
                srv.close()
            except Exception:
                pass

    def reply(self, req: dict) -> dict:
        cmd = req.get("command") or []
        if not cmd:
            return {"error": "no command"}
        if cmd[0] == "get_property":
            prop = cmd[1] if len(cmd) > 1 else ""
            if prop == "idle-active":
                return {"data": self.idle, "error": "success"}
            if prop == "pause":
                if self.unknown_prop:
                    return {"error": "property unavailable"}
                return {"data": self.pause, "error": "success"}
        if cmd[0] == "set_property":
            prop, val = cmd[1], (cmd[2] if len(cmd) > 2 else None)
            self.seen_writes.append((prop, val))
            if prop == "pause":
                self.pause = bool(val)
            return {"error": "success"}
        return {"error": "unsupported"}


tmpdir = tempfile.mkdtemp(prefix="dl_player_test_")
sock_path = os.path.join(tmpdir, "fake-mpv.sock")
fake = FakeMpv(sock_path)
fake.start()
if not fake.ready.wait(3):
    print("FAIL  假 mpv 服务端 3 秒内没起来")
    sys.exit(1)
time.sleep(0.1)

_orig_socket = config.IINA_SOCKET_PATH
config.IINA_SOCKET_PATH = sock_path

check("socket 文件建出来了", Path(sock_path).exists(), True)

fake.pause = False
check("读到 IINA 在播状态（pause=False → playing=True）",
      player._iina_playing(), True)
fake.pause = True
check("读到 IINA 已停（pause=True → playing=False）",
      player._iina_playing(), False)

check("set_property pause True 返回 True",
      player._iina_set_paused(True), True)
check("服务端真的收到了写请求", fake.seen_writes[-1], ("pause", True))
check("写完之后状态变了", fake.pause, True)

fake.idle = True
check("没加载文件（idle-active）时返回 None，不乱控制", player._iina_playing(), None)
fake.idle = False
fake.unknown_prop = True
check("属性读不到时返回 None（触发降级）", player._iina_playing(), None)
fake.unknown_prop = False

# socket 不存在
config.IINA_SOCKET_PATH = os.path.join(tmpdir, "nope.sock")
check("socket 不存在时 _iina_cmd 返回 None", player._iina_cmd(["get_property", "pause"]), None)
check("socket 不存在时 _iina_playing 返回 None，不抛异常",
      player._iina_playing(), None)


# ================================================ 2. Controller 行为
print("\n== 2. Controller 通道选择与保护 ==")

config.IINA_SOCKET_PATH = sock_path

# 让 IINA 处于"已暂停"，作为本节的起点
fake.pause = True
fake.seen_writes.clear()

cfg = player.Controller()

st = cfg.status()
check("有 IINA socket 时优选用 IINA", st["backend"], "iina")
check("IINA 通道是精确的", st["exact"], True)
check("读到已停（起点 pause=True）", st["playing"], False)

# ★ 关键保护：状态已知且已经符合目标时，**一次都不要发**
before = list(fake.seen_writes)
r = cfg.set_paused(True)
check("目标已是'暂停'时不再重复操作", fake.seen_writes, before)
check("并且明确说清了原因", "无需操作" in r["detail"], True)

r = cfg.set_paused(False)
check("需要真继续播时会真的写", fake.seen_writes[-1], ("pause", False))
check("返回 ok", r["ok"], True)
check("返回走的是 IINA", r["backend"], "iina")
check("写完之后 IINA 真的在播了", fake.pause, False)

# 反方向再来一遍，确认保护是双向的、不是碰巧
before = list(fake.seen_writes)
r = cfg.set_paused(False)
check("已在播时再要求播也不重复写", fake.seen_writes, before)
check("同样给了'无需操作'的说明", "无需操作" in r["detail"], True)

r = cfg.set_paused(True)
check("要求暂停时会真的写", fake.seen_writes[-1], ("pause", True))

check_true("describe() 里能看到 IINA", "IINA" in cfg.describe())


# 掉进只有媒体键的场景
#
# ★ 这里必须把「电源断言旁路」桩掉，否则测试会**跟着用户机器上真实是否在播
#   而变化**：用户正开着 IINA 看片时，iina_playback_active() 返回 True，
#   status() 就会走 "iina-side" 而不是降级到媒体键 —— 同一份代码，用户在看片
#   就红、没看片就绿。2026-09-15 实际踩到过（用户恰好在看视频）。
player.iina_playback_active = lambda force=False: None   # 假装读不到 IINA 状态

config.IINA_SOCKET_PATH = os.path.join(tmpdir, "nope.sock")
cfg2 = player.Controller()
st2 = cfg2.status()
check("没有 IINA socket 时降级到媒体键", st2["backend"], "mediakey")
check("媒体键通道不精确", st2["exact"], False)
check("媒体键读不到状态", st2["playing"], None)


# ================================================ 3. 总开关
print("\n== 3. 总开关 ==")
config.PLAYER_CONTROL = False
try:
    off = player.Controller()
    check("关掉后 status 返回 off", off.status()["backend"], "off")
    check("关掉后 set_paused 不做事", off.set_paused(True)["ok"], False)
    check_true("describe 说清了已关闭", "关闭" in off.describe())
finally:
    config.PLAYER_CONTROL = True


# ================================================ 4. 媒体键构造
print("\n== 4. 媒体键（dry_run，绝不真派发）==")
check("事件构造成功", player.post_media_key(dry_run=True), True)
check("NX_KEYTYPE_PLAY 是 16", player.NX_KEYTYPE_PLAY, 16)
check("NSSystemDefined 是 14", player.NSSYSTEM_DEFINED, 14)


# ================================================ 5. 面板用的缓存版说明
print("\n== 5. describe_cached：别让状态轮询反复连 IINA 的 socket ==")
_c = player.Controller()
_probes = {"n": 0}
_real_describe = _c.describe


def _counting_describe():
    _probes["n"] += 1
    return _real_describe()


_c.describe = _counting_describe
_c.describe_cached()
check("第一次会真探一次", _probes["n"], 1)
_c.describe_cached()
_c.describe_cached()
check("紧接着再问两次都走缓存（面板 600ms 刷一次也不会起火）", _probes["n"], 1)
_c._describe_at = 0.0            # 手动让它过期
_c.describe_cached()
check("缓存过期后会再真探一次", _probes["n"], 2)


# ================================================ 清理
fake.stop()
config.IINA_SOCKET_PATH = _orig_socket
try:
    os.unlink(sock_path)
    os.rmdir(tmpdir)
except Exception:
    pass

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项: {FAILED}")
    sys.exit(1)
print("全部通过")
sys.exit(0)
