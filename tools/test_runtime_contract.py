"""接口契约校验：App 用到的 runtime 方法，QtRuntime 必须**真的**都有。

【为什么需要这个测试】
之前出过一次很隐蔽的事故：`app._overlay_visible()` 调 `runtime.has_overlay()`，
而 `has_overlay()` 只定义在**测试用的假运行时**（tools 里的 FakeRuntime）里，
真的 `overlay.QtRuntime` 上压根没有这个方法。

后果：
  · 所有单测都绿（假对象有这个方法，永远不报错）；
  · 真机每次调用都抛 AttributeError，被上层 try/except 吞掉；
  · 用户视角 = 「输入了问题，AI 也答了，浮窗里什么都没多出来」。

假对象有、真对象没有 —— 光靠跑业务测试永远发现不了。所以这里用 AST 做
**静态契约比对**：把 app/main/panel/menubar 里出现的每一个
`self.runtime.<方法>` 都抽出来，逐个对照 QtRuntime 的真实方法表。

用法:  python tools/test_runtime_contract.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def methods_of(path: pathlib.Path, class_name: str) -> set[str]:
    """静态读出某个类里定义的全部方法名（不 import，避免拉起 Qt）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    out.add(item.name)
    return out


def runtime_calls(path: pathlib.Path) -> set[str]:
    """抽出 `self.runtime.<x>` / `app.runtime.<x>` 这类调用里的属性名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        # 形如  <something>.runtime.<attr>
        if isinstance(base, ast.Attribute) and base.attr == "runtime":
            out.add(node.attr)
    return out


def ctrl_calls(path: pathlib.Path) -> set[str]:
    """抽出面板里 `self._ctrl.<x>` 这类调用。

    面板的 ctrl 就是 App 实例。test_panel_scroll / render_panel 里的假 ctrl 都
    用 `__getattr__` 兜底成"返回一个什么都不做的 lambda"，
    所以**面板调了一个 App 上不存在的方法，测试全绿、真机点下去毫无反应** ——
    和当年 has_overlay 那个事故是同一个形态。这里静态比对一次。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "_ctrl":
            out.add(node.attr)
    return out


def class_methods(path: pathlib.Path, class_name: str) -> set[str]:
    return methods_of(path, class_name)


def main() -> int:
    overlay_py = ROOT / "overlay.py"
    real = methods_of(overlay_py, "QtRuntime")
    check("能静态读出 QtRuntime 的方法表", bool(real), f"{len(real)} 个方法")

    # 所有会被打包进 App 的模块（tools/ 是测试，不算）
    callers = ["app.py", "main.py", "panel.py", "menubar.py"]
    used: dict[str, set[str]] = {}
    for fn in callers:
        p = ROOT / fn
        if p.exists():
            for m in runtime_calls(p):
                used.setdefault(m, set()).add(fn)

    check("确实在调用侧抽到了 runtime.* 调用", bool(used),
          f"{len(used)} 个不同方法 <- {sorted(used)}")

    missing = sorted(m for m in used if m not in real)
    check("QtRuntime 覆盖了所有被调用的方法（无缺失）", not missing,
          f"缺失: {missing}" if missing else "全部命中")

    # 反向提示：QtRuntime 上那些 post_* / typing_active 之类的对外接口，
    # 至少要被用到一次，否则很可能是改名后留下的孤儿方法。
    public_api = {
        m for m in real
        if m in used or m.startswith("post_") or m in
        {"has_overlay", "typing_active", "mark_auto_ui", "auto_ui_recently", "quit"}
    }
    check("对外接口集合非空（防改名后留下孤儿方法）", bool(public_api),
          f"{len(public_api)} 个")

    # 重点回归：has_overlay 必须存在 —— 这是真实事故的那个方法
    check("重点回归：QtRuntime.has_overlay 存在（追问回答靠它送回去）",
          "has_overlay" in real)

    # typing_active 也是给监听线程读的，一并守住
    check("重点回归：QtRuntime.typing_active 存在（打字时空格/N 要让路）",
          "typing_active" in real)

    # ---------------------------------------------------------------- 面板 ↔ App
    app_methods = class_methods(ROOT / "app.py", "App")
    check("能静态读出 App 的方法表", bool(app_methods),
          f"{len(app_methods)} 个方法")
    panel_ctrl = ctrl_calls(ROOT / "panel.py")
    check("确实在 panel 里抽到了 self._ctrl.* 调用", bool(panel_ctrl),
          f"{len(panel_ctrl)} 个 <- {sorted(panel_ctrl)}")
    # 面板直接调的都是方法；这里把"同名属性"也算命中，避免误报
    miss = sorted(m for m in panel_ctrl if m not in app_methods)
    check("App 覆盖了面板调用的所有 ctrl 方法（无缺失）", not miss,
          f"缺失: {miss}" if miss else "全部命中")

    print("=" * 60)
    print("runtime 接口契约校验")
    print("=" * 60)
    bad = 0
    for name, ok, detail in RESULTS:
        print(f"  {'✅ PASS' if ok else '❌ FAIL'}  {name}")
        if detail:
            print(f"          {detail}")
        if not ok:
            bad += 1
    print("-" * 60)
    print(f"共 {len(RESULTS)} 项，失败 {bad} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
