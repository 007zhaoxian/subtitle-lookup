"""真机探针：浮窗到底"浮"在哪一层？会不会掉到别的 App 窗口后面？

【为什么需要它】踩过一个大坑：
  浮窗本身是可见的（Qt 层 `isVisible()` 一直 True、`grab()` 也画得出内容），
  但**屏幕上、在别的 App 窗口前面根本看不到它** —— 因为 macOS 的窗口层级
  （NSWindow.level）悄悄被重置成了普通窗口。

  成因：`enter_input()` / `exit_input()` 里为了在"能打字 / 不抢键盘"之间切换，
  调了 `setWindowFlag(WindowDoesNotAcceptFocus, ...)`。Qt 在**可见状态下改窗口
  flag 会销毁并重建原生窗口**，之前用 pyobjc 设好的
  `level = NSStatusWindowLevel(25)`、collectionBehavior 全部丢失 →
  浮窗掉到普通层级，被前台窗口（比如 WorkBuddy / 播放器）盖住。

  Qt 的 `isVisible()` 和 `grab()` 都发现不了这件事 —— 它们在"窗口自己的世界"里
  看问题。只有去问窗口服务器（WindowServer）才知道真实层级。

判据：`kCGWindowLayer == 25` 就是 NSStatusWindowLevel（正确浮起）；
      掉到 0 说明它已经变成普通窗口，会被前台 App 盖住。

用法:  python tools/probe_window_layer.py [被测命令...]
       默认被测命令: python main.py --demo-ask
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import Quartz

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATUS_LEVEL = 25          # NSStatusWindowLevel

RESULTS: list[str] = []


def own_windows(pid: int) -> list[dict]:
    """列出这个进程当前在屏幕上的所有窗口（含层级）。"""
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    infos = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    out = []
    for w in infos:
        if int(w.get("kCGWindowOwnerPID", -1)) != pid:
            continue
        b = w.get("kCGWindowBounds") or {}
        out.append({
            "layer": int(w.get("kCGWindowLayer", -1)),
            "name": str(w.get("kCGWindowName") or ""),
            "w": int(b.get("Width", 0)),
            "h": int(b.get("Height", 0)),
            "x": int(b.get("X", 0)),
            "y": int(b.get("Y", 0)),
        })
    return out


def frontmost_owner() -> str:
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    for w in infos:
        if int(w.get("kCGWindowLayer", 0)) == 0:
            return str(w.get("kCGWindowOwnerName") or "?")
    return "?"


def main() -> int:
    cmd = sys.argv[1:] or [
        str(pathlib.Path(os.environ.get("DL_PY", sys.executable))),
        str(ROOT / "main.py"), "--demo-ask",
    ]
    duration = float(os.environ.get("DL_PROBE_SEC", "75"))

    env = dict(os.environ)
    env.setdefault("DL_SETTINGS_DIR",
                   str(pathlib.Path.home() / "Library" / "Application Support"
                       / "DoubaoLookup"))

    print(f"被测命令: {' '.join(cmd)}")
    print(f"观察时长: {duration:.0f}s\n")

    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = proc.pid
    t0 = time.time()

    prev = None
    timeline: list[tuple[float, list[dict], str]] = []
    try:
        while time.time() - t0 < duration and proc.poll() is None:
            wins = own_windows(pid)
            # 只关心有像样尺寸的窗口（过滤 0x0 的辅助窗口）
            wins = [w for w in wins if w["w"] > 100 and w["h"] > 60]
            key = tuple(sorted((w["layer"], w["w"], w["h"]) for w in wins))
            if key != prev:
                t = time.time() - t0
                timeline.append((t, wins, frontmost_owner()))
                print(f"  t={t:5.1f}s  前台={frontmost_owner():<14} 我们的窗口:")
                if not wins:
                    print("            （无）")
                for w in wins:
                    flag = "← 正确浮起" if w["layer"] == STATUS_LEVEL else \
                           ("← 普通层级(会被盖住)" if w["layer"] == 0 else "")
                    print(f"            layer={w['layer']:<4} {w['w']}x{w['h']} "
                          f"@({w['x']},{w['y']}) {w['name'][:20]!r} {flag}")
                prev = key
            time.sleep(0.4)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print("\n" + "=" * 62)
    print("结论")
    print("=" * 62)

    # 找出出现过浮窗尺寸的那些时刻
    overlays = [(t, w) for t, wins, _ in timeline for w in wins if w["w"] > 200]
    bad = 0
    if not overlays:
        print("  ❌ 整个观察期都没抓到我们的浮窗窗口")
        bad += 1
    else:
        levels = sorted({w["layer"] for _, w in overlays})
        sunk = [t for t, w in overlays if w["layer"] == 0]
        print(f"  浮窗出现过的层级: {levels}")
        if sunk:
            print(f"  ❌ 有 {len(sunk)} 次掉到 layer=0（普通窗口，会被前台 App 盖住）")
            print(f"     最早发生在 t={min(sunk):.1f}s")
            bad += 1
        else:
            print(f"  ✅ 浮窗全程保持 layer={STATUS_LEVEL}（NSStatusWindowLevel，浮在前台之上）")

        # 记录窗口数量的变化（改 flag 会重建原生窗口）
        counts = [len([w for w in wins if w["w"] > 200]) for _, wins, _ in timeline]
        print(f"  浮窗数量变化序列: {counts}（>1 说明出现过重复窗口）")

    print(f"\n失败项: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
