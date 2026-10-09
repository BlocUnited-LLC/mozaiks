import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import { artifactRenderKey } from '../../chat-ui/src/core/ui/artifactRenderKey.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const chatComponents = path.join(path.dirname(shell), 'chat-ui/src/components/chat');

test('only explicit nonblocking app bundle scopes share render identity', () => {
  const payload = Object.freeze({artifact_kind:'app_bundle', target_app_id:'app-a', build_registry_id:'registry-a',
    awaiting_response:false, interaction_type:'ui_surface', display:'artifact'});
  const first = Object.freeze({tool_call_id:'authority-1', tool_name:'present_review',
    workflow_name:'ReviewFlow', component_type:'PreviewWorkspace', payload});
  const next = {...first, tool_call_id:'authority-2', payload:{...payload, artifact_version_id:'child', chat_id:'next-chat'}};
  assert.equal(artifactRenderKey(first,'message-1'), artifactRenderKey(next,'message-2'));
  assert.equal(first.tool_call_id, 'authority-1');
  assert.equal(next.tool_call_id, 'authority-2');
  const persistedPayload = {...payload, workflow_name:'ReviewFlow', component_type:'PreviewWorkspace'};
  delete persistedPayload.awaiting_response;
  assert.equal(artifactRenderKey({payload:persistedPayload}, 'stored-event'), 'stored-event');
  // ChatPage normalizes known ui_surface records at hydration; the key helper
  // itself must not infer nonblocking semantics from an incomplete record.
  const hydrated = {payload:{...persistedPayload, awaiting_response:false}};
  assert.equal(artifactRenderKey(hydrated, 'stored-event'), artifactRenderKey(first,'message-1'));
  for (const patch of [
    {awaiting_response:true}, {awaiting_response:undefined}, {awaiting_response:'false'},
    {interaction_type:'ui_tool'}, {interaction_type:undefined}, {display:'inline'},
    {artifact_kind:'workflow_bundle'}, {build_family:'workflow_bundle'},
    {target_app_id:''}, {target_app_id:'wrong app'}, {target_app_id:'_invalid'},
    {target_app_id:[]}, {build_registry_id:null}, {build_registry_id:'a'.repeat(129)},
  ]) assert.equal(artifactRenderKey({...first,payload:{...payload,...patch}},'event-fallback'), 'event-fallback');
  assert.equal(artifactRenderKey({...first,awaiting_response:true},'event-fallback'),'event-fallback');
  assert.equal(artifactRenderKey({...first,workflow_name:null},'event-fallback'),'event-fallback');
  for (const event of [
    {...first,workflow_name:'AnotherReview'}, {...first,component_type:'AnotherWorkspace'},
    {...first,payload:{...payload,target_app_id:'app-b'}},
    {...first,payload:{...payload,build_registry_id:'registry-b'}},
  ]) assert.notEqual(artifactRenderKey(event,'next-event'),artifactRenderKey(first,'first-event'));
});

test('registered app artifact survives new authority IDs while other scopes remount', async (t) => {
  const chatRoot = path.resolve(chatComponents,'../..');
  const bundle = await build({
    stdin:{resolveDir:shell,loader:'jsx',contents:`
      import React, {useEffect,useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import ArtifactPanel from ${JSON.stringify(path.join(chatComponents,'ArtifactPanel.jsx'))};
      import {registerComponent} from ${JSON.stringify(path.join(chatRoot,'registry/componentRegistry.js'))};
      window.renderLifecycle={mounts:0,active:0}; window.authorityResponses=[];
      function PreviewWorkspace({payload,toolCallId,onResponse}) {
        useEffect(()=>{window.renderLifecycle.mounts+=1;window.renderLifecycle.active+=1;
          return ()=>{window.renderLifecycle.active-=1;};},[]);
        return <section><output aria-label="Current authority">{toolCallId}</output>
          <output aria-label="Selected artifact">{payload.artifact_version_id}</output>
          <button onClick={()=>onResponse({seen:toolCallId})}>Report event</button>
          <iframe title="Registered preview" src="/preview" style={{width:500,height:180}} />
        </section>;
      }
      registerComponent('ReviewFlow:PreviewWorkspace',PreviewWorkspace);
      registerComponent('OtherFlow:PreviewWorkspace',PreviewWorkspace);
      registerComponent('ReviewFlow:OtherWorkspace',PreviewWorkspace);
      function Fixture() {
        const [event,setEvent]=useState({tool_call_id:'call-1',tool_name:'present_review',
          workflow_name:'ReviewFlow',component_type:'PreviewWorkspace',display:'artifact',
          payload:{artifact_kind:'app_bundle',artifact_version_id:'original',target_app_id:'app-a',
            build_registry_id:'registry-a',awaiting_response:false,interaction_type:'ui_surface',display:'artifact'}});
        window.patchArtifactEvent=patch=>setEvent(prior=>({...prior,...patch,payload:{...prior.payload,...patch.payload}}));
        const message={id:'message-'+event.tool_call_id,toolCall:{...event,
          onResponse:response=>{window.authorityResponses.push({id:event.tool_call_id,response});return true;}}};
        return <ArtifactPanel messages={[message]} />;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    `},
    bundle:true,write:false,jsx:'automatic',loader:{'.js':'jsx','.png':'dataurl'},nodePaths:[path.join(shell,'node_modules')],
    alias:{react:path.join(shell,'node_modules/react'),'react-dom':path.join(shell,'node_modules/react-dom')},
    plugins:[{name:'artifact-host-boundary',setup(builder){
      builder.onResolve({filter:/context\/ChatUIContext$/},()=>({path:'host',namespace:'fixture'}));
      builder.onResolve({filter:/primitives(?:\/PrimitiveRenderer|\/utils)?$/},()=>({path:'primitives',namespace:'fixture'}));
      builder.onResolve({filter:/ArtifactLoadingState$/},()=>({path:'loading',namespace:'fixture'}));
      builder.onResolve({filter:/^\.\/ui\/index\.js$/},()=>({path:'unused-core-fallback',namespace:'fixture'}));
      builder.onLoad({filter:/.*/,namespace:'fixture'},({path:kind})=>({loader:'jsx',resolveDir:shell,contents:kind==='host'
        ? 'import React from "react"; import ShellUIToolRenderer from '+JSON.stringify(path.join(chatRoot,'core/ui/ShellUIToolRenderer.js'))+'; export const useChatUI=()=>({uiToolRenderer:(event,onResponse,options)=>React.createElement(ShellUIToolRenderer,{event,onResponse,...options})});'
        : kind==='primitives' ? 'export const isCoreArtifact=()=>false; export const PrimitiveRenderer=()=>null; export default PrimitiveRenderer;'
          : 'export default ()=>null;',
      }));
    }}],
  });
  let previewRequests=0;
  const server=http.createServer((req,res)=>{
    if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
    res.setHeader('Content-Type','text/html');
    if(req.url==='/preview'){previewRequests+=1;res.end('<button onclick="this.textContent=\'Kept state\'">Try app</button>');return;}
    res.end('<div id="root"></div><script src="/fixture.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const frame=page.frameLocator('iframe[title="Registered preview"]');
  await frame.getByRole('button',{name:'Try app',exact:true}).click();
  const originalFrame=await page.locator('iframe').elementHandle();
  const patch=next=>page.evaluate(value=>window.patchArtifactEvent(value),next);
  for(const [id,version] of [['call-2','validated-child'],['call-3','failed-child'],['call-4','next-child']]) {
    await patch({tool_call_id:id,payload:{artifact_version_id:version,chat_id:'chat-'+id}});
    await expect(page.getByLabel('Current authority')).toHaveText(id);
    await expect(page.getByLabel('Selected artifact')).toHaveText(version);
    await expect(frame.getByRole('button',{name:'Kept state',exact:true})).toBeVisible();
    assert.equal(await originalFrame.evaluate(node=>node===document.querySelector('iframe')),true);
  }
  await page.getByRole('button',{name:'Report event',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.authorityResponses),[{id:'call-4',response:{seen:'call-4'}}]);
  assert.equal(await page.evaluate(()=>window.renderLifecycle.mounts),1);
  assert.equal(previewRequests,1);
  let mounts=1;
  for(const change of [
    {payload:{build_registry_id:'registry-b'}}, {payload:{target_app_id:'app-b'}},
    {workflow_name:'OtherFlow'}, {workflow_name:'ReviewFlow',component_type:'OtherWorkspace'},
    {payload:{awaiting_response:true,interaction_type:'ui_tool'}},
    {tool_call_id:'response-event-2'},
    {tool_call_id:'other-family-event-1',payload:{awaiting_response:false,interaction_type:'ui_surface',artifact_kind:'workflow_bundle'}},
    {tool_call_id:'other-family-event-2'},
  ]) {
    await patch(change); mounts+=1;
    await expect.poll(()=>page.evaluate(()=>window.renderLifecycle.mounts)).toBe(mounts);
    await expect(frame.getByRole('button',{name:'Try app',exact:true})).toBeVisible();
    assert.equal(await page.evaluate(()=>window.renderLifecycle.active),1);
  }
  assert.deepEqual(errors,[]);
});

test('chat artifact layouts retain visited iframes without exposing hidden controls', async (t) => {
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useEffect, useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import FluidChatLayout from ${JSON.stringify(path.join(chatComponents, 'FluidChatLayout.jsx'))};
      import MobileArtifactDrawer from ${JSON.stringify(path.join(chatComponents, 'MobileArtifactDrawer.jsx'))};
      window.artifactLifecycle = { mounts: 0, active: 0, maximum: 0, actions: 0 };
      function Artifact() {
        useEffect(() => {
          const lifecycle = window.artifactLifecycle;
          lifecycle.mounts += 1;
          lifecycle.active += 1;
          lifecycle.maximum = Math.max(lifecycle.maximum, lifecycle.active);
          return () => { lifecycle.active -= 1; };
        }, []);
        return <section data-artifact-probe aria-label="Interactive artifact">
          <button onClick={() => { window.artifactLifecycle.actions += 1; }}>Artifact action</button>
          <iframe title="Interactive preview" src="/preview" style={{display:'block', width:'100%', height:200}} />
        </section>;
      }
      function Fixture() {
        const [mobile, setMobile] = useState(window.innerWidth < 768);
        const [layout, setLayout] = useState('full');
        const [drawer, setDrawer] = useState('peek');
        const [viewMode, setViewMode] = useState(false);
        const [hasArtifact, setHasArtifact] = useState(true);
        const [message, setMessage] = useState('');
        const [sent, setSent] = useState('');
        useEffect(() => {
          const resize = () => setMobile(window.innerWidth < 768);
          window.addEventListener('resize', resize);
          return () => window.removeEventListener('resize', resize);
        }, []);
        window.clearArtifact = () => setHasArtifact(false);
        window.setDrawerState = setDrawer;
        window.setArtifactViewMode = setViewMode;
        const chat = <section aria-label="Conversation">
          <label>Chat message<textarea aria-label="Chat message" value={message} onChange={event => setMessage(event.target.value)} /></label>
          <button onClick={() => setSent(message)}>Send message</button>
          <output aria-label="Sent message">{sent}</output>
        </section>;
        const artifact = hasArtifact ? <Artifact /> : null;
        return <>
          <header><button onClick={() => mobile
            ? setDrawer(current => current === 'expanded' ? 'peek' : 'expanded')
            : setLayout(current => current === 'full' ? 'split' : 'full')}>Toggle artifact</button></header>
          <main style={{position:'relative', height:'calc(100dvh - 128px)'}}>
            {mobile ? <>
              {chat}
              <MobileArtifactDrawer state={drawer} onStateChange={setDrawer}
                onClose={() => setDrawer('peek')} viewMode={viewMode} artifactContent={artifact} />
            </> : <FluidChatLayout layoutMode={layout} chatContent={chat} artifactContent={artifact} />}
          </main>
          <button>After workspace</button>
        </>;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle: true, write: false, outfile: 'fixture.js', jsx: 'automatic',
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {react:path.join(shell, 'node_modules/react'), 'react-dom':path.join(shell, 'node_modules/react-dom')},
  });
  let previewRequests = 0;
  const server = http.createServer((request, response) => {
    if (request.url === '/fixture.js' || request.url === '/fixture.css') {
      response.setHeader('Content-Type', request.url.endsWith('.css') ? 'text/css' : 'text/javascript');
      response.end(bundle.outputFiles.find(file => file.path.endsWith(request.url.slice(1))).text);
      return;
    }
    response.setHeader('Content-Type', 'text/html');
    if (request.url === '/preview') {
      previewRequests += 1;
      response.end('<button onclick="this.textContent=\'Item added\'">Add item</button><input aria-label="Preview note">');
      return;
    }
    // Native layout/focus behavior with the production mobile stylesheet. The
    // flex rule also ensures author CSS cannot override the hidden attribute.
    response.end(`<link rel="stylesheet" href="/fixture.css"><style>
      body{margin:0;padding:8px;font-family:sans-serif}header{height:64px}
      .flex{display:flex}.flex-col{flex-direction:column}.flex-1{flex:1}
      .h-full{height:100%}.w-full{width:100%}.relative{position:relative}
      .absolute{position:absolute}.inset-x-0{left:0;right:0}.bottom-0{bottom:0}
      .pointer-events-none{pointer-events:none}.pointer-events-auto{pointer-events:auto}
      textarea{display:block}section[data-artifact-probe]{background:white;padding:12px}
    </style><div id="root"></div><script src="/fixture.js"></script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const url = `http://127.0.0.1:${server.address().port}`;

  for (const [name, width] of [['desktop', 1280], ['mobile', 390]]) {
    await t.test(`${name}: lazy first open, interactive state, hidden focus and reopen`, async () => {
      const page = await browser.newPage({viewport:{width,height:900}});
      try {
        const requestsBefore = previewRequests;
        await page.goto(url);
        const lifecycle = () => page.evaluate(() => window.artifactLifecycle);
        const frame = page.frameLocator('iframe[title="Interactive preview"]');
        await expect(page.locator('iframe')).toHaveCount(0);
        assert.equal((await lifecycle()).mounts, 0, 'A never-opened artifact must not mount');
        await page.getByRole('button', {name:'Toggle artifact',exact:true}).click();
        await expect(frame.getByRole('button', {name:'Add item',exact:true})).toBeVisible();
        await expect.poll(async () => (await lifecycle()).mounts).toBe(1);
        await frame.getByRole('button', {name:'Add item',exact:true}).click();
        await frame.getByLabel('Preview note').fill('Retain this preview state');
        await page.evaluate(() => { window.originalPreviewFrame = document.querySelector('iframe'); });
        const artifactButton = page.getByRole('button', {name:'Artifact action',exact:true});
        const pointerTarget = await artifactButton.boundingBox();
        if (name === 'mobile') await page.getByRole('button', {name:'Collapse artifact workspace',exact:true}).click();
        else await page.getByRole('button', {name:'Toggle artifact',exact:true}).click();

        await expect(page.locator('iframe')).toHaveCount(1);
        await expect(page.locator('iframe')).toBeHidden();
        await expect(artifactButton).toHaveCount(0);
        await expect(page.getByRole('region', {name:'Interactive artifact',exact:true})).toHaveCount(0);
        assert.equal(await page.locator('[data-artifact-probe]').evaluate(element => {
          const hiddenOwner = element.closest('[hidden]');
          return hiddenOwner?.inert && hiddenOwner.getAttribute('aria-hidden') === 'true'
            && getComputedStyle(hiddenOwner).display === 'none';
        }), true);
        await page.getByRole('button', {name:'Toggle artifact',exact:true}).focus();
        await page.keyboard.press('Tab');
        await expect(page.getByRole('textbox', {name:'Chat message',exact:true})).toBeFocused();
        await page.locator('[data-artifact-probe] button').evaluate(button => button.focus());
        await expect(page.getByRole('textbox', {name:'Chat message',exact:true})).toBeFocused();
        await frame.getByRole('button', {name:'Item added',exact:true,includeHidden:true}).evaluate(button => button.focus());
        await expect(page.getByRole('textbox', {name:'Chat message',exact:true})).toBeFocused();
        await page.keyboard.press('Tab');
        await expect(page.getByRole('button', {name:'Send message',exact:true})).toBeFocused();
        await page.keyboard.press('Tab');
        await expect(page.getByRole('button', {name:'After workspace',exact:true})).toBeFocused();
        await page.mouse.click(pointerTarget.x + pointerTarget.width / 2, pointerTarget.y + pointerTarget.height / 2);
        assert.equal((await lifecycle()).actions, 0, 'Hidden artifact controls cannot intercept pointer input');
        await page.getByRole('textbox', {name:'Chat message',exact:true}).fill('Change the button label');
        await page.getByRole('button', {name:'Send message',exact:true}).click();
        await expect(page.getByLabel('Sent message')).toHaveText('Change the button label');

        await page.getByRole('button', {name:'Toggle artifact',exact:true}).click();
        await expect(frame.getByRole('button', {name:'Item added',exact:true})).toBeVisible();
        await expect(frame.getByLabel('Preview note')).toHaveValue('Retain this preview state');
        assert.equal(await page.evaluate(() => window.originalPreviewFrame === document.querySelector('iframe')), true);
        assert.deepEqual(await lifecycle(), {mounts:1,active:1,maximum:1,actions:0});
        assert.equal(previewRequests, requestsBefore + 1, 'Reopening must not reload the iframe');

        if (name === 'mobile') {
          await page.evaluate(() => { window.setArtifactViewMode(true); window.setDrawerState('hidden'); });
          await expect(page.locator('iframe')).toBeHidden();
          await expect(page.getByRole('button', {name:'Collapse artifact workspace',exact:true})).toHaveCount(0);
          await page.evaluate(() => window.setDrawerState('peek'));
          await expect(frame.getByRole('button', {name:'Item added',exact:true})).toBeVisible();
          assert.equal((await lifecycle()).mounts, 1, 'Explicit hidden state still wins over view mode');
        }
        await page.evaluate(() => window.clearArtifact());
        await expect(page.locator('iframe')).toHaveCount(0);
        await expect.poll(async () => (await lifecycle()).active).toBe(0);
      } finally {
        await page.close();
      }
    });
  }

  await t.test('responsive branch changes never mount both artifact layouts', async () => {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    try {
      await page.goto(url);
      await page.getByRole('button', {name:'Toggle artifact',exact:true}).click();
      await expect.poll(() => page.evaluate(() => window.artifactLifecycle.active)).toBe(1);
      await page.setViewportSize({width:390,height:900});
      await expect(page.locator('iframe')).toHaveCount(0);
      await expect.poll(() => page.evaluate(() => window.artifactLifecycle.active)).toBe(0);
      await page.getByRole('button', {name:'Toggle artifact',exact:true}).click();
      await expect(page.locator('iframe')).toHaveCount(1);
      await expect.poll(() => page.evaluate(() => window.artifactLifecycle.mounts)).toBe(2);
      await page.setViewportSize({width:1280,height:900});
      await expect(page.locator('iframe')).toHaveCount(1);
      await expect.poll(() => page.evaluate(() => window.artifactLifecycle.mounts)).toBe(3);
      assert.equal(await page.evaluate(() => window.artifactLifecycle.maximum), 1);
    } finally {
      await page.close();
    }
  });
});
