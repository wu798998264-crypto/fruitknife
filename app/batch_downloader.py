"""
app/batch_downloader.py — 独立进程批量下载器 v2
=================================================
完全隔离 C 扩展，通过进度文件与 GUI 通信。

进度文件 (每行一个JSON):
    {"sid":"...", "ep":1, "status":"done"}
    {"sid":"...", "ep":2, "status":"error", "msg":"..."}
    ...
    {"sid":"...", "type":"all_done", "done":59, "error":0}

调试日志: 在 save_dir/_debug.log 写入详细步骤
"""
import sys, os, json, time, base64, hashlib, binascii, subprocess, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller 兼容: frozen 时数据文件在 sys._MEIPASS
if getattr(sys, 'frozen', False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(_HERE)
_HG = os.path.join(_BASE, "hg_src")
if _HG not in sys.path:
    sys.path.insert(0, _HG)
    sys.path.insert(0, os.path.join(_HG, "liushen"))


def find_ffmpeg():
    import glob
    for pat in [
        os.path.join(_BASE, "ffmpeg_bin", "ffmpeg-N-*-win64-gpl-shared", "bin", "ffmpeg.exe"),
        os.path.join(_BASE, "ffmpeg_bin", "bin", "ffmpeg.exe"),
        "ffmpeg.exe",
    ]:
        if "*" in pat:
            matches = sorted(glob.glob(pat), reverse=True)
            if matches: return matches[0]
        elif os.path.isfile(pat): return pat
    return None


def b64d(s):
    s = s.strip(); pad = len(s) % 4
    if pad: s += "=" * (4 - pad)
    try: return base64.b64decode(s)
    except Exception: return base64.urlsafe_b64decode(s)


def derive_content_key(spade_a):
    s = spade_a.strip(); mm = 4 - len(s) % 4
    if mm != 4: s += "=" * mm
    raw2 = base64.b64decode(s)
    v6 = raw2[0] ^ raw2[1] ^ raw2[2]; v8 = len(raw2) - v6 + 47
    if v8 <= 0 or v8 > len(raw2) * 2: v8 = len(raw2) - 1
    v13 = bytearray(raw2[1:1+v8]); va, vb = 85, 246
    for i in range(v8):
        if i & 1: v24 = va; va = v13[i]
        else: v24 = vb; vb = v13[i]
        v13[i] = (-21 - bin(i).count("1") + (v24 ^ v13[i])) & 0xFF
    return binascii.unhexlify(bytes(v13[1:33]).decode('ascii'))


def write_progress(pf, obj):
    with open(pf, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def is_valid_video(fp, ffmpeg_bin=None):
    """验证视频文件完整有效（非加密、可播放）"""
    if not os.path.exists(fp) or os.path.getsize(fp) == 0:
        return False
    # 检测 CENC 加密残留头
    try:
        with open(fp, 'rb') as f:
            head = f.read(64)
        if b'encv' in head or b'enca' in head:
            return False
    except: return False
    # ffprobe 验证
    ffprobe = None
    if ffmpeg_bin:
        ffprobe = os.path.join(os.path.dirname(ffmpeg_bin), "ffprobe.exe")
        if not os.path.exists(ffprobe):
            ffprobe = ffmpeg_bin.replace("ffmpeg", "ffprobe")
        if not os.path.exists(ffprobe):
            ffprobe = None
    if not ffprobe:
        return os.path.getsize(fp) > 0  # 回退：有内容且不加密
    try:
        subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1', fp],
                       capture_output=True, timeout=8,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
        return True
    except:
        return False


def download_one(sid, vid, ep, sdir, sname, did, iid, qual, ffmpeg=None):
    """下载单集，返回结果字典"""
    log_path = os.path.join(sdir, "_debug.log")

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[ep{ep}] {msg}\n")
        except: pass

    try:
        import requests as rq
        from Crypto.Cipher import AES
        from urllib.parse import urlsplit, parse_qsl
        from liushen.flurl.core import core_sixgod

        log(f"START vid={vid[:8]}.. qual={qual} did={did[:8]}..")

        ua = "com.phoenix.read/71332"
        api_url = (
            f"https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/"
            f"?iid={iid}&device_id={did}&ac=wifi&channel=update_64&aid=8662"
            f"&app_name=novelread&version_code=71332&version_name=7.1.3.32"
            f"&device_platform=android&os=android&ssmix=a"
            f"&device_type=25053RT47C&device_brand=Redmi&language=zh"
            f"&os_api=36&os_version=16&manifest_version_code=71332"
            f"&resolution=1280*2772&dpi=520&update_version_code=71332"
            f"&host_abi=arm64-v8a&dragon_device_type=phone&pv_player=71332"
            f"&compliance_status=0&need_personal_recommend=1"
            f"&player_so_load=1&is_android_pad_screen=0"
        )

        body = {
            "biz_param": {
                "detail_page_version": 0, "device_level": 3,
                "disable_digg_stat": False, "need_all_video_definition": True,
                "need_mp4_align": False, "use_os_player": False,
                "use_server_dns": False, "video_platform": 1024,
            },
            "mixed_video_id_map": {"1004": [vid]},
        }

        body_data = json.loads(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        ts = str(int(time.time() * 1000))
        bh = {
            "User-Agent": ua, "Accept": "application/json; charset=utf-8,application/x-protobuf",
            "Content-Type": "application/json; charset=UTF-8",
            "x-xs-from-web": "0", "x-ss-req-ticket": ts,
            "x-tt-request-tag": "t=0;n=0", "sdk-version": "2",
            "passport-sdk-version": "50561", "x-vc-bdturing-sdk-version": "3.7.2.cn",
        }

        prt = urlsplit(api_url)
        su = f"{prt.scheme}://{prt.netloc}{prt.path}"
        pr = dict(parse_qsl(prt.query, keep_blank_values=True))
        dv = {"device_id": did, "iid": iid, "install_id": iid,
              "device_brand": "Redmi", "device_model": "25053RT47C",
              "device_type": "25053RT47C", "device_manufacturer": "Xiaomi",
              "os_version": "16", "version_name": "7.1.3.32", "ua": ua}

        sh, su2 = core_sixgod(surl=su, params=pr, data=body_data, devices=dv, header=bh, log=False)
        r = rq.post(su2, headers=sh, data=body_bytes, timeout=15)
        log(f"API HTTP {r.status_code}")

        d1 = r.json()
        vid_data = (d1.get("data") or {}).get(vid)
        if not vid_data:
            log(f"NO VID DATA: keys={list(d1.get('data',{}).keys())[:3]}")
            return {"ep": ep, "status": "error", "msg": "no_vid_data"}

        vm = json.loads(vid_data["video_model"])
        fa = vm["fallback_api"]
        log(f"got fallback_api")

        r2 = rq.get(fa, headers={"User-Agent": ua}, timeout=15)
        d2 = r2.json()["video_info"]["data"]

        ks = b64d(d2["key_seed"])
        vl = d2["video_list"]
        qm = {"360p": "video_1", "480p": "video_2", "540p": "video_3",
              "720p": "video_4", "1080p": "video_5"}
        it = vl.get(qm.get(qual, "video_3")) or vl.get("video_3") or list(vl.values())[0]
        log(f"selected quality={qual}, video_list keys={list(vl.keys())}")

        # 解密视频 URL
        raw = b64d(it["main_url"])
        ciph = raw[4:]; clen = (len(ciph) // 16) * 16; ciph = ciph[:clen]
        SP = bytes([
            0x4D,0xD4,0xC2,0xE6,0xB8,0x31,0x62,0x09,0x0E,0x52,0xB3,0xC7,0xA6,0x73,0x3B,0xA4,
            0x1C,0xB2,0x46,0x2B,0x82,0x9A,0xB5,0x8A,0x19,0x6B,0x39,0xDB,0x57,0x17,0x75,0x24,
            0xF4,0x9B,0xAF,0x7F,0x08,0xE8,0xD6,0x8D,0x26,0xA7,0x2E,0x37,0xC1,0xA9,0x5A,0x2F,
            0x1F,0x05,0xA5,0x18,0x92,0xAE,0xF2,0x94,0x97,0x32,0xB6,0x2A,0x38,0xAA,0xDD,0x58,
        ])
        h1 = hashlib.sha512(ks).digest(); h2 = hashlib.sha512(h1 + SP).digest()
        aes = AES.new(h2[:16], AES.MODE_CBC, iv=h2[16:32])
        pl = aes.decrypt(ciph)
        if pl:
            pad = pl[-1]
            if 1 <= pad <= 16 and pad <= len(pl): pl = pl[:-pad]
        ru = pl.rstrip(b"\x00").decode()
        log(f"decrypted URL: {ru[:80]}...")

        ck = derive_content_key(it["spade_a"])

        if not ffmpeg: ffmpeg = find_ffmpeg()
        if not ffmpeg:
            log(f"NO FFMPEG")
            return {"ep": ep, "status": "error", "msg": "no_ffmpeg"}

        fp = os.path.join(sdir, f"{sname}_第{ep:02d}集.mp4")
        ss = subprocess.run(
            [ffmpeg, "-y", "-decryption_key", ck.hex(), "-i", ru,
             "-c", "copy", "-movflags", "+faststart", "-loglevel", "error", fp],
            capture_output=True, timeout=300,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        stderr = ss.stderr.decode(errors="replace")[:200] if ss.stderr else ""
        log(f"ffmpeg rc={ss.returncode}, stderr={stderr}")
        if ss.returncode == 0 and os.path.exists(fp) and os.path.getsize(fp) > 0:
            return {"ep": ep, "status": "done"}
        return {"ep": ep, "status": "error", "msg": "ffmpeg_fail"}
    except Exception as e:
        log(f"EXCEPTION: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        return {"ep": ep, "status": "error", "msg": f"{type(e).__name__}:{str(e)[:60]}"}


def main():
    if len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    else:
        cfg = json.loads(sys.stdin.read())

    sid      = cfg["sid"]
    vids     = cfg["vids"]
    sname    = cfg["sname"]
    sdir     = cfg["sdir"]
    qual     = cfg.get("qual", "540p")
    did      = cfg.get("did", "11223344556677889900")
    iid      = cfg.get("iid", "99887766554433221100")
    ff       = cfg.get("ffmpeg", "")
    pf       = cfg.get("progress_file", os.path.join(sdir, "_progress.jsonl"))
    start_ep = cfg.get("start_ep", 1)
    eps_list = cfg.get("eps", None)  # 可选: 指定每集的实际编号（解决非连续选集）

    os.makedirs(sdir, exist_ok=True)
    ffmpeg = ff or find_ffmpeg()

    # 清理进度文件中未完成的条目（保留 done，清除 pending/downloading/all_done）
    if os.path.exists(pf):
        try:
            lines = []
            with open(pf, "r", encoding="utf-8") as f:
                for line in f:
                    j = json.loads(line.strip())
                    if j.get("status") == "done":
                        lines.append(json.dumps(j, ensure_ascii=False))
            with open(pf, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n" if lines else "")
        except: open(pf, "w").close()
    else:
        open(pf, "w").close()

    # 读取已完成集数
    done_set = set()
    if os.path.exists(pf):
        try:
            with open(pf, "r", encoding="utf-8") as f:
                for line in f:
                    j = json.loads(line.strip())
                    if j.get("status") == "done":
                        done_set.add(j.get("ep", 0))
        except: pass

    log_path = os.path.join(sdir, "_debug.log")
    try: open(log_path, "w").close()
    except: pass
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"=== batch_downloader START ===\n")
        f.write(f"sid={sid}, sdir={sdir}, qual={qual}, did={did[:8]}..\n")
        f.write(f"vids count={len(vids)}\n")
        f.write(f"ffmpeg={ffmpeg}\n")

    ok = 0; err = 0
    for i, vid in enumerate(vids):
        ep = eps_list[i] if eps_list else start_ep + i
        fp = os.path.join(sdir, f"{sname}_第{ep:02d}集.mp4")
        # 已在进度文件中标记完成 且 文件有效 → 跳过
        if ep in done_set and is_valid_video(fp, ffmpeg):
            ok += 1; continue
        # 文件有效但进度里没标记 → 补标记（不重复下载）
        if is_valid_video(fp, ffmpeg):
            write_progress(pf, {"sid": sid, "ep": ep, "status": "done"})
            ok += 1; continue
        try:
            r = download_one(sid, vid, ep, sdir, sname, did, iid, qual, ffmpeg)
        except Exception as e:
            r = {"ep": ep, "status": "error", "msg": f"{type(e).__name__}:{e}"}

        if r["status"] == "done":
            ok += 1
        else:
            err += 1

        r["sid"] = sid
        write_progress(pf, r)

    # 写结束标记
    write_progress(pf, {"sid": sid, "type": "all_done", "done": ok, "error": err})

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"=== END === done={ok} error={err}\n")


if __name__ == "__main__":
    main()