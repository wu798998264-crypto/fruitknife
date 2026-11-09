# -*- coding: utf-8 -*-
"""重写 hongguo.js 的 searchSeries：用 suggest 接口（query_result_v2）替代被风控的 search/tab

suggest/v1 仍可用，且 query_result_v2 直接带 series_id(keyword)、剧名(name)、
封面(pic_url)、热度(sug_abstract)、视频元数据(video_data)。
"""
import io, re

P = r'D:\workspace_guozi\_fk_electron\app_src\src\native\hongguo.js'
s = io.open(P, encoding='utf-8').read()

start = s.index('async function searchSeries(keyword, opts = {}) {')
end = s.index('\n\n/**\n * 搜索关键词联想', start)
old_fn = s[start:end]
print('原函数 %d 字符' % len(old_fn))

NEW = '''async function searchSeries(keyword, opts = {}) {
  const q = String(keyword || '').trim();
  if (!q) throw new Error('请输入搜索关键词');
  if (q.length > 40) throw new Error('搜索关键词过长');

  const offset = Math.max(0, parseInt(opts.offset, 10) || 0);
  const cacheKey = `${q}@${offset}`;
  const cached = searchCache.get(cacheKey);
  if (cached && Date.now() - cached.t < SEARCH_CACHE_TTL) {
    return cached.v;
  }

  // 2026-11-09: bookapi/search/tab 全端点被服务端风控（HTTP 200 空响应体），
  // 但 suggest/v1 一直可用，且 query_result_v2 直接携带
  //   keyword = series_id、name = 剧名、pic_url = 封面、
  //   sug_abstract = "题材·...·4517万热度"、video_data = 视频元数据
  // 因此搜索改由 suggest 接口实现（结果不受影响，还能带出热度与封面）。
  const j = await signedGet(SUGGEST_PATH, { query: q });
  const v2 =
    (j && j.data && Array.isArray(j.data.query_result_v2))
      ? j.data.query_result_v2
      : [];

  const items = [];
  const seen = new Set();
  for (const it of v2) {
    const seriesId = String((it && it.keyword) || '');
    if (!/^\\d{15,25}$/.test(seriesId) || seen.has(seriesId)) continue;
    seen.add(seriesId);

    const title = String(it.name || '').trim() || '未命名短剧';

    // sug_abstract 形如 "灾难·末世灾难·4517万热度"
    let playCount = 0;
    const hm = /([\\d.]+)\\s*(万|亿)?\\s*热度/.exec(String(it.sug_abstract || ''));
    if (hm) {
      playCount = Math.round(parseFloat(hm[1]) * (hm[2] === '亿' ? 1e8 : hm[2] === '万' ? 1e4 : 1));
    }

    // 题材标签：sug_abstract 的前几节（去掉最后的热度节）
    const tags = String(it.sug_abstract || '')
      .split('·')
      .map((t) => t.trim())
      .filter((t) => t && !/热度$/.test(t) && !/^\\d/.test(t))
      .slice(0, 5);

    const vd = it.video_data || null;

    items.push({
      series_id: seriesId,
      title,
      cover: String(it.pic_url || (vd && vd.cover) || ''),
      total: 0, // suggest 不给集数；点选后由 multi_video_detail 补全
      play_count: playCount,
      followed_count: 0,
      score: '',
      intro: String((vd && vd.video_desc) || '').trim(),
      actors: '',
      categories: tags,
      tags,
      // 渲染层可能直接读这些字段
      series_title: title,
      series_cover: String(it.pic_url || ''),
      episode_cnt: 0,
      series_play_cnt: playCount,
    });
  }

  // 支持翻页：suggest 没有真翻页，offset>0 时返回空（前端据此停止加载更多）
  const result = {
    keyword: q,
    items: offset > 0 ? [] : items,
    rawCount: items.length,
    nextOffset: offset + items.length,
    hasMore: false,
  };

  searchCache.set(cacheKey, { t: Date.now(), v: result });
  trimSearchCache();
  return result;
}'''

s = s[:start] + NEW + s[end:]
io.open(P, 'w', encoding='utf-8', newline='').write(s)
print('searchSeries 已重写（基于 suggest）')

# 语法检查
import subprocess
r = subprocess.run([r'C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe',
                    '--check', P], capture_output=True, text=True)
print('语法检查:', 'OK' if r.returncode == 0 else r.stderr[:300])
