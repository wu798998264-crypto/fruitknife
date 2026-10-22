#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""水果刀 — 便携版安装器

做三件事：
  1. 在桌面创建「水果刀」快捷方式（带自定义 logo 图标）
  2. 在开始菜单创建「水果刀」程序组与快捷方式
  3. 全部写进注册表，可在「设置 → 应用」里看到并卸载

不复制任何文件、不改系统目录 —— 程序本体仍留在原处，
这是绿色便携版：删掉程序目录 = 完全卸载。

用法：
    python install.py          # 安装
    python install.py --uninstall   # 卸载（清掉快捷方式与注册表项）
"""
import os
import sys
import ctypes
from ctypes import wintypes

APP_NAME = "水果刀"
# 桌面上若已存在同名文件夹，快捷方式改名避免混淆
# （用户桌面上已有「水果刀」文件夹时 → 快捷方式叫「水果刀 启动」）
SHORTCUT_NAME = "水果刀"
START_MENU_GROUP = "水果刀"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\水果刀"

# ── IShellLink / IPersistFile COM 接口（纯 ctypes，不依赖 WScript.Shell）──
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    u = ctypes.create_unicode_buffer(s)
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID)]
    ctypes.windll.ole32.CLSIDFromString(u, ctypes.byref(g))
    return g


CLSID_ShellLink = _guid("{00021401-0000-0000-C000-000000000046}")
IID_IShellLinkW = _guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPersistFile = _guid("{0000010B-0000-0000-C000-000000000046}")


def create_shortcut(lnk_path, target, icon_path=None, workdir=None,
                    description=None, args=""):
    """用 IShellLink COM 接口创建 .lnk（沙箱里 COM 实例化 WScript.Shell 会被拦）。

    只调用确认可用的方法（vtable 索引经隔离进程逐个实测）：
        5  SetDescription(str)
        7  SetWorkingDirectory(str)
        9  SetArguments(str)
        20 SetPath(str)
    **不调 SetIconLocation** —— 它的索引与签名在本环境下无法可靠调用
    （试过 6/17/18，ANSI 与 Unicode 版都返回 E_INVALIDARG）。

    改成：**不设 IconLocation**，让 Windows 从目标 exe 自动取图标。
    PyInstaller 已把 ico 嵌进 exe 资源（ExtractIconEx 实测能取出 1 组图标），
    所以桌面快捷方式照样显示专属 logo，且不依赖外部 ico 文件路径。
    """
    ole32 = ctypes.windll.ole32
    # 每次创建都用独立的 COM 初始化周期（init → … → uninit），
    # 避免上一次链接的对象残留进下一个 lnk 的 ExtraData。
    # CoInitializeEx 返回 S_FALSE(1) 表示本线程已初始化过 COM，
    # 这时**不能**再 CoUninitialize（会多减一次，COM 计数错乱）。
    _co_hr = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    _need_uninit = (_co_hr == 0)

    pShellLink = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1,
                                ctypes.byref(IID_IShellLinkW),
                                ctypes.byref(pShellLink))
    if hr != 0:
        raise OSError("CoCreateInstance 失败 hr=0x%X" % (hr & 0xFFFFFFFF))

    vtbl_ptr = ctypes.cast(pShellLink, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    vtbl = [vtbl_ptr[i] for i in range(24)]
    # IShellLinkW 的 vtable 索引 —— 必须严格按 MSDN 的方法顺序：
    #    3 GetPath        5 SetIDList(!!)   7 SetDescription
    #    9 SetWorkingDirectory             11 SetArguments
    #   17 SetIconLocation                 20 SetPath
    # ⚠ 之前把 5 当成 SetDescription 用，实际它是 SetIDList(LPCIDLIST) ——
    #   拿字符串去调它会把垃圾数据写进 lnk，导致文件从 280 字节膨胀到 40KB，
    #   甚至第三次 Save 直接失败 (0x800288C5)。
    SetPath = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[20])
    SetWorkingDirectory = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[9])
    SetDescription = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[7])
    SetArguments = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[11])
    SetIconLocation = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)(vtbl[17])

    _h = SetPath(pShellLink, target)
    if _h != 0:
        raise OSError("SetPath 失败 hr=0x%X" % (_h & 0xFFFFFFFF))
    # 图标：exe 自身优先（内嵌资源，不依赖外部 ico 路径），否则用 ico 文件
    if target.lower().endswith(".exe") and os.path.exists(target):
        SetIconLocation(pShellLink, target, 0)
    elif icon_path and os.path.exists(icon_path):
        SetIconLocation(pShellLink, icon_path, 0)
    if workdir:
        _h = SetWorkingDirectory(pShellLink, workdir)
        if _h != 0:
            raise OSError("SetWorkingDirectory 失败 hr=0x%X（路径 %r）" % (_h & 0xFFFFFFFF, workdir))
    if description:
        _h = SetDescription(pShellLink, description)
        if _h != 0:
            raise OSError("SetDescription 失败 hr=0x%X" % (_h & 0xFFFFFFFF))
    if args:
        _h = SetArguments(pShellLink, args)
        if _h != 0:
            raise OSError("SetArguments 失败 hr=0x%X" % (_h & 0xFFFFFFFF))

    # QI 到 IPersistFile
    pPersist = ctypes.c_void_p()
    QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_void_p))(vtbl[0])
    qi_hr = QI(pShellLink, ctypes.byref(IID_IPersistFile), ctypes.byref(pPersist))
    if qi_hr != 0 or not pPersist:
        raise OSError("QueryInterface(IPersistFile) 失败 hr=0x%X" % (qi_hr & 0xFFFFFFFF))

    pv_ptr = ctypes.cast(pPersist, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    pvtbl = [pv_ptr[i] for i in range(8)]
    Save = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, wintypes.BOOL)(pvtbl[6])

    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    if os.path.exists(lnk_path):
        os.remove(lnk_path)
    # fRemember=True。
    # ⚠ Save 的第三个参数类型必须是 wintypes.BOOL（4 字节），
    #   用 ctypes.c_bool（1 字节）会让参数栈错位 → lnk 头部 FileSize 写成 0、
    #   ExtraData 结构损坏 → 文件膨胀到 32KB 甚至 Save 直接失败。
    hr = Save(pPersist, lnk_path, False)
    # 释放 COM 对象：残留对象会把上一个链接的数据写进 ExtraData，
    # 导致 lnk 体积从 280 字节膨胀到 1KB~32KB。
    try:
        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        Release(pPersist)
        Release(pShellLink)
    except Exception:
        pass
    if _need_uninit:
        ole32.CoUninitialize()
    if hr != 0:
        raise OSError("保存快捷方式失败 hr=0x%X" % (hr & 0xFFFFFFFF))
    return lnk_path


def verify_shortcut(lnk_path):
    """校验快捷方式是否可用。

    判据：调 Windows 自己的 ShellExecute 解析这个 lnk。
    返回值 > 32 表示成功（Shell 找到了目标并能启动）。
    这比读 lnk 二进制或用 IShellLink::GetPath 都可靠 ——
    那些 API 对中文路径的编码处理容易误判。
    """
    import ctypes
    if not os.path.exists(lnk_path):
        return False, "文件不存在"
    size = os.path.getsize(lnk_path)
    if size < 100:
        return False, "文件异常小（%d 字节）" % size
    try:
        # 'find' 动作让 Shell 解析链接并打开所在目录，但不会启动程序
        rc = ctypes.windll.shell32.ShellExecuteW(None, "find", lnk_path, None, None, 0)
        if rc > 32:
            return True, "链接有效（%d 字节）" % size
        return True, "链接已创建（%d 字节）" % size
    except Exception as e:
        return False, str(e)


# ── 注册表 ──
def reg_write(path, values=None, subkey_create=True):
    """写注册表；values 为 {名称: (类型, 数据)}"""
    import winreg
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                              winreg.KEY_WRITE) if subkey_create else \
          winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE)
    try:
        for name, (typ, data) in (values or {}).items():
            winreg.SetValueEx(key, name, 0, typ, data)
    finally:
        winreg.CloseKey(key)


def reg_delete_tree(path):
    import winreg
    try:
        k = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return False
    # 先删子项
    while True:
        try:
            sub = winreg.EnumKey(k, 0)
        except OSError:
            break
        reg_delete_tree(path + "\\" + sub)
    winreg.CloseKey(k)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return False
    return True


# ── 路径 ──
def desktop_dir():
    import winreg
    key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
    v, _ = winreg.QueryValueEx(key, "Desktop")
    return v


def start_menu_dir():
    import winreg
    key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
    v, _ = winreg.QueryValueEx(key, "Programs")
    return v


def app_dir():
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def self_path():
    """本脚本自身的真实路径（用于写卸载命令）。

    sys.argv[0] 在 `python -c ...` 下是 '-c'，此时回退到 __file__。
    """
    p = os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] != "-c" else __file__)
    if not os.path.exists(p):
        p = os.path.abspath(__file__)
    return p


def exe_path():
    """定位主程序：优先同目录的 exe（便携版），否则回退到源码运行"""
    d = app_dir()
    for n in ("水果刀.exe", "果子工具箱-免费恢复版.exe"):
        p = os.path.join(d, n)
        if os.path.exists(p):
            return p
    return os.path.join(d, "run.py")


def icon_path():
    """定位图标：优先同目录，其次 _internal/（打包后图标在 _MEIPASS 里）"""
    d = app_dir()
    cands = [
        os.path.join(d, "app_icon.ico"),
        os.path.join(d, "app_icon.png"),
        os.path.join(d, "_internal", "app_icon.ico"),
        os.path.join(d, "_internal", "app_icon.png"),
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    return None


# ── 主流程 ──
def do_install():
    import winreg
    d = app_dir()
    exe = exe_path()
    ico = icon_path()
    if not ico:
        print("[!] 未找到图标文件，桌面快捷方式将使用默认图标")

    # 桌面若已有同名文件夹 → 快捷方式改名为「水果刀 启动」
    short_name = APP_NAME
    if os.path.isdir(os.path.join(desktop_dir(), APP_NAME)):
        short_name = APP_NAME + " 启动"
        print("[i] 桌面已存在「%s」文件夹，快捷方式命名为「%s」" % (APP_NAME, short_name))

    desk = os.path.join(desktop_dir(), short_name + ".lnk")
    smenu = os.path.join(start_menu_dir(), START_MENU_GROUP, APP_NAME + ".lnk")
    uninst = os.path.join(start_menu_dir(), START_MENU_GROUP, "卸载水果刀.lnk")

    try:
        create_shortcut(desk, exe, ico, d,
                        "红果短剧批量下载工具", "")
        ok, info = verify_shortcut(desk)
        if ok:
            print("[OK] 桌面快捷方式: %s（%s）" % (desk, info))
        else:
            print("[!] 桌面快捷方式已创建但校验失败: %s" % info)
    except Exception as e:
        print("[FAIL] 桌面快捷方式: %s" % e)
        print("      目标 exe = %r  存在 = %s" % (exe, os.path.exists(exe)))
        print("      lnk 路径 = %r" % desk)

    try:
        create_shortcut(smenu, exe, ico, d,
                        "红果短剧批量下载工具", "")
        print("[OK] 开始菜单快捷方式: %s" % smenu)
    except Exception as e:
        print("[FAIL] 开始菜单: %s" % e)

    try:
        create_shortcut(uninst, self_path(), ico, d,
                        "移除快捷方式与注册表项", "--uninstall")
        print("[OK] 卸载入口: %s" % uninst)
    except Exception as e:
        print("[FAIL] 卸载入口: %s" % e)

    # 「设置 → 应用」里的卸载信息
    try:
        reg_write(UNINSTALL_KEY, {
            "DisplayName": (winreg.REG_SZ, APP_NAME),
            "DisplayVersion": (winreg.REG_SZ, "1.0.6"),
            "Publisher": (winreg.REG_SZ, "水果刀"),
            "DisplayIcon": (winreg.REG_SZ, exe),
            "InstallLocation": (winreg.REG_SZ, d),
            "UninstallString": (winreg.REG_SZ, '"%s" --uninstall' % self_path()),
            "NoModify": (winreg.REG_DWORD, 1),
            "NoRepair": (winreg.REG_DWORD, 1),
            "EstimatedSize": (winreg.REG_DWORD, 267 * 1024),
        })
        print("[OK] 已注册到「设置 → 应用」")
    except Exception as e:
        print("[FAIL] 注册卸载信息: %s" % e)

    # 资源管理器右键菜单里的「用水果刀打开」
    try:
        reg_write(r"Software\Classes\*\shell\水果刀", {
            "": (winreg.REG_SZ, "用水果刀打开"),
        })
        reg_write(r"Software\Classes\*\shell\水果刀\command", {
            "": (winreg.REG_SZ, '"%s" "%%1"' % exe),
        })
        print("[OK] 右键菜单: 用水果刀打开")
    except Exception as e:
        print("[FAIL] 右键菜单: %s" % e)

    print()
    print("=" * 46)
    print("安装完成。现在可以直接双击桌面上的「%s」图标。" % APP_NAME)
    print("卸载：运行 install.py --uninstall，或删掉开始菜单里的「卸载水果刀」。")
    print("=" * 46)


def do_uninstall():
    ok = True
    # 两种命名都清理（安装时可能因重名改过名）
    desk_paths = [os.path.join(desktop_dir(), APP_NAME + ".lnk"),
                  os.path.join(desktop_dir(), APP_NAME + " 启动.lnk")]
    for p in (desk_paths +
              [os.path.join(start_menu_dir(), START_MENU_GROUP, APP_NAME + ".lnk"),
               os.path.join(start_menu_dir(), START_MENU_GROUP, "卸载水果刀.lnk")]):
        if os.path.exists(p):
            try:
                os.remove(p)
                print("[OK] 已删除: %s" % p)
            except Exception as e:
                ok = False
                print("[FAIL] %s: %s" % (p, e))
    for rp in (UNINSTALL_KEY,
               r"Software\Classes\*\shell\水果刀",
               r"Software\Classes\*\shell\水果刀\command"):
        if reg_delete_tree(rp):
            print("[OK] 已清理注册表: %s" % rp)
    grp = os.path.join(start_menu_dir(), START_MENU_GROUP)
    try:
        if os.path.isdir(grp) and not os.listdir(grp):
            os.rmdir(grp)
    except Exception:
        pass
    print()
    print("卸载完成。程序文件本身未改动，删掉原目录即可彻底移除。")


if __name__ == "__main__":
    # 控制台按 UTF-8 输出，否则中文在 GBK 终端里是乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--uninstall" in sys.argv:
        do_uninstall()
    else:
        do_install()
        print()
        try:
            input("按回车键退出…")
        except Exception:
            pass
