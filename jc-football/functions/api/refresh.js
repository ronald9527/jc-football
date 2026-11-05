/**
 * Cloudflare Pages Function: /api/refresh
 * 作用：作为「手动重新抓取全部数据」按钮的服务端代理。
 *   - POST：用服务端持有的 GitHub PAT 触发仓库的 daily.yml 工作流
 *           （完整抓取竞彩/V站/高手推荐等全部数据源 → 构建 → 部署），
 *           返回本次运行的 run_id 供前端轮询进度。
 *   - GET  ?run_id=xxx：查询该次运行的状态（queued/in_progress/completed + conclusion）。
 *
 * 设计要点：
 *   - PAT 不进前端、不进代码仓库，仅作为 Pages 项目环境变量（secret_text）注入，
 *     运行时通过 env.GH_PAT 读取。
 *   - 该函数与前端同源（同 *.pages.dev 域），浏览器调用无 CORS 阻隔。
 *   - 所有出站请求走 Cloudflare 边缘，不受浏览器跨域限制。
 */

const REPO = "ronald9527/jc-football";
const WF = "daily.yml";

function ghHeaders(token) {
  return {
    Authorization: "Bearer " + token,
    Accept: "application/vnd.github+json",
    "User-Agent": "jc-football-refresh-fn",
    "Content-Type": "application/json",
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function findLatestDispatchRun(token, dispatchedAt) {
  // GitHub 的 workflow_runs 列表对新登记 run 有延迟（常 10-30s），且带缓存。
  // 必须破缓存 + 严格只认「本次触发之后登记」的 run：
  // 早期实现会在查不到新 run 时回退到历史 dispatch 列表，把几个小时前
  // 早已 success 的旧任务返回给前端 → 前端立刻重载，用户看到的仍是旧数据，
  // 表现就是「按了刷新数据按钮没反应」。
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/runs?event=workflow_dispatch&per_page=15&_t=${Date.now()}`,
    { headers: { ...ghHeaders(token), "Cache-Control": "no-cache" } }
  );
  if (!resp.ok) return null;
  const j = await resp.json();
  const runs = (j.workflow_runs || []).filter(
    (r) =>
      r.event === "workflow_dispatch" &&
      new Date(r.created_at).getTime() >= dispatchedAt - 15000
  );
  if (runs.length === 0) return null;   // 没找到就如实返回 null，绝不拿旧任务顶替
  runs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  return runs[0] || null;
}

export async function onRequestPost({ env }) {
  const token = env.GH_PAT;
  if (!token) return json({ ok: false, error: "服务端未配置 GH_PAT" }, 500);
  try {
    const dispatchedAt = Date.now();
    const disp = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WF}/dispatches`,
      {
        method: "POST",
        headers: ghHeaders(token),
        body: JSON.stringify({ ref: "main" }),
      }
    );
    if (disp.status !== 204) {
      const txt = await disp.text();
      return json(
        { ok: false, error: "触发工作流失败 HTTP " + disp.status, detail: txt.slice(0, 400) },
        502
      );
    }
    // 等待 GitHub 把 run 登记进列表（登记常需 10-30s，最多 6 次 × 3s）。
    // 仍找不到就返回 runId:null，由前端改为监听实际部署产物，绝不返回旧任务。
    let run = null;
    for (let i = 0; i < 6 && !run; i++) {
      await sleep(3000);
      run = await findLatestDispatchRun(token, dispatchedAt);
    }
    return json({
      ok: true,
      runId: run ? run.id : null,
      htmlUrl: run ? run.html_url : null,
      message: "已触发全面重新抓取（全部数据源 + 赛果回溯 + 命中统计），云端正在构建并部署",
    });
  } catch (e) {
    return json({ ok: false, error: "服务端异常：" + (e && e.message ? e.message : e) }, 500);
  }
}

export async function onRequestGet({ env, request }) {
  const token = env.GH_PAT;
  const url = new URL(request.url);
  const runId = url.searchParams.get("run_id");
  if (!runId) return json({ ok: false, error: "缺少 run_id" }, 400);
  try {
    const resp = await fetch(
      `https://api.github.com/repos/${REPO}/actions/runs/${runId}`,
      { headers: ghHeaders(token) }
    );
    if (!resp.ok) return json({ ok: false, error: "查询运行失败 HTTP " + resp.status }, 502);
    const j = await resp.json();
    return json({
      ok: true,
      status: j.status, // queued | in_progress | completed
      conclusion: j.conclusion, // success | failure | cancelled | timed_out | ...
      created_at: j.created_at,
      updated_at: j.updated_at,
      html_url: j.html_url,
    });
  } catch (e) {
    return json({ ok: false, error: "查询异常：" + (e && e.message ? e.message : e) }, 500);
  }
}
