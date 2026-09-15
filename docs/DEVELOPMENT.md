# 开发文档

面向想要改代码、自行打包或移植到其它平台的开发者。普通用户请看 [README](../README.md)。

实现过程中踩过的坑单独放在 [踩坑实录 NOTES.md](NOTES.md)，里面有很多反直觉、
用常规调试手段会误判的内容，改动核心模块前建议先读一遍。

> **本文对应 v1.2.3。** v1.2.2 删掉了整个「网页版 AI」通道（无头 Chrome + 扫码登录 +
> 抓页面回答），所以本文里**不会**出现浏览器、登录、user-data-dir 之类的内容 ——
> 那些是旧项目的架构，别照着做。

---

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | macOS 12+（开发在 macOS 26 Tahoe / Apple Silicon 上验证） |
| Python | 3.10 ~ 3.13（打包用 3.13） |
| 播放器 | **IINA**（通过 mpv IPC 拿播放状态；不装也能跑，但会退化，见 §3.2） |
| 网络 | 需要能访问所选大模型服务商（默认 DeepSeek）的 API |
| 权限 | 辅助功能、屏幕录制、输入监控（详见 §6.4） |
| 额外 | 打包需要 `pyinstaller`；签名建议跑一次 `make_cert.sh` |

---

## 2. 从源码跑起来

```bash
git clone <本仓库地址>
cd subtitle-lookup

python3 -m venv .venv && source .venv/bin/activate
# 国内网络建议加：-i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt

python3 main.py --diagnose    # 环境自检：依赖 / 权限 / IINA 通道 / API 配置
python3 main.py               # 启动
```

> **注意**：从终端跑的时候，权限要授予**终端 App**（Terminal / iTerm），而不是本程序 ——
> macOS 的 TCC 认的是「谁在监听」，此时进程归属终端。

首次使用还要做两件事：

1. **面板 → 填 API key**（默认服务商 DeepSeek）。key 存在
   `~/Library/Application Support/SubtitleLookup/settings.json`，**不会**进仓库。
2. **给 IINA 开 mpv IPC**（让程序能精确知道「在播还是暂停」）。
   面板里有一键写入按钮（`iina_setup.py`），它会往 IINA 的 `userOptions` 里
   合并 `input-ipc-server=/tmp/iina.sock`，**不会覆盖你已有的项**。

### 环境变量速查

不用改代码就能调的行为：

| 变量 | 作用 |
| --- | --- |
| `DL_TRIGGER_KEY` | 触发键，默认 `space`。可填 `enter` / `n` 等单键名 |
| `DL_CLOSE_KEY` | 关窗键，默认 `space` |
| `DL_MENUBAR` | 菜单栏后端，`qt`（默认）/ `rumps` |
| `DL_IINA_SOCKET` | IINA 的 mpv IPC socket 路径，默认 `/tmp/iina.sock` |
| `DL_VIEWLOG_DIR` | 观看记录目录。**优先级最高**，测试用它把自己的写入引到临时目录 |
| `DL_SETTINGS_DIR` | 设置文件目录，测试用来隔离真实配置 |
| `DL_VIBRANCY` | `1` 打开毛玻璃。**不推荐**，见 NOTES 里的「悬浮窗空白」 |
| `DL_SPACE_TAP` / `DL_SPACE_FALLBACK_SEC` | 空格拦截层开关与兜底等待时长 |
| `DL_PLAYER_CONTROL` | 播放控制方式（调试用） |
| `DL_DEBUG` | 调试输出 |
| `DL_LOG_DIR` | 日志目录，默认 `~/Library/Logs/SubtitleLookup` |
| `DL_QUIT_WATCHDOG_SEC` | 退出看门狗超时 |

---

## 3. 架构

### 3.1 模块职责

| 文件 | 职责 |
| --- | --- |
| `main.py` | 入口。装配菜单栏 / 监听器 / Qt 运行时；`--diagnose` 自检 |
| `config.py` | **所有可调参数**集中在这里（触发键、Prompt、外观、超时） |
| `app.py` | 核心状态机，串起「截图 → 问模型 → 展示」的完整流程 |
| `api_client.py` | 直连大模型 API（纯 `urllib`，连 `requests` 都不需要） |
| `keytap.py` | CGEventTap 空格拦截层。**有条件吞键**（见 §3.3） |
| `hotkey.py` | 按键名解析与回退路径；触发键交给 `keytap` 后它不再重复监听 |
| `mouse.py` | 全局鼠标监听：点浮窗外面自动关窗 |
| `screenshot.py` | 截图 + 按范围裁剪 + 等比缩放 |
| `region_picker.py` | 自定义截图范围的全屏框选界面 |
| `player.py` | **播放器通道**：IINA mpv IPC → 系统媒体键兜底；状态判定见 §3.2 |
| `iina_setup.py` | 往 IINA 的 `userOptions` 里合并 IPC 配置（可撤销） |
| `viewlog.py` | 观看记录：命名、分本、编号、同名复用 |
| `reply_clean.py` | 清洗「好的，截图里……」这类客套前缀与 Markdown 记号 |
| `overlay.py` | 无边框置顶悬浮窗 + Qt 运行时宿主 |
| `panel.py` | 控制面板（设置 / 权限 / 手动查词 / 观看记录） |
| `prompt_edit.py` | 提示词编辑界面（所有发给 AI 的文本都可改） |
| `menubar.py` | 菜单栏：`QSystemTrayIcon`（默认）/ rumps（参考实现） |
| `icon.py` | 运行时用 QPainter 画状态栏图标，按系统明暗自动切 |
| `settings.py` | 用户设置的持久化读写 |
| `utils.py` | 权限检测与申请、日志 |
| `subtitle_lookup.spec` | PyInstaller 打包配置 |
| `build.sh` / `make_cert.sh` | 一键打包 / 生成本地稳定签名证书 |
| `tools/` | 诊断探针与回归测试脚本 |

### 3.2 播放器通道（最容易被误判的一块）

程序要知道「视频在播还是停着」，才能正确决定按空格时该做什么。
这里有三条通道，**优先级从高到低**：

1. **IINA mpv IPC**（精确）—— socket 通、且能问到状态时用它。
2. **系统电源断言**（近似）—— 读 `pmset -g assertions` 里 IINA 的
   `playback is in progress`。不依赖 IPC、不需要权限，用来兜底。
3. **系统媒体键**（无状态）—— 两条都拿不到时才走，此时「在播/暂停」是不知道的。

⚠️ **IINA 的多个 mpv 内核会抢同一个 socket 路径**：后启动的内核会把路径
unlink 再重新 bind，于是你 `connect()` 上的可能是**空转的那个内核**
（问它什么都不知道），而真正在播的那个反而连不上。

现象是「明明在放一部有名字的剧，却记成『未知剧名』」，且 IPC 说空转的同时
`pmset` 里明明写着 `"IINA playback is in progress"`。

所以拿剧名**不要**只信 IPC —— `viewlog` 走的是「IINA 可见窗口的标题」
（就是磁盘文件名），这条通道不受内核抢占影响。

### 3.3 空格拦截：三态而非两态

空格是触发键，但它同时也是播放器的暂停键，所以必须**有条件吞键**：

| 状态 | 按空格的行为 | 是否吞掉这一下 |
| --- | --- | --- |
| `SHOWING`（浮窗正在显示） | 关窗并继续播 | **吞** |
| `BUSY`（正在识别） | 什么都不做 | 不吞（给播放器） |
| `ASKING`（正在等追问回答） | 什么都不做 | 不吞（给播放器） |
| 前台是已知播放器且没浮窗 | 触发查词 | **吞** |

历史 bug：`BUSY`/`ASKING` 曾经**既拦截又什么都不做**，表现出来就是
「刚进播放器时空格失灵」。别再改回去。

带 `Cmd` / `Ctrl` / `Option` 的空格（Spotlight、切换输入法）一律不抢。

### 3.4 线程模型

**macOS 硬性要求：`QApplication` 必须在主线程创建**，否则直接抛 `NSException` 崩溃。
这一点决定了整个架构：

```
┌─ 主线程（Qt 独占）────────────────────────────┐
│  QApplication / 菜单栏 / 悬浮窗 / 控制面板      │
│  跨线程操作一律走命令队列 + QTimer 泵入这里      │
└───────────────────────────────────────────────┘
┌─ CGEventTap 线程 ─────────────────────────────┐
│  keytap：判定这一下空格吞不吞。只读状态、只投递  │
│  命令，绝不直接碰 QWidget                      │
└───────────────────────────────────────────────┘
┌─ API 工作线程 ────────────────────────────────┐
│  发 HTTP 请求、流式收结果，完成后回调主线程      │
└───────────────────────────────────────────────┘
```

⚠️ 因此**不要用 rumps 做菜单栏** —— 它也要占主线程，和 Qt 无法共存
（实测崩溃堆栈落在 `QApplicationPrivate::init`）。`menubar.py` 里的 rumps 后端
仅作参考保留，`DL_MENUBAR` 默认是 `qt`。

---

## 4. 关键设计决策

这些是改代码时容易不小心破坏的东西，改动前请三思：

**① 悬浮窗绝不调用 `activateWindow()`**
悬浮窗带 `WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`，
播放器的键盘焦点全程不受影响。手贱加一句 `activateWindow()` 就会把焦点抢走。
置前要用 `overlay._bring_front_without_activating()`（`orderFrontRegardless`）。

**② 但输入框要能打字 → 必须临时成为 key window**
这里有个矛盾：`WindowDoesNotAcceptFocus` 会导致输入框拿不到键盘。
用户点输入框时，需要先把 AppKit 的 `becomesKeyOnlyIfNeeded` 关掉，
再 `activateApp` + `makeKeyAndOrderFront`，用完即恢复。详见 `overlay._activate_self_window`。
⚠️ 碰 AppKit 原生对象前**必须**先做平台可用性守卫（`_can_use_appkit()`），
否则 headless/offscreen 环境下 `winId()` 不是合法 NSView，直接 SIGSEGV。

**③ 毛玻璃（NSVisualEffectView）必须关**
挂上它之后 Qt 画的内容**不再被合成到屏幕**，浮窗一片空白。
而 `widget.grab()` 里文字是完好的，极易误判成「渲染正常」。见 NOTES §「悬浮窗空白」。

**④ 入口要有三层保底**
菜单栏图标可能被 Hidden Bar / Ice / Bartender 之类工具折叠到屏幕外。
所以保底入口是：控制面板（启动即弹）+ Dock 图标（点它叫回面板）+ 菜单栏图标。
**不要把菜单栏图标做成唯一入口。**

**⑤ 观看记录的文件名只许来自「可见窗口标题」**
`viewlog._media_filename()` 是多通道的，但优先级里排第一的是 IINA 可见窗口标题。
隐私上加了两道约束：只读 `OptionOnScreenOnly` 的窗口、**不遍历隐藏窗口、不把标题写进日志**。

**⑥ 同一天同一部剧只写一个文件**
开新本前先扫当天目录，用 `same_show()`（包含匹配）判断是否同一部剧，
命中就续写、不另开。编号只在本子内递增，`renumber()` 负责修脏序号。

---

## 5. 自定义配置

改 `config.py` 就够了，常用的：

| 常量 | 说明 |
| --- | --- |
| `TRIGGER_KEY` | 触发键，默认 `space`。改它界面文案会自动跟着变（`TRIGGER_LABEL`） |
| `CAPTURE_MODES` / `CAPTURE_BANDS` | 截图范围预设（字幕区 / 整屏 / 上下半屏） |
| `DEFAULT_CAPTURE_RECT` | 自定义区域默认值，格式 `[x, y, w, h]` **屏幕比例** 0~1 |
| `PROVIDERS` / `DEFAULT_PROVIDER` | 服务商与默认项（默认 `deepseek`） |
| `IINA_SOCKET_PATH` | mpv IPC 路径，默认 `/tmp/iina.sock` |
| `OVERLAY_*` | 浮窗宽度、最小宽高、字号、透明度、自动关闭秒数 |
| `MAX_IMAGE_WIDTH` | 截图上传前的缩放宽度，越小越快 |
| `ASK_TIMEOUT_SEC` | 等待模型回答的超时 |

> 所有 Prompt 在**运行时**也能改：菜单栏 → 「编辑提示词」，改动持久化到
> `~/Library/Application Support/SubtitleLookup/settings.json`。

---

## 6. 打包与签名

```bash
bash make_cert.sh   # 只需一次：生成本地稳定签名证书
bash build.sh       # 架构自检 → 依赖自检 → PyInstaller → 签名 → 打印权限指引
```

产物在 `dist/SubtitleLookup.app`（约 93 MB）。

**重打包时一定要换个新目录名**（`DIST=dist2 WORK=build2 bash build.sh`）：
`build.sh` 里的 `rm -rf build dist` 会触发「批量删除保护」（几百上千个文件），
PyInstaller 的 COLLECT 阶段自己也会 `Removing dir dist/<名字>`，同样被拦。
换目录是最省事的解法。

### 6.1 为什么必须有 `make_cert.sh`

这一步很容易被跳过，但跳过后会非常难受：

- **ad-hoc 签名**（`codesign -s -`）的「指定要求」是纯 **cdhash** —— 重新打包一次
  cdhash 就变，系统设置里那条授权开关会变成对不上号的僵尸记录：
  **开关显示是开的，但 `AXIsProcessTrusted()` 永远返回 False**，App 每次启动都弹授权框。
- 用**固定自签名证书**后，指定要求变成
  `identifier "com.zhaowentian.subtitlelookup" and certificate leaf = H"…"`，
  跟 cdhash 无关，**重打包多少次权限都保持有效**。

> 换 Mac 之后证书要在新机器上重建，权限也需要重新授予一次 —— 这是必然的。

### 6.2 `.spec` 里的关键项

| 项 | 为什么 |
| --- | --- |
| `target_arch="arm64"` | 保证产出 arm64 原生二进制 |
| `console=False` | 不弹终端窗口 |
| `LSUIElement=False` | **故意的**。设 True 会让 App 从启动台/Spotlight 消失；程序启动后自己切成 Accessory 策略收起 Dock 图标，于是「找得到 + 不占 Dock」兼得 |
| `excludes` 剔除 QtWebEngine / numpy / tkinter 等 | 体积砍一半以上 |
| `upx=False` | macOS 上 UPX 会破坏签名，必须关 |
| `hiddenimports` 含 `region_picker` | ⚠️ 动态导入的模块 PyInstaller 追不到，不加就是不报错地失效 |
| `datas` 为空 | v1.2.2 起没有任何额外数据文件（playwright 已移除） |

### 6.3 打包后的自检

`build.sh` 最后会打印签名信息。**装到 `/Applications` 之前**建议先跑一次冒烟：

```bash
timeout 25 "./dist/SubtitleLookup.app/Contents/MacOS/SubtitleLookup" --headless
# 期望看到：看剧查词-美剧 vX.Y.Z 启动 …… 无 ImportError
```

用 `--headless` 是因为**漏装一个模块时，打包不会报错、装完才会一启动就崩**。

### 6.4 权限处理

打包后的 App 是被 TCC 监管的独立程序，需要三项权限（详见 README）：

1. 先把 App **拷贝到 `/Applications`**（TCC 按路径记权限）。
2. **授权后必须完全退出 App 再重开** —— TCC 只在进程启动时读取。

权限失效需要重置时（把 bundle id 换成你的）：

```bash
tccutil reset Accessibility com.zhaowentian.subtitlelookup
tccutil reset ScreenCapture  com.zhaowentian.subtitlelookup
tccutil reset ListenEvent    com.zhaowentian.subtitlelookup
```

程序本身也会在启动时主动调 `AXIsProcessTrustedWithOptions({prompt: True})` 触发系统授权框，
否则用户在系统设置里根本不知道该授权哪个 App。

---

## 7. 测试与诊断

`tools/` 下分两类：**离线回归**（不需要真屏幕）和**真机探针**（需要真实屏幕）。
当前有 26 个 `test_*.py` 与 14 个 `probe_*.py`。

```bash
# 离线回归：整体跑一遍（离线、不花钱、不需要登录）
for f in tools/test_*.py; do python3 "$f" || exit 1; done

# 常用的几个
python3 tools/test_player.py            # 播放器通道（IPC > 电源断言 > 媒体键）
python3 tools/test_player_sidechannel.py # 窗口标题/电源断言两条旁路
python3 tools/test_space_state.py       # 空格三态状态机 + 事件掩码
python3 tools/test_viewlog.py           # 观看记录：命名 / 分本 / 编号
python3 tools/test_reply_clean.py       # 回答清洗（自由格式不上标签）

# 真机探针（必须有屏幕）
python3 tools/probe_window_layer.py     # 读真实窗口层级，确认浮窗浮在状态栏层
python3 tools/probe_overlay_render.py   # 抓真实合成画面，看浮窗有没有真的显示
```

### 7.1 测试纪律（踩过坑，请遵守）

- **会弹真窗口的测试默认关闭**。`test_focus_no_steal.py` 的实测段要建真窗口验证
  焦点语义，必须显式 `DL_TEST_FOCUS_LIVE=1` 才开；即便开启，窗口也是
  **1×1 全透明贴屏幕角**，肉眼看不见。
  ⚠️ 别用 `move(-4000,-4000)` 挪到屏幕外 —— Qt 会警告
  `outside any known screen, using primary screen`，可能被挪回主屏，等于没躲。
- **其余测试一律 `QT_QPA_PLATFORM=offscreen`**。当前只有上面那一个跑 cocoa。
- **测试不许依赖开发机真实状态**。例如 `test_player.py` 里「降级到媒体键」那一段，
  必须桩掉 `player.iina_playback_active`，否则开发机恰好在放片就会红。
- **不许往用户桌面写东西**。测试用 `DL_VIEWLOG_DIR` / `DL_SETTINGS_DIR`
  把自己的写入引到临时目录。

> 这些纪律的由来：曾经有一个测试会在屏幕上反复闪黑框并抢走前台焦点，
> 而当时用户正在看视频。

⚠️ **写涉及 AppKit 原生对象的代码时，务必在 offscreen 环境跑一遍** ——
之前就是因为缺少平台守卫直接 SIGSEGV（退出码 139），只有自动化测试能立刻暴露。

---

## 8. 移植到 Intel Mac / 其它平台

- **Intel Mac**：改 `.spec` 的 `target_arch="x86_64"`，`build.sh` 里去掉 `arch -arm64` 前缀。
- **Windows / Linux**：截图 → 托给 `mss` 已有 fallback；难点在悬浮窗、全局快捷键
  和播放器通道，需要把 `overlay.py` / `keytap.py` / `player.py` / `menubar.py`
  里的 macOS 专有部分（AppKit 调用、CGEventTap、TCC 权限、`pmset` 断言）替换掉。
  `screenshot.py` 的 `crop_box()` 是**纯函数**，可以直接拿来复用。

---

## 9. 贡献

- 改动核心模块（`overlay` / `player` / `keytap` / `app` / `viewlog`）后，
  请把 §7 的离线回归全跑一遍再提 PR。
- 遇到奇怪的现象，先翻 [NOTES.md](NOTES.md) 看有没有同源的根因。
- 提 Issue 时附上 `~/Library/Logs/SubtitleLookup/app.log` 的相关段落，定位会快很多。
  ⚠️ 贴日志前先自己过一遍 —— 里面可能含你看的剧名。
