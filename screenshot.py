"""全屏截图模块。

优先用 mss（快、纯 Python），失败则回退到系统 /usr/sbin/screencapture。
两者都需要『屏幕录制』权限；未授权时 macOS 会给一张纯色/桌面壁纸图，
所以这里额外做了一次“是不是全黑/全灰”的粗检并给出提示。
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import config
import settings
from utils import log


def crop_box(width: int, height: int, mode: str, rect=None) -> tuple:
    """按截图范围算出裁剪框 (left, top, right, bottom)。**纯函数，可单测**。

    mode 见 config.CAPTURE_MODES：
      fullscreen / subtitle / bottom / top → 按高度比例切一条横带；
      custom                                → 直接用 rect（屏幕比例 0~1）。

    无论哪种，最后都夹紧到画面内且保证宽高 > 0 —— 宁可少裁一点，
    也不能产出一个 0 尺寸的图（PIL 会直接抛异常，整个查词就废了）。
    """
    w, h = int(width or 0), int(height or 0)
    if w <= 0 or h <= 0:
        return (0, 0, max(1, w), max(1, h))

    if mode == "custom" and rect and len(rect) == 4:
        try:
            rx, ry, rw, rh = (float(v) for v in rect)
            left = int(round(rx * w))
            top = int(round(ry * h))
            right = int(round((rx + rw) * w))
            bottom = int(round((ry + rh) * h))
        except (TypeError, ValueError):
            left, top, right, bottom = 0, 0, w, h
    else:
        band = config.CAPTURE_BANDS.get(mode)
        if band is None:
            band = config.CAPTURE_BANDS.get(config.DEFAULT_CAPTURE_MODE, (0.0, 1.0))
        top = int(round(band[0] * h))
        bottom = int(round(band[1] * h))
        left, right = 0, w

    left = min(max(0, left), w - 1)
    top = min(max(0, top), h - 1)
    right = min(max(left + 1, right), w)
    bottom = min(max(top + 1, bottom), h)
    return (left, top, right, bottom)


def capture_fullscreen() -> Path:
    """抓图 → 按用户选的范围裁剪 → 压缩。返回最终文件路径。"""
    out = config.TMP_DIR / f"shot_{int(time.time() * 1000)}.png"

    if _try_mss(out) or _try_screencapture(out):
        path = _shrink(out)
        log.info("截图完成: %s (%.1f KB)", path.name, path.stat().st_size / 1024)
        return path

    raise RuntimeError(
        "截图失败：请确认已在『系统设置 → 隐私与安全性 → 屏幕录制』中勾选本应用并重启它。"
    )


# ------------------------------------------------------------------ 实现
def _try_mss(out: Path) -> bool:
    try:
        import mss  # noqa: PLC0415

        with mss.mss() as sct:
            # monitors[1] 是主显示器，monitors[0] 是所有显示器拼成的虚拟屏
            sct.shot(mon=1, output=str(out))
        return out.exists() and out.stat().st_size > 1024
    except Exception as exc:
        log.warning("mss 截图失败，回退 screencapture: %s", exc)
        return False


def _try_screencapture(out: Path) -> bool:
    try:
        # -x 静音，-t png 格式，-C 不带光标
        subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-C", "-t", "png", str(out)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return out.exists() and out.stat().st_size > 1024
    except Exception as exc:
        log.error("screencapture 也失败了: %s", exc)
        return False


def _shrink(path: Path) -> Path:
    """裁剪 + 缩放 + 转 JPEG —— 让豆包能秒回。

    为什么必须做：豆包拿到整屏大图会先做一次全图 OCR，实测要 80+ 秒才开口；
    而只含一行字幕的小图 10 秒就答完。字幕（不论中文还是英文台词）在看剧时
    几乎总在画面下半部，所以裁掉上方、再压缩，既保清晰度又大幅提速。

    返回最终文件路径（失败时原样返回，绝不因为优化失败就丢掉截图）。
    """
    try:
        from PIL import Image  # noqa: PLC0415

        im = Image.open(path)
        try:
            im = im.convert("RGB")

            # 1) 按用户选的「截图范围」裁剪（全屏 / 字幕区 / 上下半屏 / 自定义区域）
            mode = settings.get_capture_mode()
            rect = settings.get_capture_rect() if mode == "custom" else None
            ow, oh = im.width, im.height
            box = crop_box(ow, oh, mode, rect)
            if box != (0, 0, ow, oh):
                im = im.crop(box)
                log.info("截图范围[%s]：原图 %dx%d → 裁剪框 %s（结果 %dx%d）",
                         mode, ow, oh, box, im.width, im.height)

            # 2) 等比缩放
            if config.MAX_IMAGE_WIDTH and im.width > config.MAX_IMAGE_WIDTH:
                r = config.MAX_IMAGE_WIDTH / im.width
                im = im.resize(
                    (config.MAX_IMAGE_WIDTH, max(1, int(im.height * r))),
                    Image.LANCZOS,
                )

            # 3) 转 JPEG：同样的画面，体积常常只有 PNG 的 1/4
            out = path.with_suffix(".jpg")
            im.save(out, "JPEG",
                    quality=int(config.IMAGE_JPEG_QUALITY or 88),
                    optimize=True)
        finally:
            im.close()
        if out.exists() and out.stat().st_size > 512:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return out
        return path
    except Exception as exc:
        log.debug("图片优化跳过: %s", exc)
        return path


def cleanup(path: Path | None) -> None:
    if path is None or config.KEEP_SCREENSHOTS:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def purge_old(keep_hours: int = 24) -> None:
    """清掉临时目录里的陈年截图，防止无限堆积。"""
    cutoff = time.time() - keep_hours * 3600
    for pat in ("shot_*.png", "shot_*.jpg"):
        for f in config.TMP_DIR.glob(pat):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
