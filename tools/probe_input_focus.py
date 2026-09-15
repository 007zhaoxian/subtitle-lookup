"""诊断：点击浮窗输入框后，键盘到底有没有真的进去。

【为什么要这样测】
上一版探针用 processEvents() + sleep，没有真正的 NSApplication 事件循环，
CGEventPost 投递的按键可能压根没被处理 —— 那样会得出错误的结论。
这一版**跑真实的 event loop**（QTimer 串起时间线 + app.exec()），
并加一组「普通窗口」对照，用来证明「模拟按键这条路本身是通的」。

用法:  python tools/probe_input_focus.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DL_SETTINGS_DIR", "/tmp/dl_focus_probe")
os.makedirs("/tmp/dl_focus_probe", exist_ok=True)

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

import config  # noqa: E402
import overlay as ov_mod  # noqa: E402
from utils import log  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))
    RESULTS.append((name, bool(ok), detail))


def nswin(widget):
    try:
        import objc
        from ctypes import c_void_p

        view = objc.objc_object(c_void_p=int(widget.winId()))
        return view.window()
    except Exception:
        return None


def post_real_key(ch: str = "a") -> None:
    """用 CGEventPost 发一个真实按键（系统级，等价于真人敲键盘）。"""
    try:
        import Quartz

        code = {"a": 0, "b": 11, "c": 8, "x": 7}.get(ch, 0)
        down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
    except Exception as exc:
        log.warning("模拟按键失败: %s", exc)


class Harness:
    """测试驱动器：用真实 event loop 串起时间线。"""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.ov = None
        self.plain = None          # 对照组：普通窗口
        self.step = 0

    # ---------------------------------------------------------- 时间线
    def start(self) -> None:
        self.ov = self._build_overlay()
        self.plain = self._build_plain()
        QTimer.singleShot(400, self._s1_activate_other)
        QTimer.singleShot(1500, self._s2_enter_input)
        QTimer.singleShot(2600, self._s3_check_focus)
        QTimer.singleShot(3200, self._s4_post_key_overlay)
        QTimer.singleShot(4200, self._s5_result_overlay)
        QTimer.singleShot(4600, self._s6_control_group)
        QTimer.singleShot(6000, self._s7_done)

    def _build_overlay(self):
        ui = ov_mod.SharedUiState()
        cls = ov_mod._make_widget_class()
        ov = cls(
            "豆包 · 字幕生词",
            "sugarcoat —— v. 粉饰，委婉美化（坏消息）",
            on_closed=lambda: None,
            on_ask=lambda t: None,
            ui_state=ui,
            prev_app=None,
        )
        ov.show()
        ov_mod._float_over_everything(ov)
        return ov

    def _build_plain(self):
        """对照组：一个最普通的窗口 + 输入框（没有那些特殊 window flag）。"""
        w = QWidget()
        w.setWindowTitle("对照组")
        lay = QVBoxLayout(w)
        ed = QLineEdit()
        ed.setPlaceholderText("对照输入框")
        lay.addWidget(ed)
        w.ed = ed  # type: ignore[attr-defined]
        return w

    def _s1_activate_other(self) -> None:
        """把前台让给 Finder，模拟「用户在看剧」。"""
        try:
            from AppKit import NSWorkspace

            NSWorkspace.sharedWorkspace().launchApplication_("Finder")
        except Exception:
            pass
        print("\n【场景】已把前台切给 Finder（模拟你在看剧）")

    def _s2_enter_input(self) -> None:
        print("\n【动作】调用 enter_input()（= 用户点了一下输入框）")
        self.ov.enter_input()

    def _s3_check_focus(self) -> None:
        print("\n【检查】Qt / NS 层面的焦点状态")
        try:
            from AppKit import NSApplication

            app = NSApplication.sharedApplication()
            check("NSApp 已激活", bool(app.isActive()))
            kw = app.keyWindow()
            check("浮窗是 key window",
                  kw is not None and nswin(self.ov) is not None
                  and str(kw) == str(nswin(self.ov)))
        except Exception as exc:
            check("NS 层查询", False, str(exc))
        fw = QApplication.focusWidget()
        check("焦点在输入框上", fw is self.ov.edit,
              f"focusWidget={type(fw).__name__ if fw else None}")
        check("edit.hasFocus()", bool(self.ov.edit and self.ov.edit.hasFocus()))

    def _s4_post_key_overlay(self) -> None:
        print("\n【终极判据】向系统发一个真实按键 'a' → 看输入框里有没有字")
        post_real_key("a")

    def _s5_result_overlay(self) -> None:
        txt = self.ov.edit.text() if self.ov.edit else ""
        check("浮窗输入框收到了键盘", txt == "a", f"edit 内容={txt!r}")

    def _s6_control_group(self) -> None:
        """对照组：普通窗口能否收到同一个按键 —— 证明「模拟按键通路」是通的。"""
        print("\n【对照】普通窗口（无特殊 flag）能否收到按键")
        self.plain.show()
        self.plain.raise_()
        self.plain.activateWindow()
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            pass
        QTimer.singleShot(600, self._s6b_post)
        QTimer.singleShot(1400, self._s6c_result)

    def _s6b_post(self) -> None:
        post_real_key("b")

    def _s6c_result(self) -> None:
        txt = self.plain.ed.text()  # type: ignore[attr-defined]
        check("对照窗口收到了键盘（证明模拟按键这条路是通的）",
              txt == "b", f"对照 edit 内容={txt!r}")

    def _s7_done(self) -> None:
        self.app.quit()


def main() -> int:
    app = QApplication([sys.argv[0]])
    h = Harness(app)
    h.start()
    print("=" * 62)
    print("输入框焦点诊断（真实事件循环）")
    print("=" * 62)
    app.exec()

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 62)
    print(f"共 {len(RESULTS)} 项，失败 {len(bad)}")
    for n in bad:
        print(f"   ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
