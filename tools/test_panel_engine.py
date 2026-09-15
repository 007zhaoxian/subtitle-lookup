"""面板「查词引擎」分区的冒烟测试（无头，不弹真窗口）。

为什么需要它：panel.py 里引擎分区是纯 UI，py_compile 只能证明语法没错，
证明不了"切服务商会不会把 key 写串""没填 key 时会不会真发请求"。
这里用 offscreen 平台把面板真的构造出来，改控件、读 settings，验证联动。

★ v1.2.2 改了什么：
  原来这里有一半的用例在测**标签页切后端**（⚡ API 直连 ↔ 🌐 网页版豆包）。
  网页版整体删除后引擎区就是一张普通卡片，标签页没有了，
  那几节换成**反向回归钉子**：面板上不该再有 tabs / 登录状态行 / 换对话按钮，
  谁哪天把它们加回来，这里立刻红。

跑法：
    QT_QPA_PLATFORM=offscreen python tools/test_panel_engine.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DL_SETTINGS_DIR", tempfile.mkdtemp(prefix="panel_engine_"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


class _StubCtrl:
    """只提供面板会调用的那些方法，不牵扯真正的截图/网络。"""

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


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    import config
    import panel
    import settings

    app = QApplication.instance() or QApplication(sys.argv)
    Panel = panel.make_panel_class()
    p = Panel(_StubCtrl())

    print("== 1. 默认状态 ==")
    check("服务商下拉当前值", p.cmb_provider.currentData(), "deepseek")
    check("模型", p.cmb_model.currentText(), "deepseek-flash")
    check("base_url 已填", p.edt_url.text(), "https://api.deepseek.com")
    check("key 是密码模式", p.edt_key.echoMode().name, "Password")

    print("\n== 1b. ★ 回归钉子：网页版豆包的 UI 残留全清 ==")
    check("面板上已经没有标签页", hasattr(p, "tabs"), False)
    check("没有『登录状态』行", hasattr(p, "lbl_login"), False)
    check("没有『对话状态』行", hasattr(p, "lbl_chat"), False)
    check("没有『登录豆包』按钮", hasattr(p, "btn_login"), False)
    check("没有『检查登录状态』按钮", hasattr(p, "btn_check"), False)
    check("没有『抓取页面结构』按钮", hasattr(p, "btn_snap"), False)
    check("没有『换个新对话』按钮", hasattr(p, "btn_newchat"), False)
    check("没有『后端』下拉", hasattr(p, "cmb_backend"), False)
    check("设置面板里没有 set_backend 可调",
          hasattr(settings, "set_backend"), False)
    # 面板上的文字里也不该再出现"豆包"（那是网页版时代的叫法，
    # 现在查词走的是用户自己选的服务商，写给用户看的字必须对得上）
    import re

    texts: list[str] = []
    from PyQt6.QtWidgets import QLabel, QPushButton, QCheckBox

    for w in p.findChildren(QLabel) + p.findChildren(QPushButton) + p.findChildren(QCheckBox):
        texts.append(w.text())
        texts.append(w.toolTip())
    blob = "\n".join(texts)
    check("界面上不再出现『豆包』字样", "豆包" in blob, False)

    print("\n== 2. 填 key 会立刻落盘 ==")
    p.edt_key.setText("sk-abcdef1234567890")
    p.edt_key.editingFinished.emit()
    check("settings 里的 key", settings.get_api_key(), "sk-abcdef1234567890")
    p.btn_eye.setChecked(True)
    check("点眼睛后明文", p.edt_key.echoMode().name, "Normal")

    print("\n== 3. 切服务商 → 各服务商 key 独立 ==")
    p.cmb_provider.setCurrentIndex(p.cmb_provider.findData("hunyuan"))
    check("当前服务商", settings.get_provider(), "hunyuan")
    check("混元的 key 是空的", p.edt_key.text(), "")
    check("混元默认模型", p.cmb_model.currentText(), "hunyuan-turbos-vision")
    check("混元 base_url", p.edt_url.text(), "https://api.hunyuan.cloud.tencent.com/v1")
    settings.set_api_key("hy-key-123")
    p.cmb_provider.setCurrentIndex(p.cmb_provider.findData("deepseek"))
    check("切回 deepseek，key 还在", p.edt_key.text(), "sk-abcdef1234567890")
    p.cmb_provider.setCurrentIndex(p.cmb_provider.findData("hunyuan"))
    check("混元的 key 也没被冲掉", p.edt_key.text(), "hy-key-123")
    p.cmb_provider.setCurrentIndex(p.cmb_provider.findData("deepseek"))

    print("\n== 4. 手填模型 + 视觉提示 ==")
    p.cmb_model.setCurrentText("deepseek-v4-pro")
    check("模型已存", settings.get_model(), "deepseek-v4-pro")
    check("纯文本模型给出警告", p.lbl_vision.text().startswith("⚠️"), True)
    check("警告里点了 deepseek-flash", "deepseek-flash" in p.lbl_vision.text(), True)
    p.cmb_model.setCurrentText("deepseek-flash")
    check("换回 flash 变绿勾", p.lbl_vision.text().startswith("✅"), True)

    print("\n== 5. 显示区：字号滑块 + 观看记录开关 ==")
    check("字号滑块范围低端", p.sld_font.minimum(), config.OVERLAY_FONT_MIN)
    check("字号滑块范围高端", p.sld_font.maximum(), config.OVERLAY_FONT_MAX)
    check("滑块当前值 = 设置里的值",
          p.sld_font.value(), settings.get_overlay_font_size())
    check("观看记录开关 = 设置里的值",
          p.chk_viewlog.isChecked(), settings.get_viewlog_enabled())

    print("\n== 6. 没填 key 时测试按钮给出提示（不发请求） ==")
    settings.set_api_key("")
    p._sync_engine()
    p._on_test_clicked()
    check("提示还没填 key", "还没填" in p.lbl_conn.text(), True)
    check("按钮没被禁用（测试没真跑）", p.btn_conn.isEnabled(), True)

    app.processEvents()
    print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
