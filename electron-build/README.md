# 水果刀 Electron 版（v1.0.7）构建与改造说明

以 hongguo-downloader（Electron 28，用户提供的参考软件）为主体改造为水果刀。

## 改造内容

### 1. 品牌替换（_safe_patch.py，从原版 asar 恢复后做安全替换）
- main.js: APP_TITLE → 水果刀；窗口标题 → 水果刀 — 红果短剧批量下载
- 下载目录去掉「红果短剧」一级文件夹
- 渲染层：仅纯字符串字面量替换（红果短剧下载器/小菜鸟/111330.com）
- ⚠ 教训：不能对压缩后的 React bundle 做 JSX 结构级文本替换（曾把
  children 数组的字符串边界弄坏导致白屏），只能替换字符串字面量
- package.json: name=fruitknife version=1.0.7

### 2. 广告位移除（CSS 隐藏，index.html 注入）
`.top-official-banner`（顶部横幅）、`.hongguo-site-notice`（防迷路横幅）、
`.sidebar-website-card`（侧栏推广卡片）→ display:none

### 3. exe 图标（_seticon.py）
ctypes UpdateResourceW 把水果刀 ico（7 个尺寸）写入 PE 资源
（RT_ICON 1..7 + RT_GROUP_ICON id=1）

### 4. 搜索风控修复（_patch_search.py）
2026-11-09 起 `bookapi/search/tab` 全端点被服务端风控（HTTP 200 空响应体，
PyQt 版与参考实现同时失效）。但 `search/suggest/v1` 一直可用，且其
query_result_v2 直接携带 series_id(keyword)/剧名(name)/封面(pic_url)/
热度(sug_abstract)/视频元数据(video_data)。
→ searchSeries 重写为基于 suggest 实现，模糊搜索与精确剧名都能出结果。

### 已验证可用的完整链路（node 22 实测）
- searchSeries("困兽之战") → 8 条（带 sid/热度/封面）
- fetchEpisodeList(sid) → 16 集（multi_video_detail/v1）
- fetchPlayUrlSingle(vid) → bytevc1（device_level=4）
- 下载 26.98MB → deriveKey → decryptMp4File → 26.79MB hevc/hvc1

### asar 打包要点
- 用 @electron/asar pack，`--unpack-dir "{node_modules/@ffmpeg-installer,node_modules/libheif-js}"`
- **部署时 app.asar 与 app.asar.unpacked 必须成对同步**（asar 头记录了
  unpacked 文件的 integrity，缺文件会启动报 ENOENT）
- bash 的 cp -r 在该环境不可靠，一律用 Python shutil
- **ELECTRON_RUN_AS_NODE=1** 会被 WorkBuddy 的 shell 注入，从脚本启动
  Electron 前必须 unset，否则应用以 Node 模式静默运行（无窗口）
