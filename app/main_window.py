"""
app/main_window.py — 水果刀 v1.0
===========================================
- 苹果风 UI（SF 字体栈 / 层次化浅灰背景 / 胶囊按钮 / 圆角无边框窗口）
- 可展开任务卡片 — 点击展开查看每集状态网格
- 纯本地功能，功能全开放，无会员 / 等级限制
"""
from __future__ import annotations

import sys
import os
import json
import re
import time
import random
import codecs
import threading
import subprocess
import webbrowser
import urllib.request
import urllib.parse
from pathlib import Path
from collections import OrderedDict

_THIS = Path(__file__).resolve().parent
BASE_DIR = getattr(sys, "_MEIPASS", str(_THIS.parent))
BATCH_SCRIPT = os.path.join(BASE_DIR, "app", "batch_downloader.py")
SEARCH_INDEX = os.path.join(BASE_DIR, "app", "search_index.py")
VENV_PYTHON = sys.executable
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg_bin")
_HG = os.path.join(BASE_DIR, "hg_src")
if os.path.isdir(_HG):
    for _p in (_HG, os.path.join(_HG, "liushen")):
        if _p not in sys.path:
            sys.path.insert(0, _p)

# ─────────────────────────────  苹果风设计系统  ─────────────────────────────
# 颜色遵循 Apple Human Interface Guidelines 的浅色语义色
W_BG = "#F2F2F7"        # 窗口底色 — iOS/macOS 分组背景灰
W_CARD = "#FFFFFF"      # 卡片白
W_BDR = "#E3E3E8"      # 发丝描边
W_ACC = "#007AFF"      # Apple 蓝
W_GREEN = "#34C759"     # Apple 绿
W_RED = "#FF3B30"       # Apple 红
W_YELLOW = "#FF9500"    # Apple 橙
W_TXT = "#1C1C1E"       # 主文字
W_TXT2 = "#6E6E73"      # 次级文字
W_TXT3 = "#8E8E93"      # 三级文字
W_BG2 = "#FAFAFC"       # 卡片内浅底

FONT_UI = '"SF Pro Text","PingFang SC","Helvetica Neue","Microsoft YaHei UI","Segoe UI",sans-serif'

_DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_MOBILE_UA = "Mozilla/5.0 (Linux; Android 9; 2509FPN0BC) AppleWebKit/537.36"

# 设备指纹（真实注册过的；旧的假 ID 已被服务端拉黑，详见 app/devices.py）
try:
    from app.devices import DEFAULT_DEVICE_ID as _DEFAULT_DEVICE_ID
    from app.devices import DEFAULT_INSTALL_ID as _DEFAULT_INSTALL_ID
except Exception:
    _DEFAULT_DEVICE_ID = "4368802900374539"
    _DEFAULT_INSTALL_ID = "4368802899821595"

# 本地版：功能全开放，不设任何等级 / 集数限制

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QScrollArea, QCheckBox, QFileDialog, QMessageBox,
    QProgressBar, QComboBox, QSizePolicy, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QPoint, QRect
from PyQt5.QtGui import QFont, QColor, QIcon

from app.config import VERSION, APP_NAME


def parse_share_url(url):
    """
    解析红果分享链接，提取 series_id
    支持: https://novelquickapp.com/s/xxx/
    返回: series_id (纯数字字符串) 或空字符串
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _MOBILE_UA})
        resp = urllib.request.urlopen(req, timeout=10)
        final_url = resp.geturl()
        parsed = urllib.parse.urlparse(final_url)
        params = urllib.parse.parse_qs(parsed.query)

        zlink = params.get("zlink", [""])[0]
        if zlink:
            zparsed = urllib.parse.urlparse(zlink)
            zparams = urllib.parse.parse_qs(zparsed.query)
            scheme = zparams.get("schemeParams", [""])[0]
            if scheme:
                try:
                    scheme_data = json.loads(urllib.parse.unquote(scheme))
                    vid = str(scheme_data.get("vid", ""))
                    if vid.isdigit() and len(vid) >= 10:
                        return vid
                except Exception:
                    pass
            vid = zparams.get("vid", [""])[0]
            if vid and vid.isdigit() and len(vid) >= 10:
                return vid[:18]

        report = params.get("report_params", [""])[0]
        if report:
            try:
                report_data = json.loads(urllib.parse.unquote(report))
                cid = str(report_data.get("content_id", ""))
                if cid.isdigit() and len(cid) >= 10:
                    return cid
            except Exception:
                pass
    except Exception:
        return ""
    return ""


class DTask:
    """单个下载任务的全部状态"""

    __slots__ = (
        "sid", "name", "total", "vids", "sdir", "qual", "status", "pct",
        "states", "expanded", "_proc", "_pf", "cover", "_resume_after",
    )

    def __init__(self, sid):
        self.sid = sid
        self.name = ""
        self.total = 0
        self.vids = []
        self.sdir = ""
        self.qual = "540p"
        self.status = "pending"
        self.pct = 0
        self.states = []
        self.expanded = False
        self._proc = None
        self._pf = ""
        self.cover = ""
        self._resume_after = None


class ParseThread(QThread):
    """从红果页面 / 分享页抓取剧集 vid 列表"""

    result = pyqtSignal(dict)

    def __init__(self, sid, share_url=""):
        super().__init__()
        self.sid = sid
        self.share_url = share_url

    def _parse_ssr(self, html: str) -> dict:
        """从 novelquickapp SSR 页面提取剧集信息"""
        m = re.search(r'window\._ROUTER_DATA\s*=\s*', html)
        if not m:
            return {}
        start = m.end()
        while start < len(html) and html[start] in " \r\n\t":
            start += 1
        if start >= len(html) or html[start] != "{":
            return {}
        depth = 0
        end = -1
        for i in range(start, len(html)):
            ch = html[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return {}
        try:
            rd = json.loads(html[start:end + 1])
            pd = rd.get("loaderData", {}).get("video-animation-share_page", {}).get("pageData", {})
            sd = pd.get("series_data", {}) or {}
            cid = sd.get("chapter_ids", []) or []
            return {
                "series_name": sd.get("title", "") or "",
                "series_cover": sd.get("series_cover", "") or "",
                "vids": [str(c) for c in cid],
            }
        except (KeyError, TypeError, ValueError):
            return {}

    def run(self):
        try:
            url = "https://hongguoduanju.com/player/" + str(self.sid)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": _DESKTOP_UA, "Accept": "text/html,application/xhtml+xml"},
            )
            r = urllib.request.urlopen(req, timeout=15)
            t = r.read().decode("utf-8", "replace")

            vi = t.find("vid_list")
            if vi < 0:
                self.result.emit({"status": "error", "msg": "网页不含 vid_list"})
                return

            as_idx = t.find("[", vi)
            if as_idx < 0:
                self.result.emit({"status": "error", "msg": "vid解析失败"})
                return
            depth = 0
            end = -1
            limit = min(len(t), as_idx + 50000)
            for i in range(as_idx, limit):
                if t[i] == "[":
                    depth += 1
                elif t[i] == "]":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end < 0:
                self.result.emit({"status": "error", "msg": "vid解析失败"})
                return
            vids = json.loads(t[as_idx:end + 1])
            if not isinstance(vids, list) or not vids:
                self.result.emit({"status": "error", "msg": "vid解析失败"})
                return
            vids = [str(v) for v in vids][:200]

            name = ""
            m = re.search(r'series_name["\s:=]+([^"\',}]+)', t)
            if m:
                name = m.group(1).strip()
            name = name.replace("\\u002F", "/").replace("\\/", "/")

            cover = ""
            m2 = re.search(r'series_cover["\s:=]+([^"\',}]+)', t)
            if m2:
                cover = m2.group(1).strip().replace("\\u002F", "/").replace("\\/", "/")
                if cover.startswith("//"):
                    cover = "https:" + cover
                if not cover.startswith("http"):
                    cover = ""
            cover = cover.replace("640:0", "1280:0")
            try:
                if "\\u" in name:
                    name = codecs.decode(name.encode(), "unicode_escape")
            except Exception:
                pass

            if not name:
                name = "剧集 " + str(self.sid)

            # 分享页兜底：SSR 数据更完整时优先使用
            if self.share_url:
                try:
                    req2 = urllib.request.Request(
                        self.share_url, headers={"User-Agent": _MOBILE_UA, "Accept": "text/html"}
                    )
                    r2 = urllib.request.urlopen(req2, timeout=15)
                    html = r2.read().decode("utf-8", "replace")
                    info = self._parse_ssr(html)
                    if info.get("vids"):
                        vids = info["vids"]
                        if info.get("series_name"):
                            name = info["series_name"]
                        if info.get("series_cover"):
                            cover = info["series_cover"]
                except Exception:
                    pass

            self.result.emit({
                "status": "ready", "name": name, "vids": vids,
                "cover": cover, "total": len(vids),
            })
        except Exception as e:
            self.result.emit({"status": "error", "msg": f"{type(e).__name__}: {str(e)[:80]}"})


class TaskCard(QFrame):
    """单张任务卡片：折叠态显示摘要，展开态显示每集状态网格"""

    removed = pyqtSignal(str)
    open_folder = pyqtSignal(str)
    open_web = pyqtSignal(str)
    download_all = pyqtSignal(str)
    download_eps = pyqtSignal(str, list)
    retry_parse = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.t = task
        self._ep_btns = {}

        self.setObjectName("tc")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            #tc {{
                background: {W_CARD};
                border: 1px solid {W_BDR};
                border-radius: 14px;
            }}
            #tc:hover {{
                border-color: #C9C9D0;
                background: #FDFDFE;
            }}
        """)
        self.setMouseTracking(True)

        ly = QVBoxLayout(self)
        ly.setContentsMargins(18, 14, 18, 14)
        ly.setSpacing(10)

        # ── 标题行 ──
        r1 = QHBoxLayout()
        r1.setSpacing(8)

        self._tog = QPushButton("▸")
        self._tog.setFixedSize(20, 20)
        self._tog.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;font-size:11px;color:{W_TXT3};}}"
            f"QPushButton:hover{{color:{W_TXT};}}"
        )
        self._tog.setCursor(Qt.PointingHandCursor)
        self._tog.clicked.connect(self._toggle)
        r1.addWidget(self._tog)

        r1.addSpacing(2)

        self._nm = QLabel(self.t.name or "解析中…")
        self._nm.setStyleSheet(
            f"font-family:{FONT_UI};font-size:14px;font-weight:600;color:{W_TXT};"
            f"background:transparent;border:none;"
        )
        self._nm.setAttribute(Qt.WA_TranslucentBackground)
        self._nm.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        r1.addWidget(self._nm)

        self._st = QLabel()
        self._set_st()
        r1.addWidget(self._st)

        r1.addSpacing(10)

        self._cnt = QLabel(f"0/{self.t.total}")
        self._cnt.setStyleSheet(
            f"font-family:{FONT_UI};font-size:12px;color:{W_TXT2};background:transparent;border:none;"
        )
        self._cnt.setAttribute(Qt.WA_TranslucentBackground)
        r1.addWidget(self._cnt)

        r1.addSpacing(8)

        # 操作按钮组
        self._dl_btn = QPushButton("开始下载")
        self._dl_btn.setFixedHeight(26)
        self._dl_btn.setCursor(Qt.PointingHandCursor)
        self._dl_btn.setStyleSheet(
            f"QPushButton{{background:{W_GREEN};color:#fff;border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:600;padding:0 14px;}}"
            f"QPushButton:hover{{background:#2FB551;}}"
        )
        self._dl_btn.clicked.connect(lambda: self.download_all.emit(self.t.sid))

        self._retry_btn = QPushButton("重试")
        self._retry_btn.setFixedHeight(26)
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setStyleSheet(
            f"QPushButton{{background:{W_YELLOW};color:#fff;border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:600;padding:0 14px;}}"
            f"QPushButton:hover{{background:#E68600;}}"
        )
        self._retry_btn.clicked.connect(lambda: self.retry_parse.emit(self.t.sid))

        web = QPushButton("网页")
        web.setFixedHeight(26)
        web.setCursor(Qt.PointingHandCursor)
        web.setStyleSheet(
            f"QPushButton{{background:transparent;color:{W_ACC};border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:500;padding:0 10px;}}"
            f"QPushButton:hover{{background:#F0F7FF;}}"
        )
        web.clicked.connect(lambda: self.open_web.emit(self.t.sid))

        folder = QPushButton("文件夹")
        folder.setFixedHeight(26)
        folder.setCursor(Qt.PointingHandCursor)
        folder.setStyleSheet(
            f"QPushButton{{background:transparent;color:{W_ACC};border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:500;padding:0 10px;}}"
            f"QPushButton:hover{{background:#F0F7FF;}}"
        )
        folder.clicked.connect(lambda: self.open_folder.emit(self.t.sdir))

        rm = QPushButton("移除")
        rm.setFixedHeight(26)
        rm.setCursor(Qt.PointingHandCursor)
        rm.setStyleSheet(
            f"QPushButton{{background:transparent;color:{W_TXT3};border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:500;padding:0 10px;}}"
            f"QPushButton:hover{{background:#FFF0EF;color:{W_RED};}}"
        )
        rm.clicked.connect(lambda: self.removed.emit(self.t.sid))

        for b in (self._dl_btn, self._retry_btn, web, folder, rm):
            r1.addWidget(b)

        ly.addLayout(r1)

        # ── 进度条 ──
        self._bar = QProgressBar()
        self._bar.setFixedHeight(4)
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar{{background:#EFEFF4;border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{W_ACC};border-radius:2px;}}"
        )
        ly.addWidget(self._bar)

        # ── 集数网格（展开时填充） ──
        self._eg = QWidget()
        self._grid_ly = QVBoxLayout(self._eg)
        self._grid_ly.setContentsMargins(0, 2, 0, 0)
        self._grid_sa = QScrollArea()
        self._grid_sa.setWidgetResizable(True)
        self._grid_sa.setWidget(self._eg)
        self._grid_sa.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{width:6px;background:transparent;}}"
            f"QScrollBar::handle:vertical{{background:#D1D1D6;border-radius:3px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )
        self._grid_sa.hide()
        ly.addWidget(self._grid_sa)

        self.refresh()

    def _set_st(self):
        colors = {
            "pending": (W_TXT3, "等待"),
            "parsing": (W_YELLOW, "解析"),
            "downloading": (W_ACC, "下载"),
            "done": (W_GREEN, "完成"),
            "error": (W_RED, "失败"),
            "ready": (W_ACC, "就绪"),
            "paused": (W_TXT2, "暂停"),
        }
        c, lab = colors.get(self.t.status, (W_TXT3, ""))
        self._st.setText(lab)
        self._st.setStyleSheet(
            f"font-family:{FONT_UI};font-size:11px;font-weight:600;color:{c};"
            f"background:transparent;border:none;"
        )
        self._st.setAttribute(Qt.WA_TranslucentBackground)

    def _toggle(self):
        self.t.expanded = not self.t.expanded
        self._tog.setText("▾" if self.t.expanded else "▸")
        # 注意：布局里挂载的是 _grid_sa（滚动区），_eg 只是它的内容控件
        self._grid_sa.setVisible(self.t.expanded)
        if self.t.expanded:
            QTimer.singleShot(0, self._build_grid)

    def _build_grid(self):
        it = self._grid_ly.takeAt(0)
        while it:
            w = it.widget()
            if w:
                w.deleteLater()
            it = self._grid_ly.takeAt(0)

        self._ep_btns = {}
        pending = [s for s in self.t.states if s != "done"]
        if not pending:
            pending = list(range(1, min(self.t.total, 12) + 1))
        eps_sorted = sorted(set(pending))[:min(self.t.total, 100)]

        colors = {"done": W_GREEN, "downloading": W_ACC, "error": W_RED, "pending": W_TXT2}
        hover_colors = {"done": "#2FB551", "downloading": "#0062CC", "error": "#D93025", "pending": "#48484A"}
        txt_colors = {"done": "#fff", "downloading": "#fff", "error": "#fff", "pending": "#fff"}

        def _do_toggle_ep(ep):
            btn = self._ep_btns.get(ep)
            if btn is None:
                return
            s = self.t.states[ep - 1] if ep - 1 < len(self.t.states) else "pending"
            if s in ("done", "downloading"):
                QMessageBox.information(
                    self, "提示",
                    f"第 {ep} 集{s == 'done' and '已完成' or '正在下载'}，请先暂停后再切换。"
                )
                return
            sel = getattr(self, "_selected", False)
            btn.setStyleSheet(
                f"QPushButton{{background:{W_ACC};border:2px solid {W_ACC};border-radius:7px;"
                f"font-family:{FONT_UI};font-size:10px;font-weight:700;color:#fff;}}"
                if sel else
                f"QPushButton{{background:#EFEFF4;border:none;border-radius:7px;"
                f"font-family:{FONT_UI};font-size:10px;font-weight:600;color:{W_TXT2};}}"
                f"QPushButton:hover{{background:#E4E4EA;}}"
            )

        def toggle_ep(ep):
            btn = self._ep_btns.get(ep)
            if btn is None:
                return
            s = self.t.states[ep - 1] if ep - 1 < len(self.t.states) else "pending"
            if s == "downloading":
                return
            self._selected = not getattr(self, "_selected", False)
            if self._selected:
                self._pending_select = getattr(self, "_pending_select", [])
                if ep not in self._pending_select:
                    self._pending_select.append(ep)
                self.download_eps.emit(self.t.sid, list(self._pending_select))
            _do_toggle_ep(ep)

        rows = {}
        for i, s in enumerate(self.t.states):
            ep = i + 1
            if s == "done":
                continue
            rows[ep] = s

        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)

        cols = 10
        for n, ep in enumerate(sorted(rows.keys())):
            s = rows[ep]
            tc = QPushButton(f"{ep:02d}")
            tc.setFixedSize(42, 24)
            tc.setCursor(Qt.PointingHandCursor)
            tc.setStyleSheet(
                f"QPushButton{{background:{colors.get(s, W_TXT2)};border:none;border-radius:7px;"
                f"font-family:{FONT_UI};font-size:10px;font-weight:600;color:{txt_colors.get(s, '#fff')};}}"
                f"QPushButton:hover{{background:{hover_colors.get(s, W_TXT2)};}}"
            )
            tc.clicked.connect(lambda _c, e=ep: toggle_ep(e))
            self._ep_btns[ep] = tc
            row_l.addWidget(tc)
            if (n + 1) % cols == 0:
                row_l.addStretch()
                self._grid_ly.addWidget(row_w)
                row_w = QWidget()
                row_l = QHBoxLayout(row_w)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(4)
        row_l.addStretch()
        self._grid_ly.addWidget(row_w)

        h = min(max(28, ((len(rows) + cols - 1) // cols) * 28 + 8), 200)
        self._grid_sa.setMinimumHeight(h)
        self._grid_sa.setMaximumHeight(h)
        self._grid_sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def refresh(self):
        self._nm.setText(self.t.name or "解析中…")
        self._set_st()
        self._bar.setValue(self.t.pct)

        d = sum(1 for s in self.t.states if s == "done")
        e = sum(1 for s in self.t.states if s == "error")
        ready = self.t.status == "ready"
        error = self.t.status == "error"
        active = self.t.status == "downloading"
        paused = self.t.status == "paused"

        self._cnt.setText(f"{d}/{self.t.total}")
        self._dl_btn.setVisible(ready or paused or active)
        self._retry_btn.setVisible(error)

        if active:
            self._dl_btn.setText("暂停下载")
            self._dl_btn.setStyleSheet(
                f"QPushButton{{background:{W_YELLOW};color:#fff;border:none;border-radius:8px;"
                f"font-family:{FONT_UI};font-size:12px;font-weight:600;padding:0 14px;}}"
                f"QPushButton:hover{{background:#E68600;}}"
            )
        else:
            self._dl_btn.setText("开始下载")
            self._dl_btn.setStyleSheet(
                f"QPushButton{{background:{W_GREEN};color:#fff;border:none;border-radius:8px;"
                f"font-family:{FONT_UI};font-size:12px;font-weight:600;padding:0 14px;}}"
                f"QPushButton:hover{{background:#2FB551;}}"
            )

        if self.t.expanded:
            QTimer.singleShot(0, self._build_grid)

    def mousePressEvent(self, e):
        """卡片空白处点击 → 切换展开/收起。

        注意：不要在这里调 super().mousePressEvent(e)，
        那会把按下事件重新派发给子控件，导致「开始下载」等按钮被连点两次
        （第一次启动下载、第二次立刻暂停），表现为点了没反应 / 状态卡在暂停。
        """
        if e.button() == Qt.LeftButton and not self._eg.isVisible():
            local = self.mapFromGlobal(e.globalPos())
            if self.rect().contains(local):
                self._toggle()
                e.accept()
                return
        e.ignore()


class MainWindow(QWidget):
    """主窗口：左侧搜索/输入面板 + 右侧任务卡片区"""

    W, H = 1200, 800
    SHADOW_MARGIN = 18

    _search_done = pyqtSignal(str, list, str)     # mode, results, passback
    _share_resolved = pyqtSignal(str, str)        # sid, share_url
    _ads_loaded = pyqtSignal(list)

    def __init__(self, app_name="水果刀", parent=None, icon_path=None):
        super().__init__(parent)
        self._app_name = app_name

        self.setWindowTitle(app_name)
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(str(icon_path)))

        self.resize(self.W, self.H)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 可调整大小：限制最小尺寸，避免界面被压坏
        self.setMinimumSize(940, 620)
        self._normal_size = self.size()
        self._maximized = False

        self._drag = False
        self._drag_pos = None
        # 边缘拖拽缩放
        self._resize_edge = None
        self._resize_start = None
        self._resize_geo = None
        # 事件过滤器可能在 _init_ui 期间触发，先占位避免 AttributeError
        self._root = None
        self._hdr = None
        self.setMouseTracking(True)

        self._tasks = OrderedDict()
        self._cards = {}
        self._parsers = {}

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(1000)

        self._init_ui()
        self._center()
        self._add_shadow()

        self.started = self._search_done.connect(self._show_results)
        self._share_resolved.connect(self._start_with_sid)

    def closeEvent(self, e):
        self._poll_timer.stop()
        for p in list(self._parsers.values()):
            try:
                if p.isRunning():
                    p.quit()
                    p.wait(2000)
            except Exception:
                pass
        for t in self._tasks.values():
            if t._proc:
                try:
                    t._proc.poll()
                    t._proc.kill()
                except Exception:
                    pass
        super().closeEvent(e)

    # ─────────────────────────── UI 构建 ───────────────────────────
    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            self.SHADOW_MARGIN, self.SHADOW_MARGIN,
            self.SHADOW_MARGIN, self.SHADOW_MARGIN
        )
        outer.setSpacing(0)

        self._root = QWidget()
        self._root.setObjectName("root")
        self._root.setStyleSheet(f"""
            #root {{
                background: {W_BG};
                border: 1px solid #D8D8DE;
                border-radius: 16px;
            }}
        """)
        self._root.installEventFilter(self)
        self._root.setMouseTracking(True)
        outer.addWidget(self._root)

        self._main_layout = QVBoxLayout(self._root)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # ══ 标题栏 ══
        hdr = QFrame()
        hdr.setObjectName("hdr")
        hdr.setFixedHeight(52)
        hdr.setMouseTracking(True)
        hdr.setStyleSheet(
            f"QFrame#hdr{{background:{W_CARD};border:none;border-bottom:1px solid {W_BDR};"
            f"border-top-left-radius:16px;border-top-right-radius:16px;}}"
        )
        # 先赋值再装过滤器：eventFilter 里会访问 self._hdr
        self._hdr = hdr
        hdr.installEventFilter(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(18, 8, 16, 8)
        hl.setSpacing(10)

        self._logo = QLabel("◉")
        self._logo.setStyleSheet(
            f"font-family:{FONT_UI};font-size:15px;color:{W_ACC};"
            f"background:transparent;border:none;"
        )
        self._logo.setAttribute(Qt.WA_TranslucentBackground)
        hl.addWidget(self._logo)

        self._title_lbl = QLabel(self._app_name)
        self._title_lbl.setStyleSheet(
            f"font-family:{FONT_UI};font-size:14px;font-weight:600;color:{W_TXT};"
            f"background:transparent;border:none;"
        )
        self._title_lbl.setAttribute(Qt.WA_TranslucentBackground)
        hl.addWidget(self._title_lbl)

        ver = QLabel(VERSION)
        ver.setStyleSheet(
            f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};"
            f"background:transparent;border:none;"
        )
        ver.setAttribute(Qt.WA_TranslucentBackground)
        hl.addWidget(ver)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedSize(1, 16)
        sep.setStyleSheet(f"background:{W_BDR};border:none;")
        hl.addWidget(sep)

        hl.addStretch()

        # 状态徽章
        self._badge = QLabel("● 就绪")
        self._badge.setStyleSheet(
            f"font-family:{FONT_UI};font-size:11px;font-weight:600;color:{W_GREEN};"
            f"background:#E8F8ED;border-radius:8px;padding:3px 10px;"
        )
        self._badge.setAttribute(Qt.WA_TranslucentBackground)
        hl.addWidget(self._badge)

        self._footer = QLabel("粘贴链接后点击「开始下载」")
        self._footer.setStyleSheet(
            f"font-family:{FONT_UI};font-size:12px;color:{W_TXT2};"
            f"background:transparent;border:none;"
        )
        self._footer.setAttribute(Qt.WA_TranslucentBackground)
        hl.addWidget(self._footer)

        hl.addSpacing(12)

        # 窗口控制按钮（靠右，符合 Windows 习惯）
        def _wbtn(text, tip, color, cb):
            b = QPushButton(text)
            b.setFixedSize(30, 26)
            b.setCursor(Qt.ArrowCursor)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;border-radius:7px;"
                f"color:{color};font-size:15px;font-weight:500;}}"
                f"QPushButton:hover{{background:{color};color:#fff;}}"
            )
            b.clicked.connect(cb)
            return b

        self._btn_min = _wbtn("—", "最小化", W_TXT2, self.showMinimized)
        self._btn_max = _wbtn("☐", "最大化", W_TXT2, self.toggle_maximize)
        self._btn_close = _wbtn("✕", "关闭", W_TXT2, self.close)
        hl.addWidget(self._btn_min)
        hl.addSpacing(2)
        hl.addWidget(self._btn_max)
        hl.addSpacing(2)
        hl.addWidget(self._btn_close)

        self._main_layout.addWidget(hdr)

        # ══ 主体 ══
        body = QWidget()
        body.setStyleSheet(f"background:{W_BG};border:none;")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ── 左侧面板 ──
        # 左侧面板整体放进滚动区：窗口变矮时内容纵向滚动，
        # 而不是把下方控件挤出可视区造成重叠
        left_host = QWidget()
        left_host.setStyleSheet(f"background:{W_BG2};border:none;")
        left = QWidget()
        left.setStyleSheet("background:transparent;border:none;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(24, 24, 24, 20)
        ll.setSpacing(16)

        left_wrap = QScrollArea()
        left_wrap.setWidgetResizable(True)
        left_wrap.setWidget(left)
        left_wrap.setFixedWidth(380)
        left_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_wrap.setStyleSheet(
            f"QScrollArea{{background:{W_BG2};border:none;border-right:1px solid {W_BDR};}}"
            f"QScrollBar:vertical{{width:6px;background:transparent;}}"
            f"QScrollBar::handle:vertical{{background:#D1D1D6;border-radius:3px;}}"
        )

        def sec(t):
            l = QLabel(t)
            l.setStyleSheet(
                f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};font-weight:600;"
                f"background:transparent;border:none;padding:0;"
            )
            l.setAttribute(Qt.WA_TranslucentBackground)
            return l

        def mk_input(ph, w=None):
            w = QLineEdit()
            w.setFixedHeight(38)
            w.setPlaceholderText(ph)
            w.setStyleSheet(
                f"QLineEdit{{background:{W_CARD};border:1px solid {W_BDR};border-radius:10px;"
                f"padding:0 14px;font-family:{FONT_UI};font-size:13px;color:{W_TXT};}}"
                f"QLineEdit:focus{{border-color:{W_ACC};}}"
                f"QLineEdit::placeholder{{color:{W_TXT3};}}"
            )
            return w

        def mk_combo(items, idx=0):
            w = QComboBox()
            w.addItems(items)
            w.setCurrentIndex(idx)
            w.setFixedHeight(38)
            w.setStyleSheet(
                f"QComboBox{{background:{W_CARD};border:1px solid {W_BDR};border-radius:10px;"
                f"padding:0 12px;font-family:{FONT_UI};font-size:13px;color:{W_TXT};}}"
                f"QComboBox:hover{{border-color:#C9C9D0;}}"
                f"QComboBox::drop-down{{border:none;width:28px;}}"
                f"QComboBox QAbstractItemView{{background:{W_CARD};color:{W_TXT};"
                f"selection-background-color:{W_ACC};selection-color:#fff;outline:0;padding:4px;}}"
                f"QComboBox QAbstractItemView::item{{padding:6px 12px;color:{W_TXT};}}"
            )
            return w

        # 搜索区
        ll.addWidget(sec("搜索短剧"))
        self._search_input = mk_input("输入剧名、主演或题材")
        self._search_input.returnPressed.connect(self._on_search_triggered)
        ll.addWidget(self._search_input)

        self._search_hint = QLabel("支持短剧与漫剧，按热度排序")
        self._search_hint.setStyleSheet(
            f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};"
            f"background:transparent;border:none;"
        )
        self._search_hint.setAttribute(Qt.WA_TranslucentBackground)
        ll.addWidget(self._search_hint)

        self._search_list = QListWidget()
        self._search_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._search_list.setStyleSheet(
            f"QListWidget{{background:{W_CARD};border:1px solid {W_BDR};border-radius:12px;"
            f"font-family:{FONT_UI};font-size:12px;color:{W_TXT};outline:0;}}"
            f"QListWidget::item{{border-bottom:1px solid #F0F0F4;}}"
            f"QListWidget::item:hover{{background:#F5F9FF;}}"
            f"QListWidget::item:selected{{background:#EAF3FF;color:{W_TXT};}}"
        )
        self._search_list.itemClicked.connect(self._on_search_selected)
        self._search_list.hide()
        # 注意：列表交给 _search_scroll.setWidget() 托管，
        # 不要再 ll.addWidget(self._search_list) —— 同一控件被两个容器管理会导致溢出重叠

        self._search_scroll = QScrollArea()
        self._search_scroll.setWidgetResizable(True)
        self._search_scroll.setWidget(self._search_list)
        self._search_scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{width:6px;background:transparent;}}"
            f"QScrollBar::handle:vertical{{background:#D1D1D6;border-radius:3px;}}"
        )
        self._search_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._search_scroll.hide()
        ll.addWidget(self._search_scroll)
        self._search_scroll_max = 380

        self._more_btn = QPushButton("加载更多")
        self._more_btn.setFixedHeight(30)
        self._more_btn.setCursor(Qt.PointingHandCursor)
        self._more_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{W_ACC};border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:600;padding:0 12px;}}"
            f"QPushButton:hover{{background:#EAF3FF;}}"
            f"QPushButton:disabled{{color:{W_TXT3};}}"
        )
        self._more_btn.clicked.connect(self._on_load_more)
        self._more_btn.hide()
        ll.addWidget(self._more_btn)

        # 链接区
        ll.addSpacing(4)
        ll.addWidget(sec("分享链接"))
        self._url = mk_input("粘贴红果分享链接或 series_id")
        self._url.setToolTip("支持 https://novelquickapp.com/s/xxx/ 分享链接")
        ll.addWidget(self._url)

        # 保存目录
        ll.addWidget(sec("保存位置"))
        sb = QHBoxLayout()
        sb.setSpacing(8)
        self._sdir = QLineEdit(str(Path.home() / "Downloads"))
        self._sdir.setFixedHeight(38)
        self._sdir.setReadOnly(True)
        self._sdir.setStyleSheet(
            f"QLineEdit{{background:{W_CARD};border:1px solid {W_BDR};border-radius:10px;"
            f"padding:0 14px;font-family:{FONT_UI};font-size:12px;color:{W_TXT2};}}"
        )
        sb.addWidget(self._sdir)

        bx = QPushButton("选择")
        bx.setFixedSize(62, 38)
        bx.setCursor(Qt.PointingHandCursor)
        bx.setStyleSheet(
            f"QPushButton{{background:{W_CARD};border:1px solid {W_BDR};border-radius:10px;"
            f"font-family:{FONT_UI};font-size:12px;font-weight:500;color:{W_TXT};}}"
            f"QPushButton:hover{{background:#F7F7FA;}}"
        )
        bx.clicked.connect(
            lambda: self._sdir.setText(
                QFileDialog.getExistingDirectory(self, "选择目录")
            )
        )
        sb.addWidget(bx)
        ll.addLayout(sb)

        # 清晰度
        ll.addWidget(sec("清晰度"))
        self._qual = mk_combo(["360p", "480p", "540p", "720p", "1080p"], 2)
        ll.addWidget(self._qual)

        # 主按钮
        ll.addSpacing(2)
        self._start_btn = QPushButton("开始下载")
        self._start_btn.setFixedHeight(44)
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setStyleSheet(
            f"QPushButton{{background:{W_ACC};color:#fff;border:none;border-radius:12px;"
            f"font-family:{FONT_UI};font-size:14px;font-weight:600;letter-spacing:2px;}}"
            f"QPushButton:hover{{background:#3D9BFF;}}"
            f"QPushButton:pressed{{background:#0062CC;}}"
        )
        self._start_btn.clicked.connect(self._start)
        ll.addWidget(self._start_btn)

        self._auto_dl = QCheckBox("解析完成后自动开始下载")
        self._auto_dl.setChecked(False)
        self._auto_dl.setStyleSheet(
            f"QCheckBox{{font-family:{FONT_UI};font-size:11px;color:{W_TXT2};"
            f"background:transparent;border:none;spacing:6px;}}"
            f"QCheckBox::indicator{{width:16px;height:16px;border-radius:8px;"
            f"background:#D1D1D6;border:none;}}"
            f"QCheckBox::indicator:checked{{background:{W_ACC};}}"
        )
        ll.addWidget(self._auto_dl)

        ll.addStretch()

        self._clear_btn = QPushButton("清空全部任务")
        self._clear_btn.setFixedHeight(30)
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{W_TXT3};border:none;border-radius:8px;"
            f"font-family:{FONT_UI};font-size:11px;padding:0;}}"
            f"QPushButton:hover{{color:{W_RED};}}"
        )
        self._clear_btn.clicked.connect(self._clear)
        ll.addWidget(self._clear_btn)

        bl.addWidget(left_wrap)

        # ── 右侧任务区 ──
        rt = QWidget()
        rt.setStyleSheet(f"background:{W_BG};border:none;")
        rl = QVBoxLayout(rt)
        rl.setContentsMargins(26, 22, 26, 16)
        rl.setSpacing(12)

        rh = QHBoxLayout()
        ttl = QLabel("下载任务")
        ttl.setStyleSheet(
            f"font-family:{FONT_UI};font-size:18px;font-weight:700;color:{W_TXT};"
            f"background:transparent;border:none;"
        )
        ttl.setAttribute(Qt.WA_TranslucentBackground)
        rh.addWidget(ttl)
        rh.addStretch()
        self._stats = QLabel("")
        self._stats.setStyleSheet(
            f"font-family:{FONT_UI};font-size:12px;color:{W_TXT3};"
            f"background:transparent;border:none;"
        )
        self._stats.setAttribute(Qt.WA_TranslucentBackground)
        rh.addWidget(self._stats)
        rl.addLayout(rh)

        self._sw_ly = QVBoxLayout()
        self._sw_ly.setSpacing(12)

        self._empty_lbl = QLabel("还没有任务\n\n在上方搜索短剧，或直接粘贴分享链接")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"font-family:{FONT_UI};font-size:13px;color:{W_TXT3};line-height:180%;"
            f"background:transparent;border:none;"
        )
        self._empty_lbl.setAttribute(Qt.WA_TranslucentBackground)
        self._sw_ly.addWidget(self._empty_lbl)
        self._sw_ly.addStretch()

        holder = QWidget()
        holder.setLayout(self._sw_ly)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(holder)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{width:8px;background:transparent;}}"
            f"QScrollBar::handle:vertical{{background:#D8D8DE;border-radius:4px;}}"
            f"QScrollBar::handle:vertical:hover{{background:#B8B8C0;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )
        rl.addWidget(self._scroll)

        bl.addWidget(rt, 1)
        self._main_layout.addWidget(body)

    def _add_shadow(self):
        """柔和投影，贴合 macOS 窗口观感"""
        from PyQt5.QtWidgets import QGraphicsDropShadowEffect
        s = QGraphicsDropShadowEffect()
        s.setBlurRadius(48)
        s.setOffset(0, 12)
        s.setColor(QColor(0, 0, 0, 55))
        self._root.setGraphicsEffect(s)

    def _center(self):
        g = QApplication.primaryScreen().availableGeometry()
        self.move(
            (g.width() - self.width()) // 2,
            (g.height() - self.height()) // 2 - 20
        )

    def _network_time(self):
        """获取网络时间 (超时 2 秒, 失败则回退到本地时间)"""
        import ssl
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request("https://www.baidu.com", headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=2, context=ctx)
            date_str = r.headers.get("Date", "")
            if date_str:
                from email.utils import parsedate_to_datetime
                return int(parsedate_to_datetime(date_str).timestamp())
        except Exception:
            pass
        return int(time.time())

    # ─────────────────────────── 解析与启动 ───────────────────────────
    def _start(self):
        txt = self._url.text().strip()
        if not txt:
            QMessageBox.warning(self, "提示", "请先粘贴分享链接或输入 series_id")
            return

        share_m = re.search(r'https?://novelquickapp\.com/[a-zA-Z0-9_\-./?=&%#@!+*~]+', txt)
        if share_m:
            share_url = share_m.group(0)
            self._footer.setText("正在解析分享链接…")
            self._share_link = share_url
            threading.Thread(
                target=self._resolve_share, args=(share_url,), daemon=True
            ).start()
        else:
            self._share_link = ""
            self._start_with_sid(txt, "")

    def _resolve_share(self, share_url):
        """后台解析分享链接 → 拿到 series_id 后回到主线程建任务。

        短链（novelquickapp.com/s/xxx）本身不含数字 ID，
        必须请求一次让服务端跳转，真实页面里才有 series_id。
        """
        sid = ""
        try:
            from app.search_index import parse_share_url
            sid = parse_share_url(share_url) or ""
        except Exception:
            sid = ""
        if not sid:
            m = re.search(r'(\d{10,20})', share_url)
            sid = m.group(1) if m else ""
        self._share_resolved.emit(sid, share_url)

    def _start_with_sid(self, txt, share_url=""):
        m = re.search(r'(?:series_id[=?/]|^)0*(\d{10,20})', txt)
        sid = ""
        if m:
            sid = m.group(1).lstrip("0") or "0"
        else:
            m2 = re.search(r'/(\d{10,20})', txt)
            if m2:
                sid = m2.group(1)

        if not sid.isdigit() or len(sid) < 10:
            QMessageBox.warning(self, "错误", "无法提取 series_id")
            return

        if sid in self._tasks:
            QMessageBox.information(self, "提示", "该剧已在队列中")
            return

        task = DTask(sid)
        task.sdir = self._sdir.text().strip() or os.getcwd()
        task.qual = self._qual.currentText().split()[0]
        task.status = "parsing"
        self._tasks[sid] = task

        card = TaskCard(task)
        card.removed.connect(self._rm)
        card.open_folder.connect(self._open_folder)
        card.open_web.connect(self._open_web)
        card.download_all.connect(self._on_download_all)
        card.download_eps.connect(self._on_download_eps)
        card.retry_parse.connect(self._on_retry_parse)
        self._cards[sid] = card

        self._sw_ly.insertWidget(self._sw_ly.count() - 1, card)
        if self._empty_lbl.isVisible():
            self._empty_lbl.hide()
        self._scroll.show()

        parser = ParseThread(sid, share_url or getattr(self, "_share_link", ""))
        parser.result.connect(lambda info: self._parsed(sid, info))
        parser.finished.connect(lambda s=sid: self._parsers.pop(s, None))
        self._parsers[sid] = parser
        parser.start()

        self._url.clear()
        self._url.setFocus()
        self._update_stats()

    def _parsed(self, sid, info):
        task = self._tasks.get(sid)
        if not task:
            return

        if info.get("status") == "error":
            task.status = "error"
            if sid in self._cards:
                self._cards[sid].refresh()
            self._footer.setText(f"✕ {info.get('msg', '解析失败')}")
            self._update_stats()
            return

        task.name = info.get("name", "")
        task.total = info.get("total", 0) or len(info.get("vids", []))
        task.vids = info.get("vids", [])
        task.cover = info.get("cover", "")
        task.states = ["pending"] * task.total
        task.status = "ready"

        if hasattr(task, "_pf") and task._pf:
            try:
                os.remove(task._pf)
            except Exception:
                pass
        task._pf = ""

        if sid in self._cards:
            self._cards[sid].refresh()
        self._footer.setText(f"✅ {task.name} ({task.total} 集) — 点击下载")
        self._update_stats()

        if self._auto_dl.isChecked() and task.vids:
            self._launch_download(sid, 1, task.total)

    def _launch_download(self, sid, start_ep=1, count=0):
        task = self._tasks.get(sid)
        if not task or not task.vids or task.status == "downloading":
            return

        safe = re.sub(r'[<>:"/\\|?*]', "_", (task.name or "").strip())[:15]
        sdir = os.path.join(task.sdir, safe)
        try:
            os.makedirs(sdir, exist_ok=True)
        except Exception:
            pass

        if task.cover:
            try:
                req = urllib.request.Request(task.cover, headers={"User-Agent": _DESKTOP_UA})
                r = urllib.request.urlopen(req, timeout=10)
                cover_data = r.read()
                ext = ".jpg"
                cl = task.cover.lower()
                if cl.endswith(".png"):
                    ext = ".png"
                elif cl.endswith(".webp"):
                    ext = ".webp"
                with open(os.path.join(sdir, "cover" + ext), "wb") as f:
                    f.write(cover_data)
            except Exception:
                pass

        end_ep = start_ep + count - 1 if count else task.total
        eps = list(range(start_ep, min(end_ep, task.total) + 1))
        if not eps:
            return

        vids = []
        for e in eps:
            if e - 1 < len(task.vids):
                vids.append(task.vids[e - 1])
        if not vids:
            return

        task._pf = os.path.join(sdir, "_progress.jsonl")
        with open(os.path.join(sdir, "info.json"), "w", encoding="utf-8") as f:
            json.dump({"name": task.name, "sid": sid, "total": task.total}, f)

        did = os.getenv("DUANJU_DEVICE_ID", _DEFAULT_DEVICE_ID)
        iid = os.getenv("DUANJU_INSTALL_ID", _DEFAULT_INSTALL_ID)
        cfg = {
            "sid": sid, "vids": vids, "sname": safe, "sdir": sdir,
            "qual": task.qual, "did": did, "iid": iid,
            "progress_file": task._pf, "start_ep": eps[0], "eps": eps,
        }
        task.status = "downloading"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            os.makedirs(sdir, exist_ok=True)      # cwd 必须存在，否则 WinError 267
        except Exception:
            pass
        try:
            task._proc = subprocess.Popen(
                [VENV_PYTHON, BATCH_SCRIPT, json.dumps(cfg, ensure_ascii=False)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                env=env, cwd=sdir,
            )
        except Exception as e:
            task.status = "error"
            self._footer.setText(f"✕ 启动失败: {e}")
            if sid in self._cards:
                self._cards[sid].refresh()
            return

        if sid in self._cards:
            self._cards[sid].refresh()
        self._footer.setText(f"⬇ {task.name} 下载中…")
        self._update_stats()

    def _on_download_eps(self, sid, eps):
        """下载选中的集数。下载中时切到指定集，完成后恢复；暂停时只下选中，完成后保持暂停"""
        task = self._tasks.get(sid)
        if not task or not task.vids:
            return

        was_downloading = False
        if task.status == "downloading":
            was_downloading = True
            try:
                task._proc.poll()
                task._proc.kill()
            except Exception:
                pass

        eps = sorted(set(int(e) for e in eps if 1 <= int(e) <= task.total))
        if not eps:
            return

        # 清理进度文件里未完成的条目
        lines = []
        if os.path.exists(task._pf):
            try:
                with open(task._pf, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            j = json.loads(line.strip())
                        except Exception:
                            continue
                        if j.get("status") == "done":
                            lines.append(json.dumps(j, ensure_ascii=False))
                with open(task._pf, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n" if lines else "")
            except Exception:
                with open(task._pf, "w", encoding="utf-8"):
                    pass

        for ep in eps:
            if ep - 1 < len(task.states):
                task.states[ep - 1] = "pending"

        safe = re.sub(r'[<>:"/\\|?*]', "_", (task.name or "").strip())[:15]
        sdir = os.path.join(task.sdir, safe)
        # 进度文件路径必须在这里自己算：直接从「开始下载」按钮进来时
        # task._pf 还是空串，会让子进程 open('','w') 抛 FileNotFoundError。
        pf = task._pf or os.path.join(sdir, "_progress.jsonl")
        task._pf = pf

        vids = [task.vids[e - 1] for e in eps if e - 1 < len(task.vids)]
        if not vids:
            return

        did = os.getenv("DUANJU_DEVICE_ID", _DEFAULT_DEVICE_ID)
        iid = os.getenv("DUANJU_INSTALL_ID", _DEFAULT_INSTALL_ID)
        cfg = {
            "sid": sid, "vids": vids, "sname": safe,
            "sdir": sdir, "qual": task.qual,
            "did": did, "iid": iid, "progress_file": pf,
            "start_ep": eps[0], "eps": eps,
        }
        task.status = "downloading"
        task._resume_after = "downloading" if was_downloading else "paused"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            os.makedirs(cfg["sdir"], exist_ok=True)
        except Exception:
            pass
        err_log = os.path.join(cfg["sdir"], "_batch_debug.log")
        try:
            task._proc = subprocess.Popen(
                [VENV_PYTHON, BATCH_SCRIPT, json.dumps(cfg, ensure_ascii=False)],
                stdout=open(err_log, "a"), stderr=subprocess.STDOUT,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                env=env, cwd=cfg["sdir"],
            )
        except Exception as e:
            task.status = "error"
            self._footer.setText(f"✕ 启动失败: {e}")

        if sid in self._cards:
            self._cards[sid].refresh()
        self._footer.setText(f"⬇ {task.name} 下载 {len(eps)} 集中…")
        self._update_stats()

    def _on_download_ep(self, sid, ep):
        """下载指定单集"""
        task = self._tasks.get(sid)
        if not task or not task.vids or task.status == "downloading":
            return

        ep = int(ep)
        if ep < 1 or ep > task.total or ep > len(task.vids):
            return

        safe = re.sub(r'[<>:"/\\|?*]', "_", (task.name or "").strip())[:15]
        vid = task.vids[ep - 1]
        sdir = os.path.join(task.sdir, safe)
        try:
            os.makedirs(sdir, exist_ok=True)
        except Exception:
            pass

        task._pf = os.path.join(sdir, "_progress.jsonl")
        if ep - 1 < len(task.states):
            task.states[ep - 1] = "pending"

        did = os.getenv("DUANJU_DEVICE_ID", _DEFAULT_DEVICE_ID)
        iid = os.getenv("DUANJU_INSTALL_ID", _DEFAULT_INSTALL_ID)
        cfg = {
            "sid": sid, "vids": [vid], "sname": safe, "sdir": sdir,
            "qual": task.qual, "did": did, "iid": iid,
            "progress_file": task._pf, "start_ep": ep, "eps": [ep],
        }
        task.status = "downloading"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            os.makedirs(sdir, exist_ok=True)      # cwd 必须存在，否则 WinError 267
        except Exception:
            pass
        try:
            task._proc = subprocess.Popen(
                [VENV_PYTHON, BATCH_SCRIPT, json.dumps(cfg, ensure_ascii=False)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                env=env, cwd=sdir,
            )
        except Exception as e:
            task.status = "error"
            self._footer.setText(f"✕ 启动失败: {e}")

        if sid in self._cards:
            self._cards[sid].refresh()
        self._footer.setText(f"⬇ {task.name} 第 {ep} 集下载中…")

    def _on_download_all(self, sid):
        task = self._tasks.get(sid)
        if not task:
            return
        card = self._cards.get(sid)

        if task.status == "downloading":
            # 暂停
            try:
                task._proc.poll()
                task._proc.kill()
            except Exception:
                pass
            # 标记 downloading 的集次回到 pending
            for i, s in enumerate(task.states):
                if s == "downloading":
                    task.states[i] = "pending"
            task.status = "paused"
            if card:
                card.refresh()
            self._footer.setText(f"⏸ {task.name} 已暂停")
            self._update_stats()
            return

        # 全选未完成的集
        sel = []
        for i, s in enumerate(task.states):
            if s == "pending":
                sel.append(i + 1)
        if not sel:
            sel = list(range(1, task.total + 1))
        if not sel:
            return

        self._on_download_eps(sid, sel)

    def _on_retry_parse(self, sid):
        task = self._tasks.get(sid)
        if not task:
            return
        task.status = "parsing"
        if sid in self._cards:
            self._cards[sid].refresh()
        parser = ParseThread(sid, getattr(self, "_share_link", ""))
        parser.result.connect(lambda info: self._parsed(sid, info))
        parser.finished.connect(lambda s=sid: self._parsers.pop(s, None))
        self._parsers[sid] = parser
        parser.start()

    # ─────────────────────────── 进度轮询 ───────────────────────────
    def _poll(self):
        changed = False
        for sid, task in list(self._tasks.items()):
            if task.status != "downloading":
                continue

            rc = None
            try:
                rc = task._proc.poll()
            except Exception:
                pass

            d_one = d_err = 0
            try:
                with open(task._pf, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []
            except Exception:
                lines = []

            all_done = False
            for line in lines:
                try:
                    j = json.loads(line.strip())
                except Exception:
                    continue
                if j.get("type") == "all_done":
                    all_done = True
                    continue
                ep = j.get("ep")
                if not ep:
                    continue
                s = j.get("status")
                if ep - 1 < len(task.states):
                    if task.states[ep - 1] != s:
                        task.states[ep - 1] = s
                        changed = True
                # 记录实际编码/清晰度（新实现返回 codec；旧字段兼容）
                if j.get("codec"):
                    task._actual_codec = j["codec"]
                if j.get("qual"):
                    task._actual_qual = j["qual"]

            d_one = sum(1 for s in task.states if s == "done")
            d_err = sum(1 for s in task.states if s == "error")
            task.pct = int(task.pct * 0.5 + (d_one / max(1, task.total)) * 100 * 0.5) if d_one else task.pct

            if all_done or (rc is not None and rc != 0 and not all_done):
                pending_cnt = sum(1 for s in task.states if s == "pending")
                if all_done:
                    task.status = "done"
                    task.pct = 100
                elif pending_cnt == 0:
                    task.status = "error" if d_err else "done"
                    task.pct = 100
                else:
                    task.status = getattr(task, "_resume_after", None) or "paused"

                if sid in self._cards:
                    self._cards[sid].refresh()
                # 若实际清晰度与用户选择不同（服务端改用 bytevc2 导致回退），
                # 明确告知，避免用户以为下的是 540p 其实是 1080p
                qnote = ""
                ac = getattr(task, "_actual_codec", "")
                if ac:
                    fam = ""
                    try:
                        from app.hongguo_dl import codec_family as _cf
                        fam = _cf(ac)
                    except Exception:
                        fam = ""
                    tag = {"hevc": "HEVC", "h264": "H.264",
                           "bytevc2": "ByteVC2(不可播)"}.get(fam, ac)
                    qnote = f" · {tag}"
                self._footer.setText(
                    f"✓ {task.name}: {d_one}/{task.total}"
                    + (f" ({d_err} 失败)" if d_err else "")
                    + qnote
                )
                changed = True

            elif rc is not None and rc != 0:
                # 子进程异常退出且未写 all_done
                if sum(1 for s in task.states if s != "pending") == 0:
                    task.status = "error"
                    if sid in self._cards:
                        self._cards[sid].refresh()
                    self._footer.setText(f"✕ {task.name}: 异常({rc})")
                    changed = True

            if changed and sid in self._cards:
                self._cards[sid].refresh()

        if changed:
            self._update_stats()

    def _update_stats(self):
        total = len(self._tasks)
        ok = sum(1 for t in self._tasks.values() if t.status == "done")
        er = sum(1 for t in self._tasks.values() if t.status == "error")
        ac = sum(1 for t in self._tasks.values() if t.status in ("downloading", "parsing"))
        if total:
            self._stats.setText(f"{total} 个任务 · {ok} 完成 · {er} 失败 · {ac} 进行中")
        else:
            self._stats.setText("")

    # ─────────────────────────── 任务管理 ───────────────────────────
    def _rm(self, sid):
        p = self._parsers.pop(sid, None)
        if p:
            try:
                if p.isRunning():
                    p.quit()
                    p.wait(2000)
            except Exception:
                pass
        task = self._tasks.pop(sid, None)
        if task and task._proc:
            try:
                task._proc.poll()
                task._proc.kill()
            except Exception:
                pass
        card = self._cards.pop(sid, None)
        if card:
            self._sw_ly.removeWidget(card)
            card.deleteLater()
        self._update_stats()
        if not self._tasks:
            self._scroll.hide()
            self._empty_lbl.show()

    def _open_folder(self, path):
        """在 Windows 资源管理器中打开下载目录"""
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "提示", f"目录不存在：\n{path or ''}")
            return
        try:
            os.startfile(path)
        except Exception:
            try:
                subprocess.Popen(["xdg-open", path])
            except Exception:
                QMessageBox.warning(self, "错误", "无法打开目录")

    def _open_web(self, sid):
        """在系统默认浏览器中打开红果短剧详情页"""
        url = "https://hongguoduanju.com/detail?series_id=" + str(sid)
        try:
            webbrowser.open(url)
        except Exception:
            QMessageBox.warning(self, "错误", "无法打开网页")

    def _clear(self):
        for sid in list(self._tasks.keys()):
            self._rm(sid)

    # ─────────────────────────── 搜索 ───────────────────────────
    def _max_search_h(self):
        """搜索结果区最大高度：随窗口高度自适应，最多 340，下限 150。

        左侧面板已整体可滚动，这里再留出余量，保证「链接/目录/清晰度/开始按钮」
        在常见窗口尺寸下仍能完整看到。
        """
        return max(150, min(340, int(self.height() * 0.30)))

    def _on_search_triggered(self):
        text = self._search_input.text().strip()
        if not text:
            self._search_scroll.hide()
            self._search_list.hide()
            self._more_btn.hide()
            self._search_input.setPlaceholderText("输入剧名、主演或题材")
            return
        self._search_scroll.setFixedHeight(self._max_search_h())
        self._more_btn.setVisible(True)
        self._searching = True
        self._search_input.setPlaceholderText("搜索中…")
        self._more_btn.setText("加载中…")
        self._more_btn.setStyleSheet(
            f"font-family:{FONT_UI};font-size:12px;font-weight:600;color:{W_TXT2};"
            f"background:transparent;border:none;padding:2px 12px;"
        )
        self._search_hint.setText(f"正在搜索「{text}」…")
        threading.Thread(target=self._do_search, args=(text,), daemon=True).start()

    def _do_search(self, keyword):
        try:
            from app import search_index
            results, passback = search_index.search(keyword)
        except Exception:
            import traceback
            traceback.print_exc()
            results, passback = [], ""
        self._search_result = results
        self._search_done.emit("first", results, passback)

    def _do_search_more(self, _=None):
        if not hasattr(self, "_search_result"):
            return
        passback = getattr(self, "_passback", "")
        keyword = self._search_input.text().strip()
        self._search_hint.setText("正在加载更多…")
        try:
            from app import search_index
            results, next_pb = search_index.search_more(keyword, passback)
        except Exception:
            results, next_pb = [], ""
        self._search_done.emit("more", results, next_pb)

    def _on_load_more(self):
        if getattr(self, "_searching", False):
            return
        self._searching = True
        self._more_btn.setText("加载中…")
        self._more_btn.setEnabled(False)
        threading.Thread(target=self._do_search_more, daemon=True).start()

    def _show_results(self, mode, results, passback):
        self._searching = False
        if mode == "first":
            self._search_list.clear()
        self._passback = passback

        if mode == "first" and not results:
            self._search_scroll.hide()
            self._more_btn.hide()
            self._search_input.setPlaceholderText("输入剧名、主演或题材")
            self._search_hint.setText("未找到相关短剧，换个关键词试试")
            self._search_hint.setStyleSheet(
                f"font-family:{FONT_UI};font-size:12px;font-weight:600;color:{W_RED};"
                f"background:transparent;border:none;padding:2px 12px;"
            )
            return

        if results:
            self._add_items(results, self._search_list.count())
            self._search_list.show()
            self._search_scroll.show()

        total = self._search_list.count()
        more = bool(passback)
        if more:
            self._more_btn.setText("加载更多")
            self._more_btn.setEnabled(True)
            self._more_btn.show()
            self._search_hint.setText(f"已加载 {total} 个结果，可继续加载")
            self._search_hint.setStyleSheet(
                f"font-family:{FONT_UI};font-size:12px;font-weight:600;color:{W_GREEN};"
                f"background:transparent;border:none;padding:2px 12px;"
            )
        else:
            self._more_btn.hide()

        self._search_input.setPlaceholderText("输入剧名、主演或题材")
        self._resize_search_list()
        if mode == "first":
            self._search_hint.setText(f"找到 {total} 个结果")

    def _add_items(self, results, start_idx=0):
        for i, r in enumerate(results):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(r.get("series_id", "")))
            item.setSizeHint(QSize(1000, 56))
            self._search_list.addItem(item)
            self._search_list.setItemWidget(
                item, self._build_search_item_widget(r, start_idx + i)
            )
        self._search_list.setUniformItemSizes(False)

    def _resize_search_list(self):
        """只控制外层滚动区高度；内部列表交给 setWidgetResizable 自适应。

        不要给 _search_list 再设固定高度 —— 在 setWidgetResizable(True) 下
        两者会互相打架，导致列表溢出压住下方控件。
        """
        n = self._search_list.count()
        if not n:
            self._search_scroll.setFixedHeight(0)
            return
        max_h = self._max_search_h()
        content_h = n * 58
        self._search_scroll.setFixedHeight(max(58, min(content_h, max_h)))

    def _mk_tag(self, text, color=W_TXT2):
        """创建统一样式的标签"""
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-family:{FONT_UI};background:{color};color:#FFFFFF;border:none;"
            f"border-radius:5px;padding:2px 8px;font-size:11px;font-weight:500;"
        )
        lbl.setFixedHeight(20)
        hl = QHBoxLayout()
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(lbl)
        hl.addStretch()
        w = QWidget()
        w.setLayout(hl)
        return w

    def _build_search_item_widget(self, r, index):
        """搜索结果行: 第一行 编号+标题, 第二行 标签"""
        from PyQt5.QtWidgets import QVBoxLayout as _QV
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setAttribute(Qt.WA_TranslucentBackground)
        outer = _QV(w)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(2)

        r1 = QHBoxLayout()
        r1.setSpacing(6)

        idx_lbl = QLabel(f"{index + 1:02d}")
        idx_lbl.setFixedSize(18, 18)
        idx_lbl.setAlignment(Qt.AlignCenter)
        idx_lbl.setStyleSheet(
            f"font-family:{FONT_UI};font-size:10px;font-weight:700;color:{W_ACC};"
            f"background:transparent;border:none;"
        )
        r1.addWidget(idx_lbl)

        name_lbl = QLabel(str(r.get("series_name", ""))[:26])
        name_lbl.setStyleSheet(
            f"font-family:{FONT_UI};font-size:13px;color:{W_TXT};"
            f"background:transparent;border:none;"
        )
        r1.addWidget(name_lbl)
        r1.addStretch()

        ep_cnt = r.get("episode_cnt", 0)
        if ep_cnt:
            e = QLabel(f"{ep_cnt} 集")
            e.setStyleSheet(
                f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};"
                f"background:transparent;border:none;"
            )
            r1.addWidget(e)

        outer.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(4)

        dt = r.get("drama_type", "short")
        tag = QLabel("漫剧" if dt == "manju" else "短剧")
        tag.setStyleSheet(
            f"font-family:{FONT_UI};background:#E8E8ED;color:{W_TXT2};"
            f"border-radius:5px;padding:1px 7px;font-size:10px;font-weight:600;"
        )
        tag.setFixedHeight(17)
        r2.addWidget(tag)
        r2.addSpacing(2)

        play = r.get("play_cnt", 0)
        if play:
            play_str = f"{play / 10000:.1f} 万播放" if play >= 10000 else f"{play} 播放"
            p = QLabel(play_str)
            p.setStyleSheet(
                f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};"
                f"background:transparent;border:none;"
            )
            r2.addWidget(p)

        heat = r.get("heat", "")
        if heat:
            hh = QLabel(str(heat))
            hh.setStyleSheet(
                f"font-family:{FONT_UI};font-size:11px;color:{W_YELLOW};"
                f"background:transparent;border:none;"
            )
            r2.addWidget(hh)

        score = r.get("score", "")
        if score:
            sc = QLabel(f"⭐ {score}")
            sc.setStyleSheet(
                f"font-family:{FONT_UI};font-size:11px;color:{W_TXT3};"
                f"background:transparent;border:none;"
            )
            r2.addWidget(sc)

        r2.addStretch()
        outer.addLayout(r2)
        return w

    def _on_search_selected(self, item):
        sid = item.data(Qt.UserRole)
        if not sid:
            return
        self._url.setText(str(sid))
        self._start()

    # ─────────────────────────── 窗口交互 ───────────────────────────
    def _press_at(self, gpos):
        """统一的按下处理：边缘 → 进入缩放；否则 → 进入拖动。返回 True 表示已消费"""
        edge = self._edge_at(gpos)
        if edge and not self._maximized:
            self._resize_edge = edge
            self._resize_start = gpos
            self._resize_geo = QRect(self.geometry())   # 固定基准
            self._drag = False
        else:
            self._drag = True
            self._drag_pos = gpos - self.frameGeometry().topLeft()
            self._resize_edge = None
        return True

    def _move_at(self, gpos, buttons):
        """统一的移动处理：缩放优先，其次拖动。返回 True 表示已消费"""
        if self._resize_edge and (buttons & Qt.LeftButton):
            self._apply_resize(gpos)
            return True
        if self._drag and (buttons & Qt.LeftButton):
            if self._maximized:
                # 从最大化拖出 → 先还原，并把窗口挂到光标处
                ratio = gpos.x() - self.geometry().left()
                self.toggle_maximize()
                self._drag_pos = QPoint(ratio, 0)
            self.move(gpos - self._drag_pos)
            return True
        return False

    def _release_at(self):
        self._drag = False
        self._resize_edge = None
        self.unsetCursor()
        hdr = getattr(self, "_hdr", None)
        if hdr is not None:
            hdr.unsetCursor()

    def eventFilter(self, obj, event):
        """标题栏 / 根容器上的鼠标事件 → 统一走 _press_at / _move_at"""
        from PyQt5.QtCore import QEvent
        # getattr 兜底：构造早期可能尚未赋值，避免在 C++ 回调里抛异常导致崩溃
        if obj is not getattr(self, "_root", None) and obj is not getattr(self, "_hdr", None):
            return super().eventFilter(obj, event)
        et = event.type()

        if et == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                return self._press_at(event.globalPos())
            self._release_at()
            return False

        if et == QEvent.MouseMove:
            if self._move_at(event.globalPos(), event.buttons()):
                return True
            # 未按下时：仅更新边缘光标
            edge = self._edge_at(event.globalPos())
            obj.setCursor(self._EDGE_CURSOR.get(edge, Qt.ArrowCursor))
            return False

        if et == QEvent.MouseButtonRelease:
            self._release_at()
            return False

        return super().eventFilter(obj, event)

    def toggle_maximize(self):
        """最大化 / 还原（无边框窗口需自行处理，并让内容跟随可用屏幕）"""
        if not self._maximized:
            self._normal_size = self.size()
            self._normal_pos = self.pos()
            g = QApplication.primaryScreen().availableGeometry()
            # 铺满工作区（不覆盖任务栏）
            self.setGeometry(g.x() + self.SHADOW_MARGIN, g.y() + self.SHADOW_MARGIN,
                             g.width() - self.SHADOW_MARGIN * 2,
                             g.height() - self.SHADOW_MARGIN * 2)
            self._maximized = True
            self._btn_max.setText("❐")
            self._btn_max.setToolTip("还原")
        else:
            self.setGeometry(self._normal_pos.x(), self._normal_pos.y(),
                             self._normal_size.width(), self._normal_size.height())
            self._maximized = False
            self._btn_max.setText("☐")
            self._btn_max.setToolTip("最大化")

    def _edge_at(self, gpos):
        """判断鼠标是否在窗口边缘。

        **gpos 必须是全局坐标**（QMouseEvent.globalPos()）。
        之前有三处调用混用了局部坐标，导致右/下边缘永远命中不了。
        """
        m = self.SHADOW_MARGIN + 4   # 命中区域（外扩阴影那一圈）
        g = self.geometry()
        x, y = gpos.x(), gpos.y()
        left = x <= g.left() + m
        right = x >= g.left() + g.width() - m
        top = y <= g.top() + m
        bottom = y >= g.top() + g.height() - m
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return None

    _EDGE_CURSOR = {
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    }

    def mouseMoveEvent(self, e):
        if self._move_at(e.globalPos(), e.buttons()):
            return
        # 悬停在边缘 → 切换鼠标形状
        edge = self._edge_at(e.globalPos())
        self.setCursor(self._EDGE_CURSOR.get(edge, Qt.ArrowCursor))
        super().mouseMoveEvent(e)

    def _apply_resize(self, gpos):
        """按鼠标位置调整窗口几何。

        关键：`self._resize_geo` 始终是「按下鼠标那一刻」的基准矩形，全程不再更新。
        若每次都刷新基准，而位移 d 又是相对按下点的累积量，两者叠加会让窗口
        越拖越快、失控乱跑。
        """
        d = gpos - self._resize_start
        g = self._resize_geo          # 固定基准，不修改
        minw, minh = self.minimumWidth(), self.minimumHeight()

        left, top = g.x(), g.y()
        right, bottom = g.x() + g.width(), g.y() + g.height()
        e = self._resize_edge

        if "l" in e:
            # 左边界右移 → 宽度收窄，右边界保持不动
            left = min(right - minw, left + d.x())
        if "r" in e:
            right = max(left + minw, right + d.x())
        if "t" in e:
            top = min(bottom - minh, top + d.y())
        if "b" in e:
            bottom = max(top + minh, bottom + d.y())

        new_geo = QRect(left, top, right - left, bottom - top)
        if new_geo != self.geometry():     # 变了才 set，避免无谓重绘
            self.setGeometry(new_geo)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_at(e.globalPos())
            return
        self._release_at()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._release_at()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        """双击标题栏 → 最大化/还原"""
        if e.button() == Qt.LeftButton:
            y = e.pos().y()
            if y <= 52:
                self._release_at()
                self.toggle_maximize()
                return
        super().mouseDoubleClickEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    f = QFont("Microsoft YaHei", 10)
    app.setFont(f)
    # 窗口图标：优先用多尺寸 ico（任务栏/标题栏/Alt-Tab 都会用它）
    icon_path = os.path.join(BASE_DIR, "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(BASE_DIR, "app_icon.png")
    w = MainWindow(app_name=APP_NAME, icon_path=icon_path)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
