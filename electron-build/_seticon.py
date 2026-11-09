# -*- coding: utf-8 -*-
"""把水果刀图标写入 Electron exe 的 PE 资源（UpdateResourceW）"""
import ctypes
import os
import struct
from ctypes import wintypes

EXE = r'D:\workspace_guozi\_fk_electron\水果刀Electron\水果刀.exe'
ICO = r'D:\workspace_guozi\水果刀\app_icon.ico'

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

BeginUpdateResource = kernel32.BeginUpdateResourceW
BeginUpdateResource.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
BeginUpdateResource.restype = wintypes.HANDLE

UpdateResource = kernel32.UpdateResourceW
UpdateResource.argtypes = [wintypes.HANDLE, wintypes.ULONG, wintypes.ULONG,
                            wintypes.WORD, wintypes.LPVOID, wintypes.DWORD]
UpdateResource.restype = wintypes.BOOL

EndUpdateResource = kernel32.EndUpdateResourceW
EndUpdateResource.argtypes = [wintypes.HANDLE, wintypes.BOOL]
EndUpdateResource.restype = wintypes.BOOL

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0x0409  # 与 electron-builder 常用一致；用 0 也可


def parse_ico(path):
    """解析 .ico → [(width, height, bitcount, data)] + group header 条目"""
    d = open(path, 'rb').read()
    reserved, itype, count = struct.unpack('<HHH', d[:6])
    assert itype == 1, 'not an ico'
    entries = []
    group = []
    for i in range(count):
        off = 6 + i * 16
        w, h, colors, _r, planes, bc, size, offset = struct.unpack('<BBBBHHII', d[off:off+16])
        w = w or 256
        h = h or 256
        data = d[offset:offset+size]
        entries.append({'w': w, 'h': h, 'colors': colors, 'planes': planes,
                        'bc': bc, 'data': data})
        # RT_GROUP_ICON 条目头（14 字节）：宽/高/色数/保留/planes/bitcount/字节数/资源id
        group.append({'w': w, 'h': h, 'colors': colors, 'bc': bc,
                      'planes': planes, 'size': size, 'id': i + 1})
    return entries, group


def build_group_icon(group):
    """构造 GRPICONDIREN 数组（新头：ID 从 1 开始）"""
    header = struct.pack('<HHH', 0, 1, len(group))
    body = b''
    for g in group:
        body += struct.pack('<BBBBHHIH',
                            g['w'] & 0xFF, g['h'] & 0xFF, g['colors'], 0,
                            g['planes'], g['bc'], g['size'], g['id'])
    return header + body


def enum_existing_icons(exe):
    """枚举 exe 里现有的 RT_ICON 资源名/ID"""
    import ctypes
    LoadLibraryEx = ctypes.WinDLL('kernel32').LoadLibraryExW
    EnumResNames = ctypes.WinDLL("kernel32").EnumResourceNamesW
    FreeLibrary = ctypes.WinDLL('kernel32').FreeLibrary
    LOAD_LIBRARY_AS_DATA_FILE = 0x2
    h = LoadLibraryEx(exe, None, LOAD_LIBRARY_AS_DATA_FILE)
    names = []
    if not h:
        return names
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LONG,
                            wintypes.LPVOID, wintypes.LONG)
    def cb(hm, typ, name, param):
        if ctypes.cast(name, wintypes.LPCWSTR):
            try:
                names.append(ctypes.cast(name, wintypes.LPCWSTR).value)
            except Exception:
                names.append(int(name))
        else:
            names.append(int(name) if name else 0)
        return True
    EnumResNames(h, RT_ICON, None, CB(cb))
    FreeLibrary(h)
    return names


def main():
    entries, group = parse_ico(ICO)
    print('ico 条目:', [(e['w'], e['h'], e['bc']) for e in entries])
    existing = enum_existing_icons(EXE)
    print('exe 现有 RT_ICON 资源 id:', existing)

    hUpd = BeginUpdateResource(EXE, False)
    if not hUpd:
        print('BeginUpdateResource 失败:', ctypes.get_last_error())
        raise SystemExit(1)

    ok = True
    # 1) 更新/新增 RT_ICON（id = 1..N）
    for i, e in enumerate(entries):
        rid = i + 1
        buf = ctypes.create_string_buffer(e['data'], len(e['data']))
        if not UpdateResource(hUpd, RT_ICON, rid, LANG_NEUTRAL,
                              buf, len(e['data'])):
            print('  ✗ RT_ICON %d 替换失败 err=%d' % (rid, ctypes.get_last_error()))
            ok = False
        else:
            print('  ✓ RT_ICON %d (%dx%d %dbit, %d 字节)' %
                  (rid, e['w'], e['h'], e['bc'], len(e['data'])))

    # 2) 若原 exe 的 RT_ICON 数量比新 ico 多，删除多余条目
    if existing:
        try:
            old_ids = sorted(int(n) for n in existing)
        except Exception:
            old_ids = []
        for rid in old_ids:
            if rid > len(entries):
                if not UpdateResource(hUpd, RT_ICON, rid, LANG_NEUTRAL, None, 0):
                    print('  ! 删除多余 RT_ICON %d 失败（可忽略）' % rid)
                else:
                    print('  ✓ 删除多余 RT_ICON %d' % rid)

    # 3) 替换 RT_GROUP_ICON（id=1）
    gdata = build_group_icon(group)
    buf = ctypes.create_string_buffer(gdata, len(gdata))
    if not UpdateResource(hUpd, RT_GROUP_ICON, 1, LANG_NEUTRAL,
                          buf, len(gdata)):
        print('✗ RT_GROUP_ICON 替换失败 err=%d' % ctypes.get_last_error())
        ok = False
    else:
        print('  ✓ RT_GROUP_ICON (%d 条目)' % len(group))

    if not EndUpdateResource(hUpd, False):
        print('✗ EndUpdateResource 失败 err=%d（exe 可能被占用）' %
              ctypes.get_last_error())
        raise SystemExit(1)
    print('图标替换完成' if ok else '部分失败')


if __name__ == '__main__':
    main()
