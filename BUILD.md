# 构建指南

本文档面向要自己打包一份水果刀的人。普通用户请直接用发行包里的
`安装水果刀.exe`，不需要看这里。

## 环境

- Windows 10/11 x64
- Python 3.12（`hg_src/liushen` 的签名模块按 3.12 编写；3.13 也能跑）

```bash
git clone <this-repo>
cd <this-repo>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

## 1. 准备 ffmpeg

下载 ffmpeg（GPL shared 版本），解压后把 **`bin` 目录**放到：

```
ffmpeg_bin/ffmpeg-N-125907-ga7e72069f1-win64-gpl-shared/bin/
```

即最终应存在 `ffmpeg_bin/*/bin/ffmpeg.exe`。
程序查找顺序是 `ffmpeg_bin/*/bin/ffmpeg.exe` → `ffmpeg_bin/bin/ffmpeg.exe`。

⚠️ 仓库不含 ffmpeg 二进制（体积过大），需自行从
[ffmpeg.org](https://ffmpeg.org/download.html) 获取。

`ffprobe.exe` 必须保留 —— `batch_downloader.py` 会调用它做视频验证。
`ffplay.exe` 用不到，打包后可以删掉（省 17MB）。

## 2. 打包主程序

```bash
pyinstaller --noconfirm hongguo.spec
```

产物：`dist/水果刀/`

**整个 `dist/水果刀` 目录都要保留**，`_internal` 内的 ffmpeg 与
`app/` 是运行期依赖，只拷 exe 会无法运行。

### 瘦身（可选）

打包后可以删掉这些无用文件，能从 471MB 降到 267MB：

```bash
cd dist/水果刀
rm -f _internal/av*.dll _internal/swscale-10.dll   # PyInstaller 误收集的 ffmpeg 动态库，源码零引用
rm -f _internal/ffmpeg_bin/*/bin/ffplay.exe          # 播放器，本工具从不调用
```

删完**必须实测下载**确认无损：

```bash
cd dist/水果刀
./水果刀.exe --selftest
```

## 3. 打包安装器

```bash
pyinstaller --noconfirm install.spec
```

产物：`dist_inst/安装水果刀.exe`（约 9MB，单文件）。

把它和主程序放在一起：

```
水果刀/
├─ 水果刀.exe          ← 主程序
├─ 安装水果刀.exe      ← 安装器
├─ app_icon.ico
├─ 使用说明.txt
└─ _internal/
```

安装器不依赖 Python，接收方双击即可安装桌面快捷方式。

## 4. 打发行包

```bash
cd ..
python -c "
import os, zipfile
src = r'D:/path/to/dist/水果刀'
out = r'D:/path/to/水果刀桌面版.zip'
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for root,dirs,files in os.walk(src):
        for f in files:
            fp = os.path.join(root,f)
            z.write(fp, os.path.join('水果刀', os.path.relpath(fp,src)))
print('done')
"
```

⚠️ 267MB 的目录压缩要 3 分钟左右，**务必放后台跑**，
前台会被超时打断产出残包（曾生成过 81MB 的不完整 zip）。
打完用 `z.testzip()` 校验 CRC。

## 5. 发布前验证清单

- [ ] `./水果刀.exe --selftest` → 真实下载一集成功（看 `selftest.log`）
- [ ] `./水果刀.exe --guitest` → 走完整 GUI 流程（点按钮下载）成功
- [ ] 把 zip 解压到**全新目录**，在那里跑 `安装水果刀.exe` → 快捷方式创建成功
- [ ] `安装水果刀.exe --uninstall` → 快捷方式与注册表清理干净
- [ ] 桌面双击快捷方式 → 程序能启动、窗口图标正确
- [ ] 拖窗口右边缘/下边缘 → 能正常缩放

## 已知坑速查

| 现象 | 原因 |
|---|---|
| 任务栏没图标 | `main()` 没把 `icon_path` 传给 `MainWindow`；且 ico 要在 spec 的 `datas` 里 |
| 点「开始下载」后卡在「暂停」 | `TaskCard.mousePressEvent` 不要调 `super()`，会连点按钮两次 |
| 点「开始下载」报 `FileNotFoundError: ''` | `_on_download_all` 要自己拼 `progress_file`，不能复用 `task._pf` |
| 右边缘/下边缘拖不动 | `_edge_at()` 入参必须是**全局**坐标 |
| 快捷方式 lnk 变成 40KB | COM vtable 索引写错，见 README「创建桌面快捷方式」 |
| 打包后下载子进程不执行 | 冻结产物里 `sys.executable` 指向 GUI 自己，靠 `run.py` 识别 `.py` 参数分发 |
| 粘贴分享链接报「无法提取 series_id」 | 短链是字母短码，必须跟 302 跳转后从 query 取 `video_id`；详见 README「关于分享链接」 |
| 分享链接只下到一集 | 页面里 `vid` 是单集 ID、`video_id` 才是 series_id，取错了 |
