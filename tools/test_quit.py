"""「退出应用」必须真的退掉（无头，不弹窗口）。

为什么单独立一个文件：实测踩过一次很难查的坑 ——
菜单点了「退出」，日志明明打了 `退出应用`，但 App 一直挂在 Dock 里、
再点 Dock 图标还能把面板叫回来。根因是两条：
  1. `QtRuntime.quit()` 只把 `app.quit` 塞进队列，那一刻没能让 `exec()` 返回，
     队列里那条命令执行完就没了下文；
  2. `App.quit()` 先 `worker.stop()` 再发退出请求 —— 万一 stop 卡住，
     退出请求根本发不出去。

这个文件把两条都钉住：
  · 退出请求要**直接调** app.quit()，不能只排队
  · 要有看门狗，超时未退出就强制结束进程

★ v1.2.2：网页版豆包删除后 `App.quit()` 里**没有 worker 可收尾了**，
  只剩"调 on_quit_hook"。所以第 3、4 节改成钉新的契约：
    · 有 hook 就调它、没 hook 也不能炸；
    · hook 自己抛异常时**不许把异常漏出去**（否则菜单回调里会炸成崩溃）。

跑法：
    QT_QPA_PLATFORM=offscreen python tools/test_quit.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DL_SETTINGS_DIR", tempfile.mkdtemp(prefix="dl_test_quit_"))
# ★ 本进程里把看门狗调得极大：quit() 会真的起一个"超时就 os._exit(0)"的线程，
# 用它默认的 12 秒会把跑测试的自己杀掉（踩过）。子进程里再单独调小来测它。
os.environ.setdefault("DL_QUIT_WATCHDOG_SEC", "3600")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


class _FakeApp:
    """只有 quit() 的假 QApplication，用来记录"有没有被直接调用"。"""

    def __init__(self) -> None:
        self.calls = 0

    def quit(self) -> None:
        self.calls += 1


def main() -> int:
    import overlay

    print("== 1. QtRuntime.quit()：只排队（交给主线程）+ 起看门狗 ==")
    rt = overlay.QtRuntime(on_overlay_closed=lambda: None)
    fake = _FakeApp()
    rt._app = fake
    before = [t.name for t in __import__("threading").enumerate()]
    rt.quit()
    # ★ 这条是本次踩坑的核心：**绝不能**在这里直接调 app.quit()。
    # 在非主线程调会把 Cocoa 事件循环整个锁死（调用不返回、exec 不返回、
    # 连主线程的 QTimer 都不再触发）。
    check("没有在调用方线程直接调 app.quit()", fake.calls, 0)
    try:
        cmd, payload = rt._q.get_nowait()
        check("把 app.quit 放进了队列（由主线程 _pump 执行）", cmd, "call")
    except Exception as exc:  # noqa: BLE001
        check("把 app.quit 放进了队列（由主线程 _pump 执行）", f"队列空: {exc}", "call")
    after = [t.name for t in __import__("threading").enumerate()]
    check("起了看门狗线程", "quit-watchdog" in after and "quit-watchdog" not in before, True)

    print("\n== 2. app 还没就绪时不能崩 ==")
    rt2 = overlay.QtRuntime(on_overlay_closed=lambda: None)
    rt2._app = None
    try:
        rt2.quit()
        check("_app=None 不抛异常", True, True)
    except Exception as exc:  # noqa: BLE001
        check("_app=None 不抛异常", f"{type(exc).__name__}: {exc}", True)

    print("\n== 3. App.quit()：调退出钩子 ==")
    import app as app_mod

    order: list[str] = []

    inst = app_mod.App.__new__(app_mod.App)      # 绕过 __init__，只测 quit()
    inst.on_quit_hook = lambda: order.append("quit_hook")
    inst.quit()
    check("退出钩子被调用", order, ["quit_hook"])

    inst_nohook = app_mod.App.__new__(app_mod.App)
    inst_nohook.on_quit_hook = None
    try:
        inst_nohook.quit()
        check("没有钩子也不抛异常", True, True)
    except Exception as exc:                     # noqa: BLE001
        check("没有钩子也不抛异常", f"{type(exc).__name__}: {exc}", True)

    print("\n== 4. 退出钩子自己抛异常时，不许把异常漏出去 ==")
    # 钩子（QtRuntime.quit）内部出问题是可能的；它一漏，异常会顺着
    # 菜单回调 / 托盘回调往上冒 —— 在 PyQt 里那就是 qFatal → 整个进程崩。
    inst_bad = app_mod.App.__new__(app_mod.App)

    def _boom() -> None:
        raise RuntimeError("模拟退出钩子内部出错")

    inst_bad.on_quit_hook = _boom
    try:
        inst_bad.quit()
        check("钩子抛异常被吞掉，quit() 正常返回", True, True)
    except Exception as exc:                     # noqa: BLE001
        check("钩子抛异常被吞掉，quit() 正常返回",
              f"{type(exc).__name__}: {exc}", True)

    print("\n== 5. 看门狗真的会强制退出进程（子进程实测）==")
    code = (
        "import os, sys, time;"
        "sys.path.insert(0, %r);"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "os.environ['DL_QUIT_WATCHDOG_SEC']='1';"
        "import overlay;"
        "rt=overlay.QtRuntime(on_overlay_closed=lambda: None);"
        "rt._app=type('A',(),{'quit':lambda self: None})();"
        "rt.quit();"
        "time.sleep(60);"          # 故意不退出，等看门狗来收
        "print('不应该走到这里')"
    ) % str(ROOT)
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=30)
    took = time.time() - t0
    check("1 秒看门狗生效，进程被杀掉", proc.returncode, 0)
    check("  没等到 60 秒", took < 20, True)
    check("  没走到 sleep 之后（说明是被强杀的）",
          "不应该走到这里" in proc.stdout, False)

    print("\n== 6. 真 Qt 事件循环：quit() 能不能让 exec() 返回 ==")
    # 前面几条测的是"命令发出去了"，这条测真正的问题：
    # 在真实 QApplication + 真 exec() 里，quit() 之后 run_blocking 会不会返回。
    # 看门狗设 8 秒：如果 8 秒内没自己退出，进程会被强杀，输出里就没有 EXITED。
    code6 = (
        "import os, sys, threading, time;"
        "sys.path.insert(0, %r);"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "os.environ['DL_QUIT_WATCHDOG_SEC']='8';"
        "import overlay;"
        "rt=overlay.QtRuntime(on_overlay_closed=lambda: None);"
        "threading.Thread(target=lambda: (time.sleep(2.0), rt.quit()), daemon=True).start();"
        "t0=time.time();"
        "rt.run_blocking();"
        "print('EXITED %%.2f' %% (time.time()-t0));"
        "sys.stdout.flush();"
        "os._exit(0)"
    ) % str(ROOT)
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, "-c", code6], capture_output=True,
                              text=True, timeout=40)
        out = proc.stdout or ""
        took = time.time() - t0
        check("exec() 正常返回（不是靠看门狗强杀）", "EXITED" in out, True)
        check("  退出很快（2 秒请求 + 余量）", took < 8, True)
        print("     子进程输出:", out.strip().replace("\n", " | ") or "(空)")
    except subprocess.TimeoutExpired:
        check("exec() 正常返回（不是靠看门狗强杀）", "超时 40 秒仍未结束", "EXITED")

    print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
