const path='/root/.nvm/versions/node/v22.13.1/lib/node_modules/@playwright/cli/node_modules/playwright-core';
const { chromium } = require(path);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/google/chrome/chrome', chromiumSandbox:false, args:['--no-sandbox']});
  const p = await b.newPage({viewport:{width:420,height:900}});
  await p.goto('https://jc-football-1eo.pages.dev/', {waitUntil:'networkidle', timeout:60000}).catch(()=>{});
  await p.waitForTimeout(2500);
  const vis = await p.$$('input:visible');
  console.log('visible inputs:', vis.length);
  if(vis.length>=2){
    await vis[0].fill('admin');
    await vis[1].fill('512049996');
    await p.evaluate(()=>{const b=document.querySelector('#loginBtn'); if(b) b.click();});
    await p.waitForTimeout(4500);
  }
  await p.$$eval('button, .tab, .nav-item', els=>{ const t=els.find(e=>(e.textContent||'').includes('比分预测')); if(t) t.click(); });
  await p.waitForTimeout(2500);
  await p.evaluate(()=>{ const el=document.querySelector('#wsList'); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(800);
  await p.screenshot({path:'/tmp/sp_ws.png'});
  await p.evaluate(()=>{ const el=[...document.querySelectorAll('.sp-sub-head')].find(e=>e.textContent.includes('Poisson')); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(800);
  await p.screenshot({path:'/tmp/sp_poisson.png'});
  await b.close();
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
