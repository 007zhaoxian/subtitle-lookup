# 发布与维护说明

> 这份文档记录**怎么把这个项目发出去**，以及**哪些东西绝对不许提交**。
> 每次准备发新版前，照着 §2 走一遍即可。

---

## 1. 仓库边界：什么进仓库，什么不进

| 类别 | 处理 | 原因 |
| --- | --- | --- |
| 源码（`*.py`）、`subtitle_lookup.spec` | ✅ 入库 | 项目本体 |
| `build.sh` / `make_cert.sh` / `requirements.txt` | ✅ 入库 | 别人要能自己打包 |
| `README.md` / `docs/` / `LICENSE` / `assets/` | ✅ 入库 | 面向读者的文档与图标 |
| `tools/*.py` | ✅ 入库 | 回归测试与探针，是项目质量的一部分 |
| **`backup/`** | ❌ 排除 | **里面 `settings.json` 的历史快照含真实 API key** |
| **`START-HERE.md` / `HANDOFF.md`** | ❌ 排除 | 本机交接文档：含开发机绝对路径、账号、代理端口、密钥存放位置 |
| `dist*` / `build*` | ❌ 排除 | 构建产物（10G+），见 §4 |
| `settings.json`、`*.log`、`tools/_render/` | ❌ 排除 | 运行期数据与临时渲染图 |

### ⚠️ 两条用血换来的教训

**① 发布前必须扫一遍密钥。**
2026-09-15 首次开源前扫描时抓到：`tools/test_providers.py` 里把**真实的 DeepSeek
key 当成测试样本写死了**（用来验证 `mask_key` 的「前 6 后 4」）。
测试文件同样会进公开仓库，一样会泄露。现在那条已换成假 key，并留了注释警告。

**② `.gitignore` 的产物规则要跟目录命名对齐。**
本项目产物目录是 `dist0` / `dist1` / … **没有下划线**，
而旧的 `.gitignore` 只写了 `dist/` 和 `dist_*/` —— **完全挡不住**，
差点把 11G 产物提交上去。改打包目录命名习惯时，记得同步改 `.gitignore`。

### 发布前的自查命令

```bash
# 1) 确认暂存内容里没有产物 / backup / 内部文档
git diff --cached --name-only | grep -E '^(dist|build|backup)|\.app/|settings\.json$|\.log$' && echo "!! 有问题" || echo "✓ 干净"

# 2) 扫敏感词（只扫真正会入库的文件）
git diff --cached --name-only | xargs grep -lnE 'sk-[0-9a-f]{32}|gh[ops]_[A-Za-z0-9]{20,}|/Users/[a-z]+/' && echo "!! 需处理" || echo "✓ 干净"
```

---

## 2. 发一个新版本

```bash
# ① 改版本号（两处都要改，否则 .app 里的版本和代码不一致）
#    config.py:      VERSION = "x.y.z"
#    （.spec 从 config 读，不用改）

# ② 跑全量回归（离线、不花钱、不弹窗）
for f in tools/test_*.py; do python3 "$f" || exit 1; done

# ③ 打包（★ 换一对全新目录名，避开「批量删除保护」）
SKIP_VENV=1 PY=<你的 python> DIST=distN WORK=buildN bash build.sh

# ④ 冒烟：确认没有漏 import（漏了不会在打包时报错，只会在启动时崩）
./distN/SubtitleLookup.app/Contents/MacOS/SubtitleLookup --headless

# ⑤ 提交并推送
git add -A && git commit -m "vX.Y.Z：<一句话说清改了什么>"
git push origin main
```

### 2.1 网络：必须显式指定代理

国内网络下，`git` **不会**自动走系统代理，直接 push 会报
`CONNECT tunnel failed, response 502`。要显式带上：

```bash
git -c http.proxy=http://127.0.0.1:7897 push origin main
```

> 端口以「系统设置 → 网络 → 代理」里的为准（`scutil --proxy` 可以查）。
> 注意 `curl` 和 `git` 走的路径可能不同：`curl` 通不代表 `git` 通。
> 另外这类代理建立隧道可能要 6 秒以上，测试时别把超时设太短，否则会误判成「不通」。

### 2.2 打 Release 并挂 macOS 产物

```bash
# ★ 必须用 ditto，不能用 zip。.app 里有大量符号链接（Qt frameworks），
#   zip -r 会把符号链接展开成实体副本，包体积翻几倍、解开后还可能跑不起来。
ditto -c -k --sequesterRsrc --keepParent distN/SubtitleLookup.app \
      /tmp/release/SubtitleLookup-vX.Y.Z-macos-arm64.zip

# 产物文件名一律用**纯 ASCII**：
#   GitHub 会剥掉资产名里的非 ASCII 字符，中文前缀会直接消失，
#   之后用 API 改名也无效（GitHub 侧会规范化）。用中文名会导致下载链接对不上。
gh release create vX.Y.Z /tmp/release/SubtitleLookup-vX.Y.Z-macos-arm64.zip \
  --title "vX.Y.Z — <一句话>" --notes-file /tmp/release/notes.md --latest
```

打完**一定回读校验**（资产名和体积）：

```bash
gh release view vX.Y.Z --json assets --jq '.assets[] | "\(.name)  \(.size)"'

# 用匿名 curl 再验一次真的能下载（公开仓库才作数）
curl -sIL https://github.com/<owner>/<repo>/releases/download/vX.Y.Z/<asset> | grep -iE '^HTTP/|content-length'
```

> 打包后顺手确认**包内没有用户数据**（API key、观看记录、cookie 都不该被带进去）：
> ```bash
> find SubtitleLookup.app -maxdepth 4 \( -iname "*settings*.json" -o -iname "*.log" -o -iname "*cookie*" \)
> ```
> 期望输出为空。

---

## 3. 版本号与产物的对应关系

同一时刻只应该有**一份**产物是新版的：

- 源码版本号：`config.py` 里的 `VERSION`
- 已安装的 App：`/Applications/看剧查词-美剧.app`（启动日志会打印版本，可用来核对）
- Release 资产：`SubtitleLookup-vX.Y.Z-macos-arm64.zip`

启动日志是核对版本最快的方式：

```bash
tail -3 ~/Library/Logs/SubtitleLookup/app.log
# 期望看到：看剧查词-美剧 vX.Y.Z 启动 (menubar=qt, demo=False, 查词=直连 API)
```

---

## 4. 清理构建产物

每打一次包就会留下一对 `distN` / `buildN`（约 400MB + 17MB），累积很快 ——
2026-09-15 清理时，光 `dist*` 就有 **11G**，加上 `/Applications` 下的旧版备份
（21 个，4.4G），合计 **约 15.5G**。

清理原则：

- **一律移进废纸篓**（`mv ~/.Trash/`），**不用 `rm -rf`** —— 误删还能拖回来；
- 至少保留**最近一个** `.app.old-*` 作为回退点；
- 分批做，别一把梭。

```bash
# 看一眼有多大
du -sh dist[0-9]* build[0-9]* /Applications/看剧查词-美剧.app.old-* 2>/dev/null | tail -3

# 移进废纸篓（同卷内是瞬时的）
mv dist3 ~/.Trash/
```

> 注意：`~/.Trash` 受系统保护（TCC），**命令行读不到它的内容**
> （`ls` 会静默返回空、`du` 报 `Operation not permitted`）——
> 这不代表没删成功，去访达里看即可。
> 要**真正释放磁盘空间**，得在访达里点「清空废纸篓」。
