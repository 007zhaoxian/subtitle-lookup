"""状态栏（Menu Bar）两种实现，接口完全一致，由 config.MENUBAR_BACKEND 选择。

★ v1.2.2：网页版豆包删除后，菜单里去掉了「登录豆包 / 检查登录状态 /
  抓取页面结构 / 换个新对话」四项 —— 它们全都依赖那条已删除的通道。

* RumpsMenuBar —— 默认。跑在 NSApplication 主线程（rumps 要求），
  此时 Qt 悬浮窗在后台线程里自己跑事件循环。
* QtMenuBar    —— 备选。用 QSystemTrayIcon，Qt 独占主线程，
  当默认模式在你的 macOS 版本上出现 Qt 线程不稳定时切到它（DL_MENUBAR=qt）。

两者都只调用同一个 MenuController，业务逻辑不在这里。
"""
from __future__ import annotations

import config

from utils import log

APP_TITLE = "查词"


class MenuController:
    """菜单项需要的能力集合，由 app.App 实现。"""

    def is_mode_on(self) -> bool:  # pragma: no cover - 接口声明
        raise NotImplementedError

    def set_mode(self, on: bool) -> None:  # pragma: no cover
        raise NotImplementedError

    def is_busy(self) -> bool:  # pragma: no cover
        raise NotImplementedError

    def first_run_check(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def show_panel(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def test_lookup(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def status_text(self) -> str:  # pragma: no cover
        return "空闲"

    def open_permissions(self, which: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def open_log(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def open_prompt_editor(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def reset_overlay_geometry(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def quit(self) -> None:  # pragma: no cover
        raise NotImplementedError


# ===================================================================== rumps
def build_rumps_menubar(ctrl: MenuController):
    import rumps

    from icon import ensure_icon

    # rumps 支持 template 图（纯黑+alpha，系统自动反色），所以这里固定用黑
    icon = ensure_icon(color=(0, 0, 0), name="menu_icon_Template_v2")
    app = rumps.App(APP_TITLE, icon=str(icon) if icon else None, quit_button=None)
    if icon:
        app.template = True
        app.title = ""  # 只显示图标

    mode_item = rumps.MenuItem("开启看剧模式", callback=lambda _s: ctrl.set_mode(not ctrl.is_mode_on()))
    status_item = rumps.MenuItem("状态：空闲", callback=None)

    perms = [
        rumps.MenuItem("辅助功能（按键监听）", callback=lambda _s: ctrl.open_permissions("accessibility")),
        rumps.MenuItem("屏幕录制（截图）", callback=lambda _s: ctrl.open_permissions("screencapture")),
    ]

    app.menu = [
        rumps.MenuItem("打开控制面板", callback=lambda _s: ctrl.show_panel()),
        None,
        rumps.MenuItem("手动查一次（不用键盘）", callback=lambda _s: ctrl.test_lookup()),
        rumps.MenuItem("✏️ 编辑提示词（发给 AI 的全部内容）",
                       callback=lambda _s: ctrl.open_prompt_editor()),
        rumps.MenuItem("📍 重置悬浮窗位置与大小",
                       callback=lambda _s: ctrl.reset_overlay_geometry()),
        None,
        mode_item,
        status_item,
        None,
        rumps.MenuItem("权限设置说明", callback=lambda _s: ctrl.show_permission_help()),
        ("打开系统设置", perms),
        rumps.MenuItem("打开日志文件", callback=lambda _s: ctrl.open_log()),
        None,
        rumps.MenuItem("退出", callback=lambda _s: ctrl.quit()),
    ]

    def _refresh(_timer=None):
        try:
            on = ctrl.is_mode_on()
            mode_item.state = 1 if on else 0
            status_item.title = "状态：" + (ctrl.status_text() or "空闲")
        except Exception:
            log.exception("刷新菜单状态失败")

    _refresh()
    timer = rumps.Timer(_refresh, 1.5)
    timer.start()
    app._dl_timer = timer  # 防止被 GC
    return app


# ======================================================================== Qt
def build_qt_menubar(ctrl: MenuController):
    from PyQt6.QtGui import QAction, QIcon
    from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

    from icon import ensure_icon

    # Qt 不会自动按 template 反色，所以按系统明暗（深色→白图标 / 浅色→黑图标）
    # 自己选颜色；见 icon.ensure_icon 的说明。
    icon_path = ensure_icon(size=22)
    if icon_path is None:
        log.error("图标生成失败，托盘图标将不可见")
    tray = QSystemTrayIcon(QIcon(str(icon_path)) if icon_path else QIcon(), None)
    tray.setToolTip(config.APP_DISPLAY_NAME + " · 看剧模式")
    tray.setVisible(True)

    menu = QMenu()

    menu.addAction(QAction("打开控制面板", menu, triggered=lambda: ctrl.show_panel()))
    menu.addSeparator()

    menu.addAction(QAction("手动查一次（不用键盘）", menu,
                           triggered=lambda: ctrl.test_lookup()))
    menu.addAction(QAction("✏️ 编辑提示词（发给 AI 的全部内容）", menu,
                           triggered=lambda: ctrl.open_prompt_editor()))
    menu.addAction(QAction("🪟 重置悬浮窗大小", menu,
                           triggered=lambda: ctrl.reset_overlay_size()))
    menu.addAction(QAction("📍 重置悬浮窗位置与大小", menu,
                           triggered=lambda: ctrl.reset_overlay_geometry()))
    menu.addSeparator()

    act_mode = QAction("开启看剧模式", menu)
    act_mode.setCheckable(True)
    act_mode.triggered.connect(lambda checked: ctrl.set_mode(bool(checked)))
    menu.addAction(act_mode)

    act_status = QAction("状态：空闲", menu)
    act_status.setEnabled(False)
    menu.addAction(act_status)
    menu.addSeparator()

    menu.addAction(QAction("权限设置说明", menu,
                           triggered=lambda: ctrl.show_permission_help()))

    perm_menu = menu.addMenu("打开系统设置")
    perm_menu.addAction(QAction("辅助功能（按键监听）", perm_menu,
                                triggered=lambda: ctrl.open_permissions("accessibility")))
    perm_menu.addAction(QAction("屏幕录制（截图）", perm_menu,
                                triggered=lambda: ctrl.open_permissions("screencapture")))
    menu.addAction(QAction("打开日志文件", menu, triggered=lambda: ctrl.open_log()))
    menu.addSeparator()
    menu.addAction(QAction("退出", menu, triggered=lambda: ctrl.quit()))

    tray.setContextMenu(menu)
    tray.setVisible(True)

    # 左键点图标 → 直接开控制面板（右键才出菜单）。这是用户最自然的预期。
    def _on_activated(reason):
        try:
            from PyQt6.QtWidgets import QSystemTrayIcon as _T

            if reason in (_T.ActivationReason.Trigger, _T.ActivationReason.DoubleClick):
                ctrl.show_panel()
        except Exception:
            log.exception("处理托盘点击失败")

    tray.activated.connect(_on_activated)

    # 用 QTimer 同步状态（Qt 线程内，安全）
    from PyQt6.QtCore import QTimer

    def _refresh():
        try:
            on = ctrl.is_mode_on()
            if act_mode.isChecked() != on:
                act_mode.setChecked(on)
            act_status.setText("状态：" + (ctrl.status_text() or "空闲"))
        except Exception:
            log.exception("刷新菜单状态失败")

    timer = QTimer()
    timer.setInterval(1500)
    timer.timeout.connect(_refresh)
    timer.start()
    tray._dl_timer = timer
    _refresh()
    return tray
