# -*- coding: utf-8 -*-
"""app/hongguo_dl.py — 红果下载核心（移植自参考实现 hongguo-downloader）

实现来源：Electron 版 hongguo-downloader 的 src/native/hongguo.js + main.js
（2026-11-09 用户提供），逻辑逐函数对照移植为 Python。

与旧实现的本质区别
------------------
旧实现（2026-11-09 前后失效）：
    multi_video_model → fallback_api → base64 密文 main_url → AES-CBC 解密
    → ffmpeg -decryption_key（derive_content_key(spade_a)）

新实现（本文件，参考软件验证可用）：
    1. multi_video_model，device_level=4（关键：4 → bytevc1，即标准 HEVC/hvc1；
       3 → 部分剧集只给 bytevc2，字节私有编码，ffmpeg 无法解码）
    2. content_type 自动回退：官方剧 1004 / 用户投稿 1001 / 1003 / 1002
       （传错会返回 101000「服务异常」，所以按优先级轮询并记住上次成功的）
    3. 遍历 video_list，按 video_meta.codec_type 归一化：
       bytevc2 → 排除；h265/hevc/hvc1/bytevc1 → HEVC 族；
       选清晰度最高（definition > 像素 > 编码 > 码率）的一条
    4. 直接用 video_list 里的明文 main_url 下载（不再需要 fallback_api / key_seed）
    5. 下载得到的是 CENC 加密 mp4（视觉轨 sample entry 为 encv）
    6. 用 spade_a 派生 AES-128 key，按 senc 的 IV/subsample 做 AES-CTR 解密，
       moov 重建：encv→hvc1（从 sinf/frma 还原真实 fourcc）、删加密盒、修 stco/co64
    7. 输出标准 hvc1 HEVC mp4，可直接播放 —— 全程无需 ffmpeg 参与解密
"""

import os
import base64
import struct

import requests

API = "https://api5-normal-sinfonlineb.fqnovel.com"
VIDEO_REFERER = "https://novelquickapp.com/"

# 与 app/devices.py 中的指纹一致（参考软件同款）
UA = ("com.phoenix.read/71532 (Linux; U; Android 9; zh_CN; 2509FPN0BC; "
      "Build/PQ3A.190605.07291804)")


# ══════════════════════════ 参数构造 ══════════════════════════

def build_base_params(device):
    """multi_video_model 的 query 参数（签名输入）"""
    return {
        "iid": device["install_id"],
        "device_id": device["device_id"],
        "ac": "wifi",
        "channel": device.get("channel", "oppo_8662_64"),
        "aid": "8662",
        "app_name": "novelread",
        "version_code": device.get("version_code", "71532"),
        "version_name": device.get("version_name", "7.1.5.32"),
        "device_platform": "android",
        "os": "android",
        "ssmix": "a",
        "device_type": device["device_type"],
        "device_brand": device["device_brand"],
        "language": "zh",
        "os_api": device.get("os_api", "28"),
        "os_version": device["os_version"],
        "manifest_version_code": device.get("version_code", "71532"),
        "resolution": "1280*2772",
        "dpi": "520",
        "update_version_code": device.get("version_code", "71532"),
        "host_abi": "arm64-v8a",
        "dragon_device_type": "phone",
        "pv_player": device.get("version_code", "71532"),
        "compliance_status": "0",
        "need_personal_recommend": "1",
        "player_so_load": "1",
        "is_android_pad_screen": "0",
    }


def build_devices(device):
    """core_sixgod 的 devices 字典"""
    return {
        "device_id": device["device_id"],
        "iid": device["install_id"],
        "install_id": device["install_id"],
        "device_brand": device["device_brand"],
        "device_model": device["device_model"],
        "device_type": device["device_type"],
        "device_manufacturer": device.get("device_manufacturer", device["device_brand"]),
        "os_version": device["os_version"],
        "version_name": device.get("version_name", "7.1.5.32"),
        "ua": device.get("ua", UA),
    }


def build_sign_headers(device):
    return {
        "User-Agent": device.get("ua", UA),
        "Accept": "application/json; charset=utf-8,application/x-protobuf",
        "Content-Type": "application/json; charset=UTF-8",
        "x-xs-from-web": "0",
        "x-ss-req-ticket": str(int(__import__("time").time() * 1000)),
        "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2",
        "passport-sdk-version": "50561",
        "x-vc-bdturing-sdk-version": "3.7.2.cn",
    }


def model_biz_param(device_level):
    """biz_param。device_level=4 → bytevc1（标准 HEVC hvc1），3 → 可能只给 bytevc2"""
    return {
        "detail_page_version": 0,
        "device_level": device_level,
        "disable_digg_stat": False,
        "need_all_video_definition": True,
        "need_mp4_align": False,
        "use_os_player": False,
        "use_server_dns": False,
        "video_platform": 1024,
    }


# ══════════════════════════ codec 归一化与选流 ══════════════════════════

def codec_family(codec):
    """编码归类：hevc / h264 / bytevc2 / other"""
    c = str(codec or "").lower()
    if "bytevc2" in c or c == "bvc2":
        return "bytevc2"
    if ("265" in c or "hevc" in c or "hvc1" in c or "bytevc1" in c):
        return "hevc"
    if "264" in c or "avc" in c:
        return "h264"
    return "other"


def stream_score(v):
    """清晰度评分：definition 数字 > 像素 > 码率"""
    meta = v.get("video_meta") or {}
    import re
    def_str = str(meta.get("definition") or "")
    m = re.search(r"(\d+)\s*p", def_str, re.I)
    if m:
        def_num = int(m.group(1))
    else:
        table = {"4k": 2160, "2k": 1440, "fhd": 1080, "fullhd": 1080,
                 "hd": 720, "sd": 480, "ld": 360}
        def_num = table.get(def_str.lower(), 0)
    w = int(meta.get("vwidth") or 0)
    h = int(meta.get("vheight") or 0)
    bitrate = int(meta.get("bitrate") or meta.get("real_bitrate") or 0)
    return {"def_num": def_num, "pixels": w * h, "bitrate": bitrate}


def parse_model_video(vm, preference="auto"):
    """解析 video_model，选出最优流。

    返回 (url, spade_a, codec)；找不到返回 (None, None, None)。
    选流：排除 bytevc2；preference 为 hevc/h264 时先在该编码族内挑，
    该族没有流时回退全量候选（宁可有非偏好编码也不丢集数）。
    """
    if isinstance(vm, str):
        import json as _json
        try:
            vm = _json.loads(vm)
        except Exception:
            return None, None, None
    vl = vm.get("video_list") or []
    rank = {"h265": 0, "h264": 0, "h265_hvc1": 0, "hevc": 0, "hvc1": 0,
            "avc1": 0, "bytevc1": 1}
    cands = []
    for v in vl:
        if not v.get("main_url"):
            continue
        codec = ((v.get("video_meta") or {}).get("codec_type")) or ""
        fam = codec_family(codec)
        if fam == "bytevc2":
            continue  # 字节私有编码，标准播放器/ffmpeg 均无法解码
        cands.append({
            "url": v["main_url"],
            "spade_a": (v.get("encrypt_info") or {}).get("spade_a"),
            "codec": codec,
            "family": fam,
            "score": stream_score(v),
            "codec_rank": rank.get(codec, 2),
        })
    if not cands:
        return None, None, None

    pool = cands
    pref = preference if preference in ("hevc", "h264") else "auto"
    if pref != "auto":
        matched = [c for c in cands if c["family"] == pref]
        if matched:
            pool = matched

    def better(a, b):
        sa, sb = a["score"], b["score"]
        if sa["def_num"] != sb["def_num"]:
            return sa["def_num"] - sb["def_num"]
        if sa["pixels"] != sb["pixels"]:
            return sa["pixels"] - sb["pixels"]
        if a["codec_rank"] != b["codec_rank"]:
            return b["codec_rank"] - a["codec_rank"]  # rank 小的优
        return sa["bitrate"] - sb["bitrate"]

    best = pool[0]
    for c in pool[1:]:
        if better(c, best) > 0:
            best = c
    return best["url"], best["spade_a"], best["codec"]


# ══════════════════════════ 取流（带回退） ══════════════════════════

PLAY_CONTENT_TYPES = ["1004", "1001", "1003", "1002"]
_state = {"last_good_ct": "1004"}


def fetch_play_url(vid, device, content_type=None, codec_pref="hevc",
                   log=None):
    """取单集播放直链。

    device: devices.py 里的一组指纹 dict
    返回 dict(url, spade_a, codec, content_type, device_level)
    """
    _log = log or (lambda s: None)
    first = str(content_type) if content_type else _state["last_good_ct"]
    order = [first] + [x for x in PLAY_CONTENT_TYPES if x != first]
    want_hevc = codec_pref == "hevc"
    levels = [4, 3]  # device_level 4 优先（bytevc1/HEVC），3 兜底
    fallback = None

    from urllib.parse import urlsplit, parse_qsl
    import json as _json
    import time as _time
    from liushen.flurl.core import core_sixgod

    for ct in order:
        for dl in levels:
            try:
                body = {"biz_param": model_biz_param(dl),
                        "mixed_video_id_map": {ct: [vid]}}
                body_data = _json.loads(_json.dumps(
                    body, ensure_ascii=False, separators=(",", ":")))
                body_bytes = _json.dumps(
                    body, ensure_ascii=False, separators=(",", ":")).encode()
                bh = build_sign_headers(device)
                su = API + "/novel/player/multi_video_model/v1/"
                pr = build_base_params(device)
                dv = build_devices(device)
                sh, su2 = core_sixgod(surl=su, params=pr, data=body_data,
                                       devices=dv, header=bh, log=False)
                r = requests.post(su2, headers=sh, data=body_bytes, timeout=25)
                if not r.content:
                    _log(f"ct={ct} dl={dl}: 空响应（签名被拒或风控）")
                    continue
                j = r.json()
                if j.get("code") not in (0, None):
                    _log(f"ct={ct} dl={dl}: code={j.get('code')} msg={j.get('message')}")
                    continue
                item = (j.get("data") or {}).get(vid) or {}
                purl, spade_a, codec = parse_model_video(
                    item.get("video_model"), codec_pref)
                if not purl:
                    _log(f"ct={ct} dl={dl}: 无可用流（可能全为 bytevc2）")
                    continue
                res = {"url": purl, "spade_a": spade_a, "codec": codec,
                       "content_type": ct, "device_level": dl}
                if not want_hevc or codec_family(codec) == "hevc":
                    _state["last_good_ct"] = ct
                    _log(f"ct={ct} dl={dl}: codec={codec}")
                    return res
                # 非 HEVC（例如个别剧集只有 bytevc2）先记下，继续找 HEVC
                if fallback is None:
                    fallback = res
            except Exception as e:
                _log(f"ct={ct} dl={dl}: {type(e).__name__}: {str(e)[:80]}")
    if fallback:
        _log(f"未取到 HEVC 流，实际编码: {fallback['codec']}")
        _state["last_good_ct"] = fallback["content_type"]
        return fallback
    _log("取流失败: 所有 content_type 均无有效地址")
    return {"url": None, "spade_a": None, "codec": None,
            "content_type": None, "device_level": None}


# ══════════════════════════ 密钥派生 ══════════════════════════

def _av_b64decode(s):
    """ffmpeg 兼容 base64 解码（容忍缺失 padding / 非法字符）"""
    s = str(s or "").strip()
    table = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             "abcdefghijklmnopqrstuvwxyz0123456789+/")
    out = bytearray()
    vals = []
    for ch in s:
        idx = table.find(ch)
        if idx < 0:
            continue
        vals.append(idx)
        if len(vals) == 4:
            v = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6) | vals[3]
            out += bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
            vals = []
    if len(vals) == 3:
        v = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6)
        out += bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF])
    elif len(vals) == 2:
        v = (vals[0] << 18) | (vals[1] << 12)
        out += bytes([(v >> 16) & 0xFF])
    return bytes(out)


def derive_key(spade_a):
    """spade_a → 16 字节 AES key（与参考实现 deriveKey 逐行对照）"""
    if not spade_a:
        return None
    buf = _av_b64decode(spade_a)
    L = len(buf)
    if L < 3:
        return None

    key0 = buf[0] ^ buf[1] ^ buf[2]
    x27 = key0 - 0x30
    w22 = L - key0 + 0x2F
    if x27 < 2 or w22 < 2 or w22 > L - 1:
        return None

    x21 = bytearray(buf[1:1 + w22])
    w11, w12, w10 = 0x55, 0xFA, 0xEB
    for i in range(len(x21)):
        w13 = x21[i]
        pc = bin(i).count("1")
        w14 = w12 if (i & 1) == 0 else w11
        if (i & 1) == 1:
            w11 = w13
        else:
            w12 = w13
        x21[i] = (w10 - pc + (w14 ^ w13)) & 0xFF

    c0 = x21[0]
    if 0x30 <= c0 <= 0x39:
        hval = c0 - 0x30
    elif 0x61 <= c0 <= 0x7A:
        hval = c0 - 0x57
    else:
        return None

    w9 = w22 - hval
    if w9 < 2:
        return None
    str_a = bytes(x21[1:1 + w9 - 1]).decode("ascii", "replace")
    if len(str_a) != 32:
        return None
    try:
        return bytes.fromhex(str_a)
    except ValueError:
        return None


# ══════════════════════════ MP4 box 解析 ══════════════════════════

_CONTAINER_BOXES = {"moov", "trak", "mdia", "minf", "stbl", "edts",
                    "dinf", "udta", "meta"}


def parse_boxes(data, start, end):
    """解析 MP4 box 树。返回 [{typ, off, size, hdr, children}]"""
    boxes = []
    off = start
    while off + 8 <= end:
        size = struct.unpack_from(">I", data, off)[0]
        typ = bytes(data[off + 4:off + 8]).decode("latin-1")
        hdr = 8
        if size == 1:
            if off + 16 > end:
                break
            size = struct.unpack_from(">Q", data, off + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            break
        children = None
        if typ in _CONTAINER_BOXES:
            children = parse_boxes(data, off + hdr, off + size)
        boxes.append({"typ": typ, "off": off, "size": size,
                      "hdr": hdr, "children": children})
        off += size
    return boxes


def _get_box(boxes, typ):
    return next((b for b in (boxes or []) if b["typ"] == typ), None)


def _walk(boxes):
    for b in boxes or []:
        if b.get("children"):
            for x in _walk(b["children"]):
                yield x
        yield b


def _u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def _u16(data, off):
    return struct.unpack_from(">H", data, off)[0]


def read_tenc_info(data, stbl):
    """stbl → stsd → sample entry → sinf/schi/tenc 读加密参数"""
    info = {"iv_size": 8, "constant_iv": None}
    stsd = _get_box(stbl.get("children"), "stsd")
    if not stsd:
        return info
    stsd_end = stsd["off"] + stsd["size"]
    p = stsd["off"] + 16  # FullBox(4) + entry_count(4)
    if p + 8 > stsd_end:
        return info
    esize = _u32(data, p)
    if esize < 8 or p + esize > stsd_end:
        return info
    etyp = bytes(data[p + 4:p + 8]).decode("latin-1")
    hdr_size = 78
    if etyp == "enca":
        hdr_size = 28
        if p + 18 <= p + esize:
            audio_ver = _u16(data, p + 16)
            if audio_ver == 1:
                hdr_size = 44
            elif audio_ver == 2:
                hdr_size = 64

    tenc_off = -1

    def scan1(start, end):
        nonlocal tenc_off
        off = start
        while off + 8 <= end:
            size = _u32(data, off)
            typ = bytes(data[off + 4:off + 8]).decode("latin-1")
            hdr = 8
            if size == 1:
                size = struct.unpack_from(">Q", data, off + 8)[0]
                hdr = 16
            elif size == 0:
                size = end - off
            if size < hdr or off + size > end:
                break
            if typ == "sinf":
                scan2(off + hdr, off + size)
            off += size

    def scan2(start, end):
        nonlocal tenc_off
        off = start
        while off + 8 <= end:
            size = _u32(data, off)
            typ = bytes(data[off + 4:off + 8]).decode("latin-1")
            hdr = 8
            if size == 1:
                size = struct.unpack_from(">Q", data, off + 8)[0]
                hdr = 16
            elif size == 0:
                size = end - off
            if size < hdr or off + size > end:
                break
            if typ == "schi":
                o3 = off + hdr
                while o3 + 8 <= off + size:
                    s3 = _u32(data, o3)
                    t3 = bytes(data[o3 + 4:o3 + 8]).decode("latin-1")
                    if t3 == "tenc":
                        tenc_off = o3
                    if s3 < 8:
                        break
                    o3 += s3
            off += size

    scan1(p + 8 + hdr_size, p + esize)
    if tenc_off < 0:
        return info
    q = tenc_off + 8
    is_protected = data[q + 6]
    per_sample_iv = data[q + 7]
    if not is_protected:
        return info
    if per_sample_iv > 0:
        info["iv_size"] = per_sample_iv
    else:
        civ = data[q + 24]
        info["constant_iv"] = bytes(data[q + 25:q + 25 + civ])
        info["iv_size"] = civ
    return info


def parse_senc_samples(data, senc, count, iv_size, constant_iv):
    """senc box：每个 sample 的 IV + 可选 subsample 表"""
    samples = []
    box_end = senc["off"] + senc["size"]
    flags = int.from_bytes(data[senc["off"] + 9:senc["off"] + 12], "big")
    has_sub = bool(flags & 0x02)
    p = senc["off"] + 16
    for _ in range(count):
        if not constant_iv and p + iv_size > box_end:
            break
        if constant_iv:
            iv = constant_iv
        else:
            iv = bytes(data[p:p + iv_size])
            p += iv_size
        subs = None
        if has_sub:
            if p + 2 > box_end:
                break
            ns = _u16(data, p)
            p += 2
            subs = []
            for _ in range(ns):
                if p + 6 > box_end:
                    break
                subs.append([_u16(data, p), _u32(data, p + 2)])
                p += 6
        samples.append({"iv": iv, "subs": subs})
    return samples


# ══════════════════════════ AES-CTR 解密 ══════════════════════════

def build_keystream(key, iv, length):
    """CENC-AES-CTR 密钥流。
    IV 16 字节：整体作 128 位大端计数器；IV 8 字节：前 8 字节 nonce + 后 8 字节计数器"""
    from Crypto.Cipher import AES
    if length <= 0:
        return b""
    nblocks = (length + 15) // 16
    blocks = bytearray(nblocks * 16)
    if len(iv) >= 16:
        mask = (1 << 128) - 1
        base = int.from_bytes(iv[:16], "big")
        ecb = AES.new(key, AES.MODE_ECB)
        for k in range(nblocks):
            v = (base + k) & mask
            blocks[k * 16:k * 16 + 16] = v.to_bytes(16, "big")
        return ecb.encrypt(bytes(blocks))
    ecb = AES.new(key, AES.MODE_ECB)
    for k in range(nblocks):
        blocks[k * 16:k * 16 + 8] = iv[:8]
        blocks[k * 16 + 8:k * 16 + 16] = struct.pack(">Q", k)
    return ecb.encrypt(bytes(blocks))


def decrypt_sample(key, nonce, cipher, subs):
    """CENC-AES-CTR 解密单个 sample（subsample 感知）"""
    if not cipher:
        return b""
    if not subs:
        ks = build_keystream(key, nonce, len(cipher))
        return bytes(a ^ b for a, b in zip(cipher, ks))
    enc_total = sum(s[1] for s in subs)
    ks = build_keystream(key, nonce, enc_total)
    plain = bytearray(cipher)
    src = 0
    kpos = 0
    for clear, enc in subs:
        c_len = max(0, min(clear, len(cipher) - src))
        src += c_len  # clear 段原样保留
        e_len = max(0, min(enc, len(cipher) - src))
        for i in range(e_len):
            plain[src + i] ^= ks[kpos + i]
        src += e_len
        kpos += e_len
    return bytes(plain)


# ══════════════════════════ moov 重建 ══════════════════════════

def _detect_sample_entry_type(data, p, esize, hdr_size, enc_type):
    """还原加密 sample entry 的真实编码（从 sinf/frma 取）"""
    orig = ""
    has_hvcc = has_avcc = has_dac3 = has_dec3 = has_dops = False

    def scan(start, end):
        nonlocal orig, has_hvcc, has_avcc, has_dac3, has_dec3, has_dops
        off = start
        while off + 8 <= end:
            size = _u32(data, off)
            typ = bytes(data[off + 4:off + 8]).decode("latin-1")
            hdr = 8
            if size == 1:
                size = struct.unpack_from(">Q", data, off + 8)[0]
                hdr = 16
            elif size == 0:
                size = end - off
            if size < hdr or off + size > end:
                break
            if typ == "hvcC":
                has_hvcc = True
            elif typ == "avcC":
                has_avcc = True
            elif typ == "dac3":
                has_dac3 = True
            elif typ == "dec3":
                has_dec3 = True
            elif typ == "dOps":
                has_dops = True
            elif typ == "sinf":
                o2 = off + hdr
                while o2 + 8 <= off + size:
                    s2 = _u32(data, o2)
                    t2 = bytes(data[o2 + 4:o2 + 8]).decode("latin-1")
                    if t2 == "frma" and not orig and o2 + 8 + 4 <= off + size:
                        orig = bytes(data[o2 + 8:o2 + 12]).decode("latin-1")
                    if s2 < 8:
                        break
                    o2 += s2
            off += size

    scan(p + 8 + hdr_size, p + esize)
    if orig and all(0x20 <= ord(c) <= 0x7E for c in orig) \
            and orig not in ("encv", "enca"):
        return orig
    if enc_type == "enca":
        if has_dec3:
            return "ec-3"
        if has_dac3:
            return "ac-3"
        if has_dops:
            return "opus"
        return "mp4a"
    if has_hvcc:
        return "hvc1"
    if has_avcc:
        return "avc1"
    return "hvc1" if enc_type == "encv" else "mp4a"


def rebuild_moov(data, moov_off, moov_size, top):
    """重建 moov：encv/enca → 真实 fourcc，删加密盒，修 stco/co64"""
    moov_box = _get_box(top, "moov")
    if not moov_box:
        raise ValueError("no moov box")

    def clone(box):
        return {"typ": box["typ"], "off": box["off"], "size": box["size"],
                "hdr": box["hdr"],
                "children": [clone(c) for c in box["children"]]
                if box.get("children") else None}

    def prune(box):
        if not box.get("children"):
            return box
        keep = []
        for c in box["children"]:
            if c["typ"] in ("senc", "saio", "saiz", "sgpd", "sbgp"):
                continue
            keep.append(prune(c))
        box["children"] = keep
        return box

    tree = prune(clone(moov_box))

    patches = {}
    for b in _walk([tree]):
        if b["typ"] == "stsd":
            content = b["off"] + 8
            count = _u32(data, content + 4)
            p = content + 8
            entries = []
            for _ in range(count):
                esize = _u32(data, p)
                etyp = bytes(data[p + 4:p + 8]).decode("latin-1")
                if etyp in ("encv", "enca"):
                    hdr_size = 78
                    if etyp == "enca":
                        hdr_size = 28
                        if p + 18 <= p + esize:
                            av = _u16(data, p + 16)
                            if av == 1:
                                hdr_size = 44
                            elif av == 2:
                                hdr_size = 64
                    new_type = _detect_sample_entry_type(
                        data, p, esize, hdr_size, etyp)
                    entry = bytearray(8 + hdr_size)
                    struct.pack_into(">I", entry, 0, 0)
                    entry[4:8] = new_type.encode("latin-1")
                    entry[8:8 + hdr_size] = data[p + 8:p + 8 + hdr_size]

                    extra = bytearray()
                    q = p + 8 + hdr_size
                    while q + 8 <= p + esize:
                        s2 = _u32(data, q)
                        t2 = bytes(data[q + 4:q + 8]).decode("latin-1")
                        if s2 == 1:
                            s2 = struct.unpack_from(">Q", data, q + 8)[0]
                        elif s2 == 0:
                            s2 = p + esize - q
                        if t2 != "sinf":
                            extra += data[q:q + s2]
                        q += s2
                    full = bytearray(entry) + bytearray(extra)
                    struct.pack_into(">I", full, 0, len(full))
                    entries.append(bytes(full))
                else:
                    entries.append(bytes(data[p:p + esize]))
                p += esize
            stsd_hdr = bytearray(data[b["off"]:content + 8])
            struct.pack_into(">I", stsd_hdr, 12, len(entries))
            full_stsd = bytearray(stsd_hdr) + bytearray(b"".join(entries))
            struct.pack_into(">I", full_stsd, 0, len(full_stsd))
            patches[b["off"]] = bytes(full_stsd)

    def serialize(box):
        if not box.get("children"):
            return patches.get(box["off"]) or bytes(
                data[box["off"]:box["off"] + box["size"]])
        body = b"".join(serialize(c) for c in box["children"])
        hdrb = bytearray(data[box["off"]:box["off"] + box["hdr"]])
        if box["hdr"] == 8:
            struct.pack_into(">I", hdrb, 0, 8 + len(body))
        else:
            struct.pack_into(">I", hdrb, 0, 1)
            struct.pack_into(">Q", hdrb, 8, 16 + len(body))
        return bytes(hdrb) + body

    first = serialize(tree)
    delta = moov_size - len(first)

    if delta != 0:
        # stco/co64 是文件绝对偏移：moov 在 mdat 之后（红果默认布局）时
        # moov 变小不影响 mdat 内偏移，只有 moov 前移才需要修正
        moov_end = moov_off + moov_size
        for b in _walk([tree]):
            if b["typ"] == "stco":
                nc = _u32(data, b["off"] + 12)
                for i in range(nc):
                    pos = b["off"] + 16 + i * 4
                    v = _u32(data, pos)
                    if v >= moov_end:
                        struct.pack_into(">I", data, pos, v - delta)
            elif b["typ"] == "co64":
                nc = _u32(data, b["off"] + 12)
                for i in range(nc):
                    pos = b["off"] + 16 + i * 8
                    v = struct.unpack_from(">Q", data, pos)[0]
                    if v >= moov_end:
                        struct.pack_into(">Q", data, pos, v - delta)

    return serialize(tree)


def decrypt_mp4_file(src_path, dst_path, key, log=None):
    """解密 CENC 加密的 MP4 并输出标准 mp4（hvc1/mp4a）"""
    _log = log or (lambda s: None)
    with open(src_path, "rb") as f:
        data = bytearray(f.read())
    total = len(data)
    top = parse_boxes(data, 0, total)
    moov = _get_box(top, "moov")
    if not moov:
        raise ValueError("no moov box found in MP4")

    stbls = [b for b in _walk(top) if b["typ"] == "stbl"]
    track_info = []
    for stbl in stbls:
        stsz = _get_box(stbl.get("children"), "stsz")
        stco = _get_box(stbl.get("children"), "stco")
        stsc = _get_box(stbl.get("children"), "stsc")
        senc = _get_box(stbl.get("children"), "senc")
        if not stsz or not stco or not stsc:
            continue
        ss = _u32(data, stsz["off"] + 12)
        n = _u32(data, stsz["off"] + 16)
        sizes = []
        if ss == 0:
            for i in range(n):
                sizes.append(_u32(data, stsz["off"] + 20 + i * 4))
        else:
            sizes = [ss] * n

        nc = _u32(data, stco["off"] + 12)
        chunk_offs = [_u32(data, stco["off"] + 16 + i * 4) for i in range(nc)]

        ns = _u32(data, stsc["off"] + 12)
        stsc_tab = []
        for i in range(ns):
            base = stsc["off"] + 16 + i * 12
            stsc_tab.append([_u32(data, base), _u32(data, base + 4),
                             _u32(data, base + 8)])

        tenc = read_tenc_info(data, stbl)
        samples = (parse_senc_samples(data, senc, len(sizes),
                                      tenc["iv_size"], tenc["constant_iv"])
                   if senc else [])

        chunk_spc = {}
        for i in range(len(stsc_tab)):
            fc, spc = stsc_tab[i][0], stsc_tab[i][1]
            nxt = stsc_tab[i + 1][0] if i + 1 < len(stsc_tab) else nc + 1
            for ci in range(fc - 1, nxt - 1):
                chunk_spc[ci] = spc

        sample_offs = []
        si = 0
        for ci in range(nc):
            off = chunk_offs[ci]
            spc = chunk_spc.get(ci, 1)
            for _ in range(spc):
                if si >= n:
                    break
                sample_offs.append(off)
                off += sizes[si]
                si += 1

        track_info.append({"sizes": sizes, "offs": sample_offs,
                           "samples": samples})

    n_dec = 0
    for ti in track_info:
        for i in range(len(ti["offs"])):
            sample = ti["samples"][i] if i < len(ti["samples"]) else None
            if not sample or not sample.get("iv") or len(sample["iv"]) == 0:
                continue
            off = ti["offs"][i]
            sz = ti["sizes"][i]
            if off + sz > total:
                continue
            cipher = bytes(data[off:off + sz])
            plain = decrypt_sample(key, sample["iv"], cipher, sample["subs"])
            data[off:off + sz] = plain
            n_dec += 1
    _log(f"解密 sample: {n_dec} 个")

    new_moov = rebuild_moov(data, moov["off"], moov["size"], top)
    out = []
    off = 0
    while off < total:
        size = _u32(data, off)
        typ = bytes(data[off + 4:off + 8]).decode("latin-1")
        if size == 1:
            size = struct.unpack_from(">Q", data, off + 8)[0]
        elif size == 0:
            size = total - off
        if typ == "moov":
            out.append(new_moov)
        else:
            out.append(bytes(data[off:off + size]))
        off += size

    with open(dst_path, "wb") as f:
        f.write(b"".join(out))


# ══════════════════════════ 下载 ══════════════════════════

def download_stream(url, dst_path, device, log=None, progress=None):
    """流式下载视频。403/401 时带 Referer 重试一次。

    progress(received, total) 可选，用于回调进度。
    返回 (received, total)。
    """
    _log = log or (lambda s: None)
    headers = {"User-Agent": device.get("ua", UA),
               "Accept-Encoding": "identity"}
    r = requests.get(url, headers=headers, stream=True, timeout=60)
    if r.status_code in (403, 401):
        _log(f"HTTP {r.status_code}，带 Referer 重试")
        headers["Referer"] = VIDEO_REFERER
        r = requests.get(url, headers=headers, stream=True, timeout=60)
    r.raise_for_status()
    total = int(r.headers.get("Content-Length") or 0)
    received = 0
    with open(dst_path, "wb") as f:
        for chunk in r.iter_content(256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            received += len(chunk)
            if progress:
                progress(received, total)
    return received, total
