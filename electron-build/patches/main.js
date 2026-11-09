/**
 * main.js - 红果短剧下载器（独立版）主进程
 *
 * 功能：
 *   1. 红果短剧解析（分享链接 / series_id -> 全剧集列表）
 *   2. 批量提交下载任务到下载队列
 *   3. 并发下载：流式下载播放直链 -> spade_a 派生 AES Key -> CENC-AES-CTR 解密 -> 输出 mp4
 *   4. 下载管理：进度推送、暂停/取消、重试、删除、打开所在文件夹
 *   5. 设置：下载目录、命名规则、并发数（JSON 文件持久化）
 */
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const heicDecode = require('heic-decode');
const jpeg = require('jpeg-js');

const hongguo = require('./src/native/hongguo');
const transcode = require('./src/native/transcode');
const seriesMeta = require('./src/native/series-meta');
const store = require('./src/store');
const APP_VERSION = app.getVersion() || '1.0.0';

const APP_TITLE = '水果刀';
const OFFICIAL_WEBSITE = 'https://github.com/wu798998264-crypto/fruitknife';

let mainWindow = null;


// ===== 下载任务管理 =====
let downloadTasks = [];
let downloadQueue = [];
let activeDownloads = 0;
let MAX_CONCURRENT_DOWNLOADS = 3;

// ===== 设置 =====
function getDefaultSettings() {
  return {
    root: app.getPath('downloads'),
    // 文件命名模板：可用变量 剧名(series_title) 集数(vid_index) 标题(ep_title)
    name_format: '剧名_第集数集',
    max_concurrent: 3,
    // 输出编码：hevc（全部输出 HEVC）/ copy（保留平台原始编码）
    output_codec: 'hevc',
  };
}

function getCurrentSettings() {
  const saved = store.getSettings() || {};
  const merged = { ...getDefaultSettings(), ...saved };
  // 旧版本默认模板没有「第/集」字样，产出形如「剧名 001」；迁移到更易读的新模板
  const LEGACY_FORMAT = {
    '剧名 集数': '剧名_第集数集',
    '剧名 集数 标题': '剧名_第集数集_标题',
  };
  if (LEGACY_FORMAT[merged.name_format]) merged.name_format = LEGACY_FORMAT[merged.name_format];
  // 保证并发数合法
  const mc = parseInt(merged.max_concurrent, 10);
  MAX_CONCURRENT_DOWNLOADS = Number.isInteger(mc) && mc >= 1 && mc <= 10 ? mc : 3;
  return { ...merged, max_concurrent: MAX_CONCURRENT_DOWNLOADS };
}

/**
 * 输出编码保障：确保落盘文件是 HEVC
 *   - 已经是 HEVC（含平台 bytevc1 封装的 hvc1）：原样保留，不重编码、无画质损失；
 *   - H.264：用内置 ffmpeg 转为 HEVC（libx265，音频直通）；
 *   - 无法识别的编码：记录告警，不冒然转码。
 */
async function ensureOutputHevc(filePath) {
  const settings = getCurrentSettings();
  if (settings.output_codec === 'copy') return;

  const codec = transcode.readMp4VideoCodec(filePath);
  if (codec === 'hevc') return;
  if (codec === 'bytevc2') {
    throw new Error('该文件是 ByteVC2 编码（标准播放器无法播放，也无法转码），需要重新下载 HEVC 版本');
  }
  if (codec !== 'h264') {
    if (codec) console.warn('[Hongguo] 非标准视频编码，跳过转码:', codec, filePath);
    return;
  }
  if (!transcode.findFfmpeg()) {
    throw new Error('视频为 H.264，需要转换为 HEVC，但安装包内未找到 ffmpeg');
  }

  console.log('[Hongguo] 源为 H.264，正在转码为 HEVC:', filePath);
  try {
    await transcode.transcodeToHevc(filePath);
    console.log('[Hongguo] 已转为 HEVC:', filePath);
  } catch (e) {
    throw new Error(`H.264 转 HEVC 失败: ${e.message}`);
  }
}

// 净化文件夹/文件名称（移除非法字符、结尾的点和空格）
function sanitizeFolderName(name) {
  return String(name || '')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '')
    .replace(/[. ]+$/g, '')
    .trim()
    .slice(0, 80) || '';
}

// 按命名模板渲染文件名，返回 名称(不含扩展名) 或 null
function renderName(format, seriesTitle, vidIndex, epTitle) {
  const fmt = String(format || '').trim();
  if (!fmt) return null;
  const vars = {
    'series_title': String(seriesTitle || '').trim(),
    'vid_index': String(vidIndex).padStart(3, '0'),
    'ep_title': String(epTitle || '').trim(),
  };
  const zhMap = {
    '剧名': 'series_title',
    '集数': 'vid_index',
    '标题': 'ep_title',
  };
  const zhRe = /剧名|集数|标题/g;
  let name = fmt
    .replace(zhRe, (w) => zhMap[w] || w)
    .replace(/([A-Za-z]+(?:_[A-Za-z]+)*)/g, (tok) => (vars[tok] !== undefined ? (vars[tok] || '') : tok));
  name = name
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    // 变量为空时避免留下悬空的分隔符，如 "剧名__" / "剧名_ "
    .replace(/_{2,}/g, '_')
    .replace(/^[\s_\-]+|[\s_\-]+$/g, '')
    .trim();
  return name || null;
}

// ===== 加载/保存下载任务 =====
// 旧版本会把整部剧的剧情简介当成剧集标题写进任务里，加载时统一收敛成《剧名》第N集
function tidyLegacyTitle(title) {
  const t = String(title || '');
  if (t.length <= 40) return title;
  const m = t.match(/^(《[^》]+》\s*第\s*\d+\s*集)/);
  return m ? m[1] : t.slice(0, 40) + '…';
}

function loadDownloadTasks() {
  const saved = store.getTasks() || [];
  saved.sort((a, b) => (b.startTime || 0) - (a.startTime || 0));
  downloadTasks = saved.map((task) => {
    const inProgress = task.status === 'downloading' || task.status === 'pending';
    const patched = {
      ...task,
      status: inProgress ? 'failed' : task.status,
      error: inProgress ? '应用关闭时任务中断' : task.error,
    };
    if (patched.title) patched.title = tidyLegacyTitle(patched.title);
    if (patched.videoInfo && patched.videoInfo.title) {
      patched.videoInfo = { ...patched.videoInfo, title: tidyLegacyTitle(patched.videoInfo.title) };
    }
    return patched;
  });
}

function saveDownloadTasks() {
  const serializable = downloadTasks.map((task) => {
    const { cancelSource, writer, ...rest } = task;
    delete rest.deleted;
    delete rest._tmpPath;
    return rest;
  });
  store.saveTasks(serializable);
}

// 状态变化时的落盘节流，避免频繁同步写盘卡主进程
let saveTimer = null;
function scheduleSave() {
  if (saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    try {
      saveDownloadTasks();
    } catch (e) {
      console.error('[Store] 保存任务失败:', e.message);
    }
  }, 500);
}

// ===== 下载队列调度（并发 MAX_CONCURRENT_DOWNLOADS） =====
// 修复：原实现 await 单个任务，实际并发恒为 1；这里改为「泵」模型，一次把队列填满并发位
function processDownloadQueue() {
  while (activeDownloads < MAX_CONCURRENT_DOWNLOADS && downloadQueue.length > 0) {
    const task = downloadQueue.shift();
    if (!task || task.deleted || task.status !== 'pending') continue;
    activeDownloads++;
    executeDownload(task)
      .catch((error) => console.error('[Download Queue] 任务执行异常:', error && error.message))
      .finally(() => {
        activeDownloads = Math.max(0, activeDownloads - 1);
        processDownloadQueue();
      });
  }
}

// ===== 下载分发 =====
async function executeDownload(task) {
  if (task.type === 'hongguo') {
    await executeHongguoDownload(task);
    return;
  }
  throw new Error('未知任务类型: ' + task.type);
}

// ===== 红果短剧下载 =====
async function executeHongguoDownload(task) {
  const { id, hongguoInfo, filename } = task;
  const { vid, series_title, vid_index, content_type } = hongguoInfo || {};

  const tmpPath = task.savePath ? String(task.savePath) + '.enc.tmp' : '';
  let response = null;
  let writer = null;

  const cleanupTmp = () => {
    if (!tmpPath) return;
    try { if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath); } catch (_) {}
  };

  // 进度节流：避免每个 chunk 都发 IPC（大文件会拖垮渲染进程）
  let lastEmitAt = 0;
  const emitProgress = (progress, receivedBytes, totalBytes, force) => {
    const now = Date.now();
    if (!force && now - lastEmitAt < 200) return;
    lastEmitAt = now;
    sendToRenderer('download-progress', { id, progress, receivedBytes, totalBytes });
  };

  const markStopped = () => {
    task.status = 'stopped';
    task.endTime = Date.now();
    delete task.error;
    scheduleSave();
    sendToRenderer('download-stopped', { id, path: task.savePath });
  };

  try {
    if (task.deleted) return;
    if (task.cancelled) {
      cleanupTmp();
      markStopped();
      return;
    }

    console.log(`[Hongguo] 开始下载《${series_title}》第${vid_index}集:`, vid);
    task.status = 'downloading';
    task.progress = 0;
    task.receivedBytes = 0;
    task.totalBytes = 0;
    task.startTime = task.startTime || Date.now();
    scheduleSave();
    emitProgress(0, 0, 0, true);

    // 1. 获取播放直链与 spade_a 加密信息
    const settings = getCurrentSettings();
    const wantHevc = settings.output_codec !== 'copy';
    const playInfo = await hongguo.fetchPlayUrlSingle(vid, content_type, wantHevc ? 'hevc' : 'auto');
    if (!playInfo || !playInfo.url) {
      throw new Error('未获取到有效播放地址（接口可能限流，请稍后重试）');
    }
    console.log(`[Hongguo] 选中流编码: ${playInfo.codec || '未知'}（输出要求: ${wantHevc ? 'HEVC' : '原样'}）`);

    // 2. 下载目录
    const root = (settings.root && String(settings.root).trim()) ? String(settings.root).trim() : app.getPath('downloads');
    const seriesFolder = sanitizeFolderName(series_title) || '未命名短剧';
    const downloadDir = task.customDir || path.join(root, seriesFolder);
    fs.mkdirSync(downloadDir, { recursive: true });

    const finalPath = task.savePath || path.join(downloadDir, filename || `${seriesFolder}_${vid_index}.mp4`);
    task.savePath = finalPath;

    // 目标已存在且大小合格，跳过重下（仍需保证输出编码）
    if (fs.existsSync(finalPath) && fs.statSync(finalPath).size > 1024 * 100) {
      // 旧版本可能因 device_level=3 下载到 ByteVC2：这种文件播不了，删掉重下 HEVC
      if (wantHevc && transcode.readMp4VideoCodec(finalPath) === 'bytevc2') {
        console.log('[Hongguo] 已存在文件为 ByteVC2（无法播放），删除后重新下载 HEVC 版本:', finalPath);
        try { fs.unlinkSync(finalPath); } catch (_) {}
      } else {
        console.log('[Hongguo] 文件已存在，跳过下载:', finalPath);
        await ensureOutputHevc(finalPath);
        task.status = 'completed';
        task.progress = 100;
        task.endTime = Date.now();
        delete task.error;
        saveDownloadTasks();
        sendToRenderer('download-completed', { id, path: finalPath });
        return;
      }
    }

    if (task.cancelled || task.deleted) {
      cleanupTmp();
      markStopped();
      return;
    }

    // 3. HTTP 流式下载
    const CancelToken = axios.CancelToken;
    const source = CancelToken.source();
    task.cancelSource = source;

    const requestOnce = (extraHeaders) => axios({
      method: 'GET',
      url: playInfo.url,
      responseType: 'stream',
      headers: { 'User-Agent': hongguo.UA, 'Accept-Encoding': 'identity', ...extraHeaders },
      timeout: 60000,
      maxRedirects: 5,
      cancelToken: source.token,
    });

    try {
      response = await requestOnce({});
    } catch (err) {
      if (axios.isCancel(err)) throw err;
      const status = err.response && err.response.status;
      if (status === 403 || status === 401) {
        response = await requestOnce({ Referer: hongguo.VIDEO_REFERER });
      } else {
        throw err;
      }
    }

    const totalLength = parseInt(response.headers['content-length'], 10) || 0;
    task.totalBytes = totalLength;

    writer = fs.createWriteStream(tmpPath);
    task.writer = writer;

    let received = 0;
    response.data.on('data', (chunk) => {
      received += chunk.length;
      task.receivedBytes = received;
      const progress = totalLength ? Math.min(99, Math.floor((received / totalLength) * 100)) : 0;
      task.progress = progress;
      emitProgress(progress, received, totalLength, false);
    });

    response.data.pipe(writer);
    await new Promise((resolve, reject) => {
      writer.on('finish', resolve);
      writer.on('error', reject);
      response.data.on('error', reject);
      response.data.on('aborted', () => reject(new Error('连接中断')));
    });

    delete task.cancelSource;
    delete task.writer;

    if (received === 0) {
      throw new Error('下载内容为空，请重试');
    }

    if (task.cancelled || task.deleted) {
      cleanupTmp();
      markStopped();
      return;
    }

    // 4. CENC-AES-CTR 解密
    if (playInfo.spadeA) {
      console.log('[Hongguo] 正在派生 AES Key 并解密 MP4...');
      const key = hongguo.deriveKey(playInfo.spadeA);
      if (!key) {
        throw new Error('Key 派生失败（加密信息格式变更）');
      }
      hongguo.decryptMp4File(tmpPath, finalPath, key);
      try { fs.unlinkSync(tmpPath); } catch (_) {}
    } else {
      try { fs.renameSync(tmpPath, finalPath); } catch (_) {
        fs.copyFileSync(tmpPath, finalPath);
        try { fs.unlinkSync(tmpPath); } catch (__) {}
      }
    }

    if (task.deleted) {
      try { if (fs.existsSync(finalPath)) fs.unlinkSync(finalPath); } catch (_) {}
      return;
    }

    // 5. 输出编码保障：非 HEVC 的 H.264 源转码为 HEVC（HEVC 源原样保留）
    await ensureOutputHevc(finalPath);

    task.status = 'completed';
    task.progress = 100;
    task.receivedBytes = totalLength || task.receivedBytes;
    task.endTime = Date.now();
    delete task.error;
    saveDownloadTasks();

    console.log('[Hongguo] 下载完成:', finalPath);
    sendToRenderer('download-completed', { id, path: finalPath });
  } catch (error) {
    delete task.cancelSource;
    delete task.writer;

    // 用户主动停止 / 取消 / 任务被删除：统一落到「已停止」，不再误报失败
    if (task.cancelled || task.deleted || axios.isCancel(error) || error?.code === 'ERR_CANCELED') {
      cleanupTmp();
      if (task.deleted) return;
      markStopped();
      return;
    }

    console.error('[Hongguo] 下载失败:', error.message);
    cleanupTmp();
    task.status = 'failed';
    task.error = error.message;
    task.endTime = Date.now();
    saveDownloadTasks();
    sendToRenderer('download-failed', { id, error: error.message });
  }
}

// 向渲染进程发送事件
function sendToRenderer(channel, payload) {
  if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

// ===== 封面图代理：红果封面是 HEIC，Chromium 无法直接渲染，这里转成 JPEG dataURL =====
const coverCache = new Map(); // url -> dataUrl
const COVER_CACHE_LIMIT = 120;

async function fetchCoverDataUrl(url) {
  const src = String(url || '').trim();
  if (!src || !/^https?:\/\//i.test(src)) return '';
  if (coverCache.has(src)) return coverCache.get(src);

  const res = await axios.get(src, {
    responseType: 'arraybuffer',
    timeout: 15000,
    headers: { 'User-Agent': hongguo.UA, Accept: 'image/*' },
  });
  const buf = Buffer.from(res.data);
  const contentType = String(res.headers['content-type'] || '').toLowerCase();
  let dataUrl;

  const isHeic = contentType.includes('heic') || contentType.includes('heif') || /\.heic(\?|$)/i.test(src);
  if (isHeic) {
    const image = await heicDecode({ buffer: buf });
    const encoded = jpeg.encode({ data: Buffer.from(image.data), width: image.width, height: image.height }, 72);
    dataUrl = 'data:image/jpeg;base64,' + encoded.data.toString('base64');
  } else if (contentType.includes('png')) {
    dataUrl = 'data:image/png;base64,' + buf.toString('base64');
  } else if (contentType.includes('webp')) {
    dataUrl = 'data:image/webp;base64,' + buf.toString('base64');
  } else if (contentType.includes('gif')) {
    dataUrl = 'data:image/gif;base64,' + buf.toString('base64');
  } else if (contentType.includes('jpeg') || contentType.includes('jpg')) {
    dataUrl = 'data:image/jpeg;base64,' + buf.toString('base64');
  } else {
    // 未知格式，按 HEIC 再试一次
    const image = await heicDecode({ buffer: buf });
    const encoded = jpeg.encode({ data: Buffer.from(image.data), width: image.width, height: image.height }, 72);
    dataUrl = 'data:image/jpeg;base64,' + encoded.data.toString('base64');
  }

  if (coverCache.size > COVER_CACHE_LIMIT) {
    const firstKey = coverCache.keys().next().value;
    coverCache.delete(firstKey);
  }
  coverCache.set(src, dataUrl);
  return dataUrl;
}

ipcMain.handle('resolve-cover', async (event, url) => {
  try {
    const dataUrl = await fetchCoverDataUrl(url);
    return { success: !!dataUrl, dataUrl };
  } catch (error) {
    console.error('[Cover] 封面转换失败:', error.message);
    return { success: false, error: error.message };
  }
});

// ===== IPC：红果解析与批量下载 =====
ipcMain.handle('hongguo-resolve', async (event, input) => {
  try {
    const seriesId = await hongguo.resolveSeriesId(input);
    const data = await hongguo.fetchEpisodeList(seriesId);
    return { success: true, data };
  } catch (error) {
    console.error('[Hongguo] 解析失败:', error.message);
    return { success: false, error: error.message };
  }
});

// ===== IPC：搜索短剧 =====
ipcMain.handle('hongguo-search', async (event, payload) => {
  try {
    const { keyword, offset } = payload || {};
    const data = await hongguo.searchSeries(keyword, { offset });
    return { success: true, data };
  } catch (error) {
    console.error('[Hongguo] 搜索失败:', error.message);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('hongguo-search-suggest', async (event, keyword) => {
  try {
    const list = await hongguo.searchSuggest(keyword);
    return { success: true, data: list };
  } catch (error) {
    return { success: false, error: error.message, data: [] };
  }
});

ipcMain.handle('hongguo-download-batch', async (event, payload) => {
  try {
    const { seriesId, seriesTitle, seriesCover, seriesIntro, seriesCategories, seriesPlayCount, seriesTotal, episodes } = payload || {};
    if (!Array.isArray(episodes) || episodes.length === 0) {
      return { success: false, error: '未选择集数' };
    }

    const settings = getCurrentSettings();
    const root = (settings.root && String(settings.root).trim()) ? String(settings.root).trim() : app.getPath('downloads');
    const cleanSeriesTitle = sanitizeFolderName(seriesTitle) || '红果短剧';
    const downloadDir = path.join(root, '红果短剧', cleanSeriesTitle);
    try { fs.mkdirSync(downloadDir, { recursive: true }); } catch (_) {}

    // 随剧保存封面与简介（同目录，已存在则不覆盖；失败不影响下载）
    try {
      const saved = await seriesMeta.ensureSeriesMetadata(downloadDir, {
        seriesTitle: cleanSeriesTitle,
        seriesId,
        cover: seriesCover || '',
        intro: seriesIntro || '',
        categories: Array.isArray(seriesCategories) ? seriesCategories : [],
        playCount: seriesPlayCount || 0,
        total: seriesTotal || episodes.length,
      });
      if (saved.coverPath) console.log('[Hongguo] 已保存封面:', saved.coverPath);
      if (saved.txtPath) console.log('[Hongguo] 已保存简介:', saved.txtPath);
    } catch (e) {
      console.warn('[Hongguo] 保存封面/简介失败:', e.message);
    }

    const batchId = 'hongguobatch_' + Date.now().toString(36) + Math.random().toString(36).substr(2, 5);
    const firstCover = (episodes[0] && (episodes[0].cover || episodes[0].series_cover)) || '';

    const batchInfo = {
      batchId,
      platform: 'hongguo',
      nickname: `《${cleanSeriesTitle}》`,
      avatar: firstCover,
      totalCount: episodes.length,
      createTime: Date.now(),
    };

    // 已存在同 vid 的未完成任务 / 已下载完成的任务，不重复入队
    const existingByVid = new Map();
    for (const t of downloadTasks) {
      const v = t?.hongguoInfo?.vid;
      if (v) existingByVid.set(String(v), t);
    }

    const usedNames = new Set();
    let added = 0;
    let skipped = 0;

    for (const ep of episodes) {
      const vidKey = String(ep.vid);
      const dup = existingByVid.get(vidKey);
      if (dup) {
        const busy = dup.status === 'pending' || dup.status === 'downloading';
        const done = dup.status === 'completed' && dup.savePath && fs.existsSync(dup.savePath);
        if (busy || done) {
          skipped++;
          continue;
        }
      }

      const epIndexStr = String(ep.vid_index).padStart(3, '0');
      const namePart = renderName(settings.name_format, cleanSeriesTitle, ep.vid_index, ep.title);
      let baseName = namePart || `${cleanSeriesTitle}_第${epIndexStr}集`;
      // 命名模板不含集数（例如「仅剧名」）时会导致同名覆盖，这里补上集数保证唯一
      if (usedNames.has(baseName)) {
        baseName = `${baseName}_第${epIndexStr}集`;
      }
      usedNames.add(baseName);

      const filename = `${baseName}.mp4`;
      const finalPath = path.join(downloadDir, filename);

      const task = {
        id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
        batchId,
        batchInfo,
        savePath: finalPath,
        customDir: downloadDir,
        title: `《${cleanSeriesTitle}》第${epIndexStr}集${ep.title ? ' ' + ep.title : ''}`,
        filename,
        platform: 'hongguo',
        type: 'hongguo',
        status: 'pending',
        progress: 0,
        receivedBytes: 0,
        totalBytes: 0,
        startTime: Date.now(),
        videoInfo: {
          author: cleanSeriesTitle,
          title: `《${cleanSeriesTitle}》第${epIndexStr}集`,
          cover: ep.cover || firstCover,
          aweme_id: ep.vid,
        },
        hongguoInfo: {
          vid: ep.vid,
          series_id: seriesId,
          series_title: cleanSeriesTitle,
          vid_index: ep.vid_index,
          ep_title: ep.title,
          content_type: ep.content_type || '',
        },
      };

      downloadTasks.unshift(task);
      downloadQueue.push(task);
      existingByVid.set(vidKey, task);
      sendToRenderer('download-task-added', task);
      added++;
    }

    saveDownloadTasks();
    processDownloadQueue();

    if (added === 0) {
      return { success: false, error: `选中的 ${skipped} 集已在下载列表中或已下载完成` };
    }
    return { success: true, count: added, skipped };
  } catch (error) {
    console.error('[Hongguo] 提交批量下载失败:', error.message);
    return { success: false, error: error.message };
  }
});

// ===== IPC：设置 =====
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
  if (result.canceled) return null;
  return result.filePaths[0];
});

ipcMain.handle('get-settings', async () => getCurrentSettings());

ipcMain.handle('save-settings', async (event, settings) => {
  try {
    const merged = { ...getDefaultSettings(), ...(settings || {}) };
    store.saveSettings(merged);
    getCurrentSettings(); // 刷新并发数
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// ===== IPC：下载管理 =====
ipcMain.handle('get-download-tasks', () => {
  const STATUS_ORDER = {
    downloading: 0,
    pending: 1,
    failed: 2,
    stopped: 3,
    completed: 4,
  };
  const sorted = downloadTasks.slice().sort((a, b) => {
    const wa = STATUS_ORDER[a.status] ?? 99;
    const wb = STATUS_ORDER[b.status] ?? 99;
    if (wa !== wb) return wa - wb;
    if (wa <= 1) {
      // 同一部剧内部按集数排序，不同剧按入队顺序
      const ba = String(a.batchId || '');
      const bb = String(b.batchId || '');
      if (ba !== bb) return (a.startTime || 0) - (b.startTime || 0);
      return (a.hongguoInfo?.vid_index || 0) - (b.hongguoInfo?.vid_index || 0);
    }
    return (b.endTime || b.startTime || 0) - (a.endTime || a.startTime || 0);
  });
  return sorted.map((task) => {
    const { cancelSource, writer, ...serializableTask } = task;
    return serializableTask;
  });
});


// 取消一个任务（下载中/排队中都要处理），并从内存列表中移除
function removeTask(taskId) {
  const taskIndex = downloadTasks.findIndex((t) => t.id === taskId);
  if (taskIndex === -1) return false;
  const task = downloadTasks[taskIndex];

  task.deleted = true;
  task.cancelled = true;
  if (task.cancelSource) {
    try { task.cancelSource.cancel('用户删除任务'); } catch (_) {}
  }
  if (task.writer) {
    // 直接 end 会让「下载器」把文件写完并进入解密流程，这里用 destroy 立即断开
    try { task.writer.destroy(); } catch (_) {}
  }

  downloadTasks.splice(taskIndex, 1);
  const qIndex = downloadQueue.findIndex((t) => t.id === taskId);
  if (qIndex !== -1) downloadQueue.splice(qIndex, 1);

  // 清理未完成的临时文件
  if (task.savePath) {
    const tmp = String(task.savePath) + '.enc.tmp';
    try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch (_) {}
  }
  return true;
}

ipcMain.handle('delete-task', async (event, taskId) => {
  const ok = removeTask(taskId);
  if (!ok) return { success: false, error: '任务不存在' };
  saveDownloadTasks();
  return { success: true };
});

ipcMain.handle('delete-tasks', async (event, taskIds) => {
  if (!Array.isArray(taskIds) || taskIds.length === 0) return { success: false, error: '没有要删除的任务' };
  let count = 0;
  for (const id of taskIds) {
    if (removeTask(id)) count++;
  }
  saveDownloadTasks();
  return { success: true, count };
});

// 一键清空：无论下载中/等待/失败/已停止/已完成，全部取消并移除
ipcMain.handle('clear-all-tasks', async () => {
  const ids = downloadTasks.map((t) => t.id);
  let count = 0;
  for (const id of ids) {
    if (removeTask(id)) count++;
  }
  downloadQueue.length = 0;
  downloadTasks = [];
  saveDownloadTasks();
  return { success: true, count };
});

ipcMain.handle('stop-download', async (event, taskId) => {
  const task = downloadTasks.find((t) => t.id === taskId);
  if (!task) return { success: false, error: '任务不存在' };
  if (task.status === 'completed') return { success: false, error: '任务已完成' };

  task.cancelled = true;

  // 排队中的任务：直接从队列移除，避免稍后又被启动
  const qIndex = downloadQueue.findIndex((t) => t.id === taskId);
  if (qIndex !== -1) downloadQueue.splice(qIndex, 1);

  if (task.cancelSource) { try { task.cancelSource.cancel('用户停止下载'); } catch (_) {} }

  task.status = 'stopped';
  task.error = '';
  task.endTime = Date.now();
  const tmp = task.savePath ? String(task.savePath) + '.enc.tmp' : '';
  if (tmp) { try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch (_) {} }

  saveDownloadTasks();
  sendToRenderer('download-stopped', { id: taskId, path: task.savePath });
  return { success: true };
});

// 任务重新入队（只针对未完成的任务，绝不删除已完成视频）
function requeueTask(task) {
  if (!task) return false;
  if (task.status === 'completed') return false;
  if (task.status === 'pending' || task.status === 'downloading') return false;

  if (task.savePath) {
    const tmp = String(task.savePath) + '.enc.tmp';
    try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch (_) {}
  }

  task.status = 'pending';
  task.progress = 0;
  task.receivedBytes = 0;
  task.totalBytes = 0;
  task.cancelled = false;
  task.deleted = false;
  task.endTime = 0;
  delete task.error;
  delete task.cancelSource;
  delete task.writer;

  if (!downloadQueue.some((t) => t.id === task.id)) downloadQueue.push(task);
  return true;
}

ipcMain.handle('retry-task', async (event, taskId) => {
  const task = downloadTasks.find((t) => t.id === taskId);
  if (!task) return { success: false, error: '任务不存在' };
  if (task.status === 'completed') return { success: false, error: '该任务已完成，无需重试' };

  const ok = requeueTask(task);
  if (!ok) return { success: true, skipped: true };

  saveDownloadTasks();
  processDownloadQueue();
  return { success: true };
});

ipcMain.handle('retry-tasks', async (event, taskIds) => {
  if (!Array.isArray(taskIds) || taskIds.length === 0) return { success: false, error: '没有要重试的任务' };
  let count = 0;
  for (const taskId of taskIds) {
    const task = downloadTasks.find((t) => t.id === taskId);
    if (requeueTask(task)) count++;
  }
  if (count > 0) {
    saveDownloadTasks();
    processDownloadQueue();
  }
  return { success: true, count };
});

ipcMain.handle('open-folder', async (event, taskId) => {
  const task = downloadTasks.find((t) => t.id === taskId);
  if (!task) return { success: false, error: '任务不存在' };

  // 已完成：直接在资源管理器中高亮文件
  if (task.savePath && fs.existsSync(task.savePath)) {
    const stat = fs.statSync(task.savePath);
    if (stat.isDirectory()) {
      await shell.openPath(task.savePath);
    } else {
      shell.showItemInFolder(task.savePath);
    }
    return { success: true };
  }
  if (task.customDir && fs.existsSync(task.customDir)) {
    await shell.openPath(task.customDir);
    return { success: true };
  }
  if (task.savePath) {
    const parentDir = path.dirname(task.savePath);
    if (fs.existsSync(parentDir)) {
      await shell.openPath(parentDir);
      return { success: true };
    }
  }
  return { success: false, error: '文件夹不存在' };
});

// ===== 官网外链与应用信息 =====
// 获取应用信息（版本、品牌、官网链接）
ipcMain.handle('get-app-info', async () => ({
  version: APP_VERSION,
  brand: APP_TITLE,
  appName: '红果短剧下载器',
  official_website: OFFICIAL_WEBSITE,
}));

// 打开外部链接
ipcMain.handle('open-external-url', async (event, url) => {
  const target = url || OFFICIAL_WEBSITE;
  try {
    await shell.openExternal(target);
    return { success: true };
  } catch (err) {
    console.error('[Shell] 打开链接失败:', err.message);
    return { success: false, error: err.message };
  }
});

// ===== 窗口创建 =====
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 900,
    minHeight: 620,
    title: '水果刀 — 红果短剧批量下载',
    autoHideMenuBar: true,
    backgroundColor: '#f5f6fa',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.setMenuBarVisibility(false);

  // 开发模式加载 vite dev server，生产模式加载打包产物
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
  } else {
    mainWindow.loadFile(path.join(__dirname, 'dist-react', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ===== 应用生命周期 =====
// 单实例锁：防止重复启动多个进程，避免安装/更新新版本时旧进程占用文件导致「写入失败 + 重试」
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    const dataFile = path.join(app.getPath('userData'), 'data.json');
    store.init(dataFile);
    loadDownloadTasks();
    getCurrentSettings();

    createWindow();

    // 水果刀本地版：移除原作者「新版本首次启动自动弹浏览器开官网」的引流行为

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
