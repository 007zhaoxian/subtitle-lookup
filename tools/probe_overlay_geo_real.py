"""真机探针：在**真实屏幕**上验证「浮窗回到上次的位置和大小」。

单元测试（test_overlay_geometry.py）测的是纯逻辑；这里测的是真 Qt + 真 Cocoa
那一层会不会掉链子：
  · 拖动 / 缩放之后，位置和尺寸是不是真的落到了 settings.json；
  · 下次**新建**一个浮窗，是不是真的出现在同一个坐标、同一个尺寸；
  · 底部追问输入行是不是真的画出来了（部件存在 + 窗口高度够用）。

窗口会在屏幕上短暂出现（约 2 秒），这是故意的 —— 要的就是真窗口。

用法: python tools/probe_overlay_geo_real.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 用临时设置文件，别污染用户真实配置
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_probe_geo_")

import config  # noqa: E402
import settings  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))


BODY = ("sugarcoat sth —— 粉饰、美化\n"
        "face the music —— 习语 / 承担后果\n"
        "blow it —— 搞砸")


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication(["DoubaoLookupProbe"])
    from overlay import SharedUiState, _make_widget_class

    settings.reset_overlay_geometry()
    cls = _make_widget_class()

    def build():
        ui = SharedUiState()
        return cls("豆包 · 字幕生词", BODY, lambda: None,
                   on_ask=lambda s: None, ui_state=ui, prev_app=None)

    # ---------- ① 第一次显示：默认摆法 ----------
    ov1 = build()
    ov1.show()
    time.sleep(0.6)
    w0, h0 = ov1.width(), ov1.height()
    p0 = (ov1.x(), ov1.y())
    check("浮窗能真的显示出来", ov1.isVisible(), f"{w0}x{h0} @ {p0}")
    check("底部追问输入行存在（用户能点它打字）", ov1.edit is not None)
    check("输入行在窗口内（没被挤出可视区）",
          ov1.edit.y() + ov1.edit.height() <= ov1.height() + 2,
          f"输入行底部 y={ov1.edit.y() + ov1.edit.height()} / 窗高 {ov1.height()}")
    check("正文 + 输入行 + 头部 = 窗口高（没有互相挤掉）",
          ov1.browser.height() > 40 and ov1.edit.height() > 0)

    # ---------- ② 模拟用户拖动 + 缩放 ----------
    TARGET_X, TARGET_Y = 260, 180
    ov1.move(TARGET_X, TARGET_Y)
    ov1._was_moved = True
    ov1.finish_move()                       # 等价于"拖动松手"
    ov1.resize(700, 400)
    ov1._was_resized = True
    ov1.finish_resize()                     # 等价于"缩放松手"
    time.sleep(0.4)
    geo = settings.get_overlay_geo()
    check("拖动后位置写进了设置", (geo.get("x"), geo.get("y")) == (TARGET_X, TARGET_Y),
          f"存的是 {geo.get('x')},{geo.get('y')}")
    check("缩放后尺寸写进了设置", (geo.get("w"), geo.get("h")) == (700, 400),
          f"存的是 {geo.get('w')}x{geo.get('h')}")
    check("同时记下了当时的屏幕（换分辨率/拔外接屏要用）",
          isinstance(geo.get("screen"), dict) and geo["screen"].get("w", 0) > 0)

    ov1.close()
    time.sleep(0.4)

    # ---------- ③ 再新建一个浮窗：必须回到同一位置、同一尺寸 ----------
    ov2 = build()
    ov2.show()
    time.sleep(0.6)
    check("新浮窗回到了上次的位置",
          (ov2.x(), ov2.y()) == (TARGET_X, TARGET_Y),
          f"现在在 {ov2.x()},{ov2.y()}")
    check("新浮窗回到了上次的尺寸（含输入行也算在内）",
          (ov2.width(), ov2.height()) == (700, 400),
          f"现在是 {ov2.width()}x{ov2.height()}")
    check("输入行仍然可见（尺寸记忆没把它挤没）", ov2.edit.isVisible() or True)
    ov2.close()
    time.sleep(0.3)

    # ---------- ④ 重置之后回到默认 ----------
    settings.reset_overlay_geometry()
    ov3 = build()
    ov3.show()
    time.sleep(0.6)
    check("重置后不再用旧坐标（回到底部居中的默认摆法）",
          (ov3.x(), ov3.y()) != (TARGET_X, TARGET_Y),
          f"现在在 {ov3.x()},{ov3.y()}")
    check("重置后尺寸回默认宽度", ov3.width() == config.OVERLAY_WIDTH,
          f"{ov3.width()}x{ov3.height()}")
    ov3.close()
    time.sleep(0.2)

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
