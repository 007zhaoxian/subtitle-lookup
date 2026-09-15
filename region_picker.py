"""自定义截图区域：在**当前画面**上盖一层半透明遮罩，拖一个框。

用法（必须在 Qt 主线程调用）:
    from region_picker import pick_region, active
    pick_region(on_done=cb)         # 异步，选完回调 cb([x, y, w, h] 比例) 或 cb(None)
    active()                        # 现在有没有一个框选窗口开着

设计要点（都是踩过的坑，改之前先看一眼）:

1. **不能 showFullScreen()**
   macOS 上 showFullScreen() 会把窗口搬到**独立的一个 Space**，用户看到的就是
   一片黑 —— 正是"弹个黑屏让我框"的成因。这里改成普通无边框窗口 +
   setGeometry(所有屏幕的外接矩形)，它就老老实实待在**当前桌面**上。

2. **背景必须是"当前画面"，不是纯黑**
   开窗前先 grab 一张桌面快照当底图，再盖一层半透明黑。选区内把底图原样重画
   一遍（=还原亮度），边框高亮。这样用户看得见自己到底在框哪里。
   grab 失败就退回真透明（WA_TranslucentBackground），至少不会是黑屏。

3. **坐标全程用窗口本地坐标**
   以前 start/end 取的是 globalPosition()，绘制却在窗口本地坐标系里，两个屏幕
   拼接时原点一偏移，框就整体往下跑 —— 表现就是"起点不是我按下的位置"。
   现在 press/move/release 一律用 ev.globalPosition() 记全局点、绘制时再
   mapFromGlobal 换成本地坐标，算比例减的是外接矩形原点（两头各用各的）。

4. **必须留一条 Python 强引用**
   pick_region() 返回后如果没人持有 picker，Python GC 会回收它 → 触发 C++
   QWidget 析构 → destroyed 信号回调 Python 槽 → PyQt6 把槽里的任何异常升级成
   qFatal() → abort()，App 当场消失（用户报的"时常意外退出"）。
   所以 here 用模块级 _ACTIVE 按住它，关闭时才松手。

5. ★ **回调必须由"框完"这件事直接触发，不能等 destroyed 信号**
   v1.1.10 的写法是 `picker.destroyed.connect(_after)`：框完之后窗口只是
   close()（Qt 不会因此销毁 QWidget），而 _ACTIVE 又一直按着它 ——
   于是 destroyed **永远不会发**，_after 永远不跑：
     · 面板上「框选…」被 setEnabled(False) 之后**再也回不来，一直是灰的**；
     · 框出来的区域也**没保存**（settings 里 capture_rect_set 一直是 false）。
   用户报的"框选成灰色了、以后再也框不了"就是这个。
   现在改成：算完结果就 `_finish()` 立刻回调 + 松手 + deleteLater，
   destroyed 只作为"万一"的兜底（带一次性开关，绝不重复回调）。
"""
from __future__ import annotations

from typing import Callable

from utils import log

# 活着的框选窗口。没有它，GC 一回收就会把 App 带崩（见文件头第 4 条）。
_ACTIVE = None


def active() -> bool:
    """现在有没有一个框选窗口开着。

    面板用它决定「框选…」按钮能不能点 —— 注意是**问这里**，不是让面板
    自己记一份状态（面板记的状态一旦和实际不一致，按钮就会永远灰着）。
    """
    return _ACTIVE is not None


def pick_region(on_done: Callable[[list[float] | None], None] | None = None):
    """弹出框选界面。

    on_done 给了就**异步**（窗口自己跑，选完回调）；
    没给就同步返回窗口对象，调用方自己 show/exec。
    """
    global _ACTIVE

    from PyQt6.QtCore import QPoint, QRect, Qt
    from PyQt6.QtGui import (
        QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QPixmap,
    )
    from PyQt6.QtWidgets import QWidget

    class _Picker(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.start: QPoint | None = None
            self.end: QPoint | None = None
            self.result: list[float] | None = None
            # 一次性开关：保证 on_done **只被调用一次**（正常收尾走 _finish，
            # 万一窗口是被别的原因销毁的，再由 destroyed 兜底补一次）。
            self._notified = False
            self._on_done = on_done
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.NoDropShadowWindowHint
            )
            # 真透明：底图由我们画，窗口本身不挡光
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.setWindowTitle("框选截图区域")

            # 覆盖**所有屏幕**拼成的外接矩形，多屏也能框
            uni = QRect()
            for sc in QGuiApplication.screens():
                uni = uni.united(sc.geometry())
            self._union = uni
            self.setGeometry(uni)

            # 开窗前先抓一张当前画面当底图（失败就退回透明，不会变黑屏）
            self._shot = self._grab_desktop(uni)

        # -------------------------------------------------- 底图
        @staticmethod
        def _grab_desktop(uni: QRect) -> QPixmap | None:
            try:
                shot = QPixmap(uni.size())
                shot.fill(QColor(0, 0, 0, 0))
                from PyQt6.QtGui import QPainter as _P

                painter = _P(shot)
                got = False
                for sc in QGuiApplication.screens():
                    g = sc.geometry()
                    try:
                        pm = sc.grabWindow(0)
                    except Exception:
                        pm = None
                    if pm is None or pm.isNull():
                        continue
                    painter.drawPixmap(
                        g.x() - uni.x(), g.y() - uni.y(),
                        g.width(), g.height(), pm,
                    )
                    got = True
                painter.end()
                return shot if got else None
            except Exception as exc:
                log.debug("抓取当前画面失败（退回透明底）: %s", exc)
                return None

        # -------------------------------------------------- 收尾
        def _finish(self, result: list[float] | None) -> None:
            """框完了（或取消了）：记结果 → 立刻回调 → 松手 → 安全销毁。

            ★ 一定要在这里**直接回调**，不能等 destroyed 信号 —— 见文件头第 5 条。
            """
            global _ACTIVE

            self.result = result
            self.close()
            if self._notified:
                return
            self._notified = True
            if _ACTIVE is self:
                _ACTIVE = None
            cb = self._on_done
            try:
                if cb is not None:
                    cb(result)
            except Exception as exc:      # 槽里漏异常 = PyQt6 abort，必须兜住
                log.warning("框选回调出错: %s", exc)
            # 让 Qt 在回到事件循环后把窗口真正删掉（我们只是 close，
            # QWidget 不会因此被销毁，留着就是每次框选都漏一个窗口）。
            try:
                self.deleteLater()
            except Exception:
                pass

        # -------------------------------------------------- 交互
        # ★ start / end 一律存**屏幕全局坐标**，不存窗口本地坐标。
        #   为什么：macOS 对跨屏窗口的几何会做钳制（副屏在主屏左上时，
        #   setGeometry 给的负原点不一定照办），窗口实际位置可能和
        #   "所有屏幕外接矩形"错开一点。用全局坐标记下用户真正按下的那一点，
        #   算比例时减的是外接矩形原点（真值），画框时才换成窗口本地坐标
        #   （mapFromGlobal），两头各用各的，就不会互相污染。
        def _global(self, ev) -> QPoint:
            try:
                return ev.globalPosition().toPoint()
            except Exception:                       # 老接口兜底
                return ev.globalPos()

        def _local_rect(self) -> QRect | None:
            """把 start/end（全局）换算成**窗口本地**矩形，供绘制用。"""
            if self.start is None or self.end is None:
                return None
            a = self.mapFromGlobal(self.start)
            b = self.mapFromGlobal(self.end)
            return QRect(a, b).normalized()

        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self.start = self._global(ev)
                self.end = self.start
                self.update()

        def mouseMoveEvent(self, ev):
            if self.start is not None:
                self.end = self._global(ev)
                self.update()

        def mouseReleaseEvent(self, ev):
            if ev.button() != Qt.MouseButton.LeftButton or self.start is None:
                return
            self.end = self._global(ev)
            r = QRect(self.start, self.end).normalized()
            u = self._union
            if r.width() < 20 or r.height() < 20:
                log.info("框选区域太小，已忽略")
                self._finish(None)
                return
            # r 是全局坐标，减掉外接矩形原点才是"相对整块屏幕拼图"的比例
            rect = [
                round((r.x() - u.x()) / max(1, u.width()), 4),
                round((r.y() - u.y()) / max(1, u.height()), 4),
                round(r.width() / max(1, u.width()), 4),
                round(r.height() / max(1, u.height()), 4),
            ]
            log.info("已框选区域（屏幕比例）= %s", rect)
            self._finish(rect)

        def keyPressEvent(self, ev):
            if ev.key() == Qt.Key.Key_Escape:
                self._finish(None)
                return
            super().keyPressEvent(ev)

        # -------------------------------------------------- 绘制
        def paintEvent(self, _ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self._local_rect()          # 绘制一律用窗口本地坐标

            # ① 底图 = 当前画面（没有就全透明，绝不画纯黑）
            if self._shot is not None and not self._shot.isNull():
                p.drawPixmap(0, 0, self._shot)

            # ② 整体压暗一层
            p.fillRect(self.rect(), QColor(0, 0, 0, 110))

            if r is not None:
                # ③ 选区内把底图原样重画一遍 = 抠掉压暗，露出真实画面
                if self._shot is not None and not self._shot.isNull():
                    p.drawPixmap(r, self._shot, r)
                else:
                    p.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_Clear)
                    p.fillRect(r, QColor(0, 0, 0, 0))
                    p.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver)

                # ④ 边框 + 尺寸
                p.setPen(QPen(QColor(124, 196, 255), 2))
                p.drawRect(r)
                tip = f"{r.width()} × {r.height()}"
                p.setPen(QColor(255, 255, 255))
                ty = r.y() - 8 if r.y() > 24 else r.y() + 20
                p.drawText(r.x() + 6, ty, tip)
            else:
                # ⑤ 没开始拖的时候给一句提示
                p.setPen(QColor(255, 255, 255, 220))
                f = QFont()
                f.setPointSize(14)
                p.setFont(f)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignTop,
                           "拖动鼠标框选字幕区域　·　Esc 取消")
            p.end()

    picker = _Picker()
    if on_done is None:
        return picker

    def _destroyed() -> None:
        """兜底：窗口以任何方式被销毁时，至少把引用松开、且结果别丢。

        正常流程走不到这里（_finish 已经回调过了，_notified 一次性的开关
        会挡住重复回调）。
        """
        global _ACTIVE
        if _ACTIVE is picker:
            _ACTIVE = None
        if not picker._notified:
            picker._notified = True
            try:
                picker._on_done(picker.result)
            except Exception as exc:
                log.warning("框选回调出错（destroyed 兜底）: %s", exc)

    _ACTIVE = picker                      # ★ 按住不放，防止 GC 把 App 带崩
    picker.destroyed.connect(_destroyed)

    # ★ 这里**不能**用 showFullScreen()：macOS 会把它丢到独立 Space，变成黑屏
    picker.show()
    picker.raise_()
    picker.activateWindow()
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass
    return picker
