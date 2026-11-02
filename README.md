# 水果刀

红果短剧批量下载工具 · 苹果风桌面客户端

一款本地运行的红果短剧搜索 / 解析 / 批量下载工具，界面为自绘的 macOS 风格（无边框窗口、
交通灯按钮、SF 字体栈、层次化浅灰背景）。所有功能均在本地完成，不含任何远程会员 /
支付 / 心跳逻辑。

![应用界面](app_icon.png)

---

## 功能

| 功能 | 说明 |
|---|---|
| 关键词搜索 | 调用官方搜索接口，支持短剧与漫剧，按热度排序，支持翻页加载 |
| 链接解析 | 粘贴 `novelquickapp.com` 分享链接或 `series_id`，自动提取并抓取剧集列表 |
| 批量下载 | 选择清晰度（360p ~ 1080p）后整剧下载，或展开任务卡片选集下载 |
| 断点续传 | 进度文件驱动，重启后可跳过已完成集次；支持暂停 / 继续 |
| 逐集状态 | 展开卡片可见每集状态网格：灰=待下载、蓝=下载中、绿=完成、红=失败 |
| 封面抓取 | 解析成功自动下载封面图到剧集目录 |

### 关于分享链接

红果的分享链接有两种形态，都支持：

| 形态 | 例子 | 处理方式 |
|---|---|---|
| 短链 | `novelquickapp.com/s/7_9cWlOsids/` | 短码里没有数字，必须先请求一次让服务端 302 跳到真实分享页，剧集 ID 在跳转后的 query 里 |
| 长链 | 带 `series_id=` 参数 | 直接从参数取 |

短链跳转后页面里同时有 `video_id`（整部剧的 series_id）和 `vid`（某一集的 ID），
**必须优先取 `video_id`**，取错只会下到一集。

参数优先级：`series_id` > `video_id` > `book_id` > `vid`。

任务卡片支持的操作：开始下载 / 暂停 / 继续 / 重试解析 / 打开网页 / 打开文件夹 / 移除。

---

## 快速开始

### 方式一：直接使用打包好的桌面版（推荐给普通用户）

1. 解压发行包（**必须先解压**，不能直接在压缩包里双击）
2. 双击 **`安装水果刀.exe`** —— 会在桌面创建带 logo 的「水果刀」快捷方式，
   同时在开始菜单建「水果刀」程序组
3. 双击桌面图标即可使用

安装器不复制任何程序文件、不写系统目录，属于**绿色便携版**：
快捷方式指向程序所在位置，把整个文件夹挪到哪都能用。
卸载只需运行 `安装水果刀.exe --uninstall`（或删掉开始菜单里的「卸载水果刀」）。

> 桌面上若已存在同名**文件夹**，快捷方式会自动命名为「水果刀 启动」以免混淆。

### 方式二：从源码运行（开发者）

#### 1. 环境要求

- Windows 10/11 x64
- Python 3.12（签名模块按 3.12 编写；3.13 也可运行）

#### 2. 安装依赖

```bash
git clone <this-repo>
cd <this-repo>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 3. 准备 ffmpeg

下载 ffmpeg 并放到 `ffmpeg_bin/ffmpeg-N-*-win64-gpl-shared/bin/`（目录名可任意，
程序按 `ffmpeg_bin/*/bin/ffmpeg.exe` 或 `ffmpeg_bin/bin/ffmpeg.exe` 查找）。
仓库不含该二进制（体积过大），可从 [ffmpeg.org](https://ffmpeg.org/download.html) 获取。

#### 4. 运行

```bash
python run.py
```

---

## 打包

```bash
pip install pyinstaller
pyinstaller --noconfirm hongguo.spec
```

产物在 `dist/水果刀/水果刀.exe`。注意：**整个 `dist/水果刀` 目录都要保留**，
`_internal` 内的 ffmpeg 与签名模块是运行期依赖，不能只拷 exe。

---

## 命令行参数

| 参数 | 用途 |
|---|---|
| 无参数 | 正常启动图形界面 |
| `--selftest` | 自检：搜索 → 解析 → 真实下载一集，结果写入 `selftest.log` |
| `--guitest` | 走完整 GUI 流程（填链接 → 解析 → 点击下载），结果写入 `guitest.log` |
| `--uishot <path>` | 启动真实窗口并把界面截图存到 `<path>` |
| `<script.py> <cfg>` | 工作进程模式：执行下载脚本（由 GUI 内部调用，勿手动使用） |

排查问题时优先跑 `--selftest`，它会真实下载一集并给出完整链路日志。

---

## 项目结构

```
run.py                 启动入口（含工作进程分发 / 自检参数）
app/
  main_window.py       主界面（2022 行）：窗口、搜索、任务卡片、下载调度
  batch_downloader.py  下载子进程：签名 → 解密 URL → ffmpeg 解密转封装
  search_index.py      搜索接口封装
  config.py            应用名 / 版本
hg_src/
  1.py                 视频解析端点（Python 版）
  liushen/flurl/       请求签名模块（xgorgon / helios / medusa / sign_proto）
install.py             桌面安装器（快捷方式 / 开始菜单 / 卸载入口 / 右键菜单）
install.spec           安装器独立打包配置
hongguo.spec           PyInstaller 打包配置
```

---

## 实现说明

### 无边框窗口的缩放与最大化

窗口使用 `Qt.FramelessWindowHint`，缩放 / 最大化 / 拖动全部自行实现：

- `_edge_at(gpos)` 判定鼠标是否在 8 个边缘方向，**入参必须是全局坐标**
  （`QMouseEvent.globalPos()`）。若误传局部坐标，右侧与下侧边缘将永远无法命中。
- `_resize_geo` 是「按下鼠标那一刻」的基准矩形，**拖动全程不再更新**。
  若每帧刷新基准、而位移又是相对按下点的累积量，窗口会越拖越快、失控漂移。
- 最小尺寸 940×620；最大化铺满工作区但不覆盖任务栏；双击标题栏可切换。

### 打包后的子进程

PyInstaller 打包后 `sys.executable` 指向 GUI 程序自身，直接
`Popen([exe, "batch_downloader.py", cfg])` 不会执行脚本、而是又开一个界面。
`run.py` 因此识别「第一个参数是 `.py`」的情况，用 `runpy` 在当前进程内执行下载脚本。
若在冻结产物上验证下载，请用 `--selftest` / `--guitest`，不要只测源码模式。

### 任务卡片的鼠标事件

`TaskCard.mousePressEvent` 不要调用 `super().mousePressEvent(e)` ——
那会把按下事件重新派发给子控件，导致「开始下载」按钮被连点两次
（第一次启动、第二次立刻暂停），表现为点击后状态卡在「暂停」。

### 下载任务的进度文件

`_on_download_all`（手动点「开始下载」走的路径）必须在函数内自行拼出
`progress_file`，不能复用 `task._pf` —— 该字段在此刻仍为空串，
会让子进程 `open('', 'w')` 抛 `FileNotFoundError`。

### 创建桌面快捷方式（install.py）

沙箱/受限环境里 `WScript.Shell` 的 COM 实例化会被安全策略拦截，
所以安装器用 `ctypes` 直接调 `IShellLink` COM 接口。**这里有三个极易踩的坑**，
症状都是「lnk 文件从 280 字节膨胀到 40KB，第三个链接 Save 直接失败」：

1. **vtable 索引必须实测，不能凭记忆。** `IShellLinkW` 的方法顺序是
   `3 GetPath | 5 SetIDList | 7 SetDescription | 9 SetWorkingDirectory |
   11 SetArguments | 17 SetIconLocation | 20 SetPath`。
   把 `5` 当成 `SetDescription` 用是错的 —— 它实际是 `SetIDList(LPCIDLIST)`，
   拿字符串去调会把垃圾数据写进 lnk 的 ExtraData。
   排查手法：每个索引单独起一个进程调用，避免一个崩溃影响其他。

2. **`IPersistFile::Save` 的 BOOL 参数必须用 `wintypes.BOOL`（4 字节）**，
   用 `ctypes.c_bool`（1 字节）会让参数栈错位，lnk 头里的 FileSize 被写成 0。

3. **`CoInitializeEx` 返回 `S_FALSE(1)` 表示本线程已初始化过 COM**，
   这时不能再 `CoUninitialize`，否则 COM 引用计数错乱。
   同时要显式 `Release` COM 对象，否则残留状态会串到下一个 lnk。

图标方案：把 `IconLocation` 指向 **exe 自身** —— PyInstaller 已把 ico 嵌进
exe 资源（`ExtractIconExW(exe, -1, ...)` 可取到图标组），这样桌面一定显示
专属 logo，且不依赖外部 ico 文件路径。

判定 lnk 好坏**不要**用 `SHGetFileInfo` 判图标（对系统 dll 也返回默认图标，
是纯假阴性），用 `ShellExecuteW(None, 'open', lnk, ...)` 返回值 > 32 才算数。

### 窗口图标

`main()` 必须把 `icon_path` 传给 `MainWindow`，否则任务栏/标题栏/Alt-Tab
全是默认图标。优先用多尺寸 `app_icon.ico`（16/24/32/48/64/128/256），
小尺寸下比单张 PNG 清晰得多。注意 `app_icon.ico` 要写进 `hongguo.spec` 的
`datas`，否则打包后找不到。

---

## 环境变量（可选）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DUANJU_DEVICE_ID` | `11223344556677889900` | 设备号 |
| `DUANJU_INSTALL_ID` | `99887766554433221100` | 安装号 |

---

## 免责声明

本项目仅供学习交流与个人使用。使用者需自行确保其行为符合所在地法律法规、
内容平台服务条款及相关版权规定。请勿用于批量抓取、传播或商业分发受版权保护的内容，
下载的内容请仅供个人欣赏。

---

## License

Private / All rights reserved.
