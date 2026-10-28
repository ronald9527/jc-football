const path='/root/.nvm/versions/node/v22.13.1/lib/node_modules/@playwright/cli/node_modules/playwright-core';
const { chromium } = require(path);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/google/chrome/chrome', chromiumSandbox:false, args:['--no-sandbox']});
  const p = await b.newPage({viewport:{width:420,height:900}});
  await p.goto('https://jc-football-1eo.pages.dev/', {waitUntil:'networkidle', timeout:60000}).catch(()=>{});
  await p.waitForTimeout(3000);
  // 登录
  const inp = await p.$('#pwdInput, input[type=password]');
  if(inp){ await inp.fill('512049996');
    const btn = await p.$('#loginBtn'); if(btn) await btn.click(); await p.waitForTimeout(4000); }
  // 切到比分预测 Tab
  const tab = await p.$('#tabScorePred, [data-tab*="score"], [data-tab*="sp"]');
  const tabs = await p.$$eval('.tab, .nav-item, button', els=>els.map(e=>e.textContent.trim()).filter(t=>t.includes('比分')||t.includes('预测')));
  console.log('tabs:', tabs);
  if(tabs.length){
    const el = await p.$$eval('button, .tab, .nav-item', els=>{
      const t = els.find(e=>(e.textContent||'').includes('比分预测'));
      if(t){ t.click(); return true; } return false;
    });
    console.log('clicked:', el);
  }
  await p.waitForTimeout(3000);
  await p.screenshot({path:'/tmp/sp_top.png'});
  // 滚动到 Poisson 模型区
  await p.evaluate(()=>{ const el=[...document.querySelectorAll('.sp-sub-head')].find(e=>e.textContent.includes('Poisson')); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(1500);
  await p.screenshot({path:'/tmp/sp_poisson.png'});
  // 展开模型第一组，截几张
  const err = await p.evaluate(()=>document.body.innerText.slice(0,200));
  console.log('head:', err.replace(/\n/g,' | ').slice(0,150));
  await b.close();
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
