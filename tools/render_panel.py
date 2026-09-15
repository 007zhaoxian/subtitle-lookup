"""把控制面板离屏渲染成 PNG，用来"用眼睛"验收界面。

为什么需要它：offscreen 平台下 QWidget.grab() 不需要屏幕录制权限，
就能拿到真实排版结果。改完 panel.py 的布局，跑一次这个脚本，
直接看图确认有没有挤在一起、有没有孤儿控件、字号示例对不对得上。

★ v1.2.2：面板不再是"两页标签页（API 直连 / 网页版豆包）"，
  引擎区就是一张普通卡片，所以这里只出一张整页图。
  （offscreen 的虚拟屏只有几百像素高，_place() 会把窗口封到 0.9*屏高，
   直接 grab 只能截到顶部一条，所以下面要手动解开高度上限。）

跑法：
    QT_QPA_PLATFORM=offscreen python tools/render_panel.py [输出目录]
产出：
    <输出目录>/panel.png         —— 整页（引擎 + 状态 + 截图范围）
    <输出目录>/panel_display.png —— 滚到底（字号滑块 + 实时示例 + 观看记录）
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DL_SETTINGS_DIR", tempfile.mkdtemp(prefix="panel_render_"))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "tools", "_render")


class _StubCtrl:
    """只提供面板会调用的方法，不牵扯截图/网络。"""

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

    def viewlog_dir_text(self) -> str:
        """观看记录存放目录（面板「显示」区要显示它）。

        ★ 必须显式给一个**非空**返回值：下面的 __getattr__ 兜底会返回
        `lambda: None`，而 _sync_viewlog() 里写的是 `if where and ...` ——
        于是这个标签在预览图里永远是空的，看图验收根本发现不了它有没有被挤坏
        （真机上是有值的）。v1.2.3 就这么漏过一次。
        """
        return os.path.expanduser("~/Desktop")

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _pump(app, n: int = 4) -> None:
    for _ in range(n):
        if app is not None:
            app.processEvents()


def _grab(widget, path: str, app) -> None:
    widget.show()
    _pump(app)
    pm = widget.grab()
    ok = pm.save(path)
    print(f"{'OK ' if ok else '失败'} {os.path.basename(path)}  {pm.width()}x{pm.height()}")


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    import panel
    import settings

    app = QApplication(sys.argv)
    Panel = panel.make_panel_class()

    # 填一份有内容但不是真 key 的配置，让 Key 行、模型行都有字
    settings.set_provider("deepseek")
    settings.set_api_key("sk-demo-1234567890abcdef")

    os.makedirs(OUT_DIR, exist_ok=True)

    p = Panel(_StubCtrl())
    # 解开高度上限（详见文件头），并给一个能容下整页的高度
    p.setMaximumHeight(6000)
    p.resize(560, 1200)
    _pump(app)

    scroll = getattr(p, "scroll", None)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(0)
        _pump(app)
    _grab(p, os.path.join(OUT_DIR, "panel.png"), app)

    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        _pump(app)
    _grab(p, os.path.join(OUT_DIR, "panel_display.png"), app)

    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
