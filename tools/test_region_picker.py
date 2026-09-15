"""框选（region_picker）的离线测试。

钉死三件极易回归的事：

1. **坐标必须用窗口本地坐标**
   老版本 start/end 取的是 globalPosition()，绘制/算比例却在本地坐标系里。
   屏幕外接矩形原点不是 (0,0) 时（副屏在主屏左边/上边，或带刘海的机型），
   两个坐标系一混，框就整体偏移 —— 用户报的"起点不是我按下的位置"。
   这里用一个人造的 union（原点 -100,-50）把差异放大到能测出来。

2. **不能 showFullScreen()**
   macOS 上它会把窗口丢进独立 Space，用户看到一片黑。源码里的**非注释行**
   不允许出现它。

3. **Picker 必须被模块级引用按住**
   pick_region() 一返回，局部变量就没了。没人持有 → Python GC 回收 →
   C++ QWidget 析构 → destroyed 信号回调 Python 槽 → 槽里一旦有异常，
   PyQt6 直接 qFatal() → abort()，App 当场消失。
   这里直接检查 region_picker._ACTIVE 在窗口关闭前不为 None。

4. ★ **必须能反复框选**（第 9 节）
   结果回调不能挂在 destroyed 信号上（窗口只是 close 时它永远不发），
   否则面板的「框选…」被禁用后回不来、框出来的区域也不会保存。

跑法： python tools/test_region_picker.py      （内部自动走 offscreen）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt        # noqa: E402
from PyQt6.QtGui import QMouseEvent                                # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

PASS = 0
FAIL = 0

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "region_picker.py")


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"  {extra}" if extra else ""))


def make_ev(etype, local: QPoint, glob: QPoint) -> QMouseEvent:
    return QMouseEvent(
        etype,
        QPointF(local),
        QPointF(glob),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class _EscEv:
    """只要能回答 key() 就够 keyPressEvent 用了。"""

    def key(self):
        return Qt.Key.Key_Escape


def main() -> int:
    # ★ 必须赋值给变量：不持有的话 Python 立刻把它回收掉，
    #   C++ QApplication 跟着被删，后面再建 QWidget 就报
    #   "Must construct a QApplication before a QWidget" 并 abort。
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None

    import region_picker

    picker = region_picker.pick_region()
    # 人造外接矩形：原点故意不是 (0,0)，这样"全局/本地混用"立刻暴露
    uni = QRect(-100, -50, 2000, 1200)
    picker._union = uni
    picker._shot = None

    DOWN = QEvent.Type.MouseButtonPress
    MOVE = QEvent.Type.MouseMove
    UP = QEvent.Type.MouseButtonRelease

    def drag(x1, y1, x2, y2):
        """本地坐标 (x1,y1) → (x2,y2) 拖一个框，返回 result。

        全局坐标 = 本地 + union 原点，模拟"窗口原点在 (-100,-50)"的真实情形。
        """
        picker.result = None
        picker.start = None
        picker.end = None
        picker.mousePressEvent(make_ev(DOWN, QPoint(x1, y1),
                                       QPoint(x1 - 100, y1 - 50)))
        picker.mouseMoveEvent(make_ev(MOVE, QPoint(x2, y2),
                                      QPoint(x2 - 100, y2 - 50)))
        picker.mouseReleaseEvent(make_ev(UP, QPoint(x2, y2),
                                         QPoint(x2 - 100, y2 - 50)))
        return picker.result

    def expect(x1, y1, x2, y2):
        """期望值。注意 QRect(两点) 是**闭区间**（宽 = x2-x1+1），
        Qt 自己的语义，测试要跟着算，别手写成 x2-x1。"""
        r = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()
        return [round(r.x() / uni.width(), 4), round(r.y() / uni.height(), 4),
                round(r.width() / uni.width(), 4),
                round(r.height() / uni.height(), 4)]

    # ---- 1) 起点必须是按下的那一点（本地坐标，不再减 union 原点）
    r = drag(100, 50, 500, 350)
    want = expect(100, 50, 500, 350)
    check("起点=按下位置（union 原点非 0 时不偏移）", r == want, f"{r} == {want}")

    # ---- 2) 外接矩形左上角那一块的 x/y 必须是 0
    #         （老实现算成 (0-(-100))/2000 = 0.05）
    r = drag(0, 0, 400, 300)
    check("左上角框的 x/y 是 0", r is not None and r[0] == 0.0 and r[1] == 0.0,
          str(r))

    # ---- 3) 反向拖（右下 → 左上）要归一化
    r = drag(800, 600, 400, 300)
    want = expect(800, 600, 400, 300)
    check("反向拖拽归一化", r == want, f"{r} == {want}")

    # ---- 4) 太小的框忽略
    check("过小的框被忽略", drag(10, 10, 20, 20) is None)

    # ---- 5) Esc 取消不抛异常
    picker.result = [0.1, 0.1, 0.2, 0.2]
    picker.start = QPoint(0, 0)
    try:
        picker.keyPressEvent(_EscEv())
        check("Esc 取消（不抛异常）", picker.result is None)
    except Exception as exc:                                    # noqa: BLE001
        check("Esc 取消（不抛异常）", False, str(exc))

    # ---- 6) 非注释行里不许出现 showFullScreen（macOS 独立 Space → 黑屏）
    # 只看**代码行**：把 docstring/注释整段剥掉，避免把"不能 showFullScreen"
    # 这句说明本身误判成违规（真正要防的是有人哪天把它又写成一句调用）。
    import ast

    tree = ast.parse(open(_SRC, encoding="utf-8").read())
    code_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue                                  # docstring，跳过
        if hasattr(node, "lineno"):
            code_lines.add(node.lineno)
    src_lines = open(_SRC, encoding="utf-8").read().splitlines()
    bad = [src_lines[n - 1].strip() for n in sorted(code_lines)
           if "showFullScreen" in src_lines[n - 1]]
    check("源码的代码行不含 showFullScreen", not bad, str(bad))

    # ---- 7) 必须真透明（不能是不透明黑底）
    check("窗口是半透明底（WA_TranslucentBackground）",
          bool(picker.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)))

    # ---- 8) 异步路径：pick_region(on_done=...) 用模块级 _ACTIVE 按住窗口
    region_picker._ACTIVE = None
    region_picker.pick_region(on_done=lambda v: None)
    check("异步路径用模块级 _ACTIVE 按住窗口（防 GC 析构带崩 App）",
          region_picker._ACTIVE is not None)

    # ---- 9) ★ 必须能**反复框**：结果立刻回调、_ACTIVE 立刻松开
    #
    #   v1.1.10 的 bug：结果回调挂在 `picker.destroyed` 上，而框完之后窗口
    #   只是 close()、_ACTIVE 又一直按着它 → destroyed 永远不发 →
    #   回调永远不跑。后果有两条，用户都报过：
    #     · 面板上的「框选…」被禁用之后再也回不来（一直是灰的）；
    #     · 框出来的区域压根没保存（settings 里 capture_rect_set 一直是 false）。
    print("\n== 9. 反复框选：回调必到、引用必松 ==")
    region_picker._ACTIVE = None
    got: list = []

    def drag_on(picker, x1, y1, x2, y2):
        picker.start = None
        picker.end = None
        picker.mousePressEvent(make_ev(DOWN, QPoint(x1, y1),
                                       QPoint(x1 - 100, y1 - 50)))
        picker.mouseMoveEvent(make_ev(MOVE, QPoint(x2, y2),
                                      QPoint(x2 - 100, y2 - 50)))
        picker.mouseReleaseEvent(make_ev(UP, QPoint(x2, y2),
                                         QPoint(x2 - 100, y2 - 50)))

    p1 = region_picker.pick_region(on_done=got.append)
    p1._union, p1._shot = uni, None
    drag_on(p1, 100, 50, 500, 350)
    check("框完之后回调**立刻**收到结果（不再依赖 destroyed 信号）",
          len(got) == 1 and got[0] == expect(100, 50, 500, 350), str(got))
    check("框完之后 _ACTIVE 松开（active() 变 False）",
          region_picker.active() is False)

    # 第二次框选：必须能再来一遍（这就是用户要的"一直是可框选的状态"）
    p2 = region_picker.pick_region(on_done=got.append)
    p2._union, p2._shot = uni, None
    drag_on(p2, 200, 100, 700, 400)
    check("可以**反复框选**：第二次照样回调",
          len(got) == 2 and got[1] == expect(200, 100, 700, 400), str(got))

    # 取消（Esc）也要回调、也要松手 —— 否则按钮又会卡在灰色
    p3 = region_picker.pick_region(on_done=got.append)
    p3._union, p3._shot = uni, None
    p3.keyPressEvent(_EscEv())
    check("Esc 取消也会回调（送 None）", len(got) == 3 and got[2] is None, str(got))
    check("Esc 之后 _ACTIVE 同样松开", region_picker.active() is False)

    # 过小的框：同样回调 None，且不写坏上一次的结果
    p4 = region_picker.pick_region(on_done=got.append)
    p4._union, p4._shot = uni, None
    drag_on(p4, 10, 10, 20, 20)
    check("过小的框回调 None", len(got) == 4 and got[3] is None, str(got))

    # 一次性开关：重复收尾不许重复回调（会把设置写两遍、也会把按钮状态搞乱）
    p5 = region_picker.pick_region(on_done=got.append)
    p5._union, p5._shot = uni, None
    drag_on(p5, 100, 50, 500, 350)
    before_n = len(got)
    p5._finish([0.9, 0.9, 0.05, 0.05])
    check("重复收尾不会重复回调", len(got) == before_n, str(got))

    print(f"\n共 {PASS + FAIL} 项，失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
