import assert from 'node:assert/strict';
import test from 'node:test';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import { submitToolCallResponse } from '../../chat-ui/src/adapters/uiToolResponse.js';

test('review decisions require a confirmed response and carry only decision data', async () => {
  let request;
  const accepted = await submitToolCallResponse('review-1', { action: 'approve' }, {
    baseUrl: 'http://localhost:8000', token: 'test-token',
    fetchImpl: async (...args) => { request = args; return Response.json({ status: 'success' }); },
  });
  assert.equal(accepted, true);
  assert.equal(request[0], 'http://localhost:8000/api/tool-call/respond');
  assert.equal(request[1].headers.Authorization, 'Bearer test-token');
  assert.deepEqual(JSON.parse(request[1].body), { event_id: 'review-1', response_data: { action: 'approve' } });
});

for (const status of [401, 403, 404, 500, 200]) {
  test(`unaccepted review (${status}) cannot report success`, async () => {
    await assert.rejects(submitToolCallResponse('review-1', { action: 'approve' }, {
      fetchImpl: async () => Response.json({ detail: 'not accepted' }, { status }),
    }), /not accepted|no longer active|not confirm/);
  });
}

test('disconnection and malformed acknowledgement preserve review failure', async () => {
  for (const fetchImpl of [async () => { throw Error('offline'); }, async () => new Response('not-json')]) {
    await assert.rejects(submitToolCallResponse('review-1', { action: 'approve' }, { fetchImpl }), /not confirm/);
  }
  await assert.rejects(submitToolCallResponse(null, {}, {
    fetchImpl: () => assert.fail('missing review must not submit'),
  }), /no longer active/);
});


test('an inactive review explains explicit resume rather than promising replay will rearm it', async () => {
  await assert.rejects(submitToolCallResponse('old-review', {}, {
    fetchImpl: async () => Response.json({detail:'not found'}, {status:404}),
  }), error => {
    assert.match(error.message, /send a message in this chat to resume/);
    assert.doesNotMatch(error.message, /Reopen/);
    return true;
  });
});

test('inactive cards cannot submit, live cards can, and rejected decisions stay incomplete', async t => {
  const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const ui = path.resolve(shell, '../chat-ui/src');
  const bundle = await build({
    stdin:{resolveDir:shell,loader:'jsx',contents:`
      import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
      import {BrowserRouter} from 'react-router-dom';
      import ChatInterface from ${JSON.stringify(path.join(ui,'components/chat/ChatInterface.jsx'))};
      import UIToolRenderer from ${JSON.stringify(path.join(ui,'core/ui/UIToolRenderer.jsx'))};
      window.responses=[]; window.messages=[]; window.reject=false; window.missing=false; window.returnVoid=false;
      function Fixture() {
        const [event,setEvent]=useState({tool_name:'Review',tool_call_id:'saved-1',response_available:false,
          display:'inline',payload:{review_id:'review-1'}});
        const [display,setDisplay]=useState('inline');
        window.patchEvent=next=>setEvent(old=>({...old,...next}));
        window.setDisplay=setDisplay;
        const respond=async response=>{
          if(window.reject==='false')return false;
          if(window.reject)throw new Error('Decision was not accepted');
          window.responses.push({event:event.tool_call_id,response}); return window.returnVoid?undefined:true;
        };
        return <><ChatInterface key={event.tool_call_id} messages={display==='inline'?[{id:event.tool_call_id,sender:'agent',content:'',
          toolCall:event}]:[]} loading={false} onAgentAction={window.missing?undefined:action=>respond(action.response)}
          onSendMessage={message=>window.messages.push(message)} />
          {display==='artifact'&&<UIToolRenderer event={event} onResponse={respond}/>}</>;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture/></BrowserRouter>);
    `},
    bundle:true,write:false,format:'esm',jsx:'automatic',loader:{'.js':'jsx'},
    nodePaths:[path.join(shell,'node_modules')],
    alias:Object.fromEntries(['react','react-dom','react-router-dom','react-icons'].map(name=>[name,path.join(shell,'node_modules',name)])),
    define:{'process.env.NODE_ENV':'"test"'},
    plugins:[{name:'host-boundaries',setup(builder){
      builder.onResolve({filter:/(?:ChatUIContext|ChatMessage|ChatStatusBar|brandAssets|PrimitiveRenderer|primitives\/utils)$/},
        args=>({path:path.basename(args.path),namespace:'fixture'}));
      builder.onLoad({filter:/.*/,namespace:'fixture'},({path:kind})=>({
        loader:'jsx',resolveDir:shell,contents:kind==='ChatUIContext'
          ? `import React,{useState} from 'react';
             function Review({event,onResponse}){const [error,setError]=useState('');
               return <section><p>Exact payload {event.payload.review_id}</p>
                 <button onClick={async()=>{try{await onResponse({action:'approve',review_id:event.payload.review_id});}
                   catch(e){setError(e.message);}}}>Approve exact review</button>
                 {error&&<p role="alert">{error}</p>}</section>;}
             export const useChatUI=()=>({uiToolRenderer:(event,onResponse)=><Review event={event} onResponse={onResponse}/>});`
          : kind==='brandAssets' ? 'export const getBrandLogoSrc=()=>"";export const applyBrandImageFallback=()=>{};'
            : kind==='utils' ? 'export const isCoreArtifact=()=>false;'
              : 'export default ()=>null;',
      }));
    }}],
  });
  const server=http.createServer((req,res)=>{
    res.setHeader('Content-Type',req.url==='/fixture.js'?'text/javascript':'text/html');
    res.end(req.url==='/fixture.js'?bundle.outputFiles[0].text:'<div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  for(const display of ['inline','artifact']){
    await page.evaluate(value=>{window.setDisplay(value);window.patchEvent({response_available:false});},display);
    await expect(page.getByLabel('Inactive saved review')).toBeVisible();
    await expect(page.locator('[inert]')).toContainText('Exact payload review-1');
    await expect(page.getByRole('button',{name:'Approve exact review'})).toBeDisabled();
    assert.equal(await page.locator('[inert]').evaluate(element=>element.inert),true);
    await assert.rejects(page.getByRole('button',{name:'Approve exact review'}).click({timeout:200}));
    assert.deepEqual(await page.evaluate(()=>window.responses),[]);
  }
  assert.deepEqual(await page.evaluate(()=>window.messages),[]);
  await page.getByPlaceholder('Message the agent…').fill('Resume and show a fresh review');
  await page.locator('form button[type="submit"]').click();
  assert.equal((await page.evaluate(()=>window.messages))[0].content,'Resume and show a fresh review');
  await page.evaluate(()=>window.patchEvent({response_available:true}));
  await expect(page.getByLabel('Inactive saved review')).toHaveCount(0);
  await page.getByRole('button',{name:'Approve exact review'}).click();
  assert.deepEqual(await page.evaluate(()=>window.responses),[{event:'saved-1',response:{action:'approve',review_id:'review-1'}}]);
  await page.evaluate(()=>{
    window.setDisplay('inline'); window.reject=true;
    window.patchEvent({tool_call_id:'fresh-2',payload:{review_id:'review-2'},response_available:true});
  });
  await page.getByRole('button',{name:'Approve exact review'}).click();
  await expect(page.getByRole('alert')).toHaveText('Decision was not accepted');
  await expect(page.getByLabel('Completed',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Approve exact review'})).toBeVisible();
  await page.evaluate(()=>{window.reject='false';});
  await page.getByRole('button',{name:'Approve exact review'}).click();
  await expect(page.getByRole('alert')).toHaveText('The server has not accepted this response.');
  await expect(page.getByLabel('Completed',{exact:true})).toHaveCount(0);
  await page.evaluate(()=>{window.reject=false;window.missing=true;window.patchEvent({});});
  await page.getByRole('button',{name:'Approve exact review'}).click();
  await expect(page.getByRole('alert')).toHaveText('The workflow response connection is unavailable.');
  await expect(page.getByLabel('Completed',{exact:true})).toHaveCount(0);
  await page.evaluate(()=>{window.missing=false;window.returnVoid=true;window.patchEvent({});});
  await page.getByRole('button',{name:'Approve exact review'}).click();
  await expect(page.getByLabel('Completed',{exact:true})).toBeVisible();
  assert.deepEqual((await page.evaluate(()=>window.responses)).at(-1),
    {event:'fresh-2',response:{action:'approve',review_id:'review-2'}});
  assert.deepEqual(errors,[]);
});
