"""把真实浮窗截下来看 —— 验证「输入行 + 追问回答」在屏幕上的实际观感。

会真的在屏幕上显示一个浮窗（约 3 秒），然后用系统的 screencapture 抓它，
存到日志目录。为什么要这一步：部件存在 ≠ 画得对（本项目踩过毛玻璃让文字
整个消失的坑，widget.grab() 里明明有字）。所以最终一定看真屏幕。

用法: python tools/probe_overlay_shot.py [输出目录]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_shot_")

import config  # noqa: E402
import settings  # noqa: E402

BODY = ("sugarcoat sth —— 粉饰、美化（坏消息）；不委婉掩饰真相\n"
        "face the music —— 习语，承担后果、接受责罚\n"
        "blow it —— 搞砸，把事情弄砸（非正式高频口语）")


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (config.LOG_DIR / "shots")
    out_dir.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtWidgets import QApplication

    # 注意：必须留一个强引用，否则 QApplication 会被 GC 掉，
    # 之后建任何 QWidget 都会报 "Must construct a QApplication before a QWidget"。
    qapp = QApplication.instance() or QApplication(["DoubaoLookupShot"])
    assert qapp is not None
    from overlay import SharedUiState, _make_widget_class

    settings.reset_overlay_geometry()
    asked: list[str] = []
    cls = _make_widget_class()
    ov = cls("豆包 · 字幕生词", BODY, lambda: None,
             on_ask=asked.append, ui_state=SharedUiState(), prev_app=None)
    ov.show()
    ov.raise_()
    time.sleep(1.0)

    shots = []

    def shoot(tag: str) -> Path:
        p = out_dir / f"{tag}.png"
        try:
            subprocess.run(["/usr/sbin/screencapture", "-x", "-R",
                            f"{ov.x()},{ov.y()},{ov.width()},{ov.height()}", str(p)],
                           check=False, timeout=15)
        except Exception as exc:
            print("  截图失败:", exc)
        return p

    # ① 刚出来的样子（能看到提示语和输入行）
    time.sleep(0.4)
    shots.append(shoot("1-浮窗初始（含输入行）"))

    # ② 用户点了一下、打了字的样子
    ov.enter_input()
    ov.edit.setText("这句里的 face the music 是什么语气？")
    time.sleep(0.6)
    shots.append(shoot("2-点一下可以打字"))
    ov.exit_input()

    # ③ 追问 + 回答追加在下面
    ov.edit.setText("这句里的 face the music 是什么语气？")
    ov.edit.returnPressed.emit()
    time.sleep(0.4)
    ov.deliver_answer("略带无奈、认命的语气，不是书面语。\n"
                      "同义口语：face the consequences / take the heat")
    time.sleep(0.8)
    shots.append(shoot("3-追问和回答追加显示"))
    time.sleep(0.4)
    ov.close()

    for p in shots:
        print(f"{'✓' if p.exists() and p.stat().st_size > 0 else '✗'} {p}"
              f"  ({p.stat().st_size if p.exists() else 0} 字节)")
    print(f"\n问了：{asked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
