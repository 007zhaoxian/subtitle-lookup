"""用户设置（可编辑的提示词、悬浮窗位置尺寸…）的读写。

为什么要单独一份 JSON：
* 打包后的 .app bundle 是**只读**的，不能把用户改的提示词写回 bundle；
* 所以存到 ~/Library/Application Support/DoubaoLookup/settings.json，
  改完立刻生效、不需要重新打包，升级 App 也不会丢。

设计原则：**永不因为设置文件坏掉而影响启动**。任何异常都退回默认值。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import config
from utils import log

_LOCK = threading.RLock()
_CACHE: dict | None = None

_REQUIRED_KEYS = ("prompt",)


def _defaults() -> dict:
    return {
        "prompt": config.DEFAULT_PROMPT,
        "updated_at": "",
        # 用户自己改过的预设模板 {名字: 正文}。没改过的预设直接读 config 里的。
        "preset_overrides": {},
        # 悬浮窗几何：{"x","y","w","h","screen":{x,y,w,h}}
        # screen 存的是当时那块屏幕的可用区域，用于换分辨率/拔外接屏后换算位置。
        "overlay_geo": {},
        # 兼容字段（旧版本只记过尺寸，读到就并进 overlay_geo）
        "overlay_size": [],
        # 截图范围：模式见 config.CAPTURE_MODES
        "capture_mode": config.DEFAULT_CAPTURE_MODE,
        # 自定义区域的屏幕比例 [x, y, w, h]（0~1），只有 mode=custom 时用它
        "capture_rect": list(config.DEFAULT_CAPTURE_RECT),
        # 用户是否**亲自框选过**（没有的话，切到 custom 时要主动引导他框一次）
        "capture_rect_set": False,
        # ---- 看剧模式（按【空格】查词的总开关）----
        # ★ 必须**持久化**，而且全新安装默认就是开的。
        #
        #   以前这个开关只活在内存里（`App.__init__` 里写死的
        #   `self._mode_on = False`），于是每次启动都回到"关"。
        #   后果长得特别像"功能坏了"：装完新版 / 重启过 App 之后按空格，
        #   拦截层直接放行，**连一行日志都不产生** —— 用户只能得出
        #   "空格没用 / 这功能不行"的结论。（2026-09-15 实测踩到，
        #   当时 App 刚重启，按空格日志一片空白。）
        #   这程序存在的意义就是看剧时按空格查词，所以默认开启；
        #   用户真想临时关掉，关一次也会被记住，不会又自己弹回来。
        "mode_on": True,
        # ---- 查词引擎（v1.2.2 起只剩"直连 API"这一条通道）----
        # 当前选中的服务商 id，见 config.PROVIDERS
        "provider": config.DEFAULT_PROVIDER,
        # 每个服务商各自的 key：{"deepseek": "sk-…", "hunyuan": "…"}
        # 【明文】用户明确选择了明文存储（方便手改和迁移），见 README 的安全提示。
        # 按服务商分别存，切来切去不用重填。
        "api_keys": {},
        # 每个服务商各自选定的模型：{"deepseek": "deepseek-flash"}
        # 空字符串 = 用该服务商的 default_model
        "models": {},
        # 每个服务商自定义的 base_url（主要给 custom 用，也可覆盖内置的）
        "base_urls": {},
        # ---- 悬浮窗显示 ----
        # 浮窗正文字号（px）。用户在设置面板拖滑块改，**当场生效**：
        # overlay.body_font_px() 每次渲染都重新读一次，不缓存。
        "overlay_font_size": int(config.OVERLAY_FONT_SIZE),
        # 是否把每次 AI 输出记到桌面上的「观看记录」（剧名+日期.md）。
        # 默认开 —— 用户提需求时就是要它一直记着。
        "viewlog_enabled": bool(config.VIEWLOG_ENABLED),
        # 观看记录落在哪个文件夹。空串 = 用默认（桌面）。
        "viewlog_dir": "",
    }


def _read_file() -> dict:
    try:
        raw = config.SETTINGS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("settings.json 顶层不是对象")
        return data
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("读取设置失败（改用默认值）: %s", exc)
        return {}


def _as_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _sanitize_geo(raw) -> dict:
    """悬浮窗几何的清洗 —— **逐字段**清洗，不是整体作废。

    为什么逐字段：宽高和位置是两件独立的事，各自都可能单独更新。
    用户**只拖动位置、从不缩放**时，记录里就只有 x/y 而没有 w/h；
    早期版本写的是"w/h 缺一个就整体作废"，结果那种情况下位置被默默丢掉，
    下次浮窗又回到屏幕中间 —— 正是用户抱怨的那个现象。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    w = _as_int(raw.get("w"))
    h = _as_int(raw.get("h"))
    if w and h and w > 0 and h > 0:
        out["w"] = max(int(w), config.OVERLAY_MIN_WIDTH)
        out["h"] = max(int(h), config.OVERLAY_MIN_HEIGHT)
    x = _as_int(raw.get("x"))
    y = _as_int(raw.get("y"))
    if x is not None and y is not None:
        out["x"], out["y"] = int(x), int(y)
        sc = raw.get("screen")
        if isinstance(sc, dict):
            sx, sy = _as_int(sc.get("x")), _as_int(sc.get("y"))
            sw, sh = _as_int(sc.get("w")), _as_int(sc.get("h"))
            if None not in (sx, sy, sw, sh) and sw > 0 and sh > 0:
                out["screen"] = {"x": int(sx), "y": int(sy),
                                 "w": int(sw), "h": int(sh)}
    return out


def _sanitize_presets(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip():
            out[k.strip()[:60]] = v[: config.PROMPT_MAX_CHARS]
    return out


def _sanitize(data: dict) -> dict:
    """补全缺失字段 + 修正非法值。"""
    out = _defaults()
    out.update({k: v for k, v in data.items() if k in out or k == "preset_name"})

    prompt = out.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        out["prompt"] = config.DEFAULT_PROMPT
    elif len(prompt) > config.PROMPT_MAX_CHARS:
        out["prompt"] = prompt[: config.PROMPT_MAX_CHARS]

    # 看剧模式：任何非空值都按真值处理，坏值一律退回 True（默认开）
    raw_mode = out.get("mode_on")
    out["mode_on"] = True if raw_mode is None else bool(raw_mode)

    if not isinstance(out.get("preset_name"), str):
        out["preset_name"] = ""

    out["preset_overrides"] = _sanitize_presets(out.get("preset_overrides"))

    raw_geo = out.get("overlay_geo")
    geo = _sanitize_geo(raw_geo)
    # 兼容老版本：那时还没有 overlay_geo 字段，只写过 overlay_size。
    # 注意判据是"overlay_geo 缺失或是个空 dict"—— 如果是 list 这种**明显的坏值**，
    # 说明这一份记录就是坏的，绝不能拿旧的 overlay_size 把它"复活"。
    legacy_ok = (raw_geo is None) or (isinstance(raw_geo, dict) and not raw_geo)
    if not geo and legacy_ok:
        size = out.get("overlay_size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            geo = _sanitize_geo({"w": size[0], "h": size[1]})
    out["overlay_geo"] = geo
    out["overlay_size"] = ([geo["w"], geo["h"]] if geo.get("w") and geo.get("h")
                           else [])

    # ---- 截图范围 ----
    mode = out.get("capture_mode")
    if not isinstance(mode, str) or mode not in config.CAPTURE_MODES:
        mode = config.DEFAULT_CAPTURE_MODE
    out["capture_mode"] = mode
    out["capture_rect"] = _sanitize_capture_rect(out.get("capture_rect"))

    # ---- 查词引擎 ----
    provider = out.get("provider")
    if not isinstance(provider, str) or provider not in config.PROVIDERS:
        provider = config.DEFAULT_PROVIDER
    out["provider"] = provider

    # 三张 {服务商: 字符串} 的表，统一清洗：非 str 的值丢掉、超长的截断
    out["api_keys"] = _sanitize_str_map(out.get("api_keys"))
    out["models"] = _sanitize_str_map(out.get("models"))
    out["base_urls"] = _sanitize_str_map(out.get("base_urls"))

    # ---- 悬浮窗显示 ----
    out["overlay_font_size"] = _clamp_font(out.get("overlay_font_size"))
    # 同 mode_on 的写法：缺字段时 _defaults() 已经给了 True，
    # 只有用户**明确**存过 false 才会是关的。
    raw_vl = out.get("viewlog_enabled")
    out["viewlog_enabled"] = True if raw_vl is None else bool(raw_vl)
    # 记录目录：只接受字符串；去掉首尾空白与末尾斜杠，空串回落默认桌面
    raw_dir = out.get("viewlog_dir")
    out["viewlog_dir"] = (
        str(raw_dir).strip().rstrip("/") if isinstance(raw_dir, str) else ""
    )
    return out


def _sanitize_str_map(raw) -> dict:
    """把 {服务商: 字符串} 这类表洗干净。

    坏数据（不是 dict、值不是 str）一律丢掉 —— 设置文件坏掉不能影响启动，
    宁可当作"没填过 key"，也不要让程序带着一个莫名其妙的类型去发请求。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k:
            continue
        if not isinstance(v, str):
            continue
        out[k] = v.strip()[:1024]
    return out


def _clamp_font(v) -> int:
    """把字号夹进 [OVERLAY_FONT_MIN, OVERLAY_FONT_MAX]。

    坏值（None / "abc" / NaN）一律退回默认 15 —— 和别处一样的原则：
    **设置文件坏掉不能影响启动**，更不能让浮窗变成 0 号字（那样用户会以为
    "程序坏了"，其实是自己手改坏了一个数字）。
    """
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return int(config.OVERLAY_FONT_SIZE)
    return max(int(config.OVERLAY_FONT_MIN),
               min(int(config.OVERLAY_FONT_MAX), n))


def _clamp01(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                      # NaN
        return None
    return min(1.0, max(0.0, f))


def _sanitize_capture_rect(raw) -> list[float]:
    """自定义截图区域：必须是 4 个 0~1 的比例，且宽高不能为 0。

    坏数据一律退回默认（宁可截错区域，也不能因为一个坏配置就崩掉截图）。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return list(config.DEFAULT_CAPTURE_RECT)
    vals = [_clamp01(v) for v in raw]
    if any(v is None for v in vals):
        return list(config.DEFAULT_CAPTURE_RECT)
    x, y, w, h = vals               # type: ignore[misc]
    if w <= 0 or h <= 0:
        return list(config.DEFAULT_CAPTURE_RECT)
    # 越界就拉回屏幕内
    x = min(x, 1.0 - w) if x + w > 1.0 else x
    y = min(y, 1.0 - h) if y + h > 1.0 else y
    return [x, y, w, h]


def load(force: bool = False) -> dict:
    """读取设置（带缓存）。线程安全。"""
    global _CACHE
    with _LOCK:
        if _CACHE is None or force:
            _CACHE = _sanitize(_read_file())
        return dict(_CACHE)


def save(data: dict) -> bool:
    """原子写入设置文件。返回是否成功。"""
    global _CACHE
    before = load()
    patch = data or {}
    merged = _sanitize({**before, **patch})
    try:
        config.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(config.SETTINGS_DIR), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, config.SETTINGS_FILE)
    except Exception as exc:
        log.error("保存设置失败: %s", exc)
        return False
    with _LOCK:
        _CACHE = merged
    # 日志里带上"这次改了哪些字段"，以及几何的具体值 ——
    # 排查「记住了但没生效」这类问题时，这一行能直接指出是谁把字段写没了。
    changed = ",".join(sorted(k for k, v in patch.items() if before.get(k) != v))
    log.info("设置已保存（%s）到 %s", changed or "无实际变化", config.SETTINGS_FILE)
    if "overlay_geo" in patch:
        log.info("  悬浮窗几何 → %s", merged.get("overlay_geo"))
    return True


# ------------------------------------------------------------------ 提示词
def get_prompt() -> str:
    """当前生效的提示词（用户改过就用用户的）。"""
    p = (load().get("prompt") or "").strip()
    return p or config.DEFAULT_PROMPT


def set_prompt(text: str, preset_name: str = "") -> bool:
    text = (text or "").strip()
    if not text:
        return False
    import time

    return save({"prompt": text[: config.PROMPT_MAX_CHARS],
                 "preset_name": preset_name,
                 "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def reset_prompt() -> bool:
    return set_prompt(config.DEFAULT_PROMPT, "字幕生词（默认）")


def is_customized() -> bool:
    """用户是否改过提示词（面板上用来显示「已自定义」）。"""
    return get_prompt().strip() != config.DEFAULT_PROMPT.strip()


# ★ v1.2.2 删掉了「发送时自动追加的约束句」（guard_prompt / guard_enabled）。
#   那句「只分析本条消息这张图、忽略上文」是**网页版豆包专用**的：那时所有查词
#   都挤在同一个豆包对话里，模型会把上一张图的字幕也答一遍，所以要在发送时贴一句
#   硬约束压住它。API 后端每查一次词都会**重建 messages**（[提示词+图]），
#   天然没有串味问题，这句约束就成了纯粹的负担 —— 而且它常驻对话历史，
#   正是"追问被拒绝回答"的元凶之一。随网页版一起删除。

# ------------------------------------------------------------ 预设模板（可编辑）
def get_presets() -> list[tuple[str, str]]:
    """全部预设：内置的 4 套 + 用户改过/新建的（同名覆盖）。"""
    out: dict[str, str] = dict(config.PROMPT_PRESETS)
    for k, v in (load().get("preset_overrides") or {}).items():
        out[k] = v
    # 用户新增的排在后面
    return list(out.items())


def preset_names() -> list[str]:
    return [n for n, _ in get_presets()]


def save_preset(name: str, text: str) -> bool:
    name = (name or "").strip()[:60]
    text = (text or "").strip()
    if not name or not text:
        return False
    cur = dict(load().get("preset_overrides") or {})
    cur[name] = text[: config.PROMPT_MAX_CHARS]
    return save({"preset_overrides": cur})


def delete_preset(name: str) -> bool:
    """删除 / 还原预设。

    内置预设（config.PROMPT_PRESETS）是只读的，删不掉，所以对它们
    「删除」= 撤掉用户自己的覆盖 → 回到内置原文。
    用户自建的预设会被真正移除。
    """
    name = (name or "").strip()
    cur = dict(load().get("preset_overrides") or {})
    cur.pop(name, None)
    return save({"preset_overrides": cur})


# ------------------------------------------------------- 看剧模式（持久化）
def get_mode() -> bool:
    """看剧模式该不该是开的。

    持久化的意义：用户开过一次，之后**重启/升级 App 也还是开的**，
    不会出现"装完新版按空格没反应"（因为默认值是关的）。
    """
    return bool(load().get("mode_on"))


def set_mode(on: bool) -> bool:
    """记住看剧模式。失败也不抛 —— 不能让写设置失败影响正常用。"""
    try:
        return save({"mode_on": bool(on)})
    except Exception as exc:                       # pragma: no cover
        log.warning("记住看剧模式失败: %s", exc)
        return False


def settings_path() -> Path:
    return config.SETTINGS_FILE


# ------------------------------------------------------ 悬浮窗字号 / 观看记录
def get_overlay_font_size() -> int:
    """浮窗正文字号（px）。

    ★ 面板上的滑块**每动一下就调 set**，浮窗那边每次渲染都重新读 ——
      所以用户拖滑块时，浮窗里已显示的内容会当场跟着变大变小，
      不用关掉重开（用户要的"放个示例"就是这个效果的前提）。
    """
    return _clamp_font(load().get("overlay_font_size"))


def set_overlay_font_size(px) -> bool:
    """记住字号。夹在合法范围内；写失败也不抛。"""
    try:
        return save({"overlay_font_size": _clamp_font(px)})
    except Exception as exc:                       # pragma: no cover
        log.warning("记住字号失败: %s", exc)
        return False


def get_viewlog_enabled() -> bool:
    """要不要把 AI 输出记到桌面的观看记录里。"""
    return bool(load().get("viewlog_enabled"))


def set_viewlog_enabled(on: bool) -> bool:
    try:
        return save({"viewlog_enabled": bool(on)})
    except Exception as exc:                       # pragma: no cover
        log.warning("记住观看记录开关失败: %s", exc)
        return False


# ------------------------------------------------------ 观看记录文件夹
def get_viewlog_dir() -> str:
    """用户在面板里选的记录文件夹。**空串 = 没设过，用默认桌面。**"""
    return str(load().get("viewlog_dir") or "").strip()


def set_viewlog_dir(path: str) -> bool:
    """记住记录文件夹。传空串 = 恢复默认（桌面）。"""
    try:
        return save({"viewlog_dir": str(path or "").strip()})
    except Exception as exc:                       # pragma: no cover
        log.warning("记住观看记录文件夹失败: %s", exc)
        return False


def effective_viewlog_dir() -> Path:
    """**真正生效**的记录目录。

    优先级：`DL_VIEWLOG_DIR` 环境变量 > 面板里设的目录 > 「桌面」。
    环境变量放最高，是为了让回归测试/探针能强制重定向 ——
    否则跑一次测试就往用户桌面上丢一份「未知剧名 xxx.md」（踩过这个坑）。

    目录**不存在时自动建**；建不出来就退回默认桌面（调用方不必处理异常）。
    """
    env = (os.environ.get("DL_VIEWLOG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    raw = get_viewlog_dir()
    if not raw:
        return config.VIEWLOG_DIR_DEFAULT
    try:
        p = Path(raw).expanduser()
    except Exception:
        return config.VIEWLOG_DIR_DEFAULT
    return p


def ensure_viewlog_dir() -> Path:
    """拿到生效目录并**确保它存在**（建不出来就退回桌面）。"""
    p = effective_viewlog_dir()
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception as exc:
        log.warning("观看记录目录不可用（%s），退回桌面：%s", p, exc)
        return config.VIEWLOG_DIR_DEFAULT


# ------------------------------------------------------------ 悬浮窗几何（位置 + 尺寸）
def get_overlay_geo() -> dict:
    """上次的悬浮窗几何。没记录过返回 {}（那就用默认位置）。"""
    return dict(load().get("overlay_geo") or {})


def get_overlay_size() -> tuple[int, int] | None:
    """上次的悬浮窗尺寸；没记录过返回 None（高度自动适应内容）。"""
    geo = get_overlay_geo()
    w, h = geo.get("w"), geo.get("h")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return None


def _merge_geo(**kw) -> bool:
    """**部分**更新几何（只动你给的那几个字段，其余原样保留）。"""
    geo = get_overlay_geo()
    geo.update(kw)
    return save({"overlay_geo": geo})


def set_overlay_geometry(x: int, y: int, w: int, h: int,
                         screen: dict | None = None) -> bool:
    """把「位置 + 尺寸」**一次性**写进去（浮窗松手时走这条）。

    为什么要专门有这么一个函数：以前松手是连着调两次 ——
    先 `set_overlay_size()`、再 `set_overlay_pos()`，各自做一遍"读出来→改→写回去"。
    这中间只要有一次没读到另一半（写失败、缓存没跟上、别的线程刚好也在写），
    **另一半就被静默吞掉**：用户看到的现象正是"位置记得住、大小记不住"。
    一次调用只做一次读-改-写，压根不存在这个窗口。

    `screen` 传 None 时沿用上一次记下的屏幕区域（不要顺手把它抹掉）。
    """
    geo: dict = {
        "x": int(x),
        "y": int(y),
        "w": max(config.OVERLAY_MIN_WIDTH, int(w)),
        "h": max(config.OVERLAY_MIN_HEIGHT, int(h)),
    }
    if isinstance(screen, dict) and screen:
        geo["screen"] = screen
    else:
        old = get_overlay_geo().get("screen")
        if isinstance(old, dict) and old:
            geo["screen"] = old
    return save({"overlay_geo": geo})


def set_overlay_size(width: int, height: int) -> bool:
    w = max(config.OVERLAY_MIN_WIDTH, int(width))
    h = max(config.OVERLAY_MIN_HEIGHT, int(height))
    return _merge_geo(w=w, h=h)


def set_overlay_pos(x: int, y: int, screen: dict | None = None) -> bool:
    """记下位置。screen = 当时那块屏幕的可用区域 {x,y,w,h}。"""
    kw: dict = {"x": int(x), "y": int(y)}
    if isinstance(screen, dict):
        kw["screen"] = screen
    return _merge_geo(**kw)


def reset_overlay_geometry() -> bool:
    """回到「默认宽度 + 底部居中 + 高度自适应」。"""
    return save({"overlay_geo": {}, "overlay_size": []})


def reset_overlay_size() -> bool:
    """只重置尺寸，保留位置（保持旧行为，菜单里那个按钮还在用）。"""
    geo = get_overlay_geo()
    geo.pop("w", None)
    geo.pop("h", None)
    return save({"overlay_geo": geo, "overlay_size": []})


# ------------------------------------------------------------------ 截图范围
def get_capture_mode() -> str:
    """当前截图范围模式（一定是个合法值）。"""
    m = load().get("capture_mode")
    return m if isinstance(m, str) and m in config.CAPTURE_MODES \
        else config.DEFAULT_CAPTURE_MODE


def set_capture_mode(mode: str) -> bool:
    mode = (mode or "").strip()
    if mode not in config.CAPTURE_MODES:
        log.warning("忽略非法的截图范围模式: %r", mode)
        return False
    return save({"capture_mode": mode})


def get_capture_rect() -> list[float]:
    """自定义区域的屏幕比例 [x, y, w, h]（0~1）。"""
    return _sanitize_capture_rect(load().get("capture_rect"))


def set_capture_rect(rect) -> bool:
    return save({"capture_rect": _sanitize_capture_rect(rect),
                 "capture_rect_set": True})


# ------------------------------------------------------------------ 查词引擎
def get_provider() -> str:
    """当前选中的服务商 id。"""
    p = load().get("provider")
    return p if isinstance(p, str) and p in config.PROVIDERS else config.DEFAULT_PROVIDER


def set_provider(pid: str) -> bool:
    pid = (pid or "").strip()
    if pid not in config.PROVIDERS:
        log.warning("忽略非法的服务商: %r", pid)
        return False
    return save({"provider": pid})


def provider_config(pid: str | None = None) -> dict:
    """取服务商的**生效配置**：内置定义 + 用户覆盖（base_url / 模型）。

    返回的 dict 一定含 label / base_url / model / key / vision 五个键，
    调用方（api_client、设置界面）只认这五个，不用再关心来源。
    """
    pid = pid or get_provider()
    spec = dict(config.PROVIDERS.get(pid) or config.PROVIDERS[config.DEFAULT_PROVIDER])
    data = load()
    override_url = (data.get("base_urls") or {}).get(pid) or ""
    model = (data.get("models") or {}).get(pid) or ""
    key = (data.get("api_keys") or {}).get(pid) or ""

    base_url = (override_url or spec.get("base_url") or "").rstrip("/")
    model = model or spec.get("default_model") or ""
    vision = model in (spec.get("vision_models") or [])
    return {
        "id": pid,
        "label": spec.get("label", pid),
        "base_url": base_url,
        "model": model,
        "key": key,
        "vision": vision,
        "key_hint": spec.get("key_hint", ""),
        "models": list(spec.get("models") or []),
        "vision_models": list(spec.get("vision_models") or []),
        # 关掉"思考"用的额外请求字段（思考型模型必需，见 config.PROVIDERS 里的注释）。
        # 没配的服务商给空 dict —— api_client 只会把它 update 进 payload，不影响正常请求。
        "no_think": dict(spec.get("no_think") or {}),
    }


def get_api_key(pid: str | None = None) -> str:
    return provider_config(pid)["key"]


def set_api_key(key: str, pid: str | None = None) -> bool:
    """存某个服务商的 key（**明文**，用户明确选择）。传空串即清除。"""
    pid = pid or get_provider()
    if pid not in config.PROVIDERS:
        return False
    table = dict(load().get("api_keys") or {})
    key = (key or "").strip()
    if key:
        table[pid] = key
    else:
        table.pop(pid, None)
    return save({"api_keys": table})


def get_model(pid: str | None = None) -> str:
    return provider_config(pid)["model"]


def set_model(model: str, pid: str | None = None) -> bool:
    pid = pid or get_provider()
    if pid not in config.PROVIDERS:
        return False
    table = dict(load().get("models") or {})
    model = (model or "").strip()
    if model:
        table[pid] = model
    else:
        table.pop(pid, None)        # 空 = 回到该服务商的默认模型
    return save({"models": table})


def get_base_url(pid: str | None = None) -> str:
    return provider_config(pid)["base_url"]


def set_base_url(url: str, pid: str | None = None) -> bool:
    pid = pid or get_provider()
    if pid not in config.PROVIDERS:
        return False
    table = dict(load().get("base_urls") or {})
    url = (url or "").strip().rstrip("/")
    if url:
        table[pid] = url
    else:
        table.pop(pid, None)
    return save({"base_urls": table})


def mask_key(key: str) -> str:
    """把 key 变成能显示的样子：sk-62b0…ce46。太短就整体打码。"""
    key = key or ""
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:6]}…{key[-4:]}"


def provider_ready(pid: str | None = None) -> tuple[bool, str]:
    """这个服务商现在能不能用。返回 (是否可用, 不能用的原因)。

    ⚠️ 刻意**不**在这里校验"模型是否支持视觉" —— 那需要真发一次请求，
    属于「测试连通性」按钮的职责。这里只做离线可判定的检查。
    """
    cfg = provider_config(pid)
    if not cfg["key"]:
        return False, f"还没填 {cfg['label']} 的 API Key"
    if not cfg["base_url"]:
        return False, f"{cfg['label']} 还没填 Base URL"
    if not cfg["model"]:
        return False, f"{cfg['label']} 还没填模型名"
    return True, ""
