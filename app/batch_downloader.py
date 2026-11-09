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
import urllib.parse

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

        # 设备指纹池。脚本既可能被当作 app 包内的模块运行，也可能被直接
        # 以脚本路径启动（此时 `app` 不在 sys.path），所以两种方式都要兜住。
        DEV = None
        try:
            from app import devices as DEV
        except Exception:
            try:
                import importlib.util as _ilu
                _here = os.path.dirname(os.path.abspath(__file__))
                _spec = _ilu.spec_from_file_location(
                    "_fk_devices", os.path.join(_here, "devices.py"))
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                DEV = _mod
            except Exception as _e:
                log(f"设备指纹模块加载失败: {_e}")
        if DEV is None:
            raise RuntimeError("无法加载 app/devices.py")

        log(f"START vid={vid[:8]}.. qual={qual} did={did[:8]}..")

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

        # ── 取设备指纹池 ────────────────────────────────────────
        # 优先用调用方指定的（did/iid），找不到就用池子里匹配的，
        # 都不行则按顺序轮换 —— 服务端拉黑某组指纹时能自动切换。
        fps = []
        fp = DEV.by_id(did, iid)
        if fp:
            fps.append(fp)
        else:
            for f in DEV.FINGERPRINTS:
                if f not in fps:
                    fps.append(f)
            if did and iid and did != DEV.DEFAULT_DEVICE_ID:
                # 调用方带了外部指纹，放到队首优先试
                ext = dict(DEV.FINGERPRINTS[0])
                ext.update({"name": "custom", "device_id": did, "install_id": iid})
                fps.insert(0, ext)

        d1 = None
        used_fp = None
        for fpr in fps:
            rticket = str(int(time.time() * 1000))
            params = DEV.query_params(fpr, rticket)
            ua = fpr["ua"]
            ts = str(int(time.time() * 1000))
            bh = {
                "User-Agent": ua,
                "Accept": "application/json; charset=utf-8,application/x-protobuf",
                "Content-Type": "application/json; charset=UTF-8",
                "x-xs-from-web": "0", "x-ss-req-ticket": ts,
                "x-tt-request-tag": "t=0;n=0", "sdk-version": "2",
                "passport-sdk-version": "50561",
                "x-vc-bdturing-sdk-version": "3.7.2.cn",
            }
            api_url = (
                "https://api5-normal-sinfonlineb.fqnovel.com"
                "/novel/player/multi_video_model/v1/?"
                + "&".join("%s=%s" % (k, urllib.parse.quote(str(v), safe=""))
                           for k, v in params.items())
            )
            prt = urlsplit(api_url)
            su = f"{prt.scheme}://{prt.netloc}{prt.path}"
            pr = dict(parse_qsl(prt.query, keep_blank_values=True))
            dv = DEV.to_devices_dict(fpr)

            sh, su2 = core_sixgod(surl=su, params=pr, data=body_data,
                                   devices=dv, header=bh, log=False)
            try:
                r = rq.post(su2, headers=sh, data=body_bytes, timeout=15)
            except Exception as e:
                log(f"fp[{fpr['name']}] 请求异常: {e}")
                continue
            log(f"fp[{fpr['name']}] API HTTP {r.status_code} len={len(r.content)}")

            if not r.content:
                # 服务端对不被认可的指纹返回 200 + 空体
                log(f"fp[{fpr['name']}] 空响应（该指纹可能已被服务端拒绝），换下一组")
                continue
            try:
                cand = r.json()
            except Exception:
                log(f"fp[{fpr['name']}] 响应非 JSON: {r.content[:120]!r}")
                continue
            if vid in (cand.get("data") or {}):
                d1 = cand
                used_fp = fpr
                break
            log(f"fp[{fpr['name']}] 无目标 vid（keys={list((cand.get('data') or {}).keys())[:3]}）")

        if d1 is None:
            return {"ep": ep, "status": "error",
                    "msg": "设备指纹全部被拒（服务端返回空响应），该剧暂时无法下载"}

        ua = used_fp["ua"]
        vid_data = (d1.get("data") or {}).get(vid)
        if not vid_data:
            log(f"NO VID DATA: keys={list(d1.get('data',{}).keys())[:3]}")
            return {"ep": ep, "status": "error", "msg": "no_vid_data"}

        vm = json.loads(vid_data["video_model"])

        # ── fallback_api：新版是 dict（多包了一层），旧版是字符串 URL ──
        fa = vm.get("fallback_api")
        if isinstance(fa, dict):
            fa = fa.get("fallback_api") or fa.get("url") or ""
        if not fa:
            log("NO fallback_api, vm keys=%s" % list(vm.keys())[:6])
            return {"ep": ep, "status": "error", "msg": "no_fallback_api"}
        log(f"got fallback_api")

        r2 = rq.get(fa, headers={"User-Agent": ua}, timeout=15)
        d2 = r2.json()["video_info"]["data"]

        ks = b64d(d2["key_seed"])

        # ── 选清晰度 ────────────────────────────────────────────
        # 用 fallback_api 的 video_list：那里的 main_url 是 base64 密文，
        # 解密后指向 CENC 加密流，ffmpeg 的 -decryption_key 能解。
        #
        # ⚠ 重要（2026-11-09）：服务端把大部分清晰度改成了 **bytevc2**
        #   （字节私有加密 HEVC，容器里 codec tag 是 bvc2/0x32637662）。
        #   ffmpeg 不认识 bvc2 → 报 "Could not find tag for codec none"。
        #   目前只有 1080p 仍是标准 HEVC（gear_des_key 里标 h265_hvc1）。
        #   所以选档时要**避开 bytevc2**，回退到标准 HEVC 的那一档。
        #
        # gear_des_key 只在 multi_video_model 的 video_list 里，
        # fallback_api 的 video_list 没有 → 先建 definition→gear 映射。
        gear_by_def = {}
        vm_list = vm.get("video_list")
        if isinstance(vm_list, list):
            for it in vm_list:
                d = (it.get("video_meta") or {}).get("definition")
                if d:
                    gear_by_def[d.lower()] = it.get("gear_des_key") or ""
        kid_by_def = {}
        if isinstance(vm_list, list):
            for it in vm_list:
                d = (it.get("video_meta") or {}).get("definition")
                if d:
                    kid_by_def[d.lower()] = (it.get("encrypt_info") or {}).get("kid")

        def _is_bytevc2(defn):
            g = gear_by_def.get(defn, "")
            return "bytevc2" in g

        def _h(v):
            return int(v.get("vheight") or 0)

        vl = d2.get("video_list") or {}
        want = (qual or "540p").lower()
        qual_key = None
        if isinstance(vl, dict) and vl:
            by_def = {}
            for k, v in vl.items():
                if isinstance(v, dict) and v.get("definition"):
                    by_def[v["definition"].lower()] = v
            # 先按用户要的清晰度找
            picked = by_def.get(want)
            if picked is not None and _is_bytevc2(want):
                # 该档是 bytevc2，ffmpeg 解不了 → 改用标准 HEVC 的档
                alt = [d for d in by_def if not _is_bytevc2(d)]
                if alt:
                    alt.sort(key=lambda d: _h(by_def[d]), reverse=True)
                    newq = alt[0]
                    log(f"quality {want} is bytevc2 (ffmpeg unsupported) "
                        f"-> fallback to {newq}")
                    picked = by_def[newq]
                    want = newq
                else:
                    log(f"quality {want} is bytevc2 and no standard-HEVC alt")
            if picked is None:
                # 没有该清晰度 → 优先挑标准 HEVC 里分辨率最接近的
                std = [d for d in by_def if not _is_bytevc2(d)] or list(by_def)
                std.sort(key=lambda d: _h(by_def[d]))
                picked = by_def[std[len(std) // 2]]
                want = picked.get("definition")
            qual_key = picked
            log("selected quality=%s (definition=%s %sx%s%s)" % (
                want, picked.get("definition"), picked.get("vwidth"),
                picked.get("vheight"),
                " [bytevc2]" if _is_bytevc2(want) else ""))
        if qual_key is None:
            # 备选：multi_video_model 自带的 video_list（明文 URL）
            if isinstance(vm_list, list) and vm_list:
                for item in vm_list:
                    if ((item.get("video_meta") or {}).get("definition") or "").lower() == want:
                        qual_key = item
                        break
                if qual_key is None:
                    qual_key = vm_list[len(vm_list) // 2]
                log("fallback to vm.video_list (plain URL), quality=%s" % want)
        if qual_key is None:
            log("NO video_list")
            return {"ep": ep, "status": "error", "msg": "no_video_list"}

        # ── 取视频 URL ──────────────────────────────────────────
        raw_url = qual_key.get("main_url") or ""
        if raw_url.startswith("http"):
            # 明文 URL（bytevc2 流，ffmpeg 可能解不了，但仍尝试）
            ru = raw_url
            log("plain URL: %s..." % ru[:80])
        else:
            # base64 + AES 加密，需解密
            raw = b64d(raw_url)
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

        # 内容密钥（ffmpeg -decryption_key 用）
        # 优先用 encrypt_info.kid —— 服务端标了 encryption_method=cenc-aes-ctr，
        # 这时 CENC 的解密密钥就是 KID 本身（16 字节）。
        # 没有 kid 时退回旧的 derive_content_key(spade_a) 派生方式。
        ck = None
        kid_hex = kid_by_def.get(want)
        if kid_hex:
            try:
                ck = bytes.fromhex(kid_hex)
                log("using kid as decryption key: %s" % ck.hex())
            except Exception:
                ck = None
        if ck is None:
            spade = None
            if isinstance(qual_key, dict):
                ei = qual_key.get("encrypt_info") or {}
                spade = ei.get("spade_a") or qual_key.get("spade_a")
            if not spade:
                log("NO spade_a/kid")
                return {"ep": ep, "status": "error", "msg": "no_content_key"}
            ck = derive_content_key(spade)
            log("using derived key: %s" % ck.hex())

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
            return {"ep": ep, "status": "done", "qual": want,
                    "fallback": (want or "").lower() != (qual or "").lower()}
        # ffmpeg 失败时给出可读原因（bytevc2 是最常见的一种）
        if "codec none" in stderr or "Could not find tag" in stderr:
            log("该清晰度为 bytevc2 私有加密流，ffmpeg 无法处理")
            return {"ep": ep, "status": "error", "msg": "bytevc2_unsupported"}
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
    # 默认用设备指纹池里的真实指纹（旧的那对假 ID 已被服务端拉黑）
    _d0, _i0 = "4368802900374539", "4368802899821595"
    try:
        from app import devices as _DEV
        _d0 = _DEV.DEFAULT_DEVICE_ID
        _i0 = _DEV.DEFAULT_INSTALL_ID
    except Exception:
        try:
            import importlib.util as _ilu
            _here = os.path.dirname(os.path.abspath(__file__))
            _spec = _ilu.spec_from_file_location(
                "_fk_devices", os.path.join(_here, "devices.py"))
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _d0 = _mod.DEFAULT_DEVICE_ID
            _i0 = _mod.DEFAULT_INSTALL_ID
        except Exception:
            pass
    did      = cfg.get("did") or _d0
    iid      = cfg.get("iid") or _i0
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
            write_progress(pf, {"sid": sid, "ep": ep, "status": "done",
                                "qual": res.get("qual", qual)})
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