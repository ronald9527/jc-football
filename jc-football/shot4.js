const path='/root/.nvm/versions/node/v22.13.1/lib/node_modules/@playwright/cli/node_modules/playwright-core';
const { chromium } = require(path);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/google/chrome/chrome', chromiumSandbox:false, args:['--no-sandbox']});
  const p = await b.newPage({viewport:{width:420,height:900}});
  p.on('console', m=>{ if(m.type()==='error') console.log('console-err:', m.text().slice(0,150)); });
  p.on('response', r=>{ if(r.url().includes('login')||r.url().includes('auth')) console.log('resp:', r.status(), r.url().slice(0,100)); });
  await p.goto('https://jc-football-1eo.pages.dev/', {waitUntil:'networkidle', timeout:60000}).catch(()=>{});
  await p.waitForTimeout(2500);
  await p.evaluate(()=>{
    const ins=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null);
    ins[0].value='admin'; ins[0].dispatchEvent(new Event('input',{bubbles:true}));
    ins[1].value='512049996'; ins[1].dispatchEvent(new Event('input',{bubbles:true}));
    const b=document.querySelector('#loginBtn'); console.log('btnFound:', !!b);
    if(b) b.click();
  });
  await p.waitForTimeout(3000);
  const st = await p.evaluate(()=>({mask: document.querySelector('#loginMask')?.className, body: document.body.innerText.slice(0,80)}));
  console.log('after click:', JSON.stringify(st));
  // 若仍在登录页，尝试回车提交
  if(st.mask && st.mask.includes('show')){
    await p.evaluate(()=>{ const ins=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null); ins[1].dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); });
    await p.waitForTimeout(2500);
    console.log('after enter:', JSON.stringify(await p.evaluate(()=>document.querySelector('#loginMask')?.className)));
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
