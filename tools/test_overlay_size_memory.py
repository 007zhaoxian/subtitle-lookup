"""浮窗「位置 + 尺寸」**自动记忆**的端到端回归测试。

为什么单独一个文件：`test_overlay_geometry.py` 测的是 settings / fit_geometry 这些
**纯逻辑**，它没把真浮窗造出来。而用户报的现象是「位置记得住、大小记不住」——
问题只可能出在 **真窗口 ↔ settings 的往返**这一层，所以这里必须用真 Qt 造窗：

    造窗 → 拖缩放 → 关掉 → 再造一个 → 看尺寸回来没有
    造窗 → 拖位置 → 关掉 → 再造一个 → 看位置回来没有，且尺寸还在

（offscreen 平台，不显示到屏幕上；设置写在临时目录，不碰用户真实配置。）

用法: python tools/test_overlay_size_memory.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_geo_mem_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import settings  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


BODY = "\n".join(
    f"sugarcoat sth —— 粉饰，把难听的说得好听（第 {i} 条）" for i in range(1, 9)
)


def main() -> int:
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:                      # pragma: no cover
        print("SKIP：这个环境没有 PyQt6 ——", exc)
        return 0

    qapp = QApplication.instance() or QApplication(["GeoMemTest"])
    if qapp is None:                              # pragma: no cover
        print("SKIP：Qt 起不来")
        return 0

    from overlay import SharedUiState, _make_widget_class

    cls = _make_widget_class()

    def build():
        """造一个和真浮窗完全同构的浮窗（同构造参数、同 flags）。"""
        ov = cls("豆包 · 字幕生词", BODY, lambda: None,
                 on_ask=lambda _t: None,
                 ui_state=SharedUiState(),
                 prev_app=None)
        ov.show()
        return ov

    def fresh():
        """关掉所有浮窗 + 清掉几何记录，回到"从没用过"的状态。"""
        settings.reset_overlay_geometry()
        return build()

    # ================= ① 缩放：记不住的老问题在这里 =================
    ov = fresh()
    w0, h0 = ov.width(), ov.height()
    check("初始用的是默认尺寸", (w0, h0) == (config.OVERLAY_WIDTH, h0),
          f"{w0}x{h0}")

    # 模拟"拖右下角放大"：apply_resize 是拖拽过程中调的，finish_resize 是松手时调的
    ov.apply_resize("se", ov.pos(), ov.size(), 120, 90)
    w1, h1 = ov.width(), ov.height()
    check("拖动过程中尺寸确实变了", (w1, h1) != (w0, h0), f"{w0}x{h0} → {w1}x{h1}")
    ov.finish_resize()

    saved = settings.get_overlay_size()
    check("★ 缩放松手后：尺寸立刻落盘", saved == (w1, h1),
          f"落盘={saved} 实际={w1}x{h1}")

    geo = settings.get_overlay_geo()
    check("★ 落盘的尺寸和位置能共存（不是互相覆盖）",
          geo.get("w") == w1 and geo.get("h") == h1 and "x" in geo,
          f"geo={geo}")

    ov.close()
    ov2 = build()
    check("★ 关掉再开：尺寸被恢复", (ov2.width(), ov2.height()) == (w1, h1),
          f"期望 {w1}x{h1}，实际 {ov2.width()}x{ov2.height()}")
    ov2.close()

    # ================= ② 位置：先缩放再拖动，两个都不能丢 =================
    ov3 = build()
    check("重开时尺寸仍是记住的那个", (ov3.width(), ov3.height()) == (w1, h1))
    ov3._was_moved = True
    # 注意挑一个**不会撞到屏幕边缘**的位置：offscreen 屏幕只有 800x800，
    # 而窗口已经 740 宽，x 最大只能是 60 —— 拿去当"没记住"的证据会冤枉代码
    # （fit_geometry 是故意把窗口夹回可见区域的，见 test_overlay_geometry）。
    ov3.move(40, 90)
    ov3.finish_move()
    geo = settings.get_overlay_geo()
    check("★ 拖动后：位置落盘", (geo.get("x"), geo.get("y")) == (40, 90),
          f"geo={geo}")
    check("★ 拖动后：尺寸没被顺手清掉", (geo.get("w"), geo.get("h")) == (w1, h1),
          f"geo={geo}")
    ov3.close()

    ov4 = build()
    check("★ 关掉再开：位置 + 尺寸都在",
          (ov4.x(), ov4.y()) == (40, 90) and (ov4.width(), ov4.height()) == (w1, h1),
          f"实际 ({ov4.x()}, {ov4.y()}) {ov4.width()}x{ov4.height()}")

    # ================= ③ 顺序反过来：先拖位置、再缩放 =================
    ov4._was_moved = True
    ov4.move(10, 90)
    ov4.finish_move()
    ov4.apply_resize("se", ov4.pos(), ov4.size(), 20, 40)
    w2, h2 = ov4.width(), ov4.height()
    ov4.finish_resize()
    geo = settings.get_overlay_geo()
    check("★ 拖位置 → 再缩放：位置和尺寸都在",
          (geo.get("x"), geo.get("y")) == (10, 90) and geo.get("w") == w2,
          f"geo={geo}")
    ov4.close()

    # ================= ④ 两个容易踩的坑 =================
    # 坑一：松手后 _was_resized 必须复位，否则之后随便点一下手柄都会重写一次尺寸
    ov5 = build()
    ov5.apply_resize("se", ov5.pos(), ov5.size(), 20, 20)
    check("拖动中：_was_resized 被置为 True（关窗兜底要靠它）",
          ov5._was_resized is True, f"_was_resized={ov5._was_resized}")
    ov5.finish_resize()
    check("★ 松手后 _was_resized 已复位（否则点一下手柄就误存一次）",
          ov5._was_resized is False, f"_was_resized={ov5._was_resized}")

    # 坑二：松手事件丢了也不能白拖 —— 关窗时必须补存
    ov5.apply_resize("se", ov5.pos(), ov5.size(), 20, 20)
    w3 = ov5.width()
    settings.save({"overlay_geo": {}})          # 先抹掉，看关窗能不能补回来
    ov5.close()                                 # 故意不调 finish_resize
    geo = settings.get_overlay_geo()
    check("★ 松手事件丢了：关窗时补存回来（尺寸没白改）",
          geo.get("w") == w3, f"期望 w={w3}，实际 geo={geo}")

    # 坑二：坏数据不能把已经记住的尺寸弄丢，也不能让 App 崩
    settings.save({"overlay_geo": {"x": 10, "y": 20, "w": "bad", "h": 300}})
    geo = settings.get_overlay_geo()
    check("坏尺寸只丢尺寸、不丢位置", geo.get("x") == 10 and "w" not in geo, f"geo={geo}")

    # ================= ⑤ 和"重置"按钮的配合 =================
    settings.reset_overlay_geometry()
    check("重置位置与大小 → 记录清空", settings.get_overlay_geo() == {})
    settings.set_overlay_pos(240, 300, {"x": 0, "y": 25, "w": 1440, "h": 880})
    settings.set_overlay_size(700, 420)
    settings.reset_overlay_size()
    geo = settings.get_overlay_geo()
    check("只重置大小：位置留着、尺寸没了",
          (geo.get("x"), geo.get("y")) == (240, 300)
          and settings.get_overlay_size() is None, f"geo={geo}")

    print()
    passed = sum(1 for _n, ok in results if ok)
    print(f"共 {len(results)} 项，通过 {passed}，失败 {len(results) - passed}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
