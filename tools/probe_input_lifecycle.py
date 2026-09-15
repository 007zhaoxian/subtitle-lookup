"""真机探针：浮窗走完「显示 → 点输入框 → 打字 → 发送 → 收到回答」后，
每一步它到底还在不在屏幕上、在哪儿、多大。

【为什么需要它】踩过坑：`--demo-ask` 的日志一切正常（发送、收到回答、追问回答），
但 `screencapture` 抓的图里**浮窗根本不在**。日志说「在」、屏幕说「不在」——
这种分歧只能靠把窗口自己的状态打出来才分得清。

用法:  python tools/probe_input_lifecycle.py [截图输出目录]
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 用独立的设置目录，别动用户真实配置
os.environ["DL_SETTINGS_DIR"] = "/tmp/dl_probe_input_settings"
shutil.rmtree("/tmp/dl_probe_input_settings", ignore_errors=True)

SHOT_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/dl_probe_input")
SHOT_DIR.mkdir(parents=True, exist_ok=True)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication(["DoubaoLookupProbe"])
    from overlay import SharedUiState, _make_widget_class, _float_over_everything

    ui = SharedUiState()
    cls = _make_widget_class()
    asked: list[str] = []

    ov = cls(
        "豆包 · 字幕生词",
        "第一句 —— 这是截图里查出来的生词与俚语说明。\n"
        "face the music —— 习语 / 承担后果。",
        lambda: None,
        on_ask=asked.append,
        ui_state=ui,
        prev_app=None,
    )
    ov.show()
    _float_over_everything(ov)
    _pump(app, 0.6)

    def snap(tag: str) -> tuple[bool, int, int, int, int]:
        vis = ov.isVisible()
        g = ov.geometry()
        _pump(app, 0.25)
        ov.grab().save(str(SHOT_DIR / f"{tag}.png"))
        return vis, g.x(), g.y(), g.width(), g.height()

    # ① 刚显示
    vis, x, y, w, h = snap("1-刚显示")
    check("① 显示后浮窗可见", vis, f"({x},{y}) {w}x{h}")

    # ② 点一下输入框（进入输入态）
    ov.enter_input()
    _pump(app, 0.6)
    vis, x, y, w, h = snap("2-进入输入态")
    check("② 进入输入态后浮窗仍可见（改 windowFlag 会临时隐藏，必须 show 回来）",
          vis, f"({x},{y}) {w}x{h}")
    check("② 输入态已生效", ui.typing())

    # ③ 打字
    ov.edit.setText("这句里的 face the music 是什么语气？")
    _pump(app, 0.3)
    vis, x, y, w, h = snap("3-已打字")
    check("③ 打字后浮窗仍可见", vis, f"({x},{y}) {w}x{h}")
    check("③ 发送按钮已启用", ov.btn_send.isEnabled())

    # ④ 发送（这一步内部会 exit_input → 归还键盘焦点）
    ov._on_send()
    _pump(app, 0.8)
    vis, x, y, w, h = snap("4-已发送-等待回答")
    check("④ 发送后浮窗仍可见（exit_input 改回 flag 后必须 show 回来）",
          vis, f"({x},{y}) {w}x{h}")
    check("④ 问题确实交给了 App 层", asked == ["这句里的 face the music 是什么语气？"],
          f"asked={asked}")
    check("④ 输入态已退出（键盘还给播放器）", not ui.typing())

    # ⑤ 回答回来
    ov.deliver_answer("face the music 偏「自作自受、接受追责」，语气比 blow it 重。")
    _pump(app, 0.8)
    vis, x, y, w, h = snap("5-回答已追加")
    check("⑤ 回答追加后浮窗仍可见（关键：这条挂了就等于用户「问了没反应」）",
          vis, f"({x},{y}) {w}x{h}")

    body = ov.browser.toPlainText()
    check("⑤ 回答真的出现在浮窗正文里", "自作自受" in body,
          f"正文长度={len(body)}")
    check("⑤ 问题也显示在浮窗里", "face the music 是什么语气" in body)

    ov.close()
    _pump(app, 0.4)

    print("=" * 62)
    print("浮窗输入生命周期探针")
    print("=" * 62)
    bad = 0
    for name, ok, detail in RESULTS:
        print(f"  {'✅ PASS' if ok else '❌ FAIL'}  {name}")
        if detail:
            print(f"          {detail}")
        if not ok:
            bad += 1
    print("-" * 62)
    print(f"共 {len(RESULTS)} 项，失败 {bad} 项")
    print(f"截图目录: {SHOT_DIR}")
    return 1 if bad else 0


def _pump(app, sec: float) -> None:
    """跑一会儿 Qt 事件循环，让布局/重绘真正发生。"""
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


if __name__ == "__main__":
    raise SystemExit(main())
