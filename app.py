"""应用编排层：状态机 + 截图 + 后台识别 + 悬浮窗 + 播放器控制。

状态机（这是“防误触”的核心）：
    idle    ── 按【空格】→ 先暂停画面 → 截图/AI 识别开始
    busy    ── 识别期间的空格一律忽略（但会被吞掉，不落到播放器上）
    showing ── 悬浮窗已显示：按【空格】= 关掉浮窗 + 继续播放
    asking  ── 浮窗里手打的追问正在等 AI 回复：空格忽略

【空格的三种含义】（2026-09-15 用户要求改成的最终形态）
    ① 视频在播   → 暂停、截图查词、弹浮窗
    ② 浮窗在显示 → 关掉浮窗、继续播放
    ③ 视频已停   → 继续播放
详见 App._three_state()。

★ 和老设计最大的区别：**播放器由我们主动控制**（player.py：
  IINA mpv IPC → 合成系统媒体键，两级降级），
  而不是"不吞键、让空格顺带落到播放器身上"。
  老方案有一个致命依赖："我们的 App 绝不能抢前台"。一旦抢走（历史 bug：
  Qt 的 raise_() 会顺带激活整个应用），空格就再也到不了播放器，
  用户必须先鼠标点一下播放器 —— 极难描述、极难复现。
  改成主动控制后，这件事和"焦点在谁手里"彻底解耦。

★ 另一个区别：空格**会被有条件的吃掉**（keytap.py 的 CGEventTap）。
  因为"我们自己控制播放器"和"空格也送到播放器"同时成立就会双触发
  （暂停→播放来回抵消）。但只在"浮窗显示中 / 前台是 IINA 这类播放器"时才吃，
  在 Word / Slack 里打字时绝不干预。

两条仍然成立的交互原则（用户反馈驱动，改动前先读一遍）：
1) 唯一会抢键盘焦点的时刻是**用户点了浮窗输入框**（那时才能打字）。
   一发送、一按 Esc、一关窗，立刻把焦点还给播放器
   （见 overlay._return_focus_to_player）。
2) 想"关掉浮窗但先别播"，用**点浮窗外面** —— 那条路完全不碰播放器状态。
"""
from __future__ import annotations

import subprocess
import threading
import time

import api_client
import config
import player
import screenshot
import settings
import viewlog
from api_client import ApiError
from utils import (
    PERMISSION_HINT,
    is_accessibility_trusted,
    log,
    notify,
    open_privacy_pane,
    screen_capture_authorized,
)

IDLE, BUSY, SHOWING, ASKING = "idle", "busy", "showing", "asking"
# 触发键给人看的名字（面板/通知/提示语统一用它，改 config.TRIGGER_KEY 就全跟着变）
KEY = config.TRIGGER_LABEL
# 关闭键（默认空格）给人看的名字
CLOSE_KEY = config.CLOSE_KEY_LABEL



class App:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # ★ 看剧模式**持久化**，且全新安装默认是开的。
        #
        #   以前这里写死 `False`：每次启动/升级 App 都回到"关"，用户按空格
        #   会被拦截层直接放行 —— **连一行日志都不产生**，看起来就是
        #   "这功能坏了"。而用户恰恰是"装完新版就试"，所以每次升级都踩。
        #   详见 settings.get_mode() 的注释。
        self._mode_on = settings.get_mode()
        self._state = IDLE
        self._status = "空闲"
        self._pushed_status = ""
        self._busy_since = 0.0
        self.runtime = None          # overlay.QtRuntime，由 main.py 注入
        self.on_quit_hook = None     # 由 main.py 注入
        self.overlay_thread = None   # 悬浮窗是否已就绪
        # API 后端的对话历史（OpenAI messages 格式）。
        # 无状态的 API 要支持浮窗追问，就得自己记住上下文：
        # 每次新查词（新截图）重置成 [user: 提示词+图]，追问时追加 assistant/user。
        self._api_messages: list = []
        # 最近一次浮窗追问问了什么。回答回来时要连问题一起写进观看记录，
        # 否则过几天回看，只剩一段没头没尾的答案。
        self._last_question = ""
        # 「上次是不是**我们**把视频停住的」。
        # 只在读不到播放状态时才用它（系统媒体键那条通道读不到）——
        # 见 _three_state。读得到真实状态时它会被同步成事实。
        self._video_paused_by_us = False
        # 空格"兜底"用的看门狗状态：这一下空格我们**确实**处理掉了吗？
        # 处理掉了就把 _space_done 置 True 并撤掉定时器；没处理掉（动作抛错 /
        # 卡住）就补发一次原生空格，保证用户至少还能播放/暂停。
        self._space_done = True
        self._space_watch: threading.Timer | None = None
        # 启动就把模式状态打出来。这条是给排查用的：以前"按空格没反应"
        # 的第一嫌疑就是"模式没开"，而它当时**不留任何痕迹**，只能靠猜。
        log.info("看剧模式：%s（按【%s】查词）",
                 "已开启" if self._mode_on else "关闭 —— 面板点一下即可开",
                 KEY)

    # ------------------------------------------------------------ 模式开关
    def is_mode_on(self) -> bool:
        with self._lock:
            return self._mode_on

    def set_mode(self, on: bool) -> None:
        with self._lock:
            self._mode_on = bool(on)
        log.info("看剧模式 -> %s", "开启" if on else "关闭")
        # 记到设置里 —— 下次启动直接恢复，不用用户再点一次。
        settings.set_mode(bool(on))
        if on:
            notify(config.APP_DISPLAY_NAME, "看剧模式已开启", f"按【{KEY}】开始识别截图")
        else:
            self._set_status("空闲")
            # 关闭时顺手收掉可能还挂着的悬浮窗，避免残留
            if self.runtime is not None:
                self.runtime.post_hide()

    def is_busy(self) -> bool:
        with self._lock:
            return self._state == BUSY

    def is_asking(self) -> bool:
        """是否正在等 AI 回答浮窗里的追问。"""
        with self._lock:
            return self._state == ASKING

    def is_working(self) -> bool:
        """截图识别 或 追问回答中 —— 面板用它禁用按钮、提示进度。"""
        with self._lock:
            return self._state in (BUSY, ASKING)

    def typing_in_overlay(self) -> bool:
        """用户是不是正在浮窗输入框里打字（打字时空格/N 都只是普通字符）。"""
        return bool(self.runtime is not None and self.runtime.typing_active())

    def is_overlay_showing(self) -> bool:
        """浮窗是否正显示着（键盘监听线程用它决定「空格要不要当关闭键」）。"""
        with self._lock:
            return self._state == SHOWING

    def overlay_geometry(self):
        """浮窗当前矩形 (x, y, w, h)，给鼠标监听判定「点没点在浮窗外面」。"""
        if self.runtime is None:
            return None
        try:
            return self.runtime.overlay_geometry()
        except Exception:
            return None

    def on_click_outside(self) -> None:
        """鼠标点在了浮窗外面 → 关掉浮窗（**不碰播放器状态**）。

        和「按空格关窗」的区别很重要：
          · 按空格关：浮窗收起后视频**继续播**（用户就是想马上接着看）；
          · 点外面关：完全不碰播放器 → 视频保持原样（通常还是暂停中，
            方便慢慢读结果、或者接着追问）。

        唯一的例外：输入框里还有没发出去的话时，只退出输入态、不关窗 ——
        免得手一抖把打了一半的问题弄丢了。
        """
        if self.runtime is not None:
            try:
                if self.runtime.typing_active() and self.runtime.draft_text().strip():
                    log.info("浮窗外点击：有未发送的追问，只退出输入态（不关窗）")
                    self.runtime.exit_input()
                    return
            except Exception:
                pass
        self._close_overlay(resume_playback=False, log_reason="浮窗外点击")

    def _close_overlay(self, resume_playback: bool, log_reason: str = "") -> bool:
        """把浮窗收掉。resume_playback=True 时顺便让视频继续播。

        抽成一个方法是为了让「按空格关」和「点外面关」共用同一套收尾
        （状态复位 + 状态栏 + 隐藏），只在"要不要动播放器"这一点上分叉 ——
        这个分叉是用户明确要的，别在别处再抄一份。
        """
        with self._lock:
            if self._state != SHOWING:
                return False
            self._state = IDLE
        log.info("关闭悬浮窗%s%s",
                 f"（{log_reason}）" if log_reason else "",
                 "并继续播放" if resume_playback else "，播放状态不变")
        self._set_status("空闲")
        if self.runtime is not None:
            self.runtime.post_hide()
        if resume_playback and config.PLAYER_CONTROL:
            try:
                if player.controller().set_paused(False).get("ok"):
                    self._video_paused_by_us = False
            except Exception:
                log.exception("恢复播放失败（浮窗已关，不影响继续用）")
        return True

    # ------------------------------------------------------------ 进度状态
    def _set_status(self, text: str) -> None:
        with self._lock:
            self._status = text
            if text and self._state in (BUSY, ASKING):
                self._busy_since = time.time()

    def status_text(self) -> str:
        """给控制面板/菜单看的实时进度文案（含已耗时，避免用户以为卡死）。"""
        with self._lock:
            st, state, since = self._status, self._state, self._busy_since
        if state not in (BUSY, ASKING):
            return st
        el = int(max(0.0, time.time() - since))
        if el >= 45:
            return f"{st}（已 {el} 秒，超过 {config.API_TIMEOUT_SEC} 秒会自动停止）"
        return f"{st}（{el}s）"


    # ------------------------------------------------------------ 触发
    def on_trigger(self) -> None:
        """触发键被按下（默认【空格】）→ 走三态交互。"""
        self._three_state()

    def on_space(self) -> None:
        """空格被按下（keytap 吞掉键之后回调到这里）→ 走三态交互。

        和 on_trigger 是同一个入口：默认配置下【空格】既是触发键也是关闭键，
        分成两个名字只是为了让 pynput 那条老路径和 keytap 这条新路径都能接上。

        ★ 外面包了一层"看门狗"：键已经被我们吞了，如果接下来这一步抛异常
          或者卡住，用户看到的就是**空格完全失效**（既没查词、也没暂停）。
          这比"不接管"糟糕得多 —— 不接管时空格本来就能原生控制播放器。
          所以这里盯着：2.5 秒内没真的做成一件事，就补发一个原生空格，
          把控制权还给播放器。宁可多一次，不可彻底失灵。
        """
        self._space_done = False
        watch = threading.Timer(config.SPACE_FALLBACK_SEC, self._space_fallback)
        watch.daemon = True
        prev = self._space_watch
        if prev is not None:
            try:
                prev.cancel()
            except Exception:
                pass
        self._space_watch = watch
        watch.start()
        try:
            self._three_state()
            self._space_done = True
        except Exception:
            log.exception("空格动作出错（补发原生空格兜底）")
            self._space_fallback()
        finally:
            if self._space_done:
                self._cancel_space_watch()

    def _cancel_space_watch(self) -> None:
        w = self._space_watch
        self._space_watch = None
        if w is not None:
            try:
                w.cancel()
            except Exception:
                pass

    def _space_fallback(self) -> None:
        """这一下空格我们没真正处理掉 → 补发一个原生空格给前台播放器。

        兜底而已，正常流程永远走不到这里。走到了说明动作链断了（抛错 / 卡死 /
        播放器通道全挂），此时让空格恢复它本来的功能，用户至少还能播和停。
        """
        if getattr(self, "_space_done", False):
            return
        self._space_done = True
        self._cancel_space_watch()
        try:
            from Quartz import (
                CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap,
            )

            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 49, down)
                CGEventPost(kCGHIDEventTap, ev)
            log.warning("空格兜底：已补发一次原生空格（本次接管没生效）")
        except Exception as exc:
            log.warning("空格兜底失败: %s", exc)

    # ------------------------------------------------------ 空格的三种含义
    def space_should_intercept(self) -> bool:
        """这一下空格要不要由我们**接管**（= 吃掉，不让它送到播放器）。

        ★ 这个函数会被 CGEventTap 的回调调用（keytap.py），运行在系统事件
          线程上，必须**又快又不抛异常**。所以它只做判断、不做事。

        要接管的两种情形：
          · 浮窗正显示着 → 这一下空格是"关窗并继续播"的指令；
          · 前台应用是已知播放器（IINA / VLC / QuickTime…）→ 看剧中的正常查词。

        ★ **「正在识别 / 正在等追问回答」故意不接管**（v1.2.3 修）：
          历史坑 —— 以前这里把这俩状态也吞掉，而 `_three_state()` 进到这两个
          状态又什么都不做，用户体感就是**"空格彻底失灵"**（既不查词、也不暂停），
          比"不接管"糟得多。放行之后，这几秒里空格照样能原生控制播放器。

        其余情况（在 Word / Slack / 浏览器里打字）一律不管，
        空格该怎么用怎么用 —— 这是"绝不无条件吞键"的底线。
        ★ v1.2.0 起浏览器**不再是**已知播放器：本程序不支持在浏览器里控制
          在线视频（那条链路已整体删除），所以前台是浏览器时一律放行。
        """
        if not config.CLOSE_KEY_ENABLED:
            return False
        with self._lock:
            if not self._mode_on:
                return False
            state = self._state
        # 用户正在浮窗里打字：空格就是空格（哪怕浮窗正显示着）
        if self.typing_in_overlay():
            return False
        # ★ 只有"浮窗显示着"才无条件接管。识别/追问进行中**故意放行**：
        #   这两个状态下 _three_state() 本来就不会做任何事，吞键只会让用户
        #   觉得"空格坏了"（见 docstring 里那段历史坑）。
        if state == SHOWING:
            return True
        if state in (BUSY, ASKING):
            return False
        return bool(player.frontmost_backend())

    def _three_state(self) -> None:
        """【空格】一个键三种含义，按"当前在播 + 浮窗在不在"分流。

            ① 视频在播   → 暂停、截图查词、弹浮窗
            ② 浮窗在显示 → 关掉浮窗、继续播放
            ③ 视频已停   → 继续播放

        为什么要"读播放器真实状态"来决定 ①/③：用户可能自己先按了暂停
        （或者刚看完一段停在那儿），这时按空格的本意是"接着看"，
        不该反而去查词。

        ★ 状态来源（v1.2.3）：IPC 精确 → **电源断言旁路** → "上次是不是我们停的"。
          以前只有前两者，而 IINA 内部**多 core 抢 socket** 会让 IPC 连到空转的
          core、状态恒为"读不到"，于是永远按"在播"处理 —— 媒体键又是**切换**语义，
          一旦实际是暂停状态，这一下就做了**反向动作**（用户想暂停，结果视频开始播）。
          电源断言把这格补上之后，反向动作随之消失。
        """
        if not config.CLOSE_KEY_ENABLED:
            return
        if self.typing_in_overlay():
            log.debug("正在浮窗里打字，空格就是空格，不拿来当快捷键")
            return
        with self._lock:
            if not self._mode_on:
                return
            state = self._state

        # ② 浮窗正显示 → 关掉它，并让视频继续播
        if state == SHOWING:
            self._close_overlay(resume_playback=True, log_reason=f"按{CLOSE_KEY}")
            return
        # 识别/追问进行中：不打断，也不去动播放器（否则会把暂停的视频点开）。
        # ★ `space_should_intercept()` 在这两个状态下**已经不再吞键**，所以正常
        #   走不到这里；留着是为了 `on_trigger()` 这种不经过 keytap 的入口
        #   （菜单 / 快捷键触发）也能安全通过。日志用 info：万一真走到，
        #   排查时能看见"按键被忽略了"，而不是一片空白。
        if state in (BUSY, ASKING):
            log.info("识别/追问进行中，忽略这次触发（按键已放行给播放器）")
            return

        ctl = player.controller()
        info = ctl.status()
        playing = info.get("playing")

        if playing is not None:
            # 读得到真实状态 → 顺手把它记成"我们眼里的状态"
            self._video_paused_by_us = (playing is False)
        elif self._video_paused_by_us:
            # 读不到状态（只有系统媒体键可用）→ 靠"上次是不是我们把它停住的"来判断。
            # 这一步很关键：用户查完词**点浮窗外**关窗时，视频还停在原地，
            # 这时他再按空格的本意是"接着看"，不是"再查一个词"。
            # 没有这条记忆的话，媒体键通道会把它当成"在播"，于是又暂停+查一次，
            # 用户看到的就是"空格按下去浮窗又弹出来了"，非常莫名。
            playing = False
        if playing is False:
            # ③ 视频本来就停着 → 用户要的是"继续播"
            log.info("视频当前是暂停 → 继续播放（不查词）")
            if ctl.set_paused(False).get("ok"):
                self._video_paused_by_us = False
            self._set_status("继续播放")
            return
        # ① 在播（或状态读不到，按"在播"处理）→ 先停住画面，再截图查词
        self._pause_then_lookup(ctl, info)

    def _pause_then_lookup(self, ctl, info: dict) -> None:
        """把画面停住，等它真的停稳，再走截图查词。"""
        r = ctl.set_paused(True)
        if r.get("ok"):
            self._video_paused_by_us = True
            # 播放器收到暂停指令 → "画面真的停住并重绘完"之间有延迟，
            # 截太早会截到下一帧（字幕可能已经翻页了）。
            settle = float(config.PLAYER_PAUSE_SETTLE_SEC)
            if settle > 0:
                time.sleep(settle)
            log.info("已暂停画面（%s，%s）后开始查词", r.get("backend"),
                     "精确" if r.get("exact") else "切换式")
        else:
            log.info("暂停画面没成功（%s），仍然继续查词", r.get("detail"))
        self._dispatch(force=False)

    def test_lookup(self) -> None:
        """面板/菜单里的『手动查一次』：不依赖键盘权限，用来验证链路。"""
        if not self._mode_on:
            self.set_mode(True)
        self._dispatch(force=True)

    def _dispatch(self, force: bool) -> None:
        """真正去"截一张图问一次"。状态机只负责决定"该不该"，这里只管"怎么做"。"""
        # 用户正在浮窗输入框里打字：触发键就是普通字符，不能当快捷键
        if self.typing_in_overlay():
            log.debug("正在浮窗里打字，忽略触发键")
            return
        # 只要开始一次查词，就记下时间戳 —— 这期间应用被系统激活（创建浮窗、
        # 截图、浏览器起来……）都算「自动动作」，别误弹控制面板（见
        # overlay._install_reactivate_filter）。
        if self.runtime is not None:
            self.runtime.mark_auto_ui()
        with self._lock:
            if not force and not self._mode_on:
                return
            if self._state == ASKING:
                log.debug("追问还在等 AI 回复，忽略本次触发")
                return
            if self._state == BUSY:
                log.debug("识别中，忽略本次触发")
                return
            self._state = BUSY
            self._busy_since = time.time()
            self._status = "正在截图…"
        # 立刻给出屏幕反馈（小胶囊），别让用户对着黑屏猜程序是不是活着
        self._set_status(self._status)
        self._capture_and_ask()

    def _capture_and_ask(self) -> None:
        path = None
        try:
            path = screenshot.capture_fullscreen()
        except Exception as exc:
            log.error("截图失败: %s", exc)
            with self._lock:
                self._state = IDLE
            self._set_status("截图失败")
            notify(config.APP_DISPLAY_NAME, "截图失败", str(exc)[:120])
            self._show("截图失败：\n" + str(exc)[:300], title="出错了")
            return

        shot = path
        self._set_status("正在识别截图…")
        # ★ v1.2.2：这里是**唯一**一条查词通道（网页版豆包已整体删除）。
        self._ask_api(shot)

    # ------------------------------------------------------------ 查词主链路
    def _ask_api(self, shot) -> None:
        """把截图发给大模型 API（后台线程跑，不阻塞 Qt 主线程）。

        后端是无状态的 HTTP 接口，要支持浮窗追问就得**自己记住上下文**：
        每次新查词都重建 messages = [提示词 + 这张图]，追问时再往后追加
        assistant/user —— 见 submit_question / _ask_api_followup。
        """
        prompt = settings.get_prompt()
        cfg = settings.provider_config()

        def _run():
            try:
                data_url = api_client.encode_image(shot)
                self._api_messages = [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }]
                self._set_status(f"正在问 {cfg['label']}（{cfg['model']}）…")
                text = api_client.chat(self._api_messages, cfg=cfg)
                self._api_messages.append({"role": "assistant", "content": text})
                self._finish_ok(text, shot)
            except Exception as exc:  # noqa: BLE001 —— 后台线程必须兜住
                self._finish_err(exc, shot)

        threading.Thread(target=_run, name="api-ask", daemon=True).start()

    def _finish_ok(self, text: str, shot) -> None:
        screenshot.cleanup(shot)
        from reply_clean import clean_reply, is_empty_answer, normalize_answer

        # ★ 顺序很重要：**先**判断"这句没有生词"，**再**做格式归一化。
        #   反过来的话，模型回的「无」会先被归一化成「译文：无」，
        #   于是那句友好的「好好看剧吧 🍿」永远也显示不出来。
        cleaned = clean_reply(text)
        if not cleaned:
            body = ("没有识别到有效文本。\n"
                    "可能是：截图时字幕还没出现 / 模型没看清这张图。")
            kind = "失败"
        elif is_empty_answer(cleaned):
            body = "这一句没有生词和俚语，好好看剧吧 🍿"
            kind = "无内容"
        else:
            # 归一化：模型这次不管写成「生词：」还是「单词 —— 」还是带编号，
            # 到浮窗里都是同一副样子（用户要求"格式非常稳定"）。
            body = normalize_answer(text) or cleaned
            kind = "查词"
        log.info("查词成功，显示 %d 字: %s", len(body), body[:60].replace("\n", " "))
        # 观看记录：只记 AI 真说了内容的那些；"没有生词"是我们自己的提示语，
        # 记进去只会把记录撑成一堆噪音。
        if kind == "查词":
            viewlog.record(body, kind=kind)
        self._show(body)
        self._set_status(f"已显示结果（按【{CLOSE_KEY}】关闭）")

    def _finish_err(self, exc: Exception, shot) -> None:
        screenshot.cleanup(shot)
        # ApiError 里带了「给用户看的下一步建议」，要完整露出来，
        # 不能像普通异常那样只截 str(exc) 的前 120 字。
        if isinstance(exc, ApiError):
            msg = exc.user_text()
        else:
            msg = str(exc) or exc.__class__.__name__
        log.error("查词失败: %s", msg)

        notify(config.APP_DISPLAY_NAME, "查词失败", msg[:120])
        hint = ""
        # 【2026-09-14 修】错误浮窗**也走 SHOWING**（以前用 force_idle_after=True
        # 直接回 IDLE）。因为浮窗角上写着「再按一次【N】关闭」，可状态机认为
        # 自己已经空闲 —— 于是用户按 N 不是关窗，而是**又发起一次查词**，
        # 旧浮窗被新浮窗顶掉，看起来就是「按了不关闭」。用户实测反馈过。
        # 现在统一：只要浮窗显示着，下一次按键就是关掉它（再按一次才是重试）。
        self._show("查词失败：\n" + msg[:400] + hint, title="出错了")
        self._set_status("上次查词失败：" + msg.splitlines()[0][:42])

    def _overlay_title(self) -> str:
        """浮窗顶上那行小字。带上**真实服务商名**，别再写死"豆包"。

        v1.2.2 之前这里硬编码 "豆包 · 字幕生词"，但那会儿查词其实走的是
        DeepSeek 的 API —— 标题和事实不一致，用户看着也容易误会
        （"我明明填的是 deepseek 的 key，怎么显示豆包"）。
        现在按当前选中的服务商取名字：「服务商 · 字幕生词」。
        """
        try:
            label = str(settings.provider_config().get("label") or "")
        except Exception:
            return "字幕生词"
        short = label.split("（")[0].split("(")[0].strip()
        return f"{short} · 字幕生词" if short else "字幕生词"

    def _show(self, body: str, title: str | None = None,
              force_idle_after: bool = False) -> None:
        with self._lock:
            self._state = IDLE if force_idle_after else SHOWING
        if self.runtime is None:
            log.warning("Qt 运行时未就绪，结果只写日志:\n%s", body)
            return
        self.runtime.post_show(title or self._overlay_title(), body)

    # ------------------------------------------------------------ 浮窗追问
    def _overlay_visible(self) -> bool:
        """浮窗现在还在屏幕上吗？

        【原则：不确定就当成「在」】这个判断决定追问的回答往哪儿送：
          · 返回 True  → 调 post_overlay_answer()，浮窗真在就显示出来；
                          浮窗其实已经关了的话，QtRuntime 那边只会记条日志 —— 无害。
          · 返回 False → 回答直接被丢掉，用户看到的就是「问了没反应」。
        两者代价完全不对称，所以只在**确定没有**时才返回 False。
        （历史事故：QtRuntime 曾经没有 has_overlay()，这里每次抛
          AttributeError，追问的回答 100% 被丢掉。）
        """
        rt = self.runtime
        if rt is None:
            return False
        fn = getattr(rt, "has_overlay", None)
        if fn is None:
            log.warning("运行时（%s）没有 has_overlay()，按「浮窗还在」处理，"
                        "避免把回答丢掉", type(rt).__name__)
            return True
        try:
            return bool(fn())
        except Exception:
            log.exception("has_overlay() 调用异常，按「浮窗还在」处理")
            return True

    def submit_question(self, text: str) -> None:
        """浮窗输入框里手打的问题（由 overlay 回调，跑在 Qt 线程）。

        走的是**同一条 API 对话历史**（self._api_messages），模型因此看得见
        刚才那张截图和它自己的解释 —— 追问才有意义
        （"这句里的 face the music 是什么语气"）。
        """
        q = (text or "").strip()
        if not q:
            return
        # 记下这一问 —— 回答回来时要连同问题一起写进观看记录。
        self._last_question = q
        with self._lock:
            if self._state == ASKING:
                log.debug("上一个追问还没答完，忽略这次")
                return
            self._state = ASKING
            self._busy_since = time.time()
            self._status = "正在把问题发给 AI…"
        log.info("浮窗追问（%d 字）：%s", len(q), q[:80].replace("\n", " "))
        if self.runtime is not None:
            self.runtime.post_overlay_status("已发送，等待回复…")
        self._ask_api_followup(q)

    def _ask_api_followup(self, q: str) -> None:
        """把追问追加到 messages 历史后面接着问。

        ★ 提示词本身也在这条历史里（它是第一条 user 消息的一部分），所以
          「追问不受模板约束」这件事**写在提示词里**（config._FOLLOWUP_RULE），
          否则模型会把"只输出那五行"当成对整个对话都生效 → 拒答。
        """
        cfg = settings.provider_config()
        msgs = list(self._api_messages)
        if not msgs:
            # 还没有任何历史（异常情况：没查过词就先追问）→ 当纯文本问题
            msgs = [{"role": "user", "content": q}]
        else:
            msgs.append({"role": "user", "content": q})

        def _run():
            try:
                self._set_status(f"正在问 {cfg['label']}…")
                text = api_client.chat(msgs, cfg=cfg)
                self._api_messages = msgs
                self._api_messages.append({"role": "assistant", "content": text})
                self._finish_answer_ok(text)
            except Exception as exc:  # noqa: BLE001
                self._finish_answer_err(exc)

        threading.Thread(target=_run, name="api-followup", daemon=True).start()

    def _finish_answer_ok(self, text: str) -> None:
        """追问的回答到了。

        ★ v1.2.2：这里**不再无条件 normalize_answer()**。

        用户原话：「如果我继续去追问他一些问题的话，他直接就拒绝回答了」
        ＋「可以不用按照我刚才的模版回答我」。也就是说追问的回答**允许是自由
        散文**（分点、列表、几段话、带 **markdown** 加粗）。

        而 normalize_answer() 是"字段名归一化"用的：它得给每行**猜**一个字段名，
        于是一段正常解释会被改写成

            译文：能，但语气差一点，得看你想给老板什么感觉：
            句子：get around to 的核心是"一直想做、拖到现在才做"
            俚语：I'll get right on it. —— 马上办…

        —— 字段名全是错的，列表符号也被吃掉，浮窗再照着字段名配上彩色标签，
        就成了大型误标现场。

        所以现在的规则：**回答自己写成字段模板（≥2 行带字段名）才归一化**，
        否则原样保留（只做 clean_reply 的脱敏清洗）。判定见 reply_clean.is_structured。
        """
        from reply_clean import clean_reply, is_structured, normalize_answer

        # keep_markdown=True：自由格式那条路要**留着 `**加粗**` 和 `·` 列表符号**，
        # 浮窗才能真的渲染出加粗/缩进（见 overlay.format_prose 与 clean_reply 的说明）。
        # 字段模板那条路无所谓 —— normalize_answer 内部会自己再洗一遍。
        raw = clean_reply(text, keep_markdown=True).strip()
        if is_structured(raw):
            body = normalize_answer(raw)
        else:
            # 自由散文：原样显示。绝不在这里"帮它排版" ——
            # 它写什么样，用户就该看到什么样。
            body = raw
        body = body or "（这次没有返回内容）"
        ask, self._last_question = self._last_question, ""
        visible = self._overlay_visible()
        with self._lock:
            self._state = SHOWING if visible else IDLE
        log.info("追问回答 %d 字（%s）", len(body),
                 "字段模板" if is_structured(raw) else "自由格式")
        self._set_status("已追加回答（按【%s】关闭）" % CLOSE_KEY)
        # 追问的回答同样是 AI 输出 → 进观看记录（连"问了什么"一起记，
        # 不然过几天回看只剩一段没头没尾的答案）。
        viewlog.record(body, ask=ask, kind="追问")
        if visible:
            self.runtime.post_overlay_answer(body)
        else:
            log.warning("浮窗已关闭，追问回答只写日志:\n%s", body[:400])

    def _finish_answer_err(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            msg = exc.user_text()
        else:
            msg = str(exc) or exc.__class__.__name__
        log.error("追问失败: %s", msg)
        visible = self._overlay_visible()
        with self._lock:
            self._state = SHOWING if visible else IDLE
        self._set_status("追问失败：" + msg.splitlines()[0][:42])
        notify(config.APP_DISPLAY_NAME, "追问失败", msg[:120])
        if visible:
            self.runtime.post_overlay_answer("追问失败：" + msg[:300], error=True)

    # ------------------------------------------------------------ 悬浮窗回调
    def on_overlay_closed(self) -> None:
        """悬浮窗被 ✕ / Esc / 自动超时关掉时，把状态复位。"""
        with self._lock:
            if self._state == SHOWING:
                self._state = IDLE
        self._set_status("空闲")
        log.debug("悬浮窗已销毁，状态复位")

    # ------------------------------------------------------------ 首次引导
    def show_alert(self, title: str, body: str, actions=None) -> None:
        """弹一个需要点击的提示窗。actions = [(按钮文字, 回调), ...]"""
        if self.runtime is not None:
            self.runtime.post_alert(title, body, actions)

    def show_panel(self) -> None:
        """打开控制面板。

        三个入口都会调它：菜单栏图标左键、Dock 图标、菜单里的「打开控制面板」。
        注意：**查词过程中不会自动弹它**（见 overlay._install_reactivate_filter），
        否则用户每按一次键都会被一个设置窗口糊脸。
        """
        if self.runtime is not None:
            self.runtime.post_panel()
        else:
            log.warning("Qt 运行时未就绪，控制面板打不开")

    def apply_overlay_font(self, px: int | None = None) -> None:
        """设置面板改了字号 → 记下来 + 让当前浮窗立刻按新字号重画。

        `px=None` 表示"不写设置，只重画"（面板拖动过程中的实时预览用）。

        注意：字号**不缓存在内存里** —— overlay.body_font_px() 每次渲染都
        重新读 settings，所以这里只要落盘 + 通知重画就够了，
        不存在"两处各存一份、改了一个另一个忘同步"的问题。
        """
        if px is not None:
            try:
                settings.set_overlay_font_size(int(px))
            except Exception as exc:
                log.warning("记住字号失败: %s", exc)
        if self.runtime is not None:
            try:
                self.runtime.refresh_overlay_style()
            except Exception:
                log.debug("通知浮窗刷新字号失败（可忽略）")

    def open_viewlog(self) -> None:
        """在 Finder 里定位桌面上的观看记录（用户问"记到哪儿去了"时用）。"""
        try:
            import viewlog

            viewlog.open_in_finder()
        except Exception as exc:
            log.warning("打开观看记录失败: %s", exc)

    def set_viewlog_enabled(self, on: bool) -> None:
        """开关"把 AI 输出记到观看记录"。"""
        try:
            import settings

            settings.set_viewlog_enabled(bool(on))
        except Exception as exc:
            log.warning("切换观看记录失败: %s", exc)
            return
        self._set_status("观看记录已" + ("打开" if on else "关闭"))
        log.info("观看记录开关 → %s", "开" if on else "关")

    # ------------------------------------------------ 观看记录：文件夹 / 编号
    def viewlog_dir_text(self) -> str:
        """当前生效的观看记录目录（面板上显示用）。"""
        try:
            import settings

            return str(settings.effective_viewlog_dir())
        except Exception:
            return str(config.VIEWLOG_DIR_DEFAULT)

    def apply_viewlog_dir(self, path: str) -> None:
        """面板里换了记录文件夹 → 记下来。空串 = 恢复默认（桌面）。

        换了目录等于换一本新本子，所以顺手把内存里"当前正在写的那本"清掉 ——
        否则下一次写入还会往**旧目录**那份文件里追加。
        """
        try:
            import settings

            if not settings.set_viewlog_dir(str(path or "").strip()):
                self._set_status("目录没保存成功")
                return
            where = settings.ensure_viewlog_dir()
        except Exception as exc:
            log.warning("记住观看记录目录失败: %s", exc)
            self._set_status("目录没保存成功")
            return
        try:
            import viewlog

            viewlog.reset_current()
        except Exception:
            log.debug("重置当前记录文件失败（可忽略）")
        self._set_status(f"观看记录目录 → {where}")
        log.info("观看记录目录 → %s", where)

    def renumber_viewlog(self) -> None:
        """把当前这本记录的编号重排成 001 起（面板按钮）。

        被人工删改过的本子（前几十条被删掉、抬头也丢了）一键恢复整齐。
        会先把原文件备份成 `.bak`，再原子替换写回。
        """
        try:
            import viewlog

            n, p = viewlog.renumber()
        except Exception as exc:
            log.warning("重新编号失败: %s", exc)
            self._set_status("重新编号失败")
            return
        if not n or p is None:
            self._set_status("没有可重新编号的记录")
            return
        self._set_status(f"已重新编号：{p.name}（{n} 条）")

    def reset_overlay_size(self) -> None:
        """把悬浮窗尺寸恢复成默认（宽度默认、高度自动适应内容），位置保留。"""
        try:
            import settings

            settings.reset_overlay_size()
        except Exception as exc:
            log.warning("重置悬浮窗尺寸失败: %s", exc)
            return
        self._set_status("悬浮窗大小已重置")
        notify(config.APP_DISPLAY_NAME, "悬浮窗大小已重置", "下次显示时恢复默认大小")

    def reset_overlay_geometry(self) -> None:
        """把悬浮窗的**位置和大小**都恢复默认（底部居中 + 自适应高度）。"""
        try:
            import settings

            settings.reset_overlay_geometry()
        except Exception as exc:
            log.warning("重置悬浮窗位置失败: %s", exc)
            return
        self._set_status("悬浮窗位置与大小已重置")
        notify(config.APP_DISPLAY_NAME, "悬浮窗已重置",
               "下次显示时回到屏幕底部居中、默认大小")
        log.info("用户重置了悬浮窗位置与大小")

    def first_run_check(self) -> None:
        """启动后自检两个系统权限，缺什么就弹窗引导（不阻塞启动）。"""
        if not is_accessibility_trusted() or not screen_capture_authorized():
            self.show_permission_help()
        else:
            log.info("权限自检通过")

    def show_permission_help(self) -> None:
        """权限说明窗（菜单里随时可点）。大白话 + 直达按钮。"""
        ok_ax = is_accessibility_trusted()
        ok_sc = screen_capture_authorized()

        if ok_ax and ok_sc:
            self.show_alert(
                "权限都齐了 ✅",
                "辅助功能、屏幕录制都已经授权，可以正常工作了。\n\n"
                f"按【{KEY}】没反应的话，检查一下菜单里『开启看剧模式』有没有勾上。",
                [("知道了", None)],
            )
            return

        lines = ["这个 App 还需要你在系统设置里点两下。", ""]
        if not ok_ax:
            lines.append(f"① 辅助功能 —— 让它能“听见”你按{KEY}键")
        if not ok_sc:
            lines.append("② 屏幕录制 —— 让它能截图")
        lines += [
            "",
            "怎么做：",
            "　点下面的按钮 → 系统设置窗口会打开",
            "　→ 在列表里找到「" + config.APP_DISPLAY_NAME + "」并打开开关",
            "　（列表里没有？点左下角 ➕，去『应用程序』里选"
            + config.APP_DISPLAY_NAME + "）",
            "　→ 授权后从菜单退出 App，再重新打开（权限必须重启才生效）",
        ]

        actions = []
        if not ok_ax:
            actions.append(("去授权：辅助功能", lambda: open_privacy_pane("Privacy_Accessibility")))
        if not ok_sc:
            actions.append(("去授权：屏幕录制", self._request_screen_capture))
        actions.append(("知道了", None))
        self.show_alert("权限设置说明", "\n".join(lines), actions)

    @staticmethod
    def _request_screen_capture() -> None:
        from utils import request_screen_capture

        request_screen_capture()

    def open_permissions(self, which: str) -> None:
        anchor = "Privacy_ScreenCapture" if which == "screencapture" else "Privacy_Accessibility"
        open_privacy_pane(anchor)
        notify(config.APP_DISPLAY_NAME, "权限设置", PERMISSION_HINT[:180])

    # ------------------------------------------------------------ 提示词编辑
    def open_prompt_editor(self) -> None:
        """打开『编辑提示词』窗口（改动立即生效，不用重启 App）。"""
        if self.runtime is None:
            log.warning("Qt 运行时未就绪，提示词编辑器打不开")
            return

        def _open() -> None:
            from prompt_edit import open_prompt_editor

            open_prompt_editor(self)

        self.runtime.post_call(_open)

    def prompt_preview(self) -> str:
        """给面板显示的一行提示词摘要。"""
        import settings

        p = settings.get_prompt().replace("\n", " ").strip()
        return (p[:34] + "…") if len(p) > 34 else p

    def prompt_is_customized(self) -> bool:
        import settings

        return settings.is_customized()

    def open_log(self) -> None:
        try:
            subprocess.Popen(["/usr/bin/open", "-R", str(config.LOG_FILE)])
        except Exception as exc:
            log.warning("打开日志失败: %s", exc)

    def quit(self) -> None:
        log.info("退出应用")
        # ★ 顺序很重要：**先**发退出请求，**再**收尾 worker。
        # 反过来的话，一旦 worker.stop() 卡住（关无头浏览器时真会卡），
        # 退出请求就永远发不出去 —— 用户看到的就是"点了退出，App 还挂在 Dock 里"。
        # on_quit_hook（QtRuntime.quit）里带了看门狗，发出去就等于保底能退掉。
        if self.on_quit_hook is not None:
            try:
                self.on_quit_hook()
            except Exception:
                log.exception("退出钩子执行失败")
