"""面板「能滚动 + 滚轮不误改选项」的冒烟测试（无头，offscreen）。

为什么需要它：
  panel.py 内容越来越多，小屏上会顶出屏幕底部，最下面那排按钮点不到。
  修法是整块塞进 QScrollArea、并把窗口高度封顶。但光"能构造出来"证明不了
  修好了 —— 有两件很容易翻车的事必须验：
    ① 窗口高度真的被压住了（否则等于没修）
    ② 滚轮扫过下拉框时**不能改掉选项**（QComboBox 默认会吃掉滚轮换选项，
       用户滚页面就会不小心把服务商/模型/截图范围换掉）

跑法：
    QT_QPA_PLATFORM=offscreen python tools/test_panel_scroll.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DL_SETTINGS_DIR", tempfile.mkdtemp(prefix="panel_scroll_"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


def check_true(name: str, got) -> None:
    check(name, bool(got), True)


class _StubCtrl:
    """只提供面板会调用的那些方法，不牵扯真正的截图/浏览器。"""

    def __init__(self):
        self._on = False

    def set_mode(self, on: bool) -> None:
        self._on = on

    def is_mode_on(self) -> bool:
        return self._on

    def is_busy(self) -> bool:
        return False

    def is_asking(self) -> bool:
        return False

    def status_text(self) -> str:
        return "空闲"

    def prompt_is_customized(self) -> bool:
        return False

    def prompt_preview(self) -> str:
        return "默认提示词"

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _wheel(dy: int = -120):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent

    return QWheelEvent(
        QPointF(6.0, 6.0),               # pos
        QPointF(6.0, 6.0),               # globalPos
        QPoint(0, 0),                    # pixelDelta
        QPoint(0, dy),                   # angleDelta
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,                           # inverted
    )


def main() -> int:
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication, QComboBox, QScrollArea

    import panel

    app = QApplication.instance() or QApplication(sys.argv)
    Panel = panel.make_panel_class()

    # 拿一个稍小的屏幕高度，保证"内容比视口高"这个前提成立
    screen = QGuiApplication.primaryScreen()
    geo = screen.availableGeometry()
    print(f"屏幕可用区：{geo.width()}x{geo.height()}")

    p = Panel(_StubCtrl())
    p.resize(540, 400)          # 强制一个小窗口，制造出滚动场景
    p.show()
    app.processEvents()

    # ---------------------------------------------------- 1. 结构
    print("\n== 1. 结构：内容确实在滚动区里 ==")
    scrolls = p.findChildren(QScrollArea)
    check("面板里有 1 个 QScrollArea", len(scrolls), 1)
    scroll = scrolls[0]
    body = scroll.widget()
    check_true("滚动区挂了内容容器", body is not None)
    check("内容容器是面板内容（不是空的）",
          body.findChildren(QComboBox).__len__(), 3)

    # 关键控件都还在滚动区里（不是游离在面板上）
    check_true("截图范围下拉在滚动区里",
               scroll.isAncestorOf(p.cmb_capture))
    check_true("服务商下拉在滚动区里",
               scroll.isAncestorOf(p.cmb_provider))
    check_true("模型下拉在滚动区里",
               scroll.isAncestorOf(p.cmb_model))
    check_true("看剧模式按钮在滚动区里",
               scroll.isAncestorOf(p.btn_mode))
    check_true("重置位置按钮在滚动区里",
               scroll.isAncestorOf(p.btn_geo))
    # v1.2.1 新增的「显示」分区（字号滑块 + 示例卡 + 观看记录）也必须在滚动区里，
    # 不能游离在滚动区外面 —— 那样在小屏上会被窗口边缘裁掉。
    check_true("字号滑块在滚动区里", scroll.isAncestorOf(p.sld_font))
    check_true("字号示例卡在滚动区里", scroll.isAncestorOf(p.preview))
    check_true("观看记录复选框在滚动区里", scroll.isAncestorOf(p.chk_viewlog))
    check_true("观看记录按钮在滚动区里", scroll.isAncestorOf(p.btn_viewlog))

    # ---------------------------------------------------- 2. 高度封顶
    print("\n== 2. 高度封顶（别顶出屏幕）==")
    p2 = Panel(_StubCtrl())
    p2.show()
    app.processEvents()
    max_allowed = int(geo.height() * 0.90) + 2
    check_true(f"面板高度 {p2.height()} ≤ 屏幕 90%（{max_allowed}）",
               p2.height() <= max_allowed)

    # ---------------------------------------------------- 3. 下拉框滚轮安全
    print("\n== 3. 滚轮扫过下拉框不该改选项 ==")
    for nm, cmb in (("截图范围", p.cmb_capture),
                    ("服务商", p.cmb_provider),
                    ("模型", p.cmb_model)):
        check(f"{nm}下拉是滚动安全子类", type(cmb).__name__, "_NoWheelCombo")

    # ⚠️ 上面又建了一个面板 p2，激活窗口已经跑到它那边去了。
    #    而 Qt 的 hasFocus() 要求「窗口是活动窗口」才算真，所以必须先把
    #    p 抢回活动窗口，否则这里的"有焦点"根本设不上 —— 会假失败。
    p.activateWindow()
    QApplication.setActiveWindow(p)
    app.processEvents()

    cmb = p.cmb_provider
    p.btn_mode.setFocus()
    app.processEvents()
    check_true("前提：焦点不在下拉框上", not cmb.hasFocus())

    before_idx = cmb.currentIndex()
    sb0 = scroll.verticalScrollBar().value()
    ev = _wheel(-120)
    app.sendEvent(cmb, ev)
    app.processEvents()
    check("没焦点时滚轮不改选项", cmb.currentIndex(), before_idx)
    # 注意语义：事件最后是**被滚动区消费**掉的，所以 accepted=True 才是对的。
    # 真正要盯的是上面那条"选项没变" —— 别把它读成"下拉框吃掉了滚轮"。
    check("事件最终交给滚动区消费（不是下拉框自己吃）", ev.isAccepted(), True)
    check_true("滚轮改的是页面滚动位置（事件确实被交给了滚动区）",
               scroll.verticalScrollBar().value() != sb0)

    # 获焦点 → 还应该能正常用滚轮切换（没把功能整个砍掉）
    cmb.setFocus()
    app.processEvents()
    check_true("前提：下拉框拿到了焦点", cmb.hasFocus())
    n = cmb.count()
    if n >= 2:
        prev = cmb.currentIndex()
        ev2 = _wheel(-120)
        app.sendEvent(cmb, ev2)
        app.processEvents()
        check("有焦点时滚轮仍能切选项（功能没被砍）",
              cmb.currentIndex() != prev, True)
    else:
        print("SKIP  下拉项不足 2 个，跳过『有焦点能切』这一项")

    # ---------------------------------------------------- 4. 滚到底部
    print("\n== 4. 能滚到最底部（最下面的按钮够得着）==")
    sb = scroll.verticalScrollBar()
    if sb.maximum() <= 0:
        print("SKIP  视口够高，没有滚动条（小屏上才有）")
    else:
        check_true("有纵向滚动条", sb.maximum() > 0)
        sb.setValue(sb.maximum())
        app.processEvents()
        check("滚到底了", sb.value(), sb.maximum())
        # 最下面的控件此时应该在视口里（几何上可见）。
        # ★ 底部一加新控件，这一项就得跟着换成**当前最靠下**的那个 ——
        #   v1.2.3 起最下面是「这本重新编号」按钮（记录文件夹那两行的下面）。
        btn = p.btn_vlog_renum
        top = btn.mapTo(scroll.viewport(), btn.rect().topLeft()).y()
        check_true(f"最下面的「重新编号」滚到底后进入视口（y={top}）",
                   0 <= top <= scroll.viewport().height())
        # 面板变长了也不能把老按钮挤出内容区：往回滚一点就该看见它。
        # ★ 这里**不能写死"滚 260px"** —— 底部每加一行控件（v1.2.3 就加了两行：
        #   记录文件夹 + 重新编号）这个距离就变一次，写死必然失败。
        #   改成"往上滚到它进视口为止"。
        for _ in range(40):
            gt = p.btn_geo.mapTo(scroll.viewport(), p.btn_geo.rect().topLeft()).y()
            if 0 <= gt <= scroll.viewport().height():
                break
            sb.setValue(max(0, sb.value() - 60))
            app.processEvents()
        gt = p.btn_geo.mapTo(scroll.viewport(), p.btn_geo.rect().topLeft()).y()
        check_true(f"往回滚一点能看到「重置位置与大小」（y={gt}）",
                   0 <= gt <= scroll.viewport().height())

    # ---------------------------------------------------- 5. 重开回顶部
    print("\n== 5. 重新打开面板滚回顶部 ==")
    sb.setValue(sb.maximum())
    app.processEvents()
    p.hide()
    p.show()
    app.processEvents()
    check("重新 show() 后滚动条回到顶部", sb.value(), 0)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
