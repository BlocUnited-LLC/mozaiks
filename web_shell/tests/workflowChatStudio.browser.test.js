import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';
import { reactAlias } from './artifactPreviewFixture.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');

async function startWorkflowFixture(t, {localCanvas = false, viewport} = {}) {
  const boundaries = {
    '../../adapters/api': `export class WebSocketApiAdapter {
      constructor(config) { this.config = config; }
      async startChat(app, wf, user, {signal}, context, trigger, options) {
        window.starts.push({app, wf, user, context, options, token:this.config.getAccessToken()});
        return {chat_id: 'private-' + context.id};
      }
      async getWorkflowTransport() { return {transport:'websocket'}; }
      getHttpBaseUrl() { return window.location.origin; }
      createWebSocketConnection(app, user, callbacks, workflow, chat, options) {
        window.channels[chat] = callbacks;
        window.sockets.push({chat,options,token:this.config.getAccessToken()});
        if (window.failNextSocket) {
          window.failNextSocket=false;
          queueMicrotask(() => callbacks.onClose({code:1006}));
        } else queueMicrotask(callbacks.onOpen);
        return {send(message){ window.actions.push(message); },close(){ window.closedChats.push(chat); }};
      }
      async sendMessageToWorkflow(text,app,user,workflow,chat,context) {
        window.sent.push({text,context,token:this.config.getAccessToken()}); return true;
      }
    }`,
    '../../pages/hooks': `export {useChatWebSocket} from ${JSON.stringify(path.join(ui,'pages/hooks/useChatWebSocket.js'))}`,
    '../../utils/resolveWorkflow': 'export default value => value;',
    '../../config': 'export default {get: () => null};',
    '../../platform/index.js': 'export default {getAccessToken: () => null};',
    '../../styles/useTheme': `
      import {DEFAULT_THEME} from ${JSON.stringify(path.join(ui,'styles/themeProvider.js'))};
      const theme = {...DEFAULT_THEME,colors:{...DEFAULT_THEME.colors,primary:{...DEFAULT_THEME.colors.primary,main:'#346849'}},
        branding:{...DEFAULT_THEME.branding,name:'Host app',logo:'/host-mark.svg',chatbackgroundImage:'/space.svg'}};
      export const useTheme = (appId,options) => {
        window.themeScope = {appId, applyToDocument:options?.applyToDocument};
        return {theme, loading:false};
      };`,
    '../../session/chatSessionStorage': 'export const logChatPersistence=()=>{};',
    '../../context/ChatUIContext': `import React from 'react';
      function Review({event,onResponse}) {
        return <section><h3>{event.payload.title}</h3>
          <button onClick={()=>onResponse(event.payload.decision || {answer:'approve'})}>Approve review</button></section>;
      }
      export const useChatUI = () => ({uiToolRenderer: (event,onResponse) =>
        event.tool_name === 'StudyReview' ? <Review event={event} onResponse={onResponse}/> : null});
      export const useOptionalChatUI = useChatUI;`,
    '../../primitives/PrimitiveRenderer': 'export default () => null;',
    '../../core/ui/ShellUIToolRenderer': 'export default () => null;',
  };
  const bundle = await build({
    stdin: {resolveDir: shell, loader: 'jsx', contents: `
      import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
      import WorkflowChat from ${JSON.stringify(path.join(ui,'components/chat/WorkflowChat.jsx'))};
      import ${JSON.stringify(path.join(ui,'styles/chatShell.css'))};
      import ArtifactPreviewPane from ${JSON.stringify(path.join(ui,'components/preview/ArtifactPreviewPane.jsx'))};
      window.starts=[]; window.sent=[]; window.results=[]; window.candidates=[]; window.channels={}; window.closedChats=[]; window.sockets=[]; window.actions=[];
      function Example() {
        const [id,setId]=useState('first'); const [draft,setDraft]=useState('');
        const [token,setToken]=useState('test-scope');
        window.select=setId; window.rotateToken=setToken;
        if(location.pathname === '/workflow') return <WorkflowChat workflow="StudyReview" appId="orchard"
          userId="owner" brandName="Orchard Kitchen" logo="/orchard-mark.svg" backgroundImage={null}
          initialContext={{id}} getAccessToken={()=>token}
          composerLabel="Study message" transcriptLabel="Study conversation"/>;
        return <><WorkflowChat workflow="DesignHelper" appId="design-app" userId="designer"
          initialContext={{id}} getAccessToken={()=>token} freshSession presentation="studio" brandName="Designer"
          composerLabel="Private message" transcriptLabel="Private conversation" showHeader={false}
          onMessage={m=>window.results.push(m.content)} onArtifact={(e,scope)=>{
            window.candidates.push(scope);
            if(scope.afterUserInput) setDraft(e.data.payload.document);
          }}/>
          {draft && <ArtifactPreviewPane documentHtml={draft} title="Design preview"/>}</>;
      }
      createRoot(document.getElementById('root')).render(<React.StrictMode><Example/></React.StrictMode>);
    `},
    bundle: true, write: false, outfile:'fixture.js', format:'esm', jsx: 'automatic', loader: {'.js':'jsx', '.png':'dataurl'},
    alias: {...reactAlias, 'react-router-dom':path.join(shell,'node_modules/react-router-dom')},
    nodePaths: [path.join(shell, 'node_modules')], define: {'process.env.NODE_ENV':'"development"'},
    plugins: [{ name: 'transport', setup(builder) {
      builder.onResolve({filter:/.*/}, args => Object.hasOwn(boundaries,args.path) ? {path:args.path,namespace:'fixture'} : undefined);
      builder.onLoad({filter:/.*/,namespace:'fixture'}, args => ({contents:boundaries[args.path],resolveDir:shell,loader:'jsx'}));
    }}],
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage({viewport});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.text().includes('while rendering a different component')) errors.push(msg.text()); });
  const cssPath = path.join(shell,'styles.css');
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(cssPath,'utf8')) + '\n@source "' + path.join(ui,'components/chat').replaceAll('\\','/') + '";', {from:cssPath});
  const componentCss = bundle.outputFiles.find(file => file.path.endsWith('.css'));
  const script = bundle.outputFiles.find(file => file.path.endsWith('.js'));
  await page.route('http://fixture.test/**', route => {
    const url = route.request().url();
    if (url.endsWith('/fixture.js')) return route.fulfill({contentType:'text/javascript',body:script.text});
    if (url.endsWith('/fixture.css')) return route.fulfill({contentType:'text/css',body:styles.css + (componentCss?.text || '')});
    if (url.endsWith('.svg')) return route.fulfill({contentType:'image/svg+xml',
      body:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="green"/></svg>'});
    return route.fulfill({contentType:'text/html',body:`<!doctype html><title>Owner portal</title><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/fixture.css"><style>:root{--color-primary:#bc1763}html,body,#root{height:100%;margin:0}</style>
      <div id="root"></div><script type="module" src="/fixture.js"></script>`});
  });
  await page.goto(localCanvas ? 'http://fixture.test/workflow' : 'http://fixture.test/studio');
  return {page,errors};
}

test('an embedded Studio conversation renders the Factory chat and scopes preview events to each user turn', async t => {
  const {page,errors} = await startWorkflowFixture(t);
  const composer = page.getByRole('textbox',{name:'Private message'});
  const send = page.getByRole('button',{name:'Send message'});
  const transcript = page.getByRole('region',{name:'Private conversation'});
  try {
    await page.waitForFunction(() => window.channels?.['private-first']);
  } catch (error) {
    throw new Error(errors.join('\n') || 'The private workflow did not connect', { cause: error });
  }
  const emit = (type, data={}, chat='private-first') => page.evaluate(([chat,type,data]) => window.channels[chat].onMessage({type,data}),[chat,type,data]);
  const interactive = 'document.querySelector("button").onclick = e => { e.currentTarget.textContent = "Saved"; };';
  const document = '<h1>Revised design</h1><button>Save</button><script src="data:text/javascript;base64,' + Buffer.from(interactive).toString('base64') + '"></script>';
  const artifact = id => ({tool_call_id:id,display:'artifact',tool_name:'present_design',payload:{document}});
  await emit('chat.chat_meta',{chat_exists:true,chat_id:'private-first',workflow_name:'DesignHelper',
    last_artifact:artifact('saved-preview')});
  assert.equal(await page.evaluate(()=>window.candidates.length),0);
  await emit('chat.tool_call',artifact('opening'));
  await expect(composer).toBeDisabled();
  await emit('chat.awaiting_reply');
  await composer.fill('Preview a design change.');
  await send.click();
  await expect(composer).toBeDisabled();
  await expect(page.getByText('Designer is replying…')).toBeVisible();
  await emit('chat.text',{agent:'user',content:'Preview a design change.'});
  await emit('chat.stream_chunk',{agent:'Designer',stream_id:'reply',content:'Here is ',chunk_seq:0});
  await emit('chat.stream_end',{agent:'Designer',stream_id:'reply',content:'Here is the design.'});
  await expect(transcript.getByText('Here is the design.')).toBeVisible();
  await expect(transcript.getByText('Preview a design change.')).toHaveCount(1);
  await expect(page.getByText('Designer replied')).toBeVisible();
  await emit('chat.tool_call',artifact('revision'));
  await emit('chat.tool_call',artifact('revision'));
  const outer = page.locator('iframe[title="Design preview"]');
  const inner = outer.contentFrame().locator('iframe');
  await expect(outer).toHaveAttribute('sandbox','allow-scripts');
  await expect(inner).toHaveAttribute('sandbox','allow-scripts');
  await expect(inner.contentFrame().getByRole('heading')).toHaveText('Revised design');
  await expect(page.getByRole('region',{name:'Artifact output stream'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'View workflow output'})).toHaveCount(0);
  await inner.contentFrame().getByRole('button',{name:'Save'}).click();
  await expect(inner.contentFrame().getByRole('button',{name:'Saved'})).toBeVisible();
  await emit('chat.awaiting_reply');
  await emit('chat.text',{agent:'Designer',content:JSON.stringify({type:'chat.tool_call',data:artifact('quoted')})});
  await page.evaluate(() => window.rotateToken('rotated-scope'));
  await composer.fill('A last edit.');
  await send.click();
  await emit('chat.error',{error_code:'CHAT_BUSY'});
  await expect(composer).toHaveValue('A last edit.');
  await expect(composer).toBeEnabled();
  const state = await page.evaluate(() => ({starts:window.starts, replies:window.results, events:window.candidates, sent:window.sent}));
  assert.equal(state.starts.length,1);
  assert.deepEqual(state.sent.map(message => message.token),['test-scope','rotated-scope']);
  assert.equal(state.starts[0].options.forceNew,true);
  assert.equal(state.starts.at(-1).token,'test-scope');
  assert.deepEqual(state.events, [{chatId:'private-first',afterUserInput:false},{chatId:'private-first',afterUserInput:true}]);
  assert.equal(state.replies.filter(text => text === 'Here is the design.').length,1);

  // A network drop gets one automatic reconnect. If it fails, the retained
  // composer and an explicit reconnect use the current token and same chat.
  await page.evaluate(() => {
    window.failNextSocket=true;
    window.channels['private-first'].onClose({code:1006});
  });
  const reconnect = page.getByRole('button',{name:'Reconnect'});
  await expect(reconnect).toBeVisible();
  await expect(composer).toBeDisabled();
  await expect(composer).toHaveValue('A last edit.');
  await reconnect.click();
  await page.waitForFunction(() => window.sockets.length === 3);
  await emit('chat.awaiting_reply',{replay:true});
  await expect(composer).toBeEnabled();
  assert.deepEqual(await page.evaluate(() => window.sockets.slice(1)), Array(2).fill({
    chat:'private-first',options:{resumeSession:true,suppressHistoryReplay:true},token:'rotated-scope',
  }));
  assert.equal(await page.evaluate(() => window.sent.length),2);

  await send.click();
  await page.evaluate(() => window.channels['private-first'].onClose({code:1006}));
  await page.waitForFunction(() => window.sockets.length === 4);
  await expect(page.getByRole('alert')).toContainText('Delivery of your last message is unconfirmed.');
  await emit('chat.text',{agent:'user',content:'A last edit.',index:3,replay:true});
  await emit('chat.text',{agent:'Designer',content:'Here is the design.',index:2,replay:true});
  await expect(transcript.getByText('A last edit.')).toHaveCount(1);
  await expect(transcript.getByText('Here is the design.')).toHaveCount(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await emit('chat.tool_call',{...artifact('missed'),replay:true});
  assert.equal(await page.evaluate(() => window.candidates.at(-1).afterUserInput),false);
  assert.equal(await page.evaluate(() => window.sent.length),3);
  assert.equal(await page.evaluate(() => window.starts.length),1);
  await page.evaluate(() => window.select('second'));
  await page.waitForFunction(() => window.channels['private-second'] && window.closedChats.includes('private-first'));
  await emit('chat.tool_call',artifact('late'));
  assert.equal(await page.evaluate(() => window.candidates.length),3);
  await expect(transcript.getByText('Here is the design.')).toHaveCount(0);
  assert.deepEqual(errors,[]);
});

test('a standalone generated-app workflow uses its logo to toggle the live Factory canvas', async t => {
  const {page,errors} = await startWorkflowFixture(t,{localCanvas:true,viewport:{width:1280,height:800}});
  const heading = page.getByRole('heading',{name:'Artifact',exact:true});
  const output = page.getByRole('region',{name:'Artifact output stream'});
  const toggle = page.getByRole('button',{name:'View workflow output'});
  await page.waitForFunction(()=>window.channels?.['private-first']);
  const emit = (type,data={}) => page.evaluate(([type,data])=>window.channels['private-first'].onMessage({type,data}),[type,data]);
  await expect(heading).toBeVisible();
  assert.deepEqual(await page.evaluate(()=>window.themeScope),{appId:'orchard',applyToDocument:false});
  await expect.poll(()=>heading.evaluate(el=>getComputedStyle(el).getPropertyValue('--color-primary').trim())).toBe('#346849');
  assert.equal(await page.locator('html').evaluate(el=>getComputedStyle(el).getPropertyValue('--color-primary').trim()),'#bc1763');
  await expect(page).toHaveTitle('Owner portal');
  await expect(toggle.locator('img')).toHaveAttribute('src','/orchard-mark.svg');
  const placeholder = output.getByRole('img',{name:'Orchard Kitchen logo'});
  await expect(placeholder).toHaveAttribute('src','/orchard-mark.svg');
  await expect.poll(()=>placeholder.evaluate(img=>img.complete && img.naturalWidth>0)).toBe(true);
  assert.equal(await page.evaluate(()=>performance.getEntriesByType('resource').some(r => r.name.endsWith('/space.svg'))),false);
  await toggle.click();
  await expect(output).toHaveCount(0);
  await toggle.click();
  await expect(heading).toBeVisible();
  await emit('chat.awaiting_reply');
  await page.getByRole('textbox',{name:'Study message'}).fill('Review the schedule.');
  await page.getByRole('button',{name:'Send message'}).click();
  assert.deepEqual(await page.evaluate(()=>window.sent.map(m=>m.text)),['Review the schedule.']);
  await emit('chat.ui.render',{tool_call_id:'study-1',component_type:'StudyReview',tool_name:'StudyReview',
    display:'artifact',awaiting_response:true,payload:{title:'Friday schedule',artifact_id:'friday-report',
      actions:[{tool:'refresh_schedule',label:'Refresh schedule',params:{view:'team'}}]}});
  await expect(heading).toBeVisible();
  await expect(output.getByRole('heading',{name:'Friday schedule'})).toBeVisible();
  await expect(placeholder).toHaveCount(0);
  const submitted = [];
  await page.route('**/api/tool-call/respond',route=>{
    submitted.push({body:route.request().postDataJSON(),authorization:route.request().headers().authorization});
    return route.fulfill({json:{status:'success'}});
  });
  await output.getByRole('button',{name:'Approve review'}).click();
  await expect.poll(()=>submitted).toEqual([{body:{event_id:'study-1',response_data:{answer:'approve'}},authorization:'Bearer test-scope'}]);
  await output.getByRole('button',{name:'Refresh schedule'}).click();
  const action = await page.evaluate(()=>window.actions[0]);
  assert.ok(action.action_id);
  expect(action).toMatchObject({type:'artifact.action',artifact_id:'friday-report',tool:'refresh_schedule',
    params:{view:'team'},context:{app_id:'orchard',chat_id:'private-first',user_id:'owner',workflow_name:'StudyReview'}});
  await expect(output.getByRole('button',{name:'Working…'})).toBeDisabled();
  await emit('artifact.action.completed',{action_id:action.action_id,artifact_id:action.artifact_id});
  await expect(output.getByRole('button',{name:'Refresh schedule'})).toBeEnabled();
  await emit('chat.awaiting_reply');
  await page.getByRole('textbox',{name:'Study message'}).fill('What changed on Friday?');
  await page.getByRole('button',{name:'Send message'}).click();
  expect(await page.evaluate(()=>window.sent.at(-1))).toMatchObject({text:'What changed on Friday?',
    context:{artifact_context:{artifact_id:'friday-report'}}});
  // Returning to the same component at a smaller size starts with the composer.
  await page.setViewportSize({width:390,height:844});
  await page.reload();
  await page.waitForFunction(()=>window.channels?.['private-first']);
  await expect(page.getByRole('textbox',{name:'Study message'})).toBeVisible();
  await expect(heading).toHaveCount(0);
  await expect(toggle.locator('img')).toHaveAttribute('src','/orchard-mark.svg');
  await toggle.click();
  await expect(placeholder).toBeVisible();
  await page.getByRole('button',{name:'Collapse artifact workspace'}).click();
  await expect(heading).toHaveCount(0);
  await expect(toggle).toBeVisible();
  assert.deepEqual(errors,[]);
});

test('a resumed app canvas restores its saved review from session metadata and preserves live event order', async t => {
  const {page,errors} = await startWorkflowFixture(t,{localCanvas:true,viewport:{width:1280,height:800}});
  const output = page.getByRole('region',{name:'Artifact output stream'});
  const toggle = page.getByRole('button',{name:'View workflow output'});
  const composer = page.getByRole('textbox',{name:'Study message'});
  const scope = {chat_id:'private-first',app_id:'orchard',user_id:'owner',workflow_name:'StudyReview'};
  const saved = {tool_call_id:'saved-call',tool_name:'StudyReview',component_type:'StudyReview',workflow_name:'StudyReview',
    display:'artifact',response_available:true,payload:{title:'Saved team schedule',artifact_id:'schedule-v2',
      interaction_type:'ui_tool',decision:{day:'Tuesday',approved_shifts:[1,3]}}};
  const meta = {chat_exists:true,...scope,last_artifact:saved};
  const sendFrame = f => page.evaluate(frame => window.channels['private-first'].onMessage(frame),f);
  const emit = (type,data) => sendFrame({type,data});
  const connect = () => page.waitForFunction(()=>window.channels?.['private-first']);
  await connect();
  await page.reload();
  await connect();
  await emit('chat.text',{agent:'Scheduler',index:0,replay:true,content:'Review the saved schedule.'});
  await expect(page.getByRole('region',{name:'Study conversation'})).toContainText('Review the saved schedule.');
  await emit('chat.chat_meta',{...meta,app_id:'another-app'});
  await emit('chat_meta',{...meta,user_id:'another-owner'});
  await emit('chat_meta',{...meta,chat_id:'another-chat'});
  await emit('chat_meta',{...meta,last_artifact:{...saved,workflow_name:'AnotherReview'}});
  await emit('chat_meta',{...meta,last_artifact:{...saved,display:'inline'}});
  await emit('chat_meta',{...meta,chat_exists:false});
  await sendFrame({type:'unknown',content:JSON.stringify({type:'chat_meta',data:meta})});
  await expect(output.getByRole('img',{name:'Orchard Kitchen logo'})).toBeVisible();

  await emit('chat.chat_meta',{...meta,last_artifact:{...saved,response_available:false}});
  await expect(output.getByRole('region',{name:'Inactive saved review'})).toBeVisible();
  assert.equal(await output.getByText('Saved team schedule').evaluate(el=>Boolean(el.closest('[inert]'))),true);
  const responses = [];
  await page.route('**/api/tool-call/respond',route=>{
    responses.push({body:route.request().postDataJSON(),token:route.request().headers().authorization});
    return route.fulfill({json:{status:'success'}});
  });
  await sendFrame({type:'chat_meta',...meta,last_artifact:{...saved,display:'fullscreen'}});
  await expect(output.getByRole('heading',{name:'Saved team schedule'})).toBeVisible();
  await output.getByRole('button',{name:'Approve review'}).click();
  await expect.poll(()=>responses).toEqual([{body:{event_id:'saved-call',response_data:saved.payload.decision},token:'Bearer test-scope'}]);
  await composer.fill('Which team did this study cover?');
  await page.getByRole('button',{name:'Send message'}).click();
  expect(await page.evaluate(()=>window.sent.at(-1).context.artifact_context)).toMatchObject({
    id:'saved-call',artifact_id:'schedule-v2',chat_id:'private-first',workflow_name:'StudyReview',type:'StudyReview',display:'fullscreen'});

  const update = (id,title) => emit('ui.update',{...scope,tool_call_id:id,patch:{title}});
  await update('another-call','Unrelated patch');
  await expect(output.getByRole('heading',{name:'Saved team schedule'})).toBeVisible();
  await update('saved-call','Patched schedule');
  await emit('chat_meta',meta);
  await expect(output.getByRole('heading',{name:'Patched schedule'})).toBeVisible();
  await emit('ui.dismiss',{...scope,tool_call_id:'another-call'});
  await expect(output.getByRole('heading',{name:'Patched schedule'})).toBeVisible();
  await emit('ui.dismiss',{...scope,tool_call_id:'saved-call'});
  await expect(output).toHaveCount(0);
  await toggle.click();
  await expect(output.getByRole('img',{name:'Orchard Kitchen logo'})).toBeVisible();
  await emit('chat_meta',meta);
  await expect(output.getByRole('button',{name:'Approve review'})).toHaveCount(0);

  await toggle.click();
  await expect(output).toHaveCount(0);
  const render = (id,display) => emit('ui.render',{...scope,tool_call_id:id,component:'StudyReview',
    display_mode:display,awaiting_response:true,payload:{title:id}});
  await render('New review','artifact');
  await emit('chat_meta',meta);
  await emit('ui.render',{...saved,display_mode:'artifact',replay:true,payload:{title:'Old replay'}});
  await expect(output.getByRole('heading',{name:'New review'})).toBeVisible();
  await expect(composer).toBeVisible();
  for (const alias of ['view','fullscreen']) {
    await render('Focused '+alias,alias);
    await expect(output.getByRole('heading',{name:'Focused '+alias})).toBeVisible();
    await expect(composer).toHaveCount(0);
    await page.getByRole('button',{name:'Return to chat'}).click();
    await expect(composer).toBeVisible();
  }

  await page.setViewportSize({width:390,height:844});
  await page.reload();
  await connect();
  await emit('chat_meta',{...meta,last_artifact:{...saved,display:'view'}});
  await expect(composer).toBeVisible();
  await expect(output).toHaveCount(0);
  await toggle.click();
  await expect(output.getByRole('heading',{name:'Saved team schedule'})).toBeVisible();
  await page.getByRole('button',{name:'Collapse artifact workspace'}).click();
  await render('New mobile review','artifact');
  await expect(output.getByRole('heading',{name:'New mobile review'})).toBeVisible();
  await emit('ui.dismiss',{...scope,tool_call_id:'New mobile review'});
  await expect(composer).toBeVisible();
  assert.deepEqual(errors,[]);
});
