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

async function findLatestDispatchRun(token) {
  // 优先按 event=workflow_dispatch 过滤；GitHub 登记 run 有 1-3s 延迟，故多取几条。
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/runs?event=workflow_dispatch&per_page=8`,
    { headers: ghHeaders(token) }
  );
  if (!resp.ok) return null;
  const j = await resp.json();
  let runs = (j.workflow_runs || []).filter((r) => r.event === "workflow_dispatch");
  if (runs.length === 0) runs = j.workflow_runs || []; // 兜底：取最近一次任意运行
  runs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  return runs[0] || null;
}

export async function onRequestPost({ env }) {
  const token = env.GH_PAT;
  if (!token) return json({ ok: false, error: "服务端未配置 GH_PAT" }, 500);
  try {
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
    // 等待 GitHub 把 run 登记进列表
    await sleep(5000);
    const run = await findLatestDispatchRun(token);
    return json({
      ok: true,
      runId: run ? run.id : null,
      htmlUrl: run ? run.html_url : null,
      message: "已触发重新抓取，云端正在拉取全部数据并部署（约 1-4 分钟）",
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
