const path='/root/.nvm/versions/node/v22.13.1/lib/node_modules/@playwright/cli/node_modules/playwright-core';
const { chromium } = require(path);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/google/chrome/chrome', chromiumSandbox:false, args:['--no-sandbox']});
  const p = await b.newPage({viewport:{width:420,height:900}});
  await p.goto('https://jc-football-1eo.pages.dev/', {waitUntil:'networkidle', timeout:60000}).catch(()=>{});
  await p.waitForTimeout(2500);
  const maskCls = await p.evaluate(()=>document.querySelector('#loginMask')?.className||'');
  if(maskCls.includes('show')){
    await p.evaluate(()=>{
      const ins=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null);
      ins[0].value='admin'; ins[0].dispatchEvent(new Event('input',{bubbles:true}));
      ins[1].value='512049996'; ins[1].dispatchEvent(new Event('input',{bubbles:true}));
    });
    await p.evaluate(()=>{ const ins=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null); ins[1].dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); });
    await p.waitForTimeout(3000);
  }
  await p.$$eval('button, .tab, .nav-item', els=>{ const t=els.find(e=>(e.textContent||'').includes('比分预测')); if(t) t.click(); });
  await p.waitForTimeout(2500);
  await p.evaluate(()=>{ const el=document.querySelector('#spList .sp-card'); if(el) el.scrollIntoView({block:'start'}); });
  await p.waitForTimeout(800);
  await p.screenshot({path:'/tmp/sp_cards1.png'});
  await p.evaluate(()=>{ window.scrollBy(0, 880); });
  await p.waitForTimeout(800);
  await p.screenshot({path:'/tmp/sp_cards2.png'});
  await b.close();
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
