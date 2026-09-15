"""「编辑提示词」窗口 —— **所有**发给 AI 的文字都在这里改。

用户先后提过两次：
  "你应该在我写的这个软件里面，可以让我自己编辑我发给 AI 的提示词。"
  "所有的发给豆包的提示词都必须是可以编辑的。"

★ v1.2.2：删掉了原来的「② 自动附加的约束句」那一节。
  那段「只看本条消息这张图、忽略上文」是**网页版豆包专用**的硬约束
  （那时所有查词挤在同一个豆包对话里，模型会把上一张图的字幕也答一遍）。
  网页版整体删除后它就彻底没用了，而 API 每次查词都重建 messages，
  本来就没有串味问题。留着反而是"追问被拒绝回答"的隐患。

所以这个窗口现在管两样东西：
  1) **主提示词** —— 跟着每次截图一起发出去的那段（字幕生词/口语俚语/翻译…）；
  2) **预设模板** —— 内置 5 套，可以直接套用 / 改完另存为新的 / 删掉自己建的。

改动保存后**立即生效**，不需要重启 App、更不需要重新打包。
存储位置：~/Library/Application Support/DoubaoLookup/settings.json
（追问用的文字不在这里 —— 那是你在浮窗输入框里当场打的，本来就可改可编辑。
  但**追问的规则**在主提示词里，就是那段【追问规则】：纯文字追问不受字段模板
  约束、要结合上下文自由作答 —— 改模板时别把它删掉。）
"""
from __future__ import annotations

import config
import settings

# 与面板同一套深色配色
FG = "#eceef1"
FG_DIM = "rgba(236,238,241,0.62)"
ACCENT = "#7cc4ff"
BG = "#1c1f24"
CARD = "#252a31"
BORDER = "rgba(255,255,255,0.10)"

_QSS = f"""
QWidget#peRoot {{ background-color: {BG}; }}
QLabel {{ color: {FG}; font-family: 'PingFang SC','Helvetica Neue',sans-serif; }}
QLabel#peH1 {{ font-size: 18px; font-weight: 600; }}
QLabel#peSub {{ color: {FG_DIM}; font-size: 12px; }}
QLabel#peTip {{ color: {FG_DIM}; font-size: 11px; }}
QLabel#peCount {{ color: {FG_DIM}; font-size: 11px; }}
QLabel#peSec {{ color: {FG_DIM}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QPlainTextEdit {{
    background: {CARD};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 13px;
    line-height: 150%;
    font-family: 'PingFang SC','Menlo','Helvetica Neue',monospace;
    selection-background-color: rgba(124,196,255,0.35);
}}
QLineEdit {{
    background: {CARD};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
    selection-background-color: rgba(124,196,255,0.35);
}}
QComboBox {{
    background: rgba(255,255,255,0.07);
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    min-width: 190px;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    color: {FG};
    selection-background-color: rgba(124,196,255,0.28);
    border: 1px solid {BORDER};
    outline: none;
}}
QPushButton {{
    color: {FG};
    background: rgba(255,255,255,0.07);
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 9px 18px;
    font-size: 13px;
    font-family: 'PingFang SC','Helvetica Neue',sans-serif;
}}
QPushButton:hover {{ background: rgba(255,255,255,0.13); }}
QPushButton#pePrimary {{
    background: rgba(124,196,255,0.22);
    border: 1px solid rgba(124,196,255,0.5);
    font-weight: 600;
}}
QPushButton#pePrimary:hover {{ background: rgba(124,196,255,0.32); }}
"""


def make_editor_class():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
    from PyQt6.QtWidgets import (
        QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
        QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
    )

    class _PromptEditor(QWidget):
        def __init__(self, ctrl) -> None:
            super().__init__()
            self._ctrl = ctrl
            self.setObjectName("peRoot")
            self.setWindowTitle("编辑提示词 · " + config.APP_DISPLAY_NAME)
            self.setStyleSheet(_QSS)
            self.setMinimumWidth(760)

            h1 = QLabel("编辑发给 AI 的提示词")
            h1.setObjectName("peH1")
            sub = QLabel(
                "这是每次截图都会发给 AI 的那段提示词，改完点『保存并生效』—— "
                "立即生效，不用重启 App。"
            )
            sub.setObjectName("peSub")
            sub.setWordWrap(True)

            # ==================== 主提示词 ====================
            # （v1.2.2 之前这里还挂着「② 自动附加的约束句」，已删除）
            sec1 = QLabel("主提示词（跟着每张截图一起发出去）")
            sec1.setObjectName("peSec")

            self.cmb = QComboBox()
            self.cmb.addItem("套用预设模板…")
            self._reload_presets()
            self.cmb.currentIndexChanged.connect(self._on_preset)

            btn_save_preset = QPushButton("存为新预设…")
            btn_save_preset.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_save_preset.clicked.connect(self._save_as_preset)
            btn_del_preset = QPushButton("删掉这个预设")
            btn_del_preset.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del_preset.clicked.connect(self._delete_preset)

            self.lbl_count = QLabel()
            self.lbl_count.setObjectName("peCount")
            top = QHBoxLayout()
            top.setSpacing(10)
            top.addWidget(self.cmb)
            top.addWidget(btn_save_preset)
            top.addWidget(btn_del_preset)
            top.addStretch(1)
            top.addWidget(self.lbl_count)

            self.edit = QPlainTextEdit()
            self.edit.setPlainText(settings.get_prompt())
            self.edit.setMinimumHeight(250)
            self.edit.textChanged.connect(self._refresh_count)

            # ★ v1.2.2：「② 自动附加的约束句」整节删除 —— 它是网页版豆包专用的，
            #   详见文件头注释。这里只剩下写提示词的几个要点。
            tip = QLabel(
                "写提示词的三个要点：① 明确**只解释图里出现的词**，否则模型会自己发挥；"
                "② 规定**输出格式**（行首固定标签 + 全角冒号最稳），悬浮窗会自动上色；"
                "③ 约定**没有内容时回什么**（例如只回「无」），这样不会看到一堆客套话。\n"
                "★ 不管怎么改，**请保留结尾那段【追问规则】**："
                "它声明「纯文字追问不受上面字段模板的约束、要结合上下文自由回答」。"
                "删掉它，你在浮窗里追问时模型就可能只会回一个「无」——"
                "因为提示词本身常驻在追问的对话历史里。"
            )
            tip.setObjectName("peTip")
            tip.setWordWrap(True)

            self.lbl_msg = QLabel()
            self.lbl_msg.setObjectName("peCount")
            self.lbl_msg.setWordWrap(True)

            # ==================== 按钮 ====================
            btn_save = QPushButton("保存并生效")
            btn_save.setObjectName("pePrimary")
            btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_save.clicked.connect(self._save)

            btn_reset = QPushButton("全部恢复默认")
            btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reset.clicked.connect(self._reset)

            btn_close = QPushButton("关闭")
            btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_close.clicked.connect(self.close)

            row = QHBoxLayout()
            row.addWidget(btn_reset)
            row.addStretch(1)
            row.addWidget(btn_close)
            row.addWidget(btn_save)

            root = QVBoxLayout(self)
            root.setContentsMargins(24, 20, 24, 20)
            root.setSpacing(9)
            root.addWidget(h1)
            root.addWidget(sub)
            root.addSpacing(4)
            root.addWidget(sec1)
            root.addLayout(top)
            root.addWidget(self.edit)
            root.addSpacing(4)
            root.addWidget(tip)
            root.addWidget(self.lbl_msg)
            root.addLayout(row)

            # Cmd+S 保存，Esc 关闭
            QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save)
            QShortcut(QKeySequence("Meta+S"), self, activated=self._save)
            QShortcut(QKeySequence("Esc"), self, activated=self.close)

            self._refresh_count()
            self._place()

        # ------------------------------------------------ 预设
        def _reload_presets(self, keep: str = "") -> None:
            self.cmb.blockSignals(True)
            self.cmb.clear()
            self.cmb.addItem("套用预设模板…")
            for name, _text in settings.get_presets():
                self.cmb.addItem(name)
            self.cmb.blockSignals(False)
            if keep:
                idx = self.cmb.findText(keep)
                if idx >= 0:
                    self.cmb.setCurrentIndex(idx)

        def _on_preset(self, idx: int) -> None:
            if idx <= 0:
                return
            name = self.cmb.itemText(idx)
            text = dict(settings.get_presets()).get(name)
            if text:
                self.edit.setPlainText(text)
                self.lbl_msg.setText(f"已套用预设「{name}」，确认后点『保存并生效』。")

        def _save_as_preset(self) -> None:
            text = self.edit.toPlainText().strip()
            if not text:
                self.lbl_msg.setText("⚠️ 内容为空，没什么可存的。")
                return
            name, ok = QInputDialog.getText(self, "存为新预设", "给这套提示词起个名字：")
            if not ok or not (name or "").strip():
                return
            if settings.save_preset(name, text):
                self._reload_presets(keep=name.strip()[:60])
                self.lbl_msg.setText(f"✅ 已存为预设「{name.strip()[:60]}」，"
                                     f"以后可以直接从下拉里选。")
            else:
                self.lbl_msg.setText("❌ 保存预设失败，请查看日志。")

        def _delete_preset(self) -> None:
            idx = self.cmb.currentIndex()
            if idx <= 0:
                self.lbl_msg.setText("先在左边下拉里选中要删的预设。")
                return
            name = self.cmb.itemText(idx)
            builtin = name in config.PROMPT_PRESETS
            msg = (f"「{name}」是内置预设，删掉后会恢复成内置原文的默认样子。\n\n确定吗？"
                   if builtin else f"确定删掉预设「{name}」吗？")
            r = QMessageBox.question(self, "删除预设", msg,
                                     QMessageBox.StandardButton.Yes
                                     | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            if settings.delete_preset(name):
                self._reload_presets()
                self.lbl_msg.setText(f"已删除预设「{name}」。")
            else:
                self.lbl_msg.setText("❌ 删除失败，请查看日志。")

        # ------------------------------------------------ 行为
        def _place(self) -> None:
            self.adjustSize()
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            h = min(self.height(), max(400, geo.height() - 80))
            self.resize(self.width(), h)
            self.move(int(geo.x() + (geo.width() - self.width()) / 2),
                      int(geo.y() + max(30, (geo.height() - self.height()) / 2)))

        def _refresh_count(self) -> None:
            n = len(self.edit.toPlainText())
            self.lbl_count.setText(f"{n} / {config.PROMPT_MAX_CHARS} 字符")
            over = n > config.PROMPT_MAX_CHARS
            self.lbl_count.setStyleSheet(
                f"color:{'#ff8a8a' if over else FG_DIM};font-size:11px;"
            )

        def _save(self) -> None:
            text = self.edit.toPlainText().strip()
            if not text:
                self.lbl_msg.setText("⚠️ 主提示词不能为空。")
                return
            if len(text) > config.PROMPT_MAX_CHARS:
                self.lbl_msg.setText(
                    f"⚠️ 主提示词太长了（{len(text)} 字符），超过 {config.PROMPT_MAX_CHARS}。"
                )
                return
            ok1 = settings.set_prompt(text)
            if ok1:
                warn = ("" if "【追问规则" in text else
                        "\n⚠️ 注意：这段提示词里没有【追问规则】，"
                        "浮窗追问可能会被模型当成「越界」而拒绝回答。")
                self.lbl_msg.setText(
                    f"✅ 已保存并立即生效（{len(text)} 字符）。\n"
                    f"存储位置：{settings.settings_path()}{warn}"
                )
            else:
                self.lbl_msg.setText("❌ 保存失败，请查看日志。")

        def _reset(self) -> None:
            self.edit.setPlainText(config.DEFAULT_PROMPT)
            self.lbl_msg.setText("已填入默认提示词（含【追问规则】），点『保存并生效』确认。")

        def closeEvent(self, ev) -> None:  # noqa: N802
            ev.accept()
            try:
                self._ctrl._prompt_editor = None   # 允许 GC
            except Exception:
                pass
            super().closeEvent(ev)

    return _PromptEditor


def open_prompt_editor(ctrl) -> None:
    """创建（或前置）提示词编辑窗口。必须在 Qt 主线程调用。"""
    from utils import log, set_dock_icon_visible

    win = getattr(ctrl, "_prompt_editor", None)
    if win is None:
        cls = make_editor_class()
        win = cls(ctrl)
        try:
            ctrl._prompt_editor = win       # 持有引用防 GC
        except Exception:
            pass
    else:
        # 重新打开时刷新内容（可能在别处改过 / 关了又开）
        win.edit.setPlainText(settings.get_prompt())
        win._reload_presets()
    set_dock_icon_visible(True)
    win.show()
    win.raise_()
    win.activateWindow()
    log.info("提示词编辑窗口已打开")
