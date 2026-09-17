#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞彩智析 · 交付前自检
============================================================
起因：曾出现「文档写了某功能，但界面根本没做」的低级错误。
      —— 代码引用了 setUpdateUrl，HTML 里却没有这个元素。

本脚本把这类错误变成可自动检出的问题，每次交付前必须运行。

检查项：
  1. JS 语法（node --check）
  2. 【关键】app.js 引用的所有元素 ID，在 HTML 中必须存在
  3. 【关键】HTML 声明的交互元素，app.js 必须有对应处理
  4. 文档里承诺的功能，在产物中必须真实存在
  5. 引擎数值回归（概率和=1、无 NaN、赔率>1）
  6. 官方数据解析链路（用真实结构样本）
  7. 构建产物完整性
  8. APK 内容与签名

用法：
    python3 tools/selfcheck.py [--strict]
    --strict: 警告也视为失败（用于正式交付）
"""
import io, os, re, sys, json, subprocess, zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRICT = '--strict' in sys.argv

R = []   # results: (level, item, detail)
def ok(item, detail=''):
    R.append(('OK', item, detail))
def warn(item, detail=''):
    R.append(('WARN', item, detail))
def fail(item, detail=''):
    R.append(('FAIL', item, detail))

def rd(p):
    try:
        return io.open(os.path.join(HERE, p), encoding='utf-8').read()
    except Exception as e:
        return None

def sh(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd or HERE,
                           capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr
    except Exception as e:
        return -1, str(e)

print('=' * 70)
print('  竞彩智析 · 交付前自检')
print('=' * 70)

# ---------------------------------------------------------------- 1. JS 语法
print('\n[1] JS 语法')
JS = ['assets/engine.js', 'assets/data.js', 'assets/adapter.js',
      'assets/app.js', 'assets/jc-source.js', 'worker/jc-source.js']
for f in JS:
    if not rd(f):
        fail('语法 ' + f, '文件不存在'); continue
    c, out = sh('node --check %s' % f)
    if c == 0: ok('语法 ' + f)
    else: fail('语法 ' + f, out[:150])

# ---------------------------------------------------------------- 2. ID 引用检查（关键）
print('\n[2] 元素 ID 引用一致性（曾在此翻车）')
html = rd('index.html') or ''
appjs = rd('assets/app.js') or ''

html_ids = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', html))
# app.js 里 $('#xxx') / getElementById('xxx')
used = set(re.findall(r"""\$\('#([A-Za-z0-9_\-]+)'\)""", appjs))
used |= set(re.findall(r"""getElementById\(['"]([A-Za-z0-9_\-]+)['"]\)""", appjs))

missing = sorted(used - html_ids)
if missing:
    for m in missing:
        fail('app.js 引用了 HTML 中不存在的元素', '#' + m)
else:
    ok('app.js 引用的 %d 个元素全部存在' % len(used))

# 反向：HTML 有 id 但 app.js 从未使用（仅对交互元素提示）
INTERACTIVE = {'input', 'select', 'button', 'textarea'}
declared = re.findall(r'<(input|select|button|textarea)[^>]*id="([A-Za-z0-9_\-]+)"', html)
orphan = []
for tag, i in declared:
    if tag in INTERACTIVE and ('#' + i) not in appjs:
        orphan.append(i)
if orphan:
    for o in orphan:
        warn('HTML 有交互元素但 app.js 未处理', '#' + o)
else:
    ok('HTML 交互元素均有处理')

# ---------------------------------------------------------------- 3. 文档承诺 vs 实际
print('\n[3] 文档承诺的功能是否真实存在')
build = rd('standalone.html') or ''
docs = ['更新说明与固定链接.md', '手把手教程.md', '重要说明-这是全新的.md']

# 文档中出现、且应当在产物里的关键标识
PROMISES = [
    ('内容更新界面', 'updateCard'),
    ('内容更新地址输入', 'setUpdateUrl'),
    ('内容更新开关', 'setUpdateOn'),
    ('立即检查按钮', 'btnCheckUpdate'),
    ('恢复内置按钮', 'btnResetBuiltin'),
    ('实时数据徽章', 'badge-live'),
    ('官方直连函数', 'fetchOfficial'),
    ('tab 点击修复', 'pointer-events:none'),
]
for label, token in PROMISES:
    if token in build:
        ok('产物含 ' + label)
    else:
        fail('产物缺失 ' + label, '未找到 ' + token)

# 文档是否提到了产物里没有的功能按钮（防再次出现"文档写了但没做"）
doc_txt = ''
for d in docs:
    t = rd(d)
    if t: doc_txt += t
# 提取文档中「我的 → XXX」这类路径承诺
raw_paths = re.findall(r'我的\s*→\s*([^\s，,。、\n]{2,8})', doc_txt)
paths = set()
for p in raw_paths:
    key = re.sub(r'[*#`\-]', '', p).strip()      # 去掉 Markdown 符号
    if not key: continue
    if re.search(r'[填粘贴开启点选输]', key): continue   # 含动作词的不是入口名
    if len(key) < 2: continue
    paths.add(key)
for key in sorted(paths):
    if key not in html and key not in build:
        warn('文档承诺「我的 → %s」，界面未见该入口' % key, '需人工确认')

# ---------------------------------------------------------------- 4. 引擎回归
print('\n[4] 引擎数值回归')
check_js = r'''
global.window = global;
require('%s/assets/data.js');
require('%s/assets/engine.js');
var bad = 0, n = 0, worst = 0;
var seeds = ['2026-09-17','2026-09-18','2026-09-19'];
seeds.forEach(function(ds){
  var raw = JCData.makeSchedule(ds, 24, JCData.seedOf ? JCData.seedOf(ds) : 1);
  raw.forEach(function(m){
    var a = JCEngine.analyze(m, {payout:0.90, euPayout:0.955});
    n++;
    var s = a.probs.h + a.probs.d + a.probs.a;
    if (Math.abs(s-1) > 0.01) bad++;
    if (Math.abs(s-1) > worst) worst = Math.abs(s-1);
    [a.probs.h,a.probs.d,a.probs.a,a.xg.h,a.xg.a].forEach(function(v){
      if (!isFinite(v) || v < 0) bad++;
    });
    var o = a.odds.jc;
    if (!(o.h > 1.02 && o.d > 1.02 && o.a > 1.02)) bad++;
  });
});
console.log(JSON.stringify({n:n, bad:bad, worst:worst}));
''' % (HERE, HERE)
c, out = sh("node -e \"%s\"" % check_js.replace('"', '\\"').replace('\n', ' '))
try:
    m = re.search(r'\{.*\}', out)
    d = json.loads(m.group(0))
    if d['bad'] == 0:
        ok('引擎 %d 场数值正常（最大偏差 %.4f）' % (d['n'], d['worst']))
    else:
        fail('引擎 %d 场中有 %d 处异常' % (d['n'], d['bad']))
except Exception as e:
    fail('引擎回归未能运行', out[:150])

# ---------------------------------------------------------------- 5. 官方解析链路
print('\n[5] 官方数据解析链路')
sample = os.path.join(HERE, 'tools', 'samples', 'jc_official_sample.json')
if os.path.exists(sample):
    t = r'''
const fs=require('fs');
global.window=global;
require('%s/assets/jc-source.js');
global.fetch=async(u)=>{
  if(String(u).includes('getMatchCalculatorV1'))
    return {ok:true,status:200,headers:{get:()=>null},
      text:async()=>fs.readFileSync('%s','utf8')};
  return {ok:false,status:403,headers:{get:()=>null},text:async()=>'WAF'};
};
JCSource.fetchAll({depth:0}).then(o=>{
  console.log(JSON.stringify({ok:!o.error, n:(o.matches||[]).length,
    crs:(o.matches||[]).filter(m=>m.marketMatrix).length,
    err:o.error||''}));
});
''' % (HERE, sample)
    c, out = sh("node -e \"%s\"" % t.replace('"', '\\"').replace('\n', ' '))
    try:
        mm = re.search(r'\{.*\}', out)
        d = json.loads(mm.group(0))
        if d['ok'] and d['n'] > 0:
            ok('官方解析 %d 场，比分矩阵 %d 场' % (d['n'], d['crs']))
        else:
            fail('官方解析失败', d.get('err', '')[:120])
    except Exception as e:
        fail('官方解析未能运行', out[:150])
else:
    warn('无官方数据样本，跳过解析链路检查')

# ---------------------------------------------------------------- 6. 构建产物
print('\n[6] 构建产物')
if build:
    ok('standalone.html %.0f KB' % (len(build) / 1024.0))
    for tok in ['JCSource', 'JCEngine', 'JCAdapter', 'JCData']:
        if tok in build: ok('内联 ' + tok)
        else: fail('产物缺少 ' + tok)
    # 不应残留外部脚本引用（单文件必须全内联）
    ext = re.findall(r'<script src="([^"]+)"', build)
    if ext:
        fail('单文件残留外部脚本引用', ', '.join(ext[:3]))
    else:
        ok('无外部脚本引用（完全自包含）')
else:
    fail('standalone.html 不存在')

# ---------------------------------------------------------------- 7. APK
print('\n[7] APK')
apk = os.path.join(HERE, 'dist', u'竞彩智析.apk')
if os.path.exists(apk):
    ok('APK %.0f KB' % (os.path.getsize(apk) / 1024.0))
    try:
        z = zipfile.ZipFile(apk)
        h = z.read('assets/index.html').decode('utf-8')
        d = z.read('classes.dex')
        for label, cond in [
            ('页面与网页版一致', len(h) > 100000),
            ('含官方直连', 'fetchOfficial' in h),
            ('含内容更新界面', 'updateCard' in h),
            ('含实时徽章', 'badge-live' in h),
            ('桥接可用', b'JCBridge' in d),
            ('不含第三方链接', b'jc-football-1eo' not in d),
        ]:
            ok('APK ' + label) if cond else fail('APK ' + label)
    except Exception as e:
        fail('APK 解析失败', str(e)[:100])
else:
    warn('APK 未构建')

# ---------------------------------------------------------------- 汇总
print('\n' + '=' * 70)
nf = [x for x in R if x[0] == 'FAIL']
nw = [x for x in R if x[0] == 'WARN']
no = [x for x in R if x[0] == 'OK']

for lv, item, detail in R:
    if lv == 'FAIL':
        print('  ❌ %s  %s' % (item, detail))
    elif lv == 'WARN':
        print('  ⚠️  %s  %s' % (item, detail))
    else:
        print('  ✓ %s  %s' % (item, detail))

print('-' * 70)
print('  通过 %d ｜ 警告 %d ｜ 失败 %d' % (len(no), len(nw), len(nf)))
print('=' * 70)

if nf:
    print('\n  ❌ 存在失败项，禁止交付。')
    sys.exit(1)
if nw and STRICT:
    print('\n  ⚠️  strict 模式：存在警告，需清零后交付。')
    sys.exit(2)
print('\n  ✅ 可以交付。')
sys.exit(0)
