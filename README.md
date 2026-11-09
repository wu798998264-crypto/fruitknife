# 水果刀 2.0

红果短剧批量下载工具（Windows 桌面版，绿色免安装）。

**下载**：[Releases · v2.0.0](https://github.com/wu798998264-crypto/fruitknife/releases/tag/v2.0.0) → `fruitknife-2.0.0.zip`，解压运行 `水果刀.exe`。

## 2.0 内核

Electron 28 + React 桌面应用（基于成熟的红果下载内核改造为水果刀品牌）：

- **取流**：`multi_video_model`，device_level=4 直取 bytevc1（标准 HEVC/hvc1），
  content_type 自动回退（官方剧 1004 / 用户投稿 1001 / 1003 / 1002）
- **解密**：自研 CENC-AES-CTR（subsample 感知 + moov 重建 encv→hvc1 + stco/co64 修正），
  输出标准 HEVC MP4，无水印，全程不依赖 ffmpeg 解密
- **搜索**：`search/suggest/v1`（`bookapi/search/tab` 已被服务端风控），
  缺封面/集数的条目用 `multi_video_detail` 并发补全
- **界面**：无广告、无启动弹窗

## 仓库结构

```
electron-build/            2.0 构建与改造
├── README.md              改造与打包要点（asar 打包坑、ELECTRON_RUN_AS_NODE、封面修复）
├── _safe_patch.py         品牌替换（对压缩 React bundle 只做字符串字面量替换）
├── _patch_search.py       searchSeries 重写为 suggest 方案
├── _seticon.py            exe PE 资源写入水果刀图标（UpdateResourceW）
└── patches/               修复后的 main.js / hongguo.js / package.json
```

复现方式：解包参考应用 app.asar（`@electron/asar extract`）→ 应用 patches 与脚本 →
`@electron/asar pack`（`--unpack-dir "{node_modules/@ffmpeg-installer,node_modules/libheif-js}"`）→
部署时 `app.asar` 与 `app.asar.unpacked` 成对同步。

## 历史

1.x（PyQt 版）源码见 git 历史（提交 ≤ 5e3ec3c），2.0 起不再维护。
