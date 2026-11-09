"""水果刀 — 启动入口

命令行参数：
  无参数          正常启动图形界面
  <script.py>     工作进程模式：执行下载脚本（供 GUI 拉起子进程）
  --selftest      自检（不启动 GUI），搜索→解析→真实下载，结果写 selftest.log
  --guitest       走完整 GUI 流程（_start → 解析 → _launch_download），结果写 guitest.log
  --uishot <path> 启动真实主窗口并把界面截图存到 <path>
"""
import sys
import os

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _selftest():
    # 自检用设备指纹：真实注册过的（旧的假 ID 已被服务端拉黑）
    try:
        from app.devices import (DEFAULT_DEVICE_ID as _SELFTEST_DID,
                                 DEFAULT_INSTALL_ID as _SELFTEST_IID)
    except Exception:
        _SELFTEST_DID = "4368802900374539"
        _SELFTEST_IID = "4368802899821595"
    """冻结包自检：依赖、路径、搜索、解析、真实下载"""
    import json
    import traceback

    out = []
    _log_path = os.path.join(os.getcwd(), "selftest.log")

    def w(s):
        out.append(str(s))
        # 边跑边落盘：即使中途崩溃也不会丢日志
        try:
            with open(_log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(out))
        except Exception:
            pass

    try:
        from app.main_window import (
            BASE_DIR, BATCH_SCRIPT, SEARCH_INDEX, FFMPEG_DIR, APP_NAME,
        )
        w("APP_NAME     = %s" % APP_NAME)
        w("BASE_DIR     = %s" % BASE_DIR)
        w("BATCH_SCRIPT exists = %s" % os.path.isfile(BATCH_SCRIPT))
        w("SEARCH_INDEX exists = %s" % os.path.isfile(SEARCH_INDEX))
        w("ffmpeg exists= %s" % os.path.isdir(FFMPEG_DIR))
        w("frozen       = %s" % getattr(sys, "frozen", False))
    except Exception:
        w("IMPORT FAIL:\n" + traceback.format_exc())

    try:
        import glob
        pats = glob.glob(os.path.join(FFMPEG_DIR, "ffmpeg-N-*", "bin", "ffmpeg.exe")) + \
               glob.glob(os.path.join(FFMPEG_DIR, "bin", "ffmpeg.exe"))
        w("ffmpeg bin   = %s" % (pats[0] if pats else "NOT FOUND"))
    except Exception:
        w("ffmpeg probe failed")

    try:
        from app import search_index
        w("search_index import OK")
        res, pb = search_index.search("重生")
        w("search results = %d" % len(res))
        for r in res[:3]:
            w("   - %s | %s集" % (str(r.get("series_name"))[:24], r.get("episode_cnt")))
    except Exception:
        w("SEARCH FAIL:\n" + traceback.format_exc())

    # 真实下载链路
    try:
        import subprocess
        import time
        import tempfile
        vid = ""
        try:
            from app import search_index as _si
            from app.main_window import ParseThread
            from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer as _QT
            rr, _ = _si.search("重生后谁还嫁富二代")
            if rr:
                sid0 = str(rr[0]["series_id"])
                pres = {}          # 不要命名 out，会遮蔽日志列表
                pt = ParseThread(sid0)
                pt.result.connect(lambda i: pres.update(i))
                QCoreApplication.instance() or QCoreApplication([])
                loop = QEventLoop()
                _QT.singleShot(70000, loop.quit)
                pt.finished.connect(loop.quit)
                pt.start()
                loop.exec_()
                pt.wait(5000)
                vids = pres.get("vids") or []
                if vids:
                    vid = str(vids[0])
                w("parse: %s vids=%d" % (pres.get("name"), len(vids)))
        except Exception:
            w("parse skipped: %s" % traceback.format_exc()[:200])

        if vid:
            tmp = tempfile.mkdtemp(prefix="sgtest_")
            sdir = os.path.join(tmp, "T")
            os.makedirs(sdir, exist_ok=True)     # cwd 必须已存在，否则 WinError 267
            cfg = {
                "sid": "selftest", "vids": [vid], "sname": "T", "sdir": sdir,
                "qual": "540p", "did": _SELFTEST_DID,
                "iid": _SELFTEST_IID,
                "progress_file": os.path.join(sdir, "_progress.jsonl"),
                "start_ep": 1, "eps": [1],
            }
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [sys.executable, BATCH_SCRIPT, json.dumps(cfg, ensure_ascii=False)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                env=env, cwd=sdir,
            )
            t0 = time.time()
            while proc.poll() is None and time.time() - t0 < 180:
                time.sleep(1)
            w("worker rc   = %s (%.0fs)" % (proc.returncode, time.time() - t0))
            pf = cfg["progress_file"]
            if os.path.exists(pf):
                w("progress    = %s" % open(pf, encoding="utf-8").read()[:200].replace("\n", " | "))
            else:
                w("progress    = NO FILE")
            mp4s = [f for f in os.listdir(sdir) if f.endswith(".mp4")]
            for f in mp4s:
                w("MP4         = %s (%.2f MB)" % (f, os.path.getsize(os.path.join(sdir, f)) / 1048576.0))
            if not mp4s:
                w("MP4         = NONE  ← 下载失败")
            dl = os.path.join(sdir, "_debug.log")
            if os.path.exists(dl):
                w("debug tail  = %s" % open(dl, encoding="utf-8").read()[-400:].replace("\n", " | "))
        else:
            w("download test skipped: no vid")
    except Exception:
        w("DOWNLOAD FAIL:\n" + traceback.format_exc())

    print("SELFTEST WRITTEN", _log_path)


def _guitest():
    """在真实 GUI 里跑一遍：填链接 → 点开始 → 等解析 → 点下载 → 检查产出"""
    import traceback
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer

    log = []
    base = os.getcwd()
    outdir = os.path.join(base, "guitest_out")
    os.makedirs(outdir, exist_ok=True)

    def w(s):
        log.append(str(s))
        try:
            with open(os.path.join(base, "guitest.log"), "w", encoding="utf-8") as f:
                f.write("\n".join(log))
        except Exception:
            pass

    try:
        from app.main_window import MainWindow, APP_NAME
        app = QApplication(sys.argv)
        win = MainWindow(app_name=APP_NAME)
        win._sdir.setText(outdir)
        win.show()
        w("window shown, outdir=%s" % outdir)
        sid = "7689448937878473753"

        def phase1():
            try:
                win._url.setText(sid)
                w("calling _start() ...")
                win._start()
                w("tasks = %s" % list(win._tasks.keys()))
            except Exception:
                w(traceback.format_exc())
            QTimer.singleShot(80000, phase2)

        def phase2():
            try:
                t = win._tasks.get(sid)
                w("status=%s total=%s" % (t.status if t else None, t.total if t else 0))
                w("dl_btn visible = %s" % win._cards[sid]._dl_btn.isVisible())
                w("sdir field = %s" % win._sdir.text())
                w("task.sdir  = %s" % (t.sdir if t else None))
                w("calling _launch_download(sid, 1, 1) ...")
                win._launch_download(sid, 1, 1)
                w("status after launch = %s" % t.status)
                w("proc = %s" % t._proc)
                w("pf   = %s" % t._pf)
            except Exception:
                w(traceback.format_exc())
            QTimer.singleShot(90000, phase3)

        def phase3():
            try:
                t = win._tasks.get(sid)
                w("final status=%s pct=%s" % (t.status, t.pct))
                w("states[0:2] = %s" % t.states[:2])
                found = False
                for root, dirs, files in os.walk(outdir):
                    for f in files:
                        fp = os.path.join(root, f)
                        w("  %s  (%.2f MB)" % (fp, os.path.getsize(fp) / 1048576.0))
                        if f.endswith(".mp4"):
                            found = True
                w("MP4 in outdir = %s" % found)
                if t._pf and os.path.exists(t._pf):
                    w("progress: %s" % open(t._pf, encoding="utf-8").read()[:300])
            except Exception:
                w(traceback.format_exc())
            app.quit()

        QTimer.singleShot(600, phase1)
        app.exec_()
    except Exception:
        w(traceback.format_exc())
    w("GUI TEST DONE")
    print("GUITEST WRITTEN", os.path.join(base, "guitest.log"))


def _uishot(out_path):
    """启动真实主窗口并抓取自身渲染结果"""
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from app.main_window import MainWindow, APP_NAME

    app = QApplication(sys.argv)
    w = MainWindow(app_name=APP_NAME)
    w.show()

    def cap():
        try:
            w.grab().save(out_path)
            with open(out_path + ".txt", "w", encoding="utf-8") as f:
                f.write("OK title=%s" % w.windowTitle())
        except Exception:
            import traceback
            with open(out_path + ".txt", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        app.quit()

    QTimer.singleShot(2500, cap)
    app.exec_()


def _run_worker_script(script_path, cfg_json):
    """以「工作进程」模式执行下载脚本。

    打包成 exe 后 sys.executable 就是 GUI 程序自己，直接
    `Popen([exe, batch_downloader.py, cfg])` 不会再跑脚本、而是又开一个 GUI。
    这里显式识别「第一个参数是 .py 脚本」的情况，改为在当前进程里执行该脚本。
    """
    import runpy
    import traceback
    if os.path.isfile(script_path):
        import io
        old = sys.stdin
        sys.stdin = io.StringIO(cfg_json or "")
        sys.argv = [script_path]
        try:
            runpy.run_path(script_path, run_name="__main__")
        except SystemExit:
            pass
        except Exception:
            traceback.print_exc()
            sys.exit(1)
        finally:
            sys.stdin = old
    else:
        sys.stderr.write("worker script not found: %s\n" % script_path)
        sys.exit(2)


if __name__ == "__main__":
    argv = sys.argv[1:]

    # ① 工作进程模式：exe <script.py> <cfg-json>
    if argv and argv[0].lower().endswith(".py"):
        _run_worker_script(argv[0], argv[1] if len(argv) > 1 else "")
        sys.exit(0)

    if "--selftest" in argv:
        _selftest()
    elif "--guitest" in argv:
        _guitest()
    elif "--uishot" in argv:
        i = argv.index("--uishot")
        _uishot(argv[i + 1] if len(argv) > i + 1 else "uishot.png")
    else:
        from app.main_window import main
        main()
