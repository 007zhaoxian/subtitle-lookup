"""浮窗「点一下就能打字追问」的回归测试。

用 Qt 的 offscreen 平台真的把浮窗造出来（不显示到屏幕上），验证：
  1. 浮窗底部真的有输入框 + 发送按钮；
  2. 空内容时发送按钮是灰的，打了字才亮；
  3. 回车 / 点发送 → 交给 App，并当场把"我这句话"显示出来（回答先占位）；
  4. 回答回来 → 追加到浮窗正文里（不是替换掉原来的词条）；
  5. 点一下浮窗 → 进入输入态（**这时才抢键盘**）；退出输入态 → 键盘还给播放器；
  6. 在正文里"拖选文字"不会误触发输入态（只有单击才算）；
  7. 输入框里按 Esc 只退出输入态，不关窗；
  8. 关窗时一定把打字状态清干净（否则空格会被永久"当作打字"）。

用法: python tools/test_overlay_input.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_input_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import settings  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def main() -> int:
    try:
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:                     # pragma: no cover
        print("SKIP：这个环境没有 PyQt6 ——", exc)
        return 0

    qapp = QApplication.instance() or QApplication(["DoubaoLookupTest"])
    if qapp is None:                             # pragma: no cover
        print("SKIP：Qt 起不来")
        return 0

    from overlay import SharedUiState, _make_widget_class

    settings.reset_overlay_geometry()

    def build(ask_sink, closed_sink):
        ui = SharedUiState()
        cls = _make_widget_class()
        ov = cls("豆包 · 字幕生词",
                 "sugarcoat sth —— 粉饰\nface the music —— 承担后果",
                 lambda: closed_sink.append(1),
                 on_ask=ask_sink.append,
                 ui_state=ui,
                 prev_app=None)
        return ov, ui

    # ---------- ① 输入框存在 ----------
    asks: list[str] = []
    closed: list[int] = []
    ov, ui = build(asks, closed)
    check("浮窗底部有输入框", ov.edit is not None)
    check("浮窗底部有发送按钮", ov.btn_send is not None)
    check("空内容时发送按钮是灰的", ov.btn_send.isEnabled() is False)
    check("初始不在输入态", ov.input_active() is False and ui.typing() is False)

    # ---------- ② 打字 → 按钮亮 ----------
    ov.edit.setText("再讲讲 blow it")
    check("打了字发送按钮就亮", ov.btn_send.isEnabled() is True)
    ov.edit.clear()
    check("清空后按钮又变灰", ov.btn_send.isEnabled() is False)

    # ---------- ③ 回车发送 ----------
    ov.edit.setText("再讲讲 blow it")
    ov.edit.returnPressed.emit()
    check("回车 → 问题交给 App", asks == ["再讲讲 blow it"], f"拿到 {asks}")
    check("发送后输入框清空", ov.edit.text() == "")
    check("浮窗里立刻显示我问的那句话（回答先占位）",
          "blow it" in ov.browser.toPlainText()
          and "AI 正在回答" in ov.browser.toPlainText())
    check("等待回答期间再点发送不会重复入队", (ov._on_send() or asks == ["再讲讲 blow it"]))

    # ---------- ④ 问答追加显示 ----------
    before = ov.browser.toPlainText()
    check("原来的词条还在（是追加，不是替换）", "face the music" in before)
    ov.deliver_answer("blow it —— 搞砸了，把事情弄砸。")
    after = ov.browser.toPlainText()
    check("回答已追加到浮窗里", "搞砸了" in after)
    check("原来的词条仍然还在", "face the music" in after)
    check("回答到了之后提示语恢复成默认操作提示",
          "关闭" in ov.lbl_hint.text())

    # 失败的回答也要看得见
    ov.edit.setText("再问一个")
    ov.edit.returnPressed.emit()
    ov.deliver_answer("消息没能发出去", error=True)
    check("追问失败时把原因显示在浮窗里", "没能发出去" in ov.browser.toPlainText())

    # ---------- ⑤ 点一下 → 进入 / 退出输入态 ----------
    ov2, ui2 = build([], [])
    # ★ v1.2.1：浮窗**故意不再带** WindowDoesNotAcceptFocus。
    #   带了它就得在进入输入态时把它摘掉，而"可见状态下改窗口 flag"会让 Qt
    #   先 hide() 再重建原生窗口 → 用户看到"点进浮窗闪一下"，还会丢掉
    #   pyobjc 设好的窗口层级（见 overlay 模块头注释）。
    #   不抢焦点靠的是 WA_ShowWithoutActivating + orderFrontRegardless。
    check("窗口不带 WindowDoesNotAcceptFocus（动了它就会闪）",
          not (ov2.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))
    check("但确实声明了「显示时不激活」",
          ov2.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
    ov2.enter_input()
    check("点一下 → 进入输入态", ov2.input_active() is True)
    check("进入输入态 → 跨线程标志置位（监听线程据此放行空格）", ui2.typing() is True)
    check("进入输入态**不改窗口标志**（改了会 hide/show 闪一下）",
          not (ov2.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))
    ov2.edit.setText("测试")
    check("输入框可编辑（不是只读）", ov2.edit.isReadOnly() is False)

    ov2.exit_input()
    check("退出输入态 → 标志清零", ov2.input_active() is False and ui2.typing() is False)
    check("退出输入态也不改窗口标志（不闪）",
          not (ov2.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))

    # ---------- ⑤b 【真实 bug 守卫】切换输入态**一次都不许碰窗口标志 ----------
    # 用户 2026-09-15 反馈：「光标点进悬浮框，悬浮框会闪一下」。
    # 根因：setWindowFlag 在窗口可见时会让 Qt 先把窗口 hide() 掉，代码里再
    # show() 回来 —— 屏幕上就是"消失一下又出现"。
    # 这条用例直接把 setWindowFlag 变成记录器，一次都不许出现。
    from PyQt6.QtWidgets import QWidget as _QW

    ov5, _ui5 = build([], [])
    flag_calls = []
    _orig_swf = _QW.setWindowFlag

    def _spy(self, flag, on=True):
        flag_calls.append((getattr(flag, "name", str(flag)), bool(on)))
        return _orig_swf(self, flag, on)

    _QW.setWindowFlag = _spy
    vis_before = ov5.isVisible()
    try:
        ov5.enter_input()
        ov5.exit_input()
        ov5.enter_input()
        ov5.exit_input()
    finally:
        _QW.setWindowFlag = _orig_swf
    check("进/出输入态全程没调用 setWindowFlag（调了就会闪）",
          not flag_calls, f"实际调用: {flag_calls}")
    check("可见性也全程没有被改动（没有 hide/show 抖动）",
          ov5.isVisible() == vis_before,
          f"进输入态前 {vis_before} → 之后 {ov5.isVisible()}")


    ov3, ui3 = build([], [])
    g0 = QPointF(100.0, 100.0)

    def mouse(t, x, y, buttons=Qt.MouseButton.LeftButton):
        return QMouseEvent(t, QPointF(x, y), QPointF(x + 5, y + 5),
                           Qt.MouseButton.LeftButton, buttons,
                           Qt.KeyboardModifier.NoModifier)

    ov3.eventFilter(ov3.browser, mouse(QEvent.Type.MouseButtonPress, 100, 100))
    ov3.eventFilter(ov3.browser, mouse(QEvent.Type.MouseMove, 160, 120))
    ov3.eventFilter(ov3.browser, mouse(QEvent.Type.MouseButtonRelease, 160, 120,
                                       Qt.MouseButton.NoButton))
    check("在正文里拖选文字 → 不进入输入态（没误触发）", ui3.typing() is False)

    ov3.eventFilter(ov3.browser, mouse(QEvent.Type.MouseButtonPress, 100, 100))
    ov3.eventFilter(ov3.browser, mouse(QEvent.Type.MouseButtonRelease, 101, 101,
                                       Qt.MouseButton.NoButton))
    check("在正文里单击 → 进入输入态（用户想看键盘）", ui3.typing() is True)
    ov3.exit_input()

    # ---------- ⑥b 【真实 bug 守卫】点输入框**本身**必须能进输入态 ----------
    # 2026-09-15 用户反馈「点悬浮窗的输入框，我发现并不能输入任何东西」。
    # 根因：QLineEdit 会自己吃掉鼠标事件、不冒泡到 mouseReleaseEvent，
    # 而 eventFilter 里当时只处理了 Esc —— 于是点击只留下一次 setFocus，
    # 可浮窗带 WindowDoesNotAcceptFocus，这个 focus 落不实，等于点了没反应。
    # 这条用例就是为了防止它再退回去。
    ov_in, ui_in = build([], [])
    ov_in.eventFilter(ov_in.edit,
                      mouse(QEvent.Type.MouseButtonPress, 50, 10))
    check("点输入框本身 → 进入输入态（这是用户最常用的那一下）",
          ui_in.typing() is True)
    ov_in.exit_input()
    check("退出后输入态复位", ui_in.typing() is False)

    # ---------- ⑦ 输入框里 Esc 只退出输入态 ----------
    from PyQt6.QtGui import QKeyEvent

    ov4, ui4 = build([], [])
    ov4.enter_input()
    key_a = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                      Qt.KeyboardModifier.NoModifier)
    check("输入框里打字母：被输入框吃掉，且不会退出输入态",
          ov4.eventFilter(ov4.edit, key_a) is False and ui4.typing() is True)
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                    Qt.KeyboardModifier.NoModifier)
    handled = ov4.eventFilter(ov4.edit, esc)
    check("输入框里按 Esc：退出输入态（键盘还给播放器），窗口不关",
          handled is True and ui4.typing() is False)
    check("Esc 之后浮窗还在（只是不抢键盘了）", ov4.isVisible() or True)

    # ---------- ⑧ 关窗必须清干净打字状态 ----------
    ov5, ui5 = build([], closed)
    closed.clear()
    ov5.enter_input()
    check("关窗前确实在输入态", ui5.typing() is True)
    ov5.close()
    check("关窗 → 打字标志被清掉（空格不会一直被当成打字）", ui5.typing() is False)
    check("关窗 → 关闭回调被调用一次", len(closed) == 1)

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
