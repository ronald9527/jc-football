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
    # pageVersion：供 Android App 判断「网页是否已更新」，决定是否下载新页面自动同步
    mv = re.search(r'const\s+VERSION\s*=\s*"([^"]+)"', html)
    page_version = mv.group(1) if mv else "v0.0.0"

    dist = os.path.join(BASE, "dist")
    if os.path.exists(dist):
        shutil.rmtree(dist)
    os.makedirs(dist, exist_ok=True)
    shutil.copy(index_src, os.path.join(dist, "index.html"))
    meta = {"updateTime": upd, "deployTime": deploy_time, "count": count,
            "pageVersion": page_version}
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
    for data_file in ["history.json", "parlay_history.json", "hkmo_history.json",
                      "strongweak_history.json", "half_full_history.json",
                      "smart_model.json"]:
        src = os.path.join(BASE, data_file)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dist, data_file))

    print("已生成部署包 dist/：%s（pageVersion=%s，%d 场）" % (deploy_time, page_version, count))

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
        print("DEPLOY_OK updateTime=%s deployTime=%s pageVersion=%s"
              % (upd, deploy_time, page_version))
        return 0
    print("DEPLOY_FAIL 离线包已保留：%s" % dist)
    return 1


if __name__ == "__main__":
    sys.exit(main())
