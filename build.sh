#!/bin/bash
# 一键打包「看剧查词-美剧」.app（Apple Silicon / arm64）
#   用法:  bash build.sh
#   复用已有环境（不联网装依赖）:  SKIP_VENV=1 PY=/path/to/python bash build.sh
#
# 与旧项目 doubao-lookup 的区别（别混用）：
#   APP_NAME / BUNDLE_ID / 证书名 / spec 文件名 全都换成了 SubtitleLookup 一套，
#   这样它跟 /Applications/豆包查词.app 是两个独立 App，可共存、权限不串味。
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="SubtitleLookup"
APP_DISPLAY="看剧查词-美剧"
BUNDLE_ID="com.zhaowentian.subtitlelookup"
SPEC="subtitle_lookup.spec"
VENV=".venv"
PY="${PY:-python3}"

echo "==> 1/6 架构自检"
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  echo "!! 当前架构是 ${ARCH}，不是 Apple Silicon。"
  echo "   若在 Intel 上打包，请把 $SPEC 里的 target_arch 改成 x86_64。"
fi

if [ "${SKIP_VENV:-0}" = "1" ]; then
  echo "==> 2/6 跳过虚拟环境（SKIP_VENV=1），直接用 $PY"
  echo "==> 3/6 跳过依赖安装"
  PYBIN="$PY"
else
  echo "==> 2/6 准备虚拟环境"
  if [ ! -d "$VENV" ]; then
    # 用 arm64 原生 python，避免 universal2 混淆
    arch -arm64 "$PY" -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"

  echo "==> 3/6 安装依赖"
  python -m pip install --upgrade pip wheel >/dev/null
  python -m pip install -r requirements.txt
  python -m pip install pyinstaller
  PYBIN="python"
fi

echo "==> 4/6 依赖自检（打包前确认关键模块都能 import）"
# ★ v1.2.2：Playwright 那一步删掉了 —— 网页版豆包模块整体移除后它已不是依赖。
#   这里改成逐个试 import：**漏装一个模块，打包不会报错，装完才会一启动就崩**。
"$PYBIN" - <<'PYEOF'
import importlib.util   # ★ 必须显式 import importlib.util：只写 `import importlib`
                        #   在本环境（Python 3.13）拿不到 .util 子模块，会
                        #   AttributeError: module 'importlib' has no attribute 'util'
missing = [m for m in ("PyQt6.QtWidgets", "rumps", "pynput", "mss", "PIL", "objc")
           if not importlib.util.find_spec(m)]
if missing:
    raise SystemExit(f"缺少依赖：{missing} —— 先 pip install -r requirements.txt")
print("依赖自检 ok")
PYEOF

echo "==> 5/6 PyInstaller 打包"
# 输出目录可用 DIST= 覆盖。**重打包时一定要换个新目录名**（DIST=dist2、dist3…）：
#   · 这里的 `rm -rf build dist` 会触发"批量删除保护"（几百上千个文件）；
#   · 就算跳过，PyInstaller 的 COLLECT 阶段自己也会 `Removing dir dist/<名字>`，同样被拦。
# 换目录是最省事的解法；装进 /Applications 之后再手动清掉旧产物。
DIST="${DIST:-dist}"
WORK="${WORK:-build}"
# spec 里强调过：--distpath 必须是**绝对路径**，相对路径时 BUNDLE 阶段会静默不产出 .app
case "$DIST" in
  /*) ;;
  *) DIST="$PWD/$DIST" ;;
esac
case "$WORK" in
  /*) ;;
  *) WORK="$PWD/$WORK" ;;
esac
if [ "${SKIP_CLEAN:-0}" != "1" ] && [ "$DIST" = "$PWD/dist" ]; then
  rm -rf build dist
fi
# 注意：这里**不加** --clean。
# --clean 会批量删除 PyInstaller 的 bincache（成百上千个文件），
# 容易被"批量删除保护"拦下导致打包中断；而它带来的收益（强制全量重编）
# 对本项目并不需要 —— 去掉之后打包反而更快。
arch -arm64 "$PYBIN" -m PyInstaller --noconfirm \
  --distpath "$DIST" --workpath "$WORK" "$SPEC"

echo "==> 6/6 签名 + 去隔离属性"
APP="${DIST}/${APP_NAME}.app"
CERT_NAME="SubtitleLookup Local Signing"
if /usr/bin/security find-identity -v -p codesigning 2>/dev/null | grep -q "$CERT_NAME"; then
  # ⚠️ 必须写成 ${CERT_NAME}：后面紧跟中文引号「」时，
  #    bash 会把多字节字符一起当成变量名的一部分，报 unbound variable。
  echo "    使用稳定证书「${CERT_NAME}」"
  echo "    （证书签名 → TCC 权限绑定在证书上，重打包后权限依然有效）"
  /usr/bin/codesign --force --deep --sign "$CERT_NAME" \
    --keychain "$HOME/Library/Keychains/login.keychain-db" "$APP"
else
  echo "    !! 未找到稳定证书，回退 ad-hoc 签名。"
  echo "    !! ad-hoc 的权限绑定在 cdhash 上，**每次重打包后系统设置里那条开关都会失效**，"
  echo "    !! 需要 tccutil reset 后重新授权。建议先执行 bash make_cert.sh 生成稳定证书。"
  /usr/bin/codesign --force --deep --sign - "$APP"
fi
/usr/bin/xattr -cr "$APP" 2>/dev/null || true
/usr/bin/codesign --verify --verbose=2 "$APP" || true
echo "    指定要求：$(/usr/bin/codesign -d -r- "$APP" 2>&1 | grep designated || true)"

cat <<EOF

============================================================
打包完成： ${APP}
大小：$(du -sh "$APP" | cut -f1)

下一步（重要）：
 1. 安装到「应用程序」（${APP_DISPLAY} 是显示名，中文名更易认）。
    注意**不要用 rm -rf** —— /Applications 下几百个文件会被"批量删除保护"拦下。
    直接把旧版改名挪走，再拷新版进去：
      mv "/Applications/${APP_DISPLAY}.app" "/Applications/${APP_DISPLAY}.app.old-$(date +%Y%m%d-%H%M%S)"
      ditto "$APP" "/Applications/${APP_DISPLAY}.app"
    （用 ditto 而不是 cp -R：ditto 会保留 .framework 里的符号链接）
    确认新版能启动后，旧的那份可以自己在访达里拖进废纸篓。
    与 /Applications/豆包查词.app 是**两个 App**，bundle id 不同，可共存。

 2. 首次运行会请求权限，授予后**必须退出并重新打开**：
      · 辅助功能 (Accessibility)   ← 监听触发键（默认空格）
      · 输入监控 (Input Monitoring)
      · 屏幕录制 (Screen Recording) ← 截图
      open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ⚠️ 新 bundle id 意味着这三个权限要**重新勾一次**。

 3. 查词只有一条通道：**直连大模型 API**（v1.2.2 起网页版豆包模块已整体删除）
      面板 → 「查词引擎」→ 选服务商 → 填 API Key → 点「测试连通性」
    启动**不会**拉起任何浏览器，也没有登录步骤。

 4. 改完界面想用眼睛验收，不用真开窗口：
      QT_QPA_PLATFORM=offscreen python tools/render_panel.py          # 面板
      QT_QPA_PLATFORM=offscreen python tools/render_v122_preview.py   # 浮窗/提示词编辑器
      → tools/_render/*.png

重新打包后权限可能失效，重置命令：
      tccutil reset Accessibility ${BUNDLE_ID}
      tccutil reset ScreenCapture ${BUNDLE_ID}
      tccutil reset ListenEvent ${BUNDLE_ID}
============================================================
EOF
