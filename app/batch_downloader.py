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
    """下载单集（参考实现 hongguo-downloader 的方式），返回结果字典

    流程：取流（HEVC 优先，排除 bytevc2）→ 下载加密 mp4 →
    derive_key(spade_a) → CENC-AES-CTR 解密 + moov 重建 → 标准 hvc1 mp4
    """
    log_path = os.path.join(sdir, "_debug.log")

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[ep{ep}] {msg}\n")
        except: pass

    tmp_path = None
    try:
        # ── 加载设备指纹与下载核心 ─────────────────────────────
        # 脚本既可能作为 app 包内模块运行，也可能被当脚本直接启动，
        # 两种导入方式都要兜住。
        DEV = None
        HG = None
        try:
            from app import devices as DEV
            from app import hongguo_dl as HG
        except Exception:
            try:
                import importlib.util as _ilu
                _here = os.path.dirname(os.path.abspath(__file__))
                for name, fname in (("_fk_devices", "devices.py"),
                                    ("_fk_hgdl", "hongguo_dl.py")):
                    _spec = _ilu.spec_from_file_location(
                        name, os.path.join(_here, fname))
                    _mod = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    if name == "_fk_devices":
                        DEV = _mod
                    else:
                        HG = _mod
            except Exception as _e:
                log(f"核心模块加载失败: {_e}")
        if DEV is None or HG is None:
            raise RuntimeError("无法加载 app/devices.py 或 app/hongguo_dl.py")

        log(f"START vid={vid[:8]}.. qual={qual}")

        # ── 选设备指纹 ──────────────────────────────────────────
        fp = DEV.by_id(did, iid) or DEV.FINGERPRINTS[0]
        if not fp.get("device_id"):
            raise RuntimeError("设备指纹无效")

        # ── 取流 ────────────────────────────────────────────────
        info = HG.fetch_play_url(vid, fp, content_type=None,
                                  codec_pref="hevc", log=log)
        url = info.get("url")
        spade_a = info.get("spade_a")
        codec = info.get("codec") or ""
        if not url:
            return {"ep": ep, "status": "error",
                    "msg": "未获取到有效播放地址（接口可能限流，请稍后重试）"}
        log(f"选中流: codec={codec} ct={info.get('content_type')} "
            f"dl={info.get('device_level')}")

        # ── 下载 ────────────────────────────────────────────────
        os.makedirs(sdir, exist_ok=True)
        fp_out = os.path.join(sdir, f"{sname}_第{ep:02d}集.mp4")
        tmp_path = fp_out + ".part"

        import time as _t
        last = [0.0, 0]

        def _prog(received, total):
            now = _t.time()
            if now - last[0] >= 2.0:
                last[0] = now
                last[1] = received
                try:
                    if total:
                        log(f"下载中 {received/1048576:.1f}/{total/1048576:.1f} MB")
                except Exception:
                    pass

        received, total = HG.download_stream(url, tmp_path, fp,
                                              log=log, progress=_prog)
        if received == 0:
            raise RuntimeError("下载内容为空，请重试")
        log(f"下载完成: {received/1048576:.2f} MB"
            + (f" / {total/1048576:.2f} MB" if total else ""))

        # ── CENC 解密 + moov 重建 ───────────────────────────────
        if spade_a:
            key = HG.derive_key(spade_a)
            if not key:
                raise RuntimeError("Key 派生失败（加密信息格式变更）")
            log(f"derive_key: {key.hex()}")
            HG.decrypt_mp4_file(tmp_path, fp_out, key, log=log)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        else:
            # 未加密流，直接改名
            try:
                os.replace(tmp_path, fp_out)
            except Exception:
                import shutil
                shutil.copyfile(tmp_path, fp_out)
                os.remove(tmp_path)

        if os.path.exists(fp_out) and os.path.getsize(fp_out) > 0:
            log(f"done: {fp_out} ({os.path.getsize(fp_out)/1048576:.2f} MB)")
            fam = HG.codec_family(codec)
            return {"ep": ep, "status": "done", "codec": codec,
                    "hevc": fam == "hevc"}
        raise RuntimeError("输出文件为空")
    except Exception as e:
        log(f"EXCEPTION: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return {"ep": ep, "status": "error",
                "msg": f"{type(e).__name__}:{str(e)[:60]}"}


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