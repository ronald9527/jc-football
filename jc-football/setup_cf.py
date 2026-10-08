#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_cf.py —— Cloudflare 部署凭据一键配置

为什么需要你亲自粘贴：
  API Token 是绑定你个人 Cloudflare 账户的密钥，只能由你在 Cloudflare
  控制台生成。我无法、也不应该替你编造或硬编码它。但配置一次即永久生效，
  之后定时任务与手动部署都不再需要人工干预。

获取步骤（约 1 分钟）：
  1. 打开 https://dash.cloudflare.com/profile/api-tokens
  2. 「创建令牌」-> 模板列表里没有 Pages，选「自定义令牌」（Custom token）
  3. 权限只需一行：帐户 | Cloudflare Pages | 编辑
  3. 账户资源选你的账户，区域资源可留默认 -> 创建
  4. 复制那串令牌（只显示一次）
  5. Account ID：Cloudflare 控制台右侧边栏底部，或任意站点概览页右下角

用法：
  python3 setup_cf.py                                  # 交互模式
  python3 setup_cf.py --token xxxx --account yyyy      # 参数模式
  python3 setup_cf.py --token xxxx --account yyyy --verify   # 写入并联网校验
  python3 setup_cf.py --show                           # 查看当前配置状态
"""
import argparse
import getpass
import json
import os
import stat
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "cf_config.json")


def mask(s):
    if not s:
        return "(空)"
    if len(s) <= 10:
        return "*" * len(s)
    return s[:6] + "*" * 8 + s[-4:]


def load():
    if os.path.exists(CFG):
        try:
            with open(CFG, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save(cfg):
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # 凭据文件只对本用户可读写，避免被其他进程/用户读到
    try:
        os.chmod(CFG, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def verify(token, account):
    """调用 Cloudflare API 校验令牌是否有效。返回 (ok, msg)"""
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("success"):
            return True, "令牌有效"
        return False, "令牌无效：" + json.dumps(data.get("errors"), ensure_ascii=False)
    except urllib.error.HTTPError as e:
        return False, "校验失败 HTTP %s（令牌可能无效或权限不足）" % e.code
    except Exception as e:
        return False, "校验失败：%s（可能是网络不通，不影响写入）" % e


def main():
    ap = argparse.ArgumentParser(description="配置 Cloudflare Pages 部署凭据")
    ap.add_argument("--token", default="")
    ap.add_argument("--account", dest="account", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--verify", action="store_true", help="写入后立即联网校验令牌")
    ap.add_argument("--show", action="store_true", help="仅显示当前配置状态")
    args = ap.parse_args()

    cur = load()

    if args.show:
        print("配置文件: %s" % CFG)
        print("  token     : %s" % mask(cur.get("token", "")))
        print("  account_id: %s" % (cur.get("account_id") or "(空)"))
        print("  project   : %s" % (cur.get("project") or "(空)"))
        if not cur.get("token"):
            print("\n当前未配置凭据，部署会降级为只生成离线包。")
        return 0

    token = args.token.strip()
    account = args.account.strip()
    project = args.project.strip() or cur.get("project") or "jc-football"

    if not token or not account:
        print("== Cloudflare Pages 部署凭据配置 ==")
        print("获取地址: https://dash.cloudflare.com/profile/api-tokens")
        print("（输入内容不会回显到历史，直接粘贴即可）\n")
        if not token:
            token = getpass.getpass("API Token: ").strip()
        if not account:
            account = input("Account ID: ").strip()

    if not token or not account:
        print("✗ token 与 account_id 均不能为空")
        return 1

    cfg = {"token": token, "account_id": account, "project": project}
    save(cfg)
    print("\n✓ 已写入 %s（权限 600）" % CFG)
    print("  token     : %s" % mask(token))
    print("  account_id: %s" % account)
    print("  project   : %s" % project)

    if args.verify or not args.token:
        print("\n→ 正在校验令牌 ...")
        ok, msg = verify(token, account)
        print("  %s %s" % ("✓" if ok else "✗", msg))
        if not ok:
            print("\n配置已保存但校验未通过。常见原因：")
            print("  · 权限不够 —— 自定义令牌里应加：帐户 | Cloudflare Pages | 编辑")
            print("  · Account ID 与令牌所属账户不匹配")
            print("  · 沙箱网络不通（不影响本地保存，换台机器再试）")
            return 1

    print("\n下一步：执行 `python3 deploy_cf.py` 即可上传。")
    print("定时任务会在此之后自动生效，无需再干预。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
