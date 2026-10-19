package com.jcfootball.panel;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.view.KeyEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 竞彩足球数据分析台 - Android 壳（v1.4 自动同步版）
 *
 * 设计目标：网页更新 = App 更新，用户永远不需要因为「改了网页内容」而重装 APK。
 *
 * 运行策略：
 *   1. 首屏加载本地缓存页（首次安装时由 assets 快照复制而来），秒开，离线可用；
 *   2. 后台同步线程（启动 / 回到前台 / 每 3 分钟）拉取线上 version.json：
 *      - pageVersion 变了 → 下载线上 index.html 覆盖缓存 → 重新加载（仅此一次，之后秒开）
 *      - 只是数据时间变了 → 只下载 matches.json 覆盖缓存（约 100KB，很便宜）
 *   3. 页面自身的「每 2 分钟拉取」读的就是缓存目录里的 matches.json，因此始终是最新的；
 *   4. 无网 / 拉取失败 → 静默保持本地内容，不影响使用。
 *
 * 只有本文件（Java 原生代码）改动时才需要重新安装 APK；网页与数据的改动一律自动同步。
 */
public class MainActivity extends Activity {

    private static final String REMOTE_BASE = "https://jc-football-1eo.pages.dev/";
    private static final int TIMEOUT_MS = 30000;
    private static final long SYNC_INTERVAL_MS = 3 * 60 * 1000L;

    private WebView web;
    private final Handler handler = new Handler();
    private File cacheDir;
    private boolean syncing = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        cacheDir = new File(getFilesDir(), "page");
        cacheDir.mkdirs();

        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(false);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setAllowFileAccess(true);
        s.setAllowUniversalAccessFromFileURLs(true); // 允许 file:// 页面读取同目录 JSON
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setTextZoom(100);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        // 暴露给页面的同步入口：点「最新数据」时立即拉一次
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void syncNow() {
                runOnUiThread(new Runnable() {
                    @Override public void run() { startSync(true); }
                });
            }
        }, "AndroidSync");

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                if (Build.VERSION.SDK_INT >= 21 && request != null && request.getUrl() != null) {
                    String u = request.getUrl().toString();
                    if (u.endsWith("matches.json")) return serveJson("matches.json");
                    if (u.endsWith("version.json")) return serveJson("version.json");
                }
                return null;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // 停留在本地页面，禁止跳出到外部浏览器或其他 App
                return !(url != null && url.startsWith("file://"));
            }
        });

        ensureCache();
        loadPage();
        startSync(false);
        handler.postDelayed(syncLoop, SYNC_INTERVAL_MS);
    }

    // ---------------- 页面加载 ----------------

    /** 首次安装/覆盖安装时，把 assets 内的快照复制到可写缓存目录。
     *  关键修复：不能只看文件是否存在。覆盖安装时旧缓存目录可能保留，
     *  若 assets 里的快照比本地缓存新，必须强制覆盖，否则用户永远看到旧数据。 */
    private void ensureCache() {
        File idx = new File(cacheDir, "index.html");
        File mj = new File(cacheDir, "matches.json");

        String assetVer = assetPageVersion();
        String assetTime = assetUpdateTime();
        String localVer = scanVersion(idx);
        String localTime = match(readHead(mj, 8192), "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");

        boolean needCopy = !idx.exists() || idx.length() == 0;
        if (!needCopy && assetVer != null && localVer != null
                && verCode(assetVer) > verCode(localVer)) {
            needCopy = true;
        }
        if (!needCopy && assetTime != null && localTime != null
                && assetTime.compareTo(localTime) > 0) {
            needCopy = true;
        }

        if (needCopy) {
            try {
                copyAsset("index.html", idx);
            } catch (Exception e) {
                // 复制失败则直接使用 assets 快照（只读，数据无法增量更新）
            }
            try {
                copyAsset("matches.json", mj);
            } catch (Exception ignored) {
            }
            writeFile(new File(cacheDir, "version.json"),
                    "{\"updateTime\":\"" + localUpdateTime() + "\",\"deployTime\":\"" + localUpdateTime()
                            + "\",\"pageVersion\":\"" + localPageVersion() + "\"}");
        }
    }

    private void copyAsset(String name, File out) throws Exception {
        InputStream is = getAssets().open(name);
        OutputStream os = new FileOutputStream(out);
        byte[] buf = new byte[8192];
        int n;
        while ((n = is.read(buf)) > 0) os.write(buf, 0, n);
        os.close();
        is.close();
    }

    private void loadPage() {
        File idx = new File(cacheDir, "index.html");
        if (idx.exists() && idx.length() > 0) {
            // 关键修复：同一个 file:// URL WebView 会用页面缓存，导致文件更新了仍显示旧页。
            // 每次 load 都加随机时间戳，强制 WebView 重新读文件。
            web.clearCache(true);
            web.loadUrl("file://" + idx.getAbsolutePath() + "?t=" + System.currentTimeMillis());
        } else {
            web.loadUrl("file:///android_asset/index.html?t=" + System.currentTimeMillis());
        }
    }

    // ---------------- 同步 ----------------

    private final Runnable syncLoop = new Runnable() {
        @Override public void run() {
            startSync(false);
            handler.postDelayed(this, SYNC_INTERVAL_MS);
        }
    };

    private synchronized void startSync(boolean manual) {
        if (syncing) return;
        syncing = true;
        final boolean showTip = manual;
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    doSync(showTip);
                } catch (Throwable ignored) {
                } finally {
                    syncing = false;
                }
            }
        }).start();
    }

    /** 线上页面版本：优先读 version.json 的 pageVersion，缺失时直接读线上页面头部兜底 */
    private String remotePageVersion() {
        byte[] v = httpGet(REMOTE_BASE + "version.json");
        if (v != null) {
            String pv = match(new String(v, java.nio.charset.StandardCharsets.UTF_8),
                    "\"pageVersion\"\\s*:\\s*\"([^\"]+)\"");
            if (pv != null) return pv;
        }
        // 兜底：Range 请求线上页面前 256KB，直接解析 const VERSION
        // （用根路径 /：Pages 对 /index.html 是 308 跳 /，根路径直返 200 少一跳）
        byte[] head = httpGetRange(REMOTE_BASE, 0, 262143);
        if (head != null) {
            String pv = match(new String(head, java.nio.charset.StandardCharsets.UTF_8),
                    "const\\s+VERSION\\s*=\\s*\"([^\"]+)\"");
            if (pv != null) return pv;
        }
        return null;
    }

    private void doSync(final boolean showTip) {
        // 1) 轻量：拿线上版本信息
        byte[] vBytes = httpGet(REMOTE_BASE + "version.json");
        String remoteVer = remotePageVersion();   // 页面版本
        String remoteUpd = null;   // 数据时间
        if (vBytes != null) {
            String v = new String(vBytes, java.nio.charset.StandardCharsets.UTF_8);
            remoteVer = match(v, "\"pageVersion\"\\s*:\\s*\"([^\"]+)\"");
            remoteUpd = match(v, "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");
            if (remoteUpd == null) remoteUpd = match(v, "\"deployTime\"\\s*:\\s*\"([^\"]+)\"");
        }

        // 2) 页面版本变了，或线上「数据时间」比本地新 → 下载整页
        //    关键修复：数据每天刷新但版本号常不变，旧逻辑只在版本号变大时才重下整页，
        //    导致 App 一旦拉到过某版页面就死守其内嵌的旧数据。这里改为：
        //    只要线上数据时间比本地更新，也重拉整页，保证「数据已过期」不会再卡住。
        boolean verNewer = remoteVer != null && verCode(remoteVer) > verCode(localPageVersion());
        long remoteMs = parseTime(remoteUpd);
        long localMs = parseTime(localUpdateTime());
        boolean dataNewer = remoteMs > 0 && localMs > 0 && remoteMs > localMs;
        if (verNewer || dataNewer) {
            // 根路径 / 直返页面（/index.html 在 Pages 上是 308 跳 /，已由 httpGetFollow 兼容；
            // 这里直接用根路径少一跳）
            byte[] html = httpGet(REMOTE_BASE);
            if (html != null && html.length > 100000) {
                File idx = new File(cacheDir, "index.html");
                writeBytes(idx, html);
                // 同步一份最新的数据文件，避免新页面读到旧数据
                byte[] mj = httpGet(REMOTE_BASE + "matches.json");
                if (mj != null && mj.length > 0) writeBytes(new File(cacheDir, "matches.json"), mj);
                if (vBytes != null) writeBytes(new File(cacheDir, "version.json"), vBytes);
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        Toast.makeText(MainActivity.this,
                                "已同步到最新版本 " + localPageVersion(), Toast.LENGTH_SHORT).show();
                        loadPage();
                    }
                });
                return;
            }
        }

        // 3) 只更新数据（便宜，每次都做）
        byte[] mj = httpGet(REMOTE_BASE + "matches.json");
        if (mj != null && mj.length > 0) {
            String txt = new String(mj, java.nio.charset.StandardCharsets.UTF_8);
            String ut = match(txt, "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");
            String cur = localUpdateTime();
            if (ut != null && ut.compareTo(cur) > 0) {
                writeBytes(new File(cacheDir, "matches.json"), mj);
            }
        }
        if (vBytes != null) writeBytes(new File(cacheDir, "version.json"), vBytes);

        if (showTip) {
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    Toast.makeText(MainActivity.this, "已拉取最新数据", Toast.LENGTH_SHORT).show();
                    reloadDataOnly();
                }
            });
        }
    }

    /** 数据文件已更新，让页面自己重新拉取一次（不整页刷新，保留滚动位置） */
    private void reloadDataOnly() {
        if (Build.VERSION.SDK_INT >= 19) {
            web.evaluateJavascript(
                    "(function(){try{ if(typeof refreshRealtimeData==='function'){refreshRealtimeData(true); return 'ok';} }catch(e){} return 'na';})();",
                    null);
        }
    }

    // ---------------- 本地信息 ----------------

    /** 当前缓存页的版本号（从 index.html 里读 const VERSION = "x"） */
    private String localPageVersion() {
        String v = scanVersion(new File(cacheDir, "index.html"));
        if (v != null) return v;
        // 回退：assets 快照
        try {
            InputStream is = getAssets().open("index.html");
            v = scanVersion(is);
            is.close();
        } catch (Exception ignored) {
        }
        return v != null ? v : "v0.0.0";
    }

    /** APK 自带 assets/index.html 的版本号，用于覆盖安装时判断是否需要刷新本地缓存 */
    private String assetPageVersion() {
        try {
            InputStream is = getAssets().open("index.html");
            String v = scanVersion(is);
            is.close();
            return v;
        } catch (Exception ignored) {
        }
        return null;
    }

    /** APK 自带 assets/matches.json 的数据时间 */
    private String assetUpdateTime() {
        try {
            InputStream is = getAssets().open("matches.json");
            String s = readHead(is, 8192);
            is.close();
            return match(s, "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");
        } catch (Exception ignored) {
        }
        return null;
    }

    private static final Pattern VER_PATTERN =
            Pattern.compile("const\\s+VERSION\\s*=\\s*\"([^\"]+)\"");

    private String scanVersion(File f) {
        if (f == null || !f.exists()) return null;
        try {
            return scanVersion(new FileInputStream(f));
        } catch (Exception e) {
            return null;
        }
    }

    private String scanVersion(InputStream in) {
        java.io.BufferedReader r = null;
        try {
            r = new java.io.BufferedReader(new java.io.InputStreamReader(in, java.nio.charset.StandardCharsets.UTF_8), 65536);
            String line;
            int guard = 0;
            while ((line = r.readLine()) != null && guard++ < 4000) {
                Matcher m = VER_PATTERN.matcher(line);
                if (m.find()) return m.group(1);
            }
        } catch (Exception ignored) {
        } finally {
            try { if (r != null) r.close(); } catch (Exception ignored2) {}
        }
        return null;
    }

    /** 当前缓存数据的数据时间 */
    private String localUpdateTime() {
        String ut = null;
        File f = new File(cacheDir, "matches.json");
        if (f.exists()) ut = match(readHead(f, 8192), "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");
        if (ut != null) return ut;
        try {
            InputStream is = getAssets().open("matches.json");
            ut = match(readHead(is, 8192), "\"updateTime\"\\s*:\\s*\"([^\"]+)\"");
            is.close();
        } catch (Exception ignored) {
        }
        return ut != null ? ut : "1970-01-01 00:00:00";
    }

    private String readHead(File f, int max) {
        try {
            InputStream is = new FileInputStream(f);
            return readHead(is, max);
        } catch (Exception e) {
            return "";
        }
    }

    private String readHead(InputStream is, int max) {
        try {
            byte[] buf = new byte[max];
            int n = is.read(buf);
            is.close();
            return n > 0 ? new String(buf, 0, n, java.nio.charset.StandardCharsets.UTF_8) : "";
        } catch (Exception e) {
            return "";
        }
    }

    // ---------------- 请求拦截（兜底，主要针对 http 请求） ----------------

    private WebResourceResponse serveJson(String name) {
        try {
            byte[] remote = httpGet(REMOTE_BASE + name);
            if (remote != null && remote.length > 0) return jsonResponse(new String(remote, java.nio.charset.StandardCharsets.UTF_8));
        } catch (Exception ignored) {
        }
        // 回退：缓存目录 / assets
        File f = new File(cacheDir, name);
        if (f.exists()) {
            try {
                return new WebResourceResponse("application/json", "utf-8", new FileInputStream(f));
            } catch (Exception ignored) {
            }
        }
        if ("matches.json".equals(name)) {
            try {
                return new WebResourceResponse("application/json", "utf-8", getAssets().open("matches.json"));
            } catch (Exception ignored) {
            }
        }
        // version.json 兜底：告诉页面当前已是最新
        return jsonResponse("{\"updateTime\":\"" + localUpdateTime() + "\","
                + "\"deployTime\":\"" + localUpdateTime() + "\","
                + "\"pageVersion\":\"" + localPageVersion() + "\"}");
    }

    // ---------------- 工具 ----------------

    private byte[] httpGet(String urlStr) {
        return httpGetFollow(urlStr, -1, 0);
    }

    /** 只取前 maxBytes 字节（用于读页面头部版本号，避免下载整页） */
    private byte[] httpGetRange(String urlStr, long from, int maxBytes) {
        return httpGetFollow(urlStr, from, maxBytes);
    }

    /**
     * 带手动重定向跟随的 GET。rangeBytes > 0 时附加 Range 头只读前 rangeBytes 字节。
     *
     * 为什么必须手动跟随：Cloudflare Pages 对 /index.html 返回 308 永久重定向到 /，
     * 而 Java HttpURLConnection 默认只跟随 301/302，**不跟随 307/308**——这导致
     * 整页同步自 App 安装起就永远失败（version.json / matches.json 无后缀名、
     * 不跳转，所以只有数据同步是好的、页面结构同步是坏的，表现为
     * 「网页版有的新版界面，App 里永远看不到」）。
     * 这里关闭自动跟随，对 301/302/303/307/308 统一手动跳转（最多 4 跳，
     * 相对 Location 自动解析为绝对地址）；本应用所有请求均为无 body 的 GET，
     * 307/308 保持 GET 语义跟随是安全的。
     */
    private byte[] httpGetFollow(String urlStr, long rangeFrom, int rangeBytes) {
        String cur = urlStr;
        for (int hop = 0; hop < 4; hop++) {
            HttpURLConnection conn = null;
            try {
                URL url = new URL(cur);
                conn = (HttpURLConnection) url.openConnection();
                conn.setInstanceFollowRedirects(false);   // 全部手动处理，行为确定
                conn.setConnectTimeout(TIMEOUT_MS);
                conn.setReadTimeout(TIMEOUT_MS);
                conn.setRequestMethod("GET");
                conn.setRequestProperty("User-Agent", "jc-football-android/1.4");
                if (rangeBytes > 0) {
                    conn.setRequestProperty("Range",
                            "bytes=" + rangeFrom + "-" + (rangeFrom + rangeBytes - 1));
                }
                int code = conn.getResponseCode();
                if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
                    String loc = conn.getHeaderField("Location");
                    if (loc == null || loc.isEmpty()) return null;
                    cur = new URL(new URL(cur), loc).toExternalForm();
                    continue;
                }
                if (code != 200 && code != 206) return null;
                InputStream is = conn.getInputStream();
                java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n, total = 0;
                while ((n = is.read(buf)) > 0) {
                    if (rangeBytes > 0) {
                        if (total >= rangeBytes) break;
                        int take = Math.min(n, rangeBytes - total);
                        bos.write(buf, 0, take);
                        total += take;
                    } else {
                        bos.write(buf, 0, n);
                    }
                }
                is.close();
                return bos.toByteArray();
            } catch (Exception e) {
                return null;
            } finally {
                if (conn != null) conn.disconnect();
            }
        }
        return null;   // 跳转过多，放弃
    }

    private String match(String text, String regex) {
        if (text == null) return null;
        Matcher m = Pattern.compile(regex).matcher(text);
        return m.find() ? m.group(1) : null;
    }

    /** 语义化版本号转整数，便于比较大小：v1.2.1 -> 10201 */
    private static int verCode(String v) {
        if (v == null) return 0;
        try {
            String s = v.replaceAll("[^0-9.]", "");
            String[] p = s.split("\\.");
            int a = p.length > 0 ? Integer.parseInt(p[0]) : 0;
            int b = p.length > 1 ? Integer.parseInt(p[1]) : 0;
            int c = p.length > 2 ? Integer.parseInt(p[2]) : 0;
            return a * 10000 + b * 100 + c;
        } catch (Exception e) {
            return 0;
        }
    }

    /** "yyyy-MM-dd HH:mm:ss" -> 毫秒时间戳，解析失败返回 0 */
    private long parseTime(String s) {
        if (s == null) return 0;
        try {
            java.text.SimpleDateFormat f = new java.text.SimpleDateFormat(
                    "yyyy-MM-dd HH:mm:ss", java.util.Locale.US);
            return f.parse(s).getTime();
        } catch (Exception e) {
            return 0;
        }
    }

    private void writeBytes(File f, byte[] data) {
        try {
            File tmp = new File(f.getAbsolutePath() + ".tmp");
            FileOutputStream os = new FileOutputStream(tmp);
            os.write(data);
            os.close();
            if (f.exists()) f.delete();
            tmp.renameTo(f);
        } catch (Exception ignored) {
        }
    }

    private void writeFile(File f, String content) {
        writeBytes(f, content.getBytes(java.nio.charset.Charset.forName("UTF-8")));
    }

    private WebResourceResponse jsonResponse(String body) {
        InputStream is = new ByteArrayInputStream(body.getBytes(java.nio.charset.Charset.forName("UTF-8")));
        WebResourceResponse r = new WebResourceResponse("application/json", "utf-8", is);
        if (Build.VERSION.SDK_INT >= 21) {
            Map<String, String> headers = new HashMap<String, String>();
            headers.put("Access-Control-Allow-Origin", "*");
            headers.put("Cache-Control", "no-store");
            r.setResponseHeaders(headers);
        }
        return r;
    }

    // ---------------- 生命周期 ----------------

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
        startSync(false);   // 回到前台时补一次同步
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.onPause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(syncLoop);
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
