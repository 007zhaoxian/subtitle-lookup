"""入口：装配菜单栏 + 按键监听 + Qt 悬浮窗运行时。

菜单栏实现（与"查词后端"无关，纯粹是 UI 承载方式）：
    DL_MENUBAR=rumps  rumps 占主线程；Qt 悬浮窗跑在后台线程
    DL_MENUBAR=qt     Qt 占主线程（QSystemTrayIcon + 悬浮窗），最稳 —— 默认

★ v1.2.2：查词只剩"直连大模型 API"一条通道，网页版豆包（Playwright +
  扫码登录 + 无头 Chrome）已整体删除。因此这里再没有"要不要预热浏览器"
  这类分支，--diagnose 也不再检查 Playwright / 浏览器配置目录。

命令行：
    python main.py            正常启动
    python main.py --diagnose 只做环境自检（权限 / 依赖），不开菜单栏
    python main.py --self-test 真发一张图给模型，验证 key + 模型能看图
"""
from __future__ import annotations

import os
import sys
import threading

import config
import keytap
from app import App
from hotkey import HotkeyWatcher
from mouse import ClickOutsideWatcher
from overlay import QtRuntime, QtThread
from utils import (
    PERMISSION_HINT,
    acquire_single_instance_lock,
    install_crash_guard,
    is_accessibility_trusted,
    log,
    notify,
)


# ------------------------------------------------------------------ 自检
def self_test() -> int:
    """真机自检：不开菜单栏，用 App 自己的代码跑一次完整查词。

    用来确认「装好的这个 .app 确实能用」。

    和面板上『▶ 手动查一次』走同一条路：api_client.ask() ——
    不发浏览器、不弹浮窗，纯命令行验证 key + 模型能不能看图。

    用法:
        "/Applications/看剧查词-美剧.app/Contents/MacOS/SubtitleLookup" --self-test
    """
    import threading
    import time

    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        print("缺少 Pillow，无法自检:", exc)
        return 1

    img = config.TMP_DIR / "self_test_subtitle.png"
    w, h = 1280, 720
    im = Image.new("RGB", (w, h), (10, 12, 16))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, h - 230], fill=(24, 28, 38))
    d.rectangle([0, h - 230, w, h], fill=(3, 4, 7))
    d.text((70, h - 175), "I'm not gonna sugarcoat it, you have to face the music.",
           fill=(255, 255, 255))
    d.text((70, h - 119), "He totally blew it, and now we're all in hot water.",
           fill=(255, 255, 255))
    im.save(img)

    import settings

    print(f"=== {config.APP_DISPLAY_NAME} 自检（直连 API）===")
    print("测试图:", img)

    import api_client

    cfg = settings.provider_config()
    print(f"服务商: {cfg['label']}  模型: {cfg['model']}  Base URL: {cfg['base_url']}")
    if not cfg.get("key"):
        print(f"!! 还没填 {cfg['label']} 的 API Key")
        print("   面板 → 查词引擎 → API Key")
        return 1
    print(f"Key: {settings.mask_key(cfg['key'])}")
    t0 = time.time()
    try:
        text = api_client.ask(img, settings.get_prompt(),
                              cfg=cfg, timeout=config.API_TIMEOUT_SEC + 30)
    except api_client.ApiError as exc:
        print(f"!! 失败（{time.time() - t0:.1f}s）: {exc.user_text()}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"!! 失败（{time.time() - t0:.1f}s）: {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.time() - t0
    if not text.strip():
        # api_client 已经在正文为空时抛 ApiError 了，走到这里说明拿到了
        # 只有空白字符的正文 —— 同样不能报"通过"，否则自检会骗人。
        print(f"\n!! 失败（{elapsed:.1f}s）：模型返回了空回答")
        return 3
    print(f"\n=== 通过：{elapsed:.1f}s，返回 {len(text)} 字 ===")
    print(text)
    return 0

def diagnose() -> int:
    print(f"=== {config.APP_DISPLAY_NAME} 环境自检 ===")
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, hint: str = "") -> None:
        checks.append((name, ok, hint))

    for mod in ("rumps", "pynput", "PyQt6", "mss", "PIL", "objc"):
        try:
            __import__(mod)
            add(f"依赖 {mod}", True)
        except Exception as exc:
            add(f"依赖 {mod}", False, str(exc))

    add("辅助功能权限", is_accessibility_trusted(), "系统设置 → 隐私与安全性 → 辅助功能")

    for name, ok, hint in checks:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"  → {hint}" if hint and not ok else ""))
    failed = [c for c in checks if not c[1]]
    print(f"\n共 {len(checks)} 项，失败 {len(failed)} 项。日志：{config.LOG_FILE}")
    if failed:
        print(PERMISSION_HINT)
    return 1 if failed else 0


# ------------------------------------------------------------------ 主程序
def _demo_ask(app: App) -> None:
    """--demo-ask 专用：等浮窗显示出来，再模拟用户「点输入框 → 打字 → 发送」。

    这样跑一次 `--demo-ask` 就能同时自证两条链路：
      ① 截图 → 模型 → 浮窗（第一次回答）
      ② 浮窗追问 → 同一个对话 → 回答追加显示
    """
    import time

    question = "这句里的 face the music 是什么语气？一句话就行。"
    for _ in range(140):
        try:
            if app.runtime is not None and app._overlay_visible():
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        log.warning("demo 追问：等不到浮窗显示，跳过")
        return
    time.sleep(1.2)
    log.info("demo 追问：准备模拟输入 —— %s", question)
    app.runtime.post_demo_input(question)


def main() -> int:
    if "--diagnose" in sys.argv:
        return diagnose()
    if "--self-test" in sys.argv:
        return self_test()

    # --demo：自动跑一遍「截图 → 模型 → 悬浮窗」，用来验证界面链路。
    # 面板不弹出，方便截图核对悬浮窗/进度胶囊的实际观感；60 秒后自动退出。
    demo = "--demo" in sys.argv
    # --demo-ask：在 --demo 基础上，浮窗出来后自动模拟「点输入框 → 打字 → 发送」，
    # 并把追问的回答追加到浮窗里 —— 用来端到端自证「浮窗追问」这条链路。
    # 走的是**和用户手点完全相同的控件代码路径**（enter_input / edit / _on_send），
    # 只是这里由代码触发，省去人工点一遍。
    demo_ask = "--demo-ask" in sys.argv
    demo = demo or demo_ask

    # ★★ 必须在这里、**在任何 Qt 对象出现之前**装好崩溃保险。
    #   PyQt6 会把"槽里漏出来的异常"升级成 qFatal() → abort()，整个进程当场
    #   死掉（用户崩溃报告：SIGABRT @ QObject::destroyed → unislot →
    #   pyqt6_err_print）。装了它之后，同样的情况只会记一条日志、程序继续跑。
    #   位置很关键：装在 QApplication 之后等于没装（QApplication 自己就会
    #   调度事件循环）。详见 utils.install_crash_guard 的注释。
    install_crash_guard()

    log.info("=" * 56)
    log.info("%s v%s 启动 (menubar=%s, demo=%s, 查词=直连 API)",
             config.APP_DISPLAY_NAME, config.VERSION, config.MENUBAR_BACKEND,
             demo)

    # 0) 只允许跑一份。两份 App 会抢同一个**全局空格 CGEventTap**（谁后起来谁
    #    把前一个的吞键逻辑顶掉），日志文件也会互相覆盖 —— 排查起来像是"功能时好
    #    时坏"。以前这里的理由是"抢浏览器配置目录"，网页版删掉后不再成立，但
    #    "只跑一份"本身依然必要。
    if not acquire_single_instance_lock():
        log.warning("已有一份 %s 在运行，本次启动取消", config.APP_DISPLAY_NAME)
        notify(config.APP_DISPLAY_NAME, "已经在运行了",
               "不需要开第二份 —— 菜单栏或 Dock 里找一下。")
        return 0

    app = App()

    # 1) 空格：优先走 keytap（CGEventTap）—— 它能把空格**有条件地吃掉**，
    #    这样"我们主动控制播放器"和"空格落到播放器上"就不会撞成双触发。
    #
    #    ★ v1.2.1：Esc 也交给同一个 tap（on_esc）**旁听**。
    #      这样 pynput 的键盘监听在默认配置下完全不需要创建 ——
    #      而它正是那个"suddenly App 自己没了"的 SIGTRAP 崩溃的载体
    #      （pynput 会在事件 tap 线程上把系统定义事件转成 NSEvent，
    #       撞 HIToolbox 的 dispatch_assert_queue 断言）。
    #      详见 hotkey._avoid_pynput_media_key_crash 与 keytap 模块头注释。
    space_tap = None
    tap_ok = keytap.available()
    space_is_trigger = config.TRIGGER_KEY.strip().lower() in (
        "space", "空格", "空格键",
    )
    esc_handler = _make_esc_handler(app)
    if tap_ok:
        space_tap = keytap.SpaceTap(
            should_intercept_fn=app.space_should_intercept,
            on_space=app.on_space,
            on_esc=esc_handler,
        )
        try:
            space_tap.start()
        except Exception:
            log.exception("空格拦截启动失败，退回不吞键的老路径")
            space_tap = None
        if space_tap is not None and not space_tap.ok:
            space_tap = None
    else:
        log.info("空格拦截不可用（DL_SPACE_TAP 关掉或 pyobjc 缺失）")

    # Esc 是不是已经由上面那条 tap 接管了 —— 决定 pynput 那边还要不要为它留守。
    # 只认"tap 真的起来了"（space_tap.ok），没起来就得让 pynput 兜底。
    esc_by_tap = space_tap is not None

    # 2) 按键监听（独立线程；pynput 自己管理 run loop）
    #    空格归 keytap 管时，这里就不能再监听触发键了 —— 一次按键两条路径
    #    会把状态机连着推两次（查完词立刻又关窗），很难查。
    #    同理，Esc 归 keytap 时就告诉它别再管 Esc（见 esc_handled_elsewhere）。
    watcher = HotkeyWatcher(
        is_enabled=app.is_mode_on,
        on_trigger=app.on_trigger,
        on_cancel=None if esc_by_tap else esc_handler,
        # 关闭键（默认空格）：只有浮窗显示时才生效，其它时候空格归播放器。
        # 空格已经被 keytap 接管时，这两项必须关掉，否则同一个空格被处理两次。
        is_close_active=None if space_tap is not None else app.is_overlay_showing,
        on_close=None if space_tap is not None else app.on_space,
        watch_trigger=not (space_tap is not None and space_is_trigger),
        esc_handled_elsewhere=esc_by_tap,
    )
    try:
        watcher.start()
    except Exception as exc:
        log.exception("监听启动失败: %s", exc)
        notify(config.APP_DISPLAY_NAME, "按键监听失败", PERMISSION_HINT[:150])
        return 2

    # 3) 鼠标监听：浮窗显示时，点浮窗外面就关掉它（和按空格关是互补的两条路）
    clicker = ClickOutsideWatcher(
        is_active=app.is_overlay_showing,
        get_rect=app.overlay_geometry,
        on_outside=app.on_click_outside,
    )
    try:
        clicker.start()
    except Exception as exc:
        log.warning("鼠标监听启动失败（不影响其它功能）: %s", exc)

    if not is_accessibility_trusted():
        log.warning("辅助功能权限未授予，按键不会被捕获")
        # 主动弹系统授权框：不然用户根本不知道要授权哪个 App，
        # 系统设置列表里也不会自动出现这个 App。
        try:
            from utils import prompt_accessibility

            prompt_accessibility()
        except Exception:
            pass

    if demo:
        # 稍等一下再自动跑一次查词；90 秒后自动退出，避免留残留进程
        threading.Timer(6.0, app.test_lookup).start()
        # demo 专用：浮窗出来之后自动「按一次触发键」，用来验证
        # 「浮窗显示中按一下就关」这条交互（正式运行时由 pynput 触发，
        # 这里只是让 demo 能自证一下，不影响任何正常逻辑）。
        # --demo-ask 时不关窗，留着给追问用。
        if demo_ask:
            threading.Timer(11.0, lambda: _demo_ask(app)).start()
            threading.Timer(90.0, app.quit).start()
        else:
            threading.Timer(40.0, app.on_trigger).start()
            threading.Timer(90.0, app.quit).start()

    try:
        if config.MENUBAR_BACKEND == "qt":
            _run_qt_menubar(app, show_panel=not demo)
        else:
            _run_rumps_menubar(app)
    finally:
        watcher.stop()
        if space_tap is not None:
            space_tap.stop()

    # 【兜底强制退出】历史上这里是为了绕开 Playwright 那个不肯退出的 node
    # driver（主线程卡在 join + waitpid，表现是"点了退出，App 还挂在 Dock 里"）。
    # 网页版删掉后已经没有任何子进程了，但**这条 os._exit 保留** —— 它同时兜住了
    # 按键监听线程和 Qt 线程可能没退干净的情况，代价为零。
    log.info("收尾完成，强制结束进程")
    os._exit(0)


def _make_esc_handler(app: App):
    """Esc 也用来关悬浮窗（不吞键，只做响应）。

    区别对待：如果用户正在浮窗输入框里打字，Esc 只退出输入态（窗口留着），
    否则才关掉浮窗 —— 见 overlay.QtRuntime._esc。
    """
    def _esc() -> None:
        if app.runtime is not None and app.is_mode_on():
            app.runtime.post_esc()
    return _esc


def _run_rumps_menubar(app: App) -> None:
    """rumps 主线程 + Qt 后台线程。"""
    qt = QtThread(on_overlay_closed=app.on_overlay_closed,
                  on_overlay_ask=app.submit_question)
    qt.start()
    if not qt.runtime.wait_ready(10):
        log.warning("Qt 线程启动较慢，继续等待…")
    app.runtime = qt.runtime
    app.overlay_thread = qt

    import rumps

    from menubar import build_rumps_menubar

    bar = build_rumps_menubar(app)
    app.on_quit_hook = rumps.quit_application
    log.info("菜单栏已就绪（rumps）")
    bar.run()


def _run_qt_menubar(app: App, show_panel: bool = True) -> None:
    """Qt 独占主线程：QSystemTrayIcon + 悬浮窗 + 控制面板同线程，最稳。"""
    from menubar import build_qt_menubar
    from utils import set_dock_icon_visible

    runtime = QtRuntime(on_overlay_closed=app.on_overlay_closed,
                        on_overlay_ask=app.submit_question)
    app.runtime = runtime
    app.on_quit_hook = runtime.quit

    def _build_menu():
        tray = build_qt_menubar(app)
        # Dock 图标保持可见：菜单栏图标不一定显眼，Dock 是用户最后的后悔药，
        # 点它就能把控制面板叫回来。
        set_dock_icon_visible(True)
        return tray

    log.info("菜单栏已就绪（Qt）")
    runtime.run_blocking(
        menubar_builder=_build_menu,
        ctrl=app,
        show_panel_first=show_panel,   # 启动即弹控制面板，杜绝「双击后什么都没发生」
    )


if __name__ == "__main__":
    sys.exit(main())
