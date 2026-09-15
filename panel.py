"""控制面板窗口（双击 App 后你能立刻看到的东西）。

为什么要它：菜单栏图标在某些 macOS 版本 / 深色主题下可能很不显眼，
用户双击 App 后如果只看到「什么都没发生」，就会以为程序坏了。
所以启动时直接弹一个看得见的窗口，所有操作都摆成按钮。

关掉窗口 ≠ 退出程序：程序继续在菜单栏驻留，点 Dock 图标可以再打开。
"""
from __future__ import annotations

import config

# 触发键给人看的名字（改 config.TRIGGER_KEY，面板文案自动跟着变）
KEY = config.TRIGGER_LABEL

# 面板配色（跟随悬浮窗的深色系，保证在浅色/深色系统下都清楚）
PANEL_BG = "#1c1f24"
PANEL_CARD = "#252a31"
PANEL_BORDER = "rgba(255,255,255,0.10)"
FG = "#eceef1"
FG_DIM = "rgba(236,238,241,0.62)"
ACCENT = "#7cc4ff"
ACCENT_BG = "rgba(124,196,255,0.18)"
OK_COLOR = "#5fd38a"
WARN_COLOR = "#ffbf5f"

# 「字号示例」那张小卡片的底色 —— **故意直接用悬浮窗的卡片色**。
# 示例要是用面板自己的灰色底，用户就没法判断"调成这个大小，压在全屏视频上
# 到底看不看得清"。底色一致，看到的才是真的。
_pb_r, _pb_g, _pb_b = config.OVERLAY_BG
PREVIEW_BG = f"rgb({_pb_r},{_pb_g},{_pb_b})"

_QSS = f"""
QWidget#panel {{
    background-color: {PANEL_BG};
}}
QScrollArea#panelscroll {{
    background: transparent;
    border: none;
}}
/* 滚动区的内容容器和它的 viewport 都要透明，否则会在面板底色上盖一层
   系统默认的浅灰，深色主题下非常刺眼。 */
QScrollArea#panelscroll > QWidget > QWidget {{
    background: transparent;
}}
QWidget#panelbody {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 0 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.22);
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.34); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QLabel {{
    color: {FG};
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
}}
QLabel#h1  {{ font-size: 21px; font-weight: 600; }}
QLabel#sub {{ color: {FG_DIM}; font-size: 12px; }}
QLabel#sec {{ color: {FG_DIM}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#stat {{ color: {FG_DIM}; font-size: 13px; }}
QLabel#progress {{ color: {ACCENT}; font-size: 12px; }}
QLabel#note {{ color: {FG_DIM}; font-size: 11px; }}
QFrame#card {{
    background-color: {PANEL_CARD};
    border: 1px solid {PANEL_BORDER};
    border-radius: 12px;
}}
QPushButton {{
    color: {FG};
    background: rgba(255,255,255,0.07);
    border: 1px solid {PANEL_BORDER};
    border-radius: 9px;
    padding: 10px 16px;
    font-size: 13px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
}}
QPushButton:hover  {{ background: rgba(255,255,255,0.13); }}
QPushButton:pressed{{ background: rgba(255,255,255,0.05); }}
QPushButton#primary {{
    background: {ACCENT_BG};
    border: 1px solid rgba(124,196,255,0.5);
    font-size: 15px;
    font-weight: 600;
    padding: 14px 20px;
}}
QPushButton#primary:hover {{ background: rgba(124,196,255,0.30); }}
QPushButton#primary:checked {{
    background: rgba(95,211,138,0.22);
    border: 1px solid rgba(95,211,138,0.55);
}}
QPushButton:disabled {{
    color: rgba(236,238,241,0.35);
    background: rgba(255,255,255,0.03);
}}
QLineEdit {{
    color: {FG};
    background: rgba(0,0,0,0.28);
    border: 1px solid {PANEL_BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 12px;
    font-family: 'PingFang SC','Menlo',monospace;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border: 1px solid rgba(124,196,255,0.55); }}
QComboBox {{
    color: {FG};
    background: rgba(255,255,255,0.07);
    border: 1px solid {PANEL_BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 12px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
}}
QComboBox:hover {{ background: rgba(255,255,255,0.13); }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_CARD};
    color: {FG};
    border: 1px solid {PANEL_BORDER};
    selection-background-color: {ACCENT_BG};
}}
QFrame#enginecard {{
    background-color: {PANEL_CARD};
    border: 1px solid {PANEL_BORDER};
    border-radius: 12px;
}}
QTabWidget::pane {{
    background-color: {PANEL_CARD};
    border: 1px solid {PANEL_BORDER};
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    color: {FG_DIM};
    background: transparent;
    border: none;
    padding: 7px 14px;
    margin-right: 4px;
    font-size: 13px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
}}
QTabBar::tab:hover {{ color: {FG}; }}
QTabBar::tab:selected {{
    color: {FG};
    background: {ACCENT_BG};
    border: 1px solid rgba(124,196,255,0.45);
    border-radius: 9px;
    font-weight: 600;
}}
/* ---------- 字号滑块 ---------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: rgba(255,255,255,0.14);
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    height: 4px;
    background: rgba(124,196,255,0.65);
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 15px;
    height: 15px;
    margin: -6px 0;
    background: {FG};
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
/* ---------- 字号示例卡片 ---------- */
QFrame#fontpreview {{
    background-color: {PREVIEW_BG};
    border: 1px solid {PANEL_BORDER};
    border-radius: 10px;
}}
QFrame#fontpreview QLabel {{
    background: transparent;
}}
QCheckBox {{
    color: {FG};
    font-size: 13px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid rgba(255,255,255,0.30);
    background: rgba(0,0,0,0.25);
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}
"""


def make_panel_class():
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
    )
    import settings
    from utils import log

    class _NoWheelCombo(QComboBox):
        """滚轮**只有在它已经拿到焦点时**才切换选项。

        为什么要有这个类：面板被塞进了 QScrollArea，而 QComboBox 的默认行为
        是"吃掉滚轮事件来换选项"。于是用户往下滚页面时，鼠标一旦扫过
        「截图范围 / 服务商 / 模型」这几行，**页面不动、选项却被悄悄改了** ——
        既让人以为滚不动，又可能把配置改成别的服务商/模型。
        所以没焦点时把事件交给外层滚动区去滚。

        ⚠️ 只 `ev.ignore()` 是**不够**的：实测（tools/test_panel_scroll.py）
        Qt 并不会把这个被忽略的滚轮事件一路冒泡到 QScrollArea 的 viewport，
        页面照样不动。必须显式转给最近的祖先滚动区的 viewport。
        """

        def wheelEvent(self, ev) -> None:            # noqa: N802（Qt 命名）
            if self.hasFocus():
                super().wheelEvent(ev)               # 用户确实在操作它，照常切换
                return
            ev.ignore()
            p = self.parentWidget()
            while p is not None and not isinstance(p, QScrollArea):
                p = p.parentWidget()
            if p is not None:
                # 直接发给 viewport：QAbstractScrollArea 在 viewport 上装了过滤器，
                # 会把它当成自己的滚轮事件去滚动（不会绕回来，没有递归危险）。
                QApplication.sendEvent(p.viewport(), ev)

    def _has_custom_rect() -> bool:
        """用户是否已经自己框过一次区域（没框过就别老弹框选窗口烦人）。"""
        return bool(settings.load().get("capture_rect_set"))

    def _picker_active() -> bool:
        """现在有没有一个框选窗口正开着。

        区域选择器在 region_picker 里是**模块级单例**（_ACTIVE 按住防 GC），
        所以"有没有开着"要去问它，而不是靠面板自己记 —— 面板记的那份一旦
        与实际不一致（历史上就是这么翻车的），按钮就会永远灰着。
        """
        try:
            import region_picker

            return bool(region_picker.active())
        except Exception:
            return False

    class _Panel(QWidget):
        def __init__(self, ctrl) -> None:
            super().__init__()
            self._ctrl = ctrl
            self.setObjectName("panel")
            self.setWindowTitle(config.APP_DISPLAY_NAME)
            self.setStyleSheet(_QSS)
            self.setFixedWidth(540)

            # ---------- 头部 ----------
            h1 = QLabel(config.APP_DISPLAY_NAME)
            h1.setObjectName("h1")
            sub = QLabel(f"美剧沉浸式 AI 查词 · 开启看剧模式后，按【{KEY}】识别当前字幕")
            sub.setObjectName("sub")
            sub.setWordWrap(True)

            # ---------- 状态卡 ----------
            card = QFrame()
            card.setObjectName("card")
            self.lbl_mode = QLabel()
            self.lbl_mode.setObjectName("stat")
            self.lbl_perm = QLabel()
            self.lbl_perm.setObjectName("stat")
            # 这行原来叫 lbl_login（网页版豆包时代显示"登录状态"）。
            # v1.2.2 删掉网页版之后它只剩"按【空格】会发生什么"这段提示，
            # 所以改名 lbl_tip —— 名字和内容对不上是下一个人的坑。
            self.lbl_tip = QLabel()
            self.lbl_tip.setObjectName("stat")
            self.lbl_tip.setWordWrap(True)
            self.lbl_step = QLabel()
            self.lbl_step.setObjectName("progress")
            self.lbl_step.setWordWrap(True)
            # 以前这里漏建了 lbl_prompt —— _refresh 里对它 setText 被 except 吞掉，
            # 面板上永远看不到"提示词现在是什么"。现在补上。
            self.lbl_prompt = QLabel()
            self.lbl_prompt.setObjectName("note")
            self.lbl_prompt.setWordWrap(True)
            cbox = QVBoxLayout(card)
            cbox.setContentsMargins(16, 13, 16, 13)
            cbox.setSpacing(6)
            for w in (self.lbl_mode, self.lbl_perm,
                      self.lbl_step, self.lbl_tip, self.lbl_prompt):
                cbox.addWidget(w)

            # ---------- 主按钮：看剧模式 ----------
            self.btn_mode = QPushButton()
            self.btn_mode.setObjectName("primary")
            self.btn_mode.setCheckable(True)
            self.btn_mode.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_mode.clicked.connect(
                lambda checked: self._ctrl.set_mode(bool(checked))
            )

            # ---------- 次按钮 ----------
            def _mk(text: str, cb, tip: str = "") -> QPushButton:
                b = QPushButton(text)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                if tip:
                    b.setToolTip(tip)
                b.clicked.connect(cb)
                return b

            self.btn_perm = _mk("检查 / 修复权限", self._ctrl.show_permission_help,
                                f"辅助功能（听{KEY}键）、屏幕录制（截图）")
            self.btn_test = _mk("▶ 手动查一次（不用键盘）", self._ctrl.test_lookup,
                                "不用按键，直接截屏一次并查词 —— 用来验证整条链路")
            self.btn_prompt = _mk("✏️ 编辑提示词", self._ctrl.open_prompt_editor,
                                  "改发送给 AI 的提示词 —— 保存后立即生效，不用重启")
            self.btn_log = _mk("打开日志", self._ctrl.open_log)
            self.btn_size = _mk("🪟 重置悬浮窗大小", self._ctrl.reset_overlay_size,
                                "把悬浮窗恢复成默认宽度、高度自动适应内容（位置保留）")
            self.btn_geo = _mk("📍 重置位置与大小", self._ctrl.reset_overlay_geometry,
                               "悬浮窗会记住你上次摆的位置和大小；点这里回到"
                               "「屏幕底部居中 + 默认大小」")

            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(self.btn_test, 3)      # 手动查一次
            row1.addWidget(self.btn_perm, 2)      # 权限

            row2 = QHBoxLayout()
            row2.setSpacing(10)
            row2.addWidget(self.btn_prompt, 3)
            row2.addWidget(self.btn_log, 2)

            row4 = QHBoxLayout()
            row4.setSpacing(10)
            row4.addWidget(self.btn_size, 2)
            row4.addWidget(self.btn_geo, 2)

            # ---------- 截图范围 ----------
            self.cmb_capture = _NoWheelCombo()
            self.cmb_capture.setCursor(Qt.CursorShape.PointingHandCursor)
            for key, label in config.CAPTURE_MODES.items():
                self.cmb_capture.addItem(label, key)
            self.cmb_capture.setToolTip(
                "决定每次截屏幕的哪一块发给模型 —— 截得越准，出结果越快。"
            )
            self.cmb_capture.currentIndexChanged.connect(self._on_capture_changed)
            self.btn_pick = _mk("框选…", self._pick_region,
                                "自己拖一个框，作为以后每次截图的区域。\n"
                                "★ 随时都能再框一次 —— 按钮不会因为框过一遍就变灰。")
            self.btn_pick.setFixedWidth(76)
            self.lbl_capture = QLabel("")
            self.lbl_capture.setObjectName("note")
            self.lbl_capture.setWordWrap(True)

            row_cap = QHBoxLayout()
            row_cap.setSpacing(10)
            row_cap.addWidget(QLabel("截图范围"), 0)
            row_cap.addWidget(self.cmb_capture, 3)
            row_cap.addWidget(self.btn_pick, 0)

            # ---------- 查词引擎：服务商 / Key / 模型 ----------
            # ★ v1.2.2：这里原本是个**两页标签页**（⚡ API 直连 / 🌐 网页版豆包），
            #   选哪页 = 用哪个后端。网页版整体删除后只剩一页，标签页就没有存在
            #   的意义了（单个标签页既难看又误导），改回一张普通卡片。
            #   服务商、key、模型、Base URL 这些控件本身没动。
            self._test_running = False
            self._test_result: dict | None = None

            def _erow(text: str, main, *extra) -> QWidget:
                """引擎分区的一行：固定宽度的标签 + 主控件 + 可选的小按钮。

                包成 QWidget 是为了能整行隐藏 —— 切后端时有些行是没意义的，
                只藏控件会留下一排孤儿标签。
                """
                w = QWidget()
                row = QHBoxLayout(w)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(10)
                lab = QLabel(text)
                lab.setFixedWidth(66)
                row.addWidget(lab, 0)
                row.addWidget(main, 1)
                for x in extra:
                    row.addWidget(x, 0)
                return w

            self.cmb_provider = _NoWheelCombo()
            self.cmb_provider.setCursor(Qt.CursorShape.PointingHandCursor)
            for pid, spec in config.PROVIDERS.items():
                self.cmb_provider.addItem(spec["label"], pid)
            self.cmb_provider.setToolTip("各家都走 OpenAI 兼容接口，所以换服务商不用改代码")
            self.cmb_provider.currentIndexChanged.connect(self._on_provider_changed)

            self.edt_key = QLineEdit()
            self.edt_key.setPlaceholderText("sk-…（填好点下面的测试按钮验证）")
            self.edt_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.edt_key.editingFinished.connect(self._on_key_edited)
            self.btn_eye = QPushButton("👁")
            self.btn_eye.setFixedWidth(38)
            self.btn_eye.setCheckable(True)
            self.btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_eye.setToolTip("显示 / 隐藏 API Key")
            self.btn_eye.toggled.connect(self._on_eye_toggled)

            self.cmb_model = _NoWheelCombo()
            self.cmb_model.setEditable(True)
            self.cmb_model.setCursor(Qt.CursorShape.PointingHandCursor)
            self.cmb_model.setToolTip(
                "必须选**支持图片**的模型，否则查词会哑掉。\n"
                "不确定就点下面的「测试连通性」—— 它会真发一张图过去。"
            )
            self.cmb_model.currentTextChanged.connect(self._on_model_changed)

            self.edt_url = QLineEdit()
            self.edt_url.setPlaceholderText("https://…/v1（自定义服务商必填）")
            self.edt_url.editingFinished.connect(self._on_url_edited)

            self.btn_conn = _mk("测试连通性（真发一张图）", self._on_test_clicked,
                                "生成一张带英文字幕的图发给模型，看它能不能读出来 —— "
                                "这是判断「这个 key + 模型到底能不能看图」的唯一可靠办法")
            self.lbl_vision = QLabel("")
            self.lbl_vision.setObjectName("note")
            self.lbl_vision.setWordWrap(True)
            self.lbl_conn = QLabel("")
            self.lbl_conn.setObjectName("note")
            self.lbl_conn.setWordWrap(True)

            self.row_provider = _erow("服务商", self.cmb_provider)
            self.row_key = _erow("API Key", self.edt_key, self.btn_eye)
            self.row_model = _erow("模型", self.cmb_model)
            self.row_url = _erow("Base URL", self.edt_url)

            api_box = QVBoxLayout()
            api_box.setContentsMargins(16, 14, 16, 14)
            api_box.setSpacing(9)
            api_box.addWidget(self.row_provider)
            api_box.addWidget(self.row_key)
            api_box.addWidget(self.row_model)
            api_box.addWidget(self.row_url)
            api_box.addWidget(self.lbl_vision)
            api_box.addWidget(self.btn_conn)
            api_box.addWidget(self.lbl_conn)
            api_box.addStretch(1)

            ecard = QFrame()
            ecard.setObjectName("card")
            ecard.setLayout(api_box)
            sec_engine = QLabel("查 词 引 擎")
            sec_engine.setObjectName("sec")

            # 触发键和关闭键可能撞成同一个（现在默认都是空格），
            # 写死成"按【X】或【Y】关掉它"就会变成"按【空格】或【空格】"，很蠢。
            if config.CLOSE_KEY == config.TRIGGER_KEY:
                close_hint = f"再按一次【{KEY}】关掉它"
            else:
                close_hint = f"按【{config.CLOSE_KEY_LABEL}】或【{KEY}】关掉它"
            note = QLabel(
                f"关掉这个窗口不会退出程序 —— 它继续在菜单栏驻留，随时按【{KEY}】查词；"
                "想再打开这个窗口，点 Dock 里的图标即可。\n"
                f"浮窗出来后：{close_hint}；也可以**点浮窗外面**关掉它"
                "（区别在于：按空格关会顺带让视频继续播，点外面关则不动播放状态）。\n"
                "想接着问？**点一下浮窗**就能打字，回车发送，AI 的回答会接在下面。\n"
                "查词全程在后台进行（不弹浏览器窗口），结果会浮在视频画面上方，"
                "全屏看剧也能看到。位置和大小会自动记住，下次还在老地方。\n"
                "第一次用？在「查词引擎」里填 Key → 点「测试连通性」"
                "→ 确认权限 → 开启看剧模式 → 点「手动查一次」验证。"
            )
            note.setObjectName("note")
            note.setWordWrap(True)

            sec = QLabel("操 作")
            sec.setObjectName("sec")

            # ---------- 播放控制 ----------
            # 「按空格三态控制播放」这条链路的可视入口：既能看出**当前走的是
            # 哪条通道**（精确 / 只能切换），也能一键查看诊断、一键给 IINA
            # 配上 mpv IPC —— 没有 IPC 的话 IINA 就只能靠不可靠的媒体键。
            sec_player = QLabel("播 放 控 制（按【空格】时自动暂停 / 继续）")
            sec_player.setObjectName("sec")

            self.lbl_player = QLabel("")
            self.lbl_player.setObjectName("note")
            self.lbl_player.setWordWrap(True)

            self.btn_pctl = _mk("🎬 检测播放器", self._on_diagnose_player,
                                "看你现在的播放器能不能被精确控制"
                                "（能读到「在播 / 已停」才算精确）")
            self.btn_iina = _mk("🛠 配置 IINA 高速通道",
                                self._on_setup_iina,
                                "给 IINA 写入 input-ipc-server —— 配好后 IINA 就能被"
                                "精确控制，不再依赖不可靠的系统媒体键。\n"
                                "⚠️ 需要先完全退出 IINA（⌘Q）再点")

            row_player = QHBoxLayout()
            row_player.setSpacing(10)
            row_player.addWidget(self.btn_pctl, 2)
            row_player.addWidget(self.btn_iina, 3)

            # ---------- 显示：浮窗文字大小（带实时示例）+ 观看记录 ----------
            # 用户要求：「可以让我在设置界面去设置悬浮框的文字大小，放个示例。」
            # 「示例」不是一句说明文字，而是**用浮窗同款 HTML 渲染的真卡片**：
            # 拖滑块时它当场重排，看到的就是浮窗里的样子（连配色都一样）。
            sec_disp = QLabel("显 示")
            sec_disp.setObjectName("sec")

            self.sld_font = QSlider(Qt.Orientation.Horizontal)
            self.sld_font.setMinimum(int(config.OVERLAY_FONT_MIN))
            self.sld_font.setMaximum(int(config.OVERLAY_FONT_MAX))
            self.sld_font.setSingleStep(1)
            self.sld_font.setPageStep(2)
            self.sld_font.setMinimumWidth(180)
            self.sld_font.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sld_font.setToolTip(
                "浮窗里正文的字号（px）。拖动时下面那张示例卡会当场跟着变；\n"
                "浮窗如果正开着，也会立刻按新字号重画一遍。"
            )
            self.sld_font.valueChanged.connect(self._on_font_changed)

            self.lbl_font = QLabel("")
            self.lbl_font.setObjectName("note")
            self.lbl_font.setFixedWidth(48)

            lab_font = QLabel("文字大小")
            lab_font.setFixedWidth(66)
            row_font = QHBoxLayout()
            row_font.setSpacing(10)
            row_font.addWidget(lab_font, 0)
            row_font.addWidget(self.sld_font, 1)
            row_font.addWidget(self.lbl_font, 0)

            self.preview = QFrame()
            self.preview.setObjectName("fontpreview")
            pv = QVBoxLayout(self.preview)
            pv.setContentsMargins(16, 13, 16, 14)
            pv.setSpacing(6)
            lbl_pv = QLabel("示例（浮窗里长这样）")
            lbl_pv.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
            self.lbl_preview = QLabel()
            self.lbl_preview.setWordWrap(True)
            self.lbl_preview.setTextFormat(Qt.TextFormat.RichText)
            self.lbl_preview.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            pv.addWidget(lbl_pv)
            pv.addWidget(self.lbl_preview)

            # 防抖：拖动过程中每次都写一次 settings.json 太浪费（原子写 + fsync），
            # 松手 220ms 之后再落盘；但**示例是每动一下就重画的**，手感是实时的。
            self._font_save_timer = QTimer(self)
            self._font_save_timer.setSingleShot(True)
            self._font_save_timer.setInterval(220)
            self._font_save_timer.timeout.connect(self._save_font)
            self._last_font_px = -1

            self.chk_viewlog = QCheckBox("把 AI 输出记到「观看记录」")
            self.chk_viewlog.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chk_viewlog.setToolTip(
                "文件名是「剧名 日期.md」，内容按编号 + 时间排列，"
                "包含每次查词和追问的回答。\n"
                "默认打开；跨零点或换剧会自动开新文件，"
                "同一部剧同一天只会有一本。"
            )
            self.chk_viewlog.toggled.connect(
                lambda on: self._ctrl.set_viewlog_enabled(bool(on)))
            self.btn_viewlog = _mk("📄 打开记录", self._ctrl.open_viewlog,
                                   "在 Finder 里定位当前这本观看记录")
            self.btn_viewlog.setFixedWidth(104)
            row_vlog = QHBoxLayout()
            row_vlog.setSpacing(10)
            row_vlog.addWidget(self.chk_viewlog, 1)
            row_vlog.addWidget(self.btn_viewlog, 0)

            # ---------- 记录文件夹（用户要求：能自己选放哪） ----------
            # 做成"当前目录 + 选择文件夹… + 默认"，**不做手输路径的输入框** ——
            # 手输容易拼错，还得自己确认目录存不存在，选目录对话框省事得多。
            self.lbl_vlog_dir = QLabel("")
            self.lbl_vlog_dir.setObjectName("hint")
            self.lbl_vlog_dir.setToolTip("观看记录（笔记本）存放的文件夹")
            self.lbl_vlog_dir.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self.btn_vlog_dir = _mk("📁 选择文件夹…", self._choose_vlog_dir,
                                    "换一个文件夹存放观看记录。\n"
                                    "立即生效 —— 下一次写记录就用新目录。")
            self.btn_vlog_reset = _mk("默认", self._reset_vlog_dir,
                                      "恢复成默认位置（桌面）")
            self.btn_vlog_reset.setFixedWidth(64)
            row_vlog_dir = QHBoxLayout()
            row_vlog_dir.setSpacing(10)
            row_vlog_dir.addWidget(self.lbl_vlog_dir, 1)
            row_vlog_dir.addWidget(self.btn_vlog_reset, 0)
            row_vlog_dir.addWidget(self.btn_vlog_dir, 0)

            # ---------- 重新编号（治"序号看着像凭空冒出来的"） ----------
            # 记录本是边看边追加的，用户手工删掉前面若干条之后，剩下的编号
            # 会从中间开始（例如 054 起）。这个按钮把它重排成 001 起。
            self.btn_vlog_renum = _mk(
                "🔢 这本重新编号（从 001 起）", self._renumber_viewlog,
                "把当前这本记录的编号按顺序重排成 001、002…\n"
                "手工删改过、编号看着乱的时候整理用。\n"
                "原文件会先备份成 .bak，随时能改回去。")
            row_vlog_renum = QHBoxLayout()
            row_vlog_renum.setSpacing(10)
            row_vlog_renum.addWidget(self.btn_vlog_renum, 1)

            # ---------- 根布局：内容塞进滚动区 ----------
            # 【为什么必须能滚】
            # 面板内容已经很多了（头部 + 状态卡 + 引擎标签页 + 三排按钮 +
            # 截图范围 + 一大段说明），在 13 寸屏上高度会顶出屏幕底部 ——
            # 底部那排按钮（重置悬浮窗大小 / 重置位置）就点不到了。
            # 所以把内容作为 body 塞进 QScrollArea，窗口高度封顶（见 _place），
            # 超出就出滚动条。
            body = QWidget()
            body.setObjectName("panelbody")
            root = QVBoxLayout(body)
            root.setContentsMargins(26, 22, 26, 26)
            root.setSpacing(14)
            root.addWidget(h1)
            root.addWidget(sub)
            root.addWidget(card)
            root.addSpacing(2)
            root.addWidget(self.btn_mode)
            root.addWidget(sec_engine)
            root.addWidget(ecard)
            root.addWidget(sec_player)
            root.addWidget(self.lbl_player)
            root.addLayout(row_player)
            root.addWidget(sec)
            root.addLayout(row1)
            root.addLayout(row2)
            root.addLayout(row_cap)
            root.addWidget(self.lbl_capture)
            root.addLayout(row4)
            root.addWidget(sec_disp)
            root.addLayout(row_font)
            root.addWidget(self.preview)
            root.addLayout(row_vlog)
            root.addLayout(row_vlog_dir)
            root.addLayout(row_vlog_renum)
            root.addStretch(1)
            root.addWidget(note)

            self.scroll = QScrollArea()
            self.scroll.setObjectName("panelscroll")
            self.scroll.setWidget(body)
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            # 宽度是固定的（setFixedWidth(540)），永远不需要横向滚动条
            self.scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(self.scroll)

            self._place()

            self._timer = QTimer(self)
            self._timer.setInterval(600)
            self._timer.timeout.connect(self._refresh)
            self._timer.start()
            self._sync_capture()      # 下拉框先和 settings 对齐（放 refresh 前，避免闪）
            self._sync_engine()       # 引擎分区同理，先对齐再进定时刷新
            self._sync_font()         # 滑块 + 示例卡先画出来（别等第一次定时刷新）
            self._sync_viewlog()
            self._refresh()

        # ----------------------------------------------------- 播放控制
        def _on_diagnose_player(self) -> None:
            """把播放器诊断报告直接摆出来（不用去翻日志）。"""
            try:
                import player

                text = player.controller().diagnose()
            except Exception as exc:
                text = f"诊断失败：{type(exc).__name__}: {exc}"
            self._info_dialog("🎬 播放器检测", text)

        def _on_setup_iina(self) -> None:
            """一键给 IINA 写入 input-ipc-server。"""
            try:
                import iina_setup
            except Exception as exc:
                self._info_dialog("配置 IINA 失败", str(exc))
                return
            ok, msg = iina_setup.configure()
            self._info_dialog("🛠 配置 IINA 高速通道" if ok else "配置没成功", msg)

        def _info_dialog(self, title: str, text: str) -> None:
            """一个朴素的只读弹窗（诊断/配置结果都走它）。"""
            from PyQt6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(text)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()

        # ----------------------------------------------------- 截图范围
        def _sync_capture(self) -> None:
            """把 settings 里的截图范围同步到下拉框（不触发保存）。

            ★ 「框选…」按钮**永远可见、永远可点**（用户 2026-09-15 明确要求：
              框过一次之后还要能随时重新框，不能灰掉）。
              这里还顺手做**自愈**：只要当前没有活着的框选窗口，就把它恢复成
              可点状态 —— 哪怕上一次框选是以我们没预料到的方式结束的，
              下一次刷新（600ms 一轮）也会把按钮兜回来。
            """
            mode = settings.get_capture_mode()
            idx = self.cmb_capture.findData(mode)
            if idx >= 0:
                self.cmb_capture.blockSignals(True)
                self.cmb_capture.setCurrentIndex(idx)
                self.cmb_capture.blockSignals(False)
            self.btn_pick.setVisible(True)
            self.btn_pick.setEnabled(not _picker_active())
            if mode == "custom":
                r = settings.get_capture_rect()
                self.lbl_capture.setText(
                    f"已框选：横向 {int(r[0] * 100)}% 起、宽 {int(r[2] * 100)}%；"
                    f"纵向 {int(r[1] * 100)}% 起、高 {int(r[3] * 100)}%"
                    "（按屏幕比例记，换分辨率也不会跑偏）　·　想改就再点一次「框选…」"
                )
            else:
                self.lbl_capture.setText(
                    "截得越小出结果越快；默认「字幕区」对看剧最合适。"
                    "想精确框一块自己的区域，随时点右边的「框选…」。"
                )

        def _on_capture_changed(self, _idx: int) -> None:
            mode = self.cmb_capture.currentData()
            if not mode:
                return
            settings.set_capture_mode(mode)
            self._sync_capture()
            if mode == "custom" and not _has_custom_rect():
                self._pick_region()

        def _pick_region(self) -> None:
            """弹出遮罩让用户拖一个框 —— **任何时候都能再框一次**。"""
            if _picker_active():
                log.debug("已经有一个框选窗口开着，忽略这次点击")
                return
            try:
                from region_picker import pick_region
            except Exception as exc:
                log.warning("框选功能不可用: %s", exc)
                return
            self.btn_pick.setEnabled(False)
            try:
                pick_region(on_done=self._on_region_picked)
            except Exception as exc:
                log.warning("框选失败: %s", exc)
                self.btn_pick.setEnabled(True)

        def _on_region_picked(self, rect) -> None:
            """框选收尾：**无论选没选中，按钮都要立刻恢复可点**。"""
            self.btn_pick.setEnabled(True)
            if not rect:
                log.info("框选未产生新区域（取消或过小），保持原设置")
                return
            settings.set_capture_rect(rect)
            settings.set_capture_mode("custom")
            self._sync_capture()
            log.info("截图区域已更新为自定义区域：%s", rect)

        # ----------------------------------------------------- 查词引擎
        def _sync_engine(self) -> None:
            """把 settings 对齐到引擎分区的控件上。

            ⚠️ 全程 blockSignals，否则"读设置→写控件"会反过来触发
            "控件改了→存设置"，在切换服务商时把上一个服务商的值写串。
            另外：正在输入的控件不覆盖，免得打字打到一半光标被重置。
            """
            cfg = settings.provider_config()
            i = self.cmb_provider.findData(cfg["id"])
            if i >= 0:
                self.cmb_provider.blockSignals(True)
                self.cmb_provider.setCurrentIndex(i)
                self.cmb_provider.blockSignals(False)

            if not self.edt_key.hasFocus():
                self.edt_key.blockSignals(True)
                self.edt_key.setText(cfg["key"])
                self.edt_key.blockSignals(False)

            if not self.cmb_model.hasFocus():
                models = list(cfg.get("models") or [])
                cur = cfg["model"]
                if cur and cur not in models:
                    models.insert(0, cur)      # 用户手填过的模型也要留在候选里
                self.cmb_model.blockSignals(True)
                self.cmb_model.clear()
                if models:
                    self.cmb_model.addItems(models)
                if cur:
                    self.cmb_model.setCurrentText(cur)
                self.cmb_model.blockSignals(False)

            if not self.edt_url.hasFocus():
                self.edt_url.blockSignals(True)
                self.edt_url.setText(cfg["base_url"])
                self.edt_url.blockSignals(False)

            # 视觉（图片输入）是本程序的硬前提，这里先给个离线判断的提示。
            # 真正的验证要靠「测试连通性」—— 离线只能查已知名单。
            if not cfg["model"]:
                self.lbl_vision.setText("")
            elif cfg["vision"]:
                self.lbl_vision.setText("✅ 这个模型在已知支持图片（视觉）的名单里。")
                self.lbl_vision.setStyleSheet(f"color: {OK_COLOR}; font-size: 11px;")
            else:
                self.lbl_vision.setText(
                    "⚠️ 没记录这个模型支持图片，查词很可能失败"
                    "（DeepSeek 请用 deepseek-flash）。\n"
                    "不确定就点「测试连通性」，它会真的发一张图过去验证。"
                )
                self.lbl_vision.setStyleSheet(f"color: {WARN_COLOR}; font-size: 11px;")

            if not self.lbl_conn.text() and not self._test_running:
                ready, why = settings.provider_ready()
                if not ready:
                    self._set_conn(False, why + "　→ 填好上面几项，再点「测试连通性」")

        def _on_provider_changed(self, _i: int) -> None:
            pid = self.cmb_provider.currentData()
            if pid:
                settings.set_provider(pid)
            self.lbl_conn.setText("")
            self._sync_engine()

        def _on_key_edited(self) -> None:
            settings.set_api_key(self.edt_key.text())
            self.lbl_conn.setText("")
            self._sync_engine()

        def _on_model_changed(self, _text: str = "") -> None:
            m = self.cmb_model.currentText().strip()
            if m:
                settings.set_model(m)
            self.lbl_conn.setText("")
            self._sync_engine()

        def _on_url_edited(self) -> None:
            settings.set_base_url(self.edt_url.text())
            self.lbl_conn.setText("")

        def _on_eye_toggled(self, show: bool) -> None:
            self.edt_key.setEchoMode(
                QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
            self.btn_eye.setText("🙈" if show else "👁")

        # -------------------------------------------------- 连通性自检
        def _on_test_clicked(self) -> None:
            """真发一张带文字的图给模型，看它读不读得出来。

            跑在后台线程里 —— 这一步是实打实的网络请求（几秒到几十秒），
            放主线程会把整个面板卡死。结果由 _refresh 里的 _pump_test_result 收。
            """
            import threading

            import api_client

            ready, why = settings.provider_ready()
            if not ready:
                self._set_conn(False, why)
                return

            cfg = settings.provider_config()
            self._test_running = True
            self._test_result = None
            self.btn_conn.setEnabled(False)
            self.btn_conn.setText("正在发图测试…")
            self.lbl_conn.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
            self.lbl_conn.setText(
                f"正在给 {cfg['label']} 的 {cfg['model']} 发一张带英文字幕的测试图…")

            def _run() -> None:
                try:
                    self._test_result = api_client.test_connection(cfg)
                except Exception as exc:  # noqa: BLE001
                    log.exception("连通性测试异常")
                    self._test_result = {"ok": False, "message": f"意外错误：{exc}"}
                finally:
                    self._test_running = False

            threading.Thread(target=_run, name="api-selftest", daemon=True).start()

        def _pump_test_result(self) -> None:
            """把后台线程跑完的结果搬回界面（在主线程里执行，安全改控件）。"""
            if self._test_running or self._test_result is None:
                return
            res = self._test_result
            self._test_result = None
            self.btn_conn.setEnabled(True)
            self.btn_conn.setText("测试连通性（真发一张图）")
            self._set_conn(bool(res.get("ok")), res.get("message", ""))

        def _set_conn(self, ok: bool, msg: str) -> None:
            if not msg:
                self.lbl_conn.setText("")
                return
            color = OK_COLOR if ok else WARN_COLOR
            self.lbl_conn.setStyleSheet(f"color: {color}; font-size: 11px;")
            self.lbl_conn.setText(("✅ " if ok else "⚠️ ") + msg)

        # ----------------------------------------------------- 布局/位置
        def _place(self) -> None:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            # ★ 高度封顶。
            #   内容都在 QScrollArea 里，所以窗口不必再"长到内容那么高" ——
            #   顶到屏幕外面的话，最下面那排按钮就永远点不到了。
            #   留 10% 余量（小屏幕上再收紧一点），超出部分靠滚动查看。
            self.setMaximumHeight(max(380, int(geo.height() * 0.90)))
            self.adjustSize()
            self.move(
                int(geo.x() + (geo.width() - self.width()) / 2),
                int(geo.y() + (geo.height() - self.height()) / 3),
            )

        def _scroll_top(self) -> None:
            """把滚动条拉回顶部（重新打开面板时用，免得停在半路）。"""
            try:
                self.scroll.verticalScrollBar().setValue(0)
            except Exception:
                pass

        def showEvent(self, ev) -> None:             # noqa: N802（Qt 命名）
            super().showEvent(ev)
            # 面板是复用同一个窗口的（关掉再点 Dock 图标又打开），
            # 每次都从顶部开始看，不要停在上次滚到的地方。
            self._scroll_top()

        # ----------------------------------------------------- 字号 + 示例
        # 示例正文：刻意的与**当前提示词要求的那五种行**一一对应
        # （句子 / 译文 / 生词（带音标 + 词根词缀）/ 俚语 / 结构），
        # 这样"格式稳不稳、醒不醒目"一眼就能看出来，不用真去查一个词。
        PREVIEW_BODY = (
            "句子：You're gonna have to face the music sooner or later.\n"
            "译文：你迟早得为自己做的事承担后果。\n"
            "生词：gonna /ˈɡɔːnə/ v. going to 的口语缩略 词根词缀：无（缩略词）\n"
            "生词：sooner or later /ˈsuːnər ɔːr ˈleɪtər/ adv. 迟早，早晚\n"
            "俚语：face the music → 承担（自己行为带来的）后果\n"
            "结构：have to 后面接的是省略了 that 的从句，语气上是被迫的"
        )

        def _render_font_preview(self) -> None:
            """按**滑块当前的**字号把示例画出来（哪怕还没保存）。

            关键在于走的是 overlay 里那套真渲染（`build_html` + `format_lines`），
            不是另写一份"长得差不多"的 HTML —— 否则示例迟早会和浮窗长得不一样，
            那这个示例就成了误导。
            """
            px = int(self.sld_font.value())
            try:
                import overlay

                html = overlay.build_html("字幕生词", self.PREVIEW_BODY,
                                          overlay.ui_sizes(px))
            except Exception as exc:
                log.debug("示例渲染失败: %s", exc)
                html = (f'<span style="color:#ff8a8a">示例渲染失败：'
                        f'{exc}</span>')
            self.lbl_preview.setText(html)
            self.lbl_font.setText(f"{px} px")

        def _on_font_changed(self, value: int) -> None:
            self._last_font_px = int(value)
            self._render_font_preview()
            self._font_save_timer.start()

        def _save_font(self) -> None:
            """把字号落盘 + 通知浮窗立刻重画（一个字号只存一次）。

            开头先 `stop()` 防抖定时器：正常路径是定时器**自己**触发的，
            stop 是空操作；但如果是被直接调用（测试、或将来加了别的入口），
            把所有待处理的防抖收掉才是一致状态 —— 不清掉的话 `_sync_font()`
            会以为"用户还在拖"，一直拒绝同步外部改过的值。
            """
            self._font_save_timer.stop()
            try:
                self._ctrl.apply_overlay_font(int(self.sld_font.value()))
            except Exception as exc:
                log.debug("保存字号失败: %s", exc)

        def _sync_font(self) -> None:
            """滑块对齐真实设置。**用户正在拖的时候绝不插手。**

            为什么要挡一下：拖动中 settings 里还是旧值（防抖还没落盘），
            这时若把滑块设回旧值，用户的手感就变成了"拖不动"。
            """
            if self._font_save_timer.isActive():
                return
            try:
                px = int(settings.get_overlay_font_size())
            except Exception:
                px = int(config.OVERLAY_FONT_SIZE)
            if px == self._last_font_px:
                return
            self._last_font_px = px
            self.sld_font.blockSignals(True)
            self.sld_font.setValue(px)
            self.sld_font.blockSignals(False)
            self._render_font_preview()

        def _sync_viewlog(self) -> None:
            try:
                on = bool(settings.get_viewlog_enabled())
            except Exception:
                on = bool(config.VIEWLOG_ENABLED)
            if self.chk_viewlog.isChecked() != on:
                self.chk_viewlog.blockSignals(True)
                self.chk_viewlog.setChecked(on)
                self.chk_viewlog.blockSignals(False)
            # 当前记录的文件夹（可能被手改 settings.json 改过，所以每轮都刷）
            try:
                where = self._ctrl.viewlog_dir_text()
            except Exception:
                where = ""
            if where and self.lbl_vlog_dir.text() != where:
                self.lbl_vlog_dir.setText(where)
                self.lbl_vlog_dir.setToolTip(f"观看记录（笔记本）存放位置：\n{where}")

        def _choose_vlog_dir(self) -> None:
            """选一个文件夹当观看记录的落点。"""
            try:
                cur = self._ctrl.viewlog_dir_text()
            except Exception:
                cur = ""
            picked = QFileDialog.getExistingDirectory(
                self, "选择观看记录（笔记本）存放的文件夹", cur or "")
            if not picked:
                return                       # 用户取消了
            try:
                self._ctrl.apply_viewlog_dir(picked)
            except Exception as exc:
                log.warning("设置观看记录目录失败: %s", exc)
                return
            self._sync_viewlog()

        def _reset_vlog_dir(self) -> None:
            """记录文件夹恢复默认（桌面）。"""
            try:
                self._ctrl.apply_viewlog_dir("")
            except Exception as exc:
                log.warning("恢复默认记录目录失败: %s", exc)
                return
            self._sync_viewlog()

        def _renumber_viewlog(self) -> None:
            """把当前这本记录的编号重排成 001 起。

            ★ 这是**会改写用户文件**的操作，所以先弹一次确认，
              而且 app 层会先把原文件备份成 `.bak`。
            """
            from PyQt6.QtWidgets import QMessageBox

            ans = QMessageBox.question(
                self, "重新编号",
                "会把当前这本观看记录里的编号按顺序重排成 001、002…\n\n"
                "原文件会先备份成 .bak，随时可以改回去。\n"
                "确定继续吗？",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if ans != QMessageBox.StandardButton.Ok:
                return
            try:
                self._ctrl.renumber_viewlog()
            except Exception as exc:
                log.warning("重新编号失败: %s", exc)

        # ----------------------------------------------------- 状态刷新
        def _refresh(self) -> None:
            try:
                from utils import is_accessibility_trusted, screen_capture_authorized

                self._pump_test_result()   # 收后台「测试连通性」的结果

                on = bool(self._ctrl.is_mode_on())
                if self.btn_mode.isChecked() != on:
                    self.btn_mode.setChecked(on)
                self.btn_mode.setText(
                    "看剧模式：已开启　（点此关闭）" if on
                    else f"开启看剧模式　（遇到生词按【{KEY}】）"
                )
                self.lbl_mode.setText(
                    "看剧模式：" + (f"✅ 已开启，正在监听{KEY}键" if on else "⏸ 未开启")
                )
                busy = False
                asking = False
                try:
                    busy = bool(self._ctrl.is_busy())
                    asking = bool(self._ctrl.is_asking())
                except Exception:
                    busy = False
                working = busy or asking
                if busy:
                    self.lbl_mode.setText("看剧模式：🔍 正在识别截图…")
                elif asking:
                    self.lbl_mode.setText("看剧模式：💬 AI 正在回答你的追问…")
                self.btn_test.setEnabled(not working)
                self.btn_test.setText("⏳ 正在识别，请稍候…" if working
                                      else "▶ 手动查一次（不用键盘）")

                try:
                    self.lbl_step.setText("进度：" + self._ctrl.status_text())
                except Exception:
                    self.lbl_step.setText("")

                # 截图范围可能被别处改过（比如刚框选完），跟着刷新一下
                try:
                    self._sync_capture()
                except Exception:
                    pass

                # 字号 / 观看记录开关（可能被别处或手改 settings.json 改过）
                try:
                    self._sync_font()
                    self._sync_viewlog()
                except Exception:
                    pass

                # 播放控制通道（带缓存，不会每秒去起 osascript）
                try:
                    import player

                    self.lbl_player.setText(player.controller().describe_cached())
                except Exception:
                    self.lbl_player.setText("")

                ok_ax, ok_sc = is_accessibility_trusted(), screen_capture_authorized()
                if ok_ax and ok_sc:
                    self.lbl_perm.setText("系统权限：✅ 辅助功能 + 屏幕录制 都已授权")
                    self.btn_perm.setText("② 权限已就绪 ✅")
                else:
                    miss = []
                    if not ok_ax:
                        miss.append("辅助功能")
                    if not ok_sc:
                        miss.append("屏幕录制")
                    self.lbl_perm.setText("系统权限：⚠️ 还缺 " + "、".join(miss) + "（点下面的按钮）")
                    self.btn_perm.setText("② 去授权：" + "、".join(miss))

                if self._ctrl.is_mode_on():
                    self.lbl_tip.setText(
                        f"提示：按【{KEY}】后屏幕上会先冒出一个「正在识别…」小条，"
                        "几秒后结果卡片直接浮在视频上方 —— 全程不会弹浏览器。"
                        f"想收起来就按【{config.CLOSE_KEY_LABEL}】；"
                        "想追问就点一下浮窗，在里面打字、回车发送。"
                    )

                try:
                    custom = self._ctrl.prompt_is_customized()
                    self.lbl_prompt.setText(
                        ("提示词：已自定义（" if custom else "提示词：默认（")
                        + self._ctrl.prompt_preview()
                        + "）　点下面的『✏️ 编辑提示词』可随时修改"
                    )
                except Exception:
                    self.lbl_prompt.setText("")
            except Exception:
                pass

        # ----------------------------------------------------- 事件
        def closeEvent(self, ev) -> None:
            # 仅隐藏，不退出程序（quitOnLastWindowClosed 已关）
            ev.ignore()
            self.hide()

        def keyPressEvent(self, ev):
            from PyQt6.QtCore import Qt as _Qt

            if ev.key() == _Qt.Key.Key_Escape:
                self.hide()
                return
            super().keyPressEvent(ev)

    return _Panel
