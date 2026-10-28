const path='/root/.nvm/versions/node/v22.13.1/lib/node_modules/@playwright/cli/node_modules/playwright-core';
const { chromium } = require(path);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/google/chrome/chrome', chromiumSandbox:false, args:['--no-sandbox']});
  const p = await b.newPage({viewport:{width:420,height:900}});
  await p.goto('https://jc-football-1eo.pages.dev/', {waitUntil:'networkidle', timeout:60000}).catch(()=>{});
  await p.waitForTimeout(2500);
  const inputs = await p.$$('input');
  if(inputs.length>=2){
    await inputs[0].fill('admin');
    await inputs[1].fill('512049996');
    const btn = await p.$('#loginBtn') || await p.$('button');
    if(btn) await btn.click();
    await p.waitForTimeout(4000);
  }
  console.log('loggedIn:', await p.evaluate(()=>!document.querySelector('#loginBtn') || document.querySelector('#loginBtn').offsetParent===null));
  await p.$$eval('button, .tab, .nav-item', els=>{ const t=els.find(e=>(e.textContent||'').includes('比分预测')); if(t) t.click(); });
  await p.waitForTimeout(2500);
  // 截 WhoScored 主区（今日竞彩预测比分）
  await p.evaluate(()=>{ const el=document.querySelector('#wsList'); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(1000);
  await p.screenshot({path:'/tmp/sp_ws.png'});
  // 截 Poisson 模型区
  await p.evaluate(()=>{ const el=[...document.querySelectorAll('.sp-sub-head')].find(e=>e.textContent.includes('Poisson')); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(1000);
  await p.screenshot({path:'/tmp/sp_poisson.png'});
  await b.close();
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
