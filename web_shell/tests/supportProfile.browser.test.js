import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium, expect} from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const component = path.resolve(shell, '../factory_app/build_context/support/templates/ui/components/SupportProfileTab.jsx');
for (const operator of [false, true]) {
  test(`support ${operator ? 'Admin inbox' : 'profile'} loads persisted requests and replies`, async t => {
    const bundle = await build({
      stdin: {contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
        import Support, {SupportAdminPanel} from ${JSON.stringify(component)};
        window.actions=[]; window.messages=[{message_id:'first',body:'Please help',sender_role:'user'}];
        createRoot(document.getElementById('root')).render(${operator ? '<SupportAdminPanel/>' : '<Support/>'});`, resolveDir:shell, loader:'jsx'},
      bundle:true, write:false, format:'esm', jsx:'automatic',
      alias:{react:path.join(shell,'node_modules/react'),'react-dom':path.join(shell,'node_modules/react-dom')},
      plugins:[{name:'ticket-api',setup(builder){
        builder.onResolve({filter:/moduleApi\.js$/},()=>({path:'api',namespace:'fixture'}));
        builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`export async function moduleAction(module,action,params){
          window.actions.push({module,action,params});
          if(action==='list_support_requests')return {requests:[{request_id:'ticket',subject:'Account issue',message_thread_id:'thread'},{request_id:'second',subject:'Other issue',message_thread_id:'second-thread'}],total:2};
          if(action==='get_support_conversation'&&params.request_id==='second')return new Promise(resolve=>{window.resolveSecond=()=>resolve({success:true,thread:{thread_id:'second-thread'},messages:[{message_id:'second-message',body:'Second conversation'}]});});
          if(action==='get_support_conversation')return {success:true,thread:{thread_id:'thread'},messages:window.messages};
          if(action==='reply_support_request'){window.messages.push({message_id:'reply',body:params.body});return {success:true};}
          throw new Error('Unexpected action '+action);
        }` }));
      }}],
    });
    const server=http.createServer((req,res)=>{
      res.setHeader('Content-Type',req.url==='/fixture.js'?'text/javascript':'text/html');
      res.end(req.url==='/fixture.js'?bundle.outputFiles[0].text:'<div id="root"></div><script type="module" src="/fixture.js"></script>');
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    t.after(()=>new Promise(resolve=>server.close(resolve)));
    const browser=await chromium.launch({headless:true});
    t.after(()=>browser.close());
    const page=await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await expect(page.getByText('Please help',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:/Account issue/}).click();
    await expect(page.getByText('Please help',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Reply to support request')).toBeVisible();
    await page.getByLabel('Reply to support request').fill('Follow-up');
    await page.getByRole('button',{name:'Reply',exact:true}).click();
    await expect(page.getByText('Follow-up',{exact:true})).toBeVisible();
    const actions=await page.evaluate(()=>window.actions);
    assert.equal(actions.find(row=>row.action==='list_support_requests').params.scope,operator?'app':'user');
    assert.ok(actions.some(row=>row.action==='reply_support_request'&&row.params.request_id==='ticket'));
    assert.equal(actions.filter(row=>row.module!=='support').length,0);
    assert.equal(await page.getByLabel('New request').count(),0);
    await page.getByRole('button',{name:/Other issue/}).click();
    await expect(page.getByText('Loading conversation...', {exact:true})).toBeVisible();
    await expect(page.getByLabel('Reply to support request')).toHaveCount(0);
    await expect(page.getByText('Please help',{exact:true})).toHaveCount(0);
    await page.waitForFunction(()=>typeof window.resolveSecond==='function');
    await page.evaluate(()=>window.resolveSecond());
    await expect(page.getByText('Second conversation',{exact:true})).toBeVisible();
    await expect(page.getByLabel('Reply to support request')).toHaveValue('');
  });
}
