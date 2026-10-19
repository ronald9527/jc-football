#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署 index.html + version.json 到 Cloudflare Pages。

设计原则（重要）：
  本项目是「无人值守定时任务」驱动的——每 30 分钟自动跑一次。
  因此**缺凭据时本脚本必须优雅降级，绝不能让定时任务失败**：
    · 无凭据 -> 照常生成 dist/ 离线部署包，打印配置引导，退出码 0
    · 有凭据 -> 正常部署，退出码 0；只有真正部署出错才非 0

  凭据读取优先级（便于 GitHub Actions 与本地两种环境）：
    1. 环境变量 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID
    2. 同目录 cf_config.json（本地用 setup_cf.py 写入，权限 600）

用法:
  python3 deploy_cf.py                 部署默认 index.html
  python3 deploy_cf.py --check         只检查凭据是否可用，不部署
"""
import os, re, json, shutil, sys, datetime, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "cf_config.json")


def load_cfg():
    """返回 (token, account, project, source)。缺项返回空串而非直接退出。"""
    cfg = {}
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print("! cf_config.json 解析失败：%s" % e)
            cfg = {}
    # 环境变量优先：GitHub Actions / CI 场景通过 Secrets 注入
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "") or cfg.get("token", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "") or cfg.get("account_id", "")
    project = os.environ.get("CLOUDFLARE_PROJECT", "") or cfg.get("project", "jc-football")
    source = "环境变量" if os.environ.get("CLOUDFLARE_API_TOKEN") else (
        "cf_config.json" if cfg.get("token") else "无")
    return token.strip(), account.strip(), (project or "jc-football").strip(), source


def _bump_patch(v):
    """v1.7.10 -> v1.7.11（保持 v 前缀与三段式；格式异常时原样返回）"""
    m = re.match(r'^(v?)(\d+)\.(\d+)\.(\d+)$', v or "")
    if not m:
        return v
    pre, a, b, c = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return "%s%d.%d.%d" % (pre, a, b, c + 1)


def template_autobump(html):
    """模板哈希自动升版防护（返回 (可能改写过的 html, page_version, page_build)）。

    场景：改了 template.html（UI/结构变化）却忘了手升 const VERSION。
    App 整页同步有两条判定：pageVersion 变大（verNewer）或线上数据时间比
    本地新（dataNewer）。数据每轮都刷所以 dataNewer 常为真，但「纯 UI 改动」
    也可能遇到数据时间相同的部署批次——手升版本号是唯一可靠的「页面结构
    已变」信号，漏升 → App 永远停在旧界面（用户侧症状：网页有的，App 没有）。
    这里以 template.html 的 SHA256 为指纹：与 .last_template.sha 记录不同
    即自动升 patch 位，同步改写 template.html（唯一真相源，保证下次
    build_panel/version_sync 拿到新号）与本次部署的 index.html。
    .last_template.sha 随自举包一起传播（见下方打包规则），保证模板与指纹
    同源比对；沙箱里即便残留旧指纹，也会被同包的新指纹覆盖，不会误判。
    """
    import hashlib
    template_src = os.path.join(BASE, "template.html")
    sha_file = os.path.join(BASE, ".last_template.sha")
    mv = re.search(r'const\s+VERSION\s*=\s*"([^"]+)"', html)
    page_version = mv.group(1) if mv else "v0.0.0"
    tpl_hash_now = ""
    if os.path.exists(template_src):
        try:
            with open(template_src, "rb") as f:
                tpl_hash_now = hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            print("! 读取 template.html 算哈希失败：%s" % e)
    page_build = tpl_hash_now[:12]
    tpl_hash_prev = ""
    if os.path.exists(sha_file):
        try:
            with open(sha_file, encoding="utf-8") as f:
                tpl_hash_prev = f.read().strip()
        except Exception:
            tpl_hash_prev = ""
    if tpl_hash_now and tpl_hash_prev and tpl_hash_now != tpl_hash_prev:
        bumped = _bump_patch(page_version)
        if bumped != page_version:
            print("! 模板已变化但 VERSION 未手升：自动 %s → %s"
                  % (page_version, bumped))
            page_version = bumped
            try:
                with open(template_src, encoding="utf-8") as f:
                    tpl_txt = f.read()
                tpl_new = re.sub(r'(const VERSION = ")[^"]+(")',
                                 r'\g<1>' + bumped + r'\g<2>', tpl_txt, count=1)
                if tpl_new != tpl_txt:
                    with open(template_src, "w", encoding="utf-8") as f:
                        f.write(tpl_new)
                    print("  已写回 template.html（下次构建即用新版本号）")
            except Exception as e:
                print("! 自动升版写回 template.html 失败：%s" % e)
            html = re.sub(r'(const VERSION = ")[^"]+(")',
                          r'\g<1>' + bumped + r'\g<2>', html, count=1)
    if tpl_hash_now:
        try:
            with open(sha_file, "w", encoding="utf-8") as f:
                f.write(tpl_hash_now)
        except Exception as e:
            print("! 写入 .last_template.sha 失败：%s" % e)
    return html, page_version, page_build


def build_app_html(page_version, update_time):
    """生成下载引导页 app.html：自动识别微信/QQ 弹出"在浏览器打开"指引；
    内嵌二维码（相机扫码即可在系统浏览器下载）；可复制直链。
    页面以 /app 提供（Cloudflare 干净 URL 不跳转），避免 app.html 的 308 在微信里打不开。"""
    pv = page_version.lstrip("v")
    apk_name = "jc-football-v%s.apk" % pv
    apk_url = "https://jc-football-1eo.pages.dev/" + apk_name
    # 本地生成二维码（PNG base64），指向 apk 直链：手机相机扫码 → 系统浏览器 → 直接下载
    qr_data = ""
    try:
        import io, base64, qrcode as _qr
        buf = io.BytesIO()
        _qr.make(apk_url, box_size=8, border=2).save(buf, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print("! 生成下载页二维码失败（不影响下载）：%s" % e)
    BASE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>竞彩足球数据分析台 · APP 下载</title>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       background:linear-gradient(160deg,#0f2027,#203a43,#2c5364);color:#eaf2f8;min-height:100vh;padding:20px}
  .card{width:100%;max-width:440px;margin:0 auto;background:rgba(255,255,255,.06);
        border:1px solid rgba(255,255,255,.15);border-radius:18px;padding:26px 22px;text-align:center;backdrop-filter:blur(8px)}
  h1{margin:0 0 4px;font-size:22px;letter-spacing:1px}
  .sub{margin:0 0 14px;color:#9fb6c4;font-size:14px}
  .ver{font-size:13px;color:#7fd1ff;margin-bottom:18px;min-height:18px}
  .btn{display:block;width:100%;padding:16px;margin:10px 0;border-radius:12px;text-decoration:none;
       font-size:18px;font-weight:700;color:#06222e;background:linear-gradient(90deg,#36d1dc,#5b86e5);
       box-shadow:0 6px 18px rgba(54,209,220,.35)}
  .btn:active{transform:scale(.98)}
  .qr{margin:16px auto 6px;width:170px;height:170px;background:#fff;border-radius:12px;padding:8px}
  .qr img{width:100%;height:100%;display:block}
  .qrcap{font-size:12px;color:#9fb6c4;margin:0 0 8px}
  .copy{display:inline-block;font-size:12px;color:#9fb6c4;border:1px solid rgba(255,255,255,.25);
        border-radius:8px;padding:6px 10px;margin:4px 0 14px;text-decoration:none}
  .steps{text-align:left;background:rgba(0,0,0,.18);border-radius:12px;padding:14px 16px;margin:8px 0 6px;font-size:13px;line-height:1.7}
  .steps h3{margin:0 0 8px;font-size:14px;color:#ffd479}
  ol{margin:0;padding-left:20px}
  .web{display:inline-block;margin-top:8px;color:#9fb6c4;font-size:13px;text-decoration:underline}
  /* 微信/QQ 内置浏览器拦截.apk，弹此横幅引导去系统浏览器 */
  #wx{display:none;position:fixed;top:0;left:0;right:0;background:#ff7043;color:#fff;
      font-size:14px;line-height:1.6;padding:14px 16px 16px;text-align:center;z-index:99;box-shadow:0 2px 8px rgba(0,0,0,.3)}
  #wx .arr{font-size:26px;line-height:1;display:block;margin-bottom:2px}
</style>
</head>
<body>
  <div id="wx"><span class="arr">↗</span>请点右上角「<b>⋯</b>」→ 选择「<b>在浏览器打开</b>」，<br>即可下载安装本 APP</div>
  <div class="card">
    <h1>竞彩足球数据分析台</h1>
    <p class="sub">安卓 APP 安装包</p>
    <div class="ver" id="ver">版本加载中…</div>
    <a class="btn" id="dl" href="__APK__" download>⬇ 下载安装包</a>
    <div class="qr"><img id="qr" src="__QR__" alt="扫码下载"></div>
    <p class="qrcap">或用手机相机扫上方二维码，在浏览器中下载</p>
    <a class="copy" id="copy" href="javascript:void(0)">复制下载链接</a>
    <div class="steps">
      <h3>安装步骤</h3>
      <ol>
        <li>用手机<b>浏览器</b>（Chrome / Safari / 系统浏览器）打开本页</li>
        <li>点上方「下载安装包」按钮</li>
        <li>若提示"未知来源"，到设置里允许本次安装即可</li>
        <li>装好后打开，数据与网页每 30 分钟自动同步，<b>无需再重装</b></li>
      </ol>
    </div>
    <a class="web" href="/">或直接使用网页版（无需安装，永远实时）</a>
  </div>
  <script>
    // 识别微信/QQ 内置浏览器，弹出"在浏览器打开"横幅
    var ua = navigator.userAgent || "";
    if (/MicroMessenger/i.test(ua) || /QQ\\//.test(ua)) {
      document.getElementById('wx').style.display = 'block';
    }
    fetch('version.json').then(function(r){return r.json();}).then(function(d){
      document.getElementById('ver').innerHTML =
        '当前版本 <b>'+(d.pageVersion||'')+'</b> · 数据更新 '+(d.updateTime||'');
      if(d.pageVersion){document.getElementById('dl').href='jc-football-'+d.pageVersion+'.apk';}
    }).catch(function(){});
    // 复制直链
    document.getElementById('copy').addEventListener('click', function(){
      var link = location.origin + '/__APK__';
      if(navigator.clipboard){navigator.clipboard.writeText(link).then(function(){
        document.getElementById('copy').textContent='已复制：'+link;});}
      else{var t=document.createElement('textarea');t.value=link;document.body.appendChild(t);t.select();
        try{document.execCommand('copy');document.getElementById('copy').textContent='已复制';}catch(e){}
        document.body.removeChild(t);}
    });
  </script>
</body>
</html>
"""
    BASE = BASE.replace("__APK__", apk_name).replace("__QR__", qr_data)
    return BASE


def find_wrangler():
    """按常见安装位置依次查找 wrangler（nvm / npm 全局 / PATH）。"""
    import glob
    cands = ["/home/user/.npm-global/bin/wrangler",
             os.path.expanduser("~/.npm-global/bin/wrangler"),
             "/usr/local/bin/wrangler"]
    # nvm 管理的 node 下全局安装的 wrangler，取版本号最大的那个
    found = sorted(glob.glob("/root/.nvm/versions/node/*/bin/wrangler"),
                   reverse=True)
    cands += found
    cands += glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/wrangler"))
    for p in cands:
        if p and os.path.exists(p):
            return p
    try:
        r = subprocess.run(["which", "wrangler"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def guide():
    print("-" * 60)
    print("未配置 Cloudflare 凭据 —— 已生成离线部署包，未上传。")
    print("配置一次即可永久生效，两种方式任选其一：")
    print()
    print("  方式一（本地，推荐）：")
    print("    cd %s && python3 setup_cf.py" % BASE)
    print("    按提示粘贴 API Token 与 Account ID，自动写入 cf_config.json")
    print()
    print("  方式二（GitHub Actions）：")
    print("    仓库 Settings -> Secrets and variables -> Actions")
    print("    新增 CLOUDFLARE_API_TOKEN 与 CLOUDFLARE_ACCOUNT_ID")
    print()
    print("  获取路径：Cloudflare 控制台 -> 我的个人资料 -> API 令牌")
    print("            -> 创建令牌：模板列表里没有 Pages，选「自定义令牌」，")
    print("               权限一行：帐户 | Cloudflare Pages | 编辑")
    print("-" * 60)


def main():
    args = set(sys.argv[1:])
    token, account, project, source = load_cfg()

    if "--check" in args:
        if token and account:
            print("CRED_OK 来源=%s project=%s" % (source, project))
            return 0
        print("CRED_MISSING 未检测到可用凭据")
        guide()
        return 1

    index_src = os.path.join(BASE, "index.html")
    for i, a in enumerate(sys.argv[1:]):
        if a == "--index" and i + 2 <= len(sys.argv) - 1:
            index_src = sys.argv[i + 2]
    if not os.path.exists(index_src):
        print("DEPLOY_FAIL: 找不到 %s" % index_src)
        return 1

    html = open(index_src, encoding="utf-8").read()
    m = re.search(r'"updateTime"\s*:\s*"([^"]+)"', html)
    upd = m.group(1) if m else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mc = re.search(r'"count"\s*:\s*(\d+)', html)
    count = int(mc.group(1)) if mc else 0
    deploy_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # pageVersion：供 Android App 判断「网页是否已更新」，决定是否下载新页面自动同步。
    # 经模板哈希自动升版防护：改了 template 忘手升 VERSION 时自动补升，并算 pageBuild 指纹
    html, page_version, page_build = template_autobump(html)

    dist = os.path.join(BASE, "dist")
    if os.path.exists(dist):
        shutil.rmtree(dist)
    os.makedirs(dist, exist_ok=True)
    shutil.copy(index_src, os.path.join(dist, "index.html"))
    meta = {"updateTime": upd, "deployTime": deploy_time, "count": count,
            "pageVersion": page_version, "pageBuild": page_build}
    with open(os.path.join(dist, "version.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    # 部署数据文件，供页面端动态 fetch 实时刷新
    for data_file in ["matches.json", "hkmo_cache.json", "odds_bsd_cache.json",
                      "odds_bsd.json", "fd_data.json"]:
        src = os.path.join(BASE, data_file)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dist, data_file))

    # 关键：历史战绩库一并部署到 Pages，作为云端副本。
    # 一旦沙箱被重置、本地 history.json 丢失，ensure_history.py 能从这里拉回来，
    # 避免「空历史重新部署 → 把线上那份也抹掉」的双杀。
    # whoscored 三件套（评估历史/预测缓存/近期元数据）同理：不上 Pages 的话，
    # 沙箱一重置，「比分预测」的历史回溯就归零，且预测缓存全部丢失无法补救。
    for data_file in ["history.json", "parlay_history.json", "hkmo_history.json",
                      "strongweak_history.json", "half_full_history.json",
                      "smart_model.json",
                      "whoscored_history.json", "whoscored_preview_cache.json",
                      "whoscored_recent.json"]:
        src = os.path.join(BASE, data_file)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dist, data_file))

    # ---- 自举代码包：让冷启动定时任务无需 git 即可拿到最新代码 ----
    # 定时任务沙箱无法访问 GitHub（网络受限），但能访问 Cloudflare Pages（部署要走 CF API）。
    # 因此把整套可重建代码打包上传；定时任务运行时从 Pages 下载即可自我更新，
    # 彻底摆脱「git pull 拉不到新代码 → 一直部署旧版」的死结。
    import tarfile
    bundle = os.path.join(BASE, "code-bundle.tar.gz")
    if os.path.exists(bundle):
        os.remove(bundle)
    exclude_dirs = {".git", "dist", "__pycache__", ".wrangler", "apk-build",
                    "node_modules"}
    exclude_ext = (".bak", ".tar.gz", ".idsig", ".apk", ".pyc")
    # 安全红线：自举包上传到公开 Pages，任何人可下载。
    # 含凭据的配置文件（cf_config.json 等）与 .last_ 指纹文件绝不进包，
    # 否则 Cloudflare token 直接泄露（2026-10-15 事故：漏排除 cf_config.json，
    # token 随自举包公开数小时，已修复+建议轮换）。
    def _is_secret(fn):
        return fn.endswith("_config.json") and not fn.endswith(".example")
    # 持久化数据/缓存：必须在生产端跨轮累积，绝不能被本地快照重置，
    # 因此不进自举代码包（代码包只承载「代码」，数据由流水线生成/累积）。
    persistent_data = {
        "whoscored_recent.json", "whoscored_preview_cache.json",
        "whoscored_history.json", "history.json", "parlay_history.json",
        "hkmo_history.json", "strongweak_history.json", "half_full_history.json",
        "smart_model.json", "matches.json", "whoscored_data.json",
    }
    # .last_deploy.* 是部署节流状态（时间戳/SHA），跨环境共享会让定时沙箱
    # 误判「刚部署过」而跳过部署——必须排除。
    # .last_template.sha 恰恰相反：它是 template.html 的指纹，必须与模板
    # 同包传播。若排除它，沙箱里残留的旧指纹会与新包模板必然不匹配 →
    # template_autobump 每轮都误判「模板变了但 VERSION 未升」→ 版本号
    # 每 30 分钟 +1 无限膨胀（2026-10-15 13:00 轮实测 v1.7.14→v1.7.15）。
    # 修故：模板指纹随包走，template 与 sha 永远同源，比对才可信。
    deploy_state_files = {".last_deploy.ts", ".last_deploy.sha"}
    # ---- APK 安装包：先定位，再进自举包，最后上 Pages ----
    # 为什么必须进自举包：定时任务沙箱解包 code-bundle 覆盖 /workspace/jc-football 后，
    # /workspace 顶层并没有 APK 文件（沙箱不是构建机）→ 部署时找不到 → 跳过上传；
    # 而 Pages 部署是全量替换，旧部署里的 APK 随之消失，线上 .apk 全部变成
    # HTML fallback（_headers 又强制 APK content-type，形成「假 200」：
    # 用户下载到 3.9MB HTML，装不了）。2026-10-18 实测复现。
    # 修故：APK 显式加入自举包（arc 为顶层文件名，解包后正好落在
    # /workspace/竞彩足球数据分析台-vX.apk，即下方 apk_src 寻找的位置），
    # 让每一轮定时部署都带着 APK 上线。
    apk_src = os.path.join(os.path.dirname(BASE),
                           "竞彩足球数据分析台-v%s.apk" % page_version.lstrip("v"))
    if not os.path.exists(apk_src):
        # 版本推进后本地没有对应版本 APK（如只改了网页忘了打包 App）时，
        # 回退用本地已有的最新版 APK——绝不能不上线，Pages 全量替换会让
        # 线上 .apk 全部消失（_headers 再把 HTML fallback 伪装成 APK content-type）。
        import glob as _glob
        import re as _re
        def _ver_key(path):
            m = _re.search(r'-v(\d+(?:\.\d+)*)\.apk$', path)
            return tuple(int(x) for x in m.group(1).split('.')) if m else (0,)
        _apks = sorted(_glob.glob(os.path.join(os.path.dirname(BASE),
                                               "竞彩足球数据分析台-v*.apk")),
                       key=_ver_key)
        if _apks:
            apk_src = _apks[-1]
            print("! 未找到 v%s APK，回退使用本地最新版：%s"
                  % (page_version.lstrip("v"), os.path.basename(apk_src)))
    with tarfile.open(bundle, "w:gz") as tf:
        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for fn in files:
                if (fn.endswith(exclude_ext) or _is_secret(fn)
                        or fn in persistent_data or fn in deploy_state_files):
                    continue
                if fn.startswith(".last_") and fn != ".last_template.sha":
                    continue
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, os.path.dirname(BASE))  # -> jc-football/...
                try:
                    tf.add(fp, arc, recursive=False)
                except Exception as e:
                    print("! 打包跳过 %s：%s" % (arc, e))
        if os.path.exists(apk_src):
            tf.add(apk_src, os.path.basename(apk_src), recursive=False)
            print("APK 已加入自举包：%s（定时沙箱解包后可直接部署上线）"
                  % os.path.basename(apk_src))
    shutil.copy(bundle, os.path.join(dist, "code-bundle.tar.gz"))
    print("已打包自举代码包 code-bundle.tar.gz（%d 字节）"
          % os.path.getsize(bundle))

    # ---- APK 安装包一并上线，方便手机端直接下载覆盖安装 ----
    # 同时产出「带版本号」文件名（如 jc-football-v1.7.10.apk）与稳定名 jc-football.apk。
    # 带版本号文件名每次部署都是新 URL -> 新 CDN 对象，彻底绕过 Cloudflare 对 .apk
    # 的 HEAD 脏缓存（表现为 content-length=32，手机下载器据此判损坏打不开）。
    if os.path.exists(apk_src):
        shutil.copy(apk_src, os.path.join(dist, "jc-football.apk"))
        shutil.copy(apk_src,
                    os.path.join(dist, "jc-football-v%s.apk" % page_version.lstrip("v")))
        print("已附带 APK 安装包 -> dist/jc-football.apk + jc-football-v%s.apk"
              % page_version.lstrip("v"))
    else:
        print("! 未找到 APK：%s（跳过，不影响网页）" % apk_src)

    # ---- _headers：让 .apk 强制下载、不被 CDN 缓存成脏对象 ----
    # Content-Disposition: attachment 让浏览器直接下载而非尝试打开；
    # Cache-Control: no-cache 每次回源校验，消除 32 字节脏缓存。
    headers_txt = (
        "/jc-football*.apk\n"
        "  Content-Type: application/vnd.android.package-archive\n"
        "  Content-Disposition: attachment\n"
        "  Cache-Control: no-cache\n"
    )
    with open(os.path.join(dist, "_headers"), "w", encoding="utf-8") as f:
        f.write(headers_txt)
    print("已写入 _headers（.apk 强制下载 / 不缓存）")

    # ---- 下载引导页 app.html：醒目下载按钮 + 安装指引，避免裸 .apk 被浏览器拦截 ----
    app_html = build_app_html(page_version, upd)
    with open(os.path.join(dist, "app.html"), "w", encoding="utf-8") as f:
        f.write(app_html)
    print("已生成下载引导页 dist/app.html")

    print("已生成部署包 dist/：%s（pageVersion=%s，pageBuild=%s，%d 场）"
          % (deploy_time, page_version, page_build or "-", count))

    # ---- 降级路径：无凭据 / 无 wrangler，均不视为失败 ----
    if not token or not account:
        guide()
        print("DEPLOY_SKIPPED 离线包已就绪：%s" % dist)
        return 0

    wrangler = find_wrangler()
    if not wrangler:
        print("! 未找到 wrangler，无法上传（离线包已生成于 dist/）")
        print("  可在任意装了 Node 的机器上执行：npx wrangler pages deploy dist --project-name %s" % project)
        print("DEPLOY_SKIPPED")
        return 0

    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = token
    env["CLOUDFLARE_ACCOUNT_ID"] = account
    env["CLOUDFLARE_ACCOUNT_ID_OVERRIDE"] = account
    try:
        r = subprocess.run([wrangler, "pages", "deploy", dist,
                            "--project-name", project, "--branch", "main"],
                           env=env, capture_output=True, text=True, timeout=180)
    except Exception as e:
        print("! wrangler 执行异常：%s" % e)
        print("DEPLOY_SKIPPED 离线包已就绪：%s" % dist)
        return 0

    out = (r.stdout or "") + (r.stderr or "")
    print(out[-1800:])
    if "Deployment complete" in out or ("Success" in out and "Uploaded" in out):
        print("DEPLOY_OK updateTime=%s deployTime=%s pageVersion=%s pageBuild=%s"
              % (upd, deploy_time, page_version, page_build or "-"))
        return 0
    print("DEPLOY_FAIL 离线包已保留：%s" % dist)
    return 1


if __name__ == "__main__":
    sys.exit(main())
