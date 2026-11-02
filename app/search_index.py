"""
app/search_index.py — 红果短剧搜索索引 v15
==========================================
- 搜索: 官方API实时搜索（含封面/热度/播放量/收藏/评分）
- 翻页: API passback 机制
- 排序: 按热度降序
"""
import urllib.parse
import json
import re
import time
import sys
import ast
import uuid
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

# 六神签名
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "hg_src"))
sys.path.insert(0, str(BASE_DIR / "hg_src" / "liushen"))
from liushen.flurl.core import core_sixgod
import requests as _requests
_SIGN_ENABLED = True


def _parse_heat(heat_text: str) -> int:
    """解析热度文本 → 数值, 用于排序
    '4503万热度' → 45030000
    """
    if not heat_text:
        return 0
    m = re.search(r'([\d.]+)\s*万', heat_text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'(\d+)', heat_text)
    return int(m.group(1)) if m else 0


# 设备信息（从Frida抓包获取，公用）
_API_DEVICE_ID = "4368802900374539"
_API_INSTALL_ID = "4368802899821595"
_API_CDID = "e205b786-e86c-403e-947e-d3dc94725924"
_API_DEVICE = {
    "device_id": _API_DEVICE_ID, "iid": _API_INSTALL_ID,
    "install_id": _API_INSTALL_ID, "device_brand": "XIAOMI",
    "device_model": "2509FPN0BC", "device_type": "2509FPN0BC",
    "device_manufacturer": "XIAOMI", "os_version": "9",
    "version_name": "7.1.5.32", "version_code": "71532",
    "ua": "com.phoenix.read/71532 (Linux; U; Android 9; zh_CN; 2509FPN0BC; Build/PQ3A.190605.07291804)",
    "channel": "oppo_8662_64", "cdid": _API_CDID,
}
_API_SEARCH_URL = "https://api5-normal-sinfonlinec.fqnovel.com/reading/bookapi/search/tab/v"


def _api_search(keyword, offset=0, count=20, passback=""):
    """调用官方搜索API，返回格式化结果"""
    if not _SIGN_ENABLED or not keyword:
        return []

    rt = str(int(time.time() * 1000))
    sid = str(uuid.uuid4())

    params = {
        "query": keyword, "tab_type": "11", "tab_name": "feed",
        "offset": str(offset), "count": str(count),
        "bookstore_tab": "16", "search_source": "1",
        "only_feed": "false", "use_correct": "true",
        "aid": "8662", "device_platform": "android",
        "version_code": "71532", "version_name": "7.1.5.32",
        "iid": _API_INSTALL_ID, "device_id": _API_DEVICE_ID,
        "_rticket": rt, "ac": "wifi", "channel": "oppo_8662_64",
        "app_name": "novelread", "device_type": "2509FPN0BC",
        "device_brand": "XIAOMI", "os_api": "28", "os_version": "9",
        "resolution": "1920*1080", "dpi": "280",
        "host_abi": "arm64-v8a", "dragon_device_type": "pad",
        "language": "zh", "ssmix": "a",
        "manifest_version_code": "71532", "update_version_code": "71532",
        "cdid": _API_CDID, "rom_version": "PQ3A.190605.07291804+release-keys",
        "user_is_login": "0", "bookshelf_search_plan": "4",
        "is_first_enter_search": "false", "is_android_pad_screen": "1",
        "network_type": "4", "need_personal_recommend": "1",
        "player_so_load": "1", "font_scale": "100",
        "pv_player": "71532", "compliance_status": "0",
        "gender": "2", "normal_session_id": sid,
    }
    if passback:
        params["passback"] = str(passback)
        params["offset"] = str(passback)
        params["count"] = "0"

    base_url = f"{_API_SEARCH_URL}?{'&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())}"
    up = urlsplit(base_url)
    bu = f"{up.scheme}://{up.netloc}{up.path}"
    pr = {k: v for k, v in parse_qsl(up.query, keep_blank_values=True)}

    ts_ms = str(int(time.time() * 1000))
    base_headers = {
        "User-Agent": _API_DEVICE["ua"], "Accept": "*/*",
        "Accept-Encoding": "gzip", "x-xs-from-web": "0",
        "x-ss-req-ticket": ts_ms, "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2", "passport-sdk-version": "50561",
    }

    try:
        sh, su = core_sixgod(surl=bu, params=pr, data={},
                             devices=_API_DEVICE, header=base_headers)
        r = _requests.get(su, headers=sh, timeout=15)
        if r.status_code != 200 or len(r.content) < 100:
            return [], ""
        data = r.json()
        if data.get("code") != 0:
            return [], ""

        results = []
        next_passback = ""
        tabs = data.get("search_tabs", [])
        for tab in tabs:
            items = tab.get("data") or []
            pb = tab.get("passback", "")
            if pb:
                next_passback = str(pb)
            for item in items:
                vd_list = item.get("video_data")
                if isinstance(vd_list, str):
                    try:
                        vd_list = ast.literal_eval(vd_list)
                    except Exception:
                        continue
                if not vd_list or not isinstance(vd_list, list) or not vd_list[0]:
                    continue
                vd = vd_list[0]
                if not vd.get("series_id"):
                    continue
                detail = item.get("video_detail")
                if isinstance(detail, dict):
                    series_id = str(detail.get("series_id", vd.get("series_id", "")))
                    series_name = detail.get("series_title", vd.get("title", ""))
                    cover = detail.get("series_cover", vd.get("cover", ""))
                    intro = detail.get("series_intro", "")
                    episode_cnt = detail.get("episode_cnt", 0)
                    followed_cnt = detail.get("followed_cnt", 0)
                    drama_type = "short"
                else:
                    series_id = str(vd.get("series_id", ""))
                    series_name = vd.get("title", "")
                    cover = vd.get("cover", "")
                    intro = ""
                    episode_cnt = vd.get("episode_cnt", 0)
                    followed_cnt = 0
                    drama_type = "manju"
                results.append({
                    "series_id": series_id,
                    "series_name": series_name,
                    "series_cover": cover,
                    "series_intro": intro,
                    "tags": [],
                    "episode_cnt": episode_cnt,
                    "play_cnt": vd.get("play_cnt", 0),
                    "heat": vd.get("rec_text", ""),
                    "score": vd.get("score", ""),
                    "followed_cnt": followed_cnt,
                    "copyright": vd.get("copyright", ""),
                    "content_type": vd.get("content_type", 1004),
                    "drama_type": drama_type,
                    "_heat_val": _parse_heat(vd.get("rec_text", "")),
                })
        return results, next_passback
    except Exception:
        return [], ""


def search(keyword, sort_by_heat=True):
    """搜索短剧/漫剧 — 返回 (results, passback)"""
    if not keyword or not _SIGN_ENABLED:
        return [], ""
    results, passback = _api_search(keyword.strip(), count=20)
    if sort_by_heat:
        results.sort(key=lambda x: x.get("_heat_val", 0), reverse=True)
    return results, passback


def search_more(keyword, passback):
    """翻页加载更多 — 返回 (results, passback)"""
    if not _SIGN_ENABLED:
        return [], ""
    results, next_pb = _api_search(keyword, offset=0, passback=passback, count=0)
    results.sort(key=lambda x: x.get("_heat_val", 0), reverse=True)
    return results, next_pb


# ──────────────────── 分享链接解析 ────────────────────
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
              "Mobile/15E148 Safari/604.1")

# 短链跳转后的页面里，剧集 ID 出现的参数名（按优先级）
_SID_PARAMS = ("series_id", "video_id", "book_id", "vid")


def _ids_from_text(text):
    """从文本里找候选 ID。

    优先看常见键名（series_id / video_id / book_id），
    找不到再退回页面里的 15~20 位长数字。

    注意：分享页里同时存在 series_id 与单集 vid，
    必须优先取 series_id，否则会拿到某一集的 ID。
    """
    if not text:
        return ""
    for key in _SID_PARAMS:
        m = re.search(r'["\']?%s["\']?\s*[:=]\s*["\']?(\d{10,20})' % key, text)
        if m:
            return m.group(1)
    m = re.search(r'(?<!\d)(\d{15,20})(?!\d)', text)
    return m.group(1) if m else ""


def parse_share_url(share_url, timeout=20):
    """解析红果分享链接 → series_id。

    短链形如 https://novelquickapp.com/s/7_9cWlOsids/
    本身不含数字 ID，必须请求一次让服务端 302 跳转到真实页面，
    真实页面的 query 里带 series_id / video_id。

    返回 series_id 字符串；失败返回 ""。
    """
    if not share_url:
        return ""
    url = share_url.strip()

    # 1) 链接里已经直接带数字 ID —— 不用发请求
    direct = _ids_from_text(url)
    if direct:
        return direct

    # 2) 短链 → 跟随重定向
    try:
        r = _requests.get(url, allow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _MOBILE_UA})
        try:
            r.encoding = r.encoding or "utf-8"
        except Exception:
            pass
    except Exception:
        return ""

    # 2a) 跳转后的 URL query 里取
    try:
        q = dict(parse_qsl(urlsplit(r.url).query))
        for key in _SID_PARAMS:
            v = q.get(key, "")
            if v.isdigit() and 10 <= len(v) <= 20:
                return v
    except Exception:
        pass

    # 2b) 页面正文里取
    return _ids_from_text(r.text)


def resolve_share(share_url, timeout=20):
    """解析分享链接，返回 (series_id, series_name)。

    拿到 series_id 后顺带查一次名称，好让任务卡片显示剧名
    （分享页本身只有 id，没有名字）。
    """
    sid = parse_share_url(share_url, timeout=timeout)
    if not sid:
        return "", ""
    name = ""
    try:
        res, _ = search(sid, sort_by_heat=False)
        for r in res or []:
            if str(r.get("series_id")) == sid:
                name = r.get("series_name") or ""
                break
    except Exception:
        pass
    return sid, name
