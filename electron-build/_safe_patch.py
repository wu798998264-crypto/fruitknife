# -*- coding: utf-8 -*-
"""从原版 asar 恢复渲染 JS，做安全替换（纯字符串字面量，不碰 JSX 结构）"""
import struct, json, os, shutil, io

ASAR = r'D:\workspace_guozi\_ref_dl\hongguo-downloader\resources\app.asar'
APP = r'D:\workspace_guozi\_fk_electron\app_src'
NODE = r'C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe'

d = open(ASAR, 'rb').read()
h2 = struct.unpack('<I', d[4:8])[0]
jsz = struct.unpack('<I', d[12:16])[0]
j = json.loads(d[16:16+jsz].decode('utf-8'))
data_off = 8 + h2

def extract(rel, out):
    node = {'files': j['files']}
    for part in rel.strip('/').split('/'):
        node = node['files'][part]
    off = data_off + int(node['offset'])
    content = d[off:off + node['size']]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(content)
    return content

def to_text(b):
    return b.decode('utf-8', errors='replace')

def put_text(p, s):
    io.open(p, 'w', encoding='utf-8', newline='').write(s)

# ── 1. 恢复原版 main.js / 渲染 JS ──
extract('/main.js', os.path.join(APP, 'main.js'))
print('main.js 已恢复原版')

rel_js = '/dist-react/assets/index-CgKAkSm0.js'
orig = to_text(extract(rel_js, os.path.join(APP, rel_js.lstrip('/'))))
print('渲染 JS 已恢复原版 (%d 字节)' % len(orig))

# ── 2. main.js 品牌替换（纯字符串，安全）──
p_main = os.path.join(APP, 'main.js')
s = io.open(p_main, encoding='utf-8', errors='replace').read()
s = s.replace("const APP_TITLE = '小菜鸟 软件';", "const APP_TITLE = '水果刀';")
s = s.replace("APP_TITLE + ' - 红果短剧下载器'", "'水果刀 — 红果短剧批量下载'")
s = s.replace("path.join(root, '红果短剧', seriesFolder)", "path.join(root, seriesFolder)")
put_text(p_main, s)
print('main.js 替换完成')

# ── 3. 渲染 JS：只做纯字符串字面量替换（不碰任何 JSX 结构）──
pairs = [
    ('红果短剧下载器', '水果刀'),
    ('小菜鸟 软件', '水果刀'),
    ('小菜鸟', '水果刀'),
    ('https://111330.com', 'https://github.com/wu798998264-crypto/fruitknife'),
    ('111330.com', '水果刀'),
]
total = 0
for a, b in pairs:
    c = orig.count(a)
    orig = orig.replace(a, b)
    total += c
put_text(os.path.join(APP, rel_js.lstrip('/')), orig)
print('渲染 JS 安全替换 %d 处' % total)

# ── 4. index.html：title + 广告位 CSS 隐藏 ──
p_html = os.path.join(APP, 'dist-react', 'index.html')
s = io.open(p_html, encoding='utf-8').read()
import re
s = re.sub(r'<title>[^<]*</title>', '<title>水果刀 — 红果短剧批量下载</title>', s)
HIDE = '''<style>
/* 水果刀本地版：隐藏原作者推广位（顶部横幅/防迷路横幅/侧栏卡片） */
.top-official-banner, .hongguo-site-notice, .sidebar-website-card { display: none !important; }
</style>'''
if '.top-official-banner' not in s:
    s = s.replace('</head>', HIDE + '\n</head>')
put_text(p_html, s)
print('index.html: title + CSS 隐藏完成')

# ── 5. package.json ──
p_pkg = os.path.join(APP, 'package.json')
s = io.open(p_pkg, encoding='utf-8').read()
import re as _re
s = _re.sub(r'"name":\s*"[^"]*"', '"name": "fruitknife"', s)
s = _re.sub(r'"description":\s*"[^"]*"', '"description": "水果刀 — 红果短剧批量下载工具"', s)
s = _re.sub(r'"homepage":\s*"[^"]*"', '"homepage": ""', s)
s = _re.sub(r'"version":\s*"1\.0\.4"', '"version": "1.0.7"', s)
put_text(p_pkg, s)
print('package.json 完成')

# ── 6. 语法校验 ──
import subprocess
r = subprocess.run([NODE, '--check', os.path.join(APP, rel_js.lstrip('/'))],
                   capture_output=True, text=True)
print('渲染 JS 语法:', 'OK' if r.returncode == 0 else '✗ ' + r.stderr[:200])
r2 = subprocess.run([NODE, '--check', p_main], capture_output=True, text=True)
print('main.js 语法:', 'OK' if r2.returncode == 0 else '✗ ' + r2.stderr[:200])
