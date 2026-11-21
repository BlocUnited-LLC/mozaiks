import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import { reactAlias } from './artifactPreviewFixture.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');

test('an embedded Studio conversation renders the Factory chat and scopes preview events to each user turn', async t => {
  const boundaries = {
    '../../adapters/api': `export class WebSocketApiAdapter {
      constructor(config) { this.config = config; }
      async startChat(app, wf, user, {signal}, context, trigger, options) {
        window.starts.push({app, wf, user, context, options, token:this.config.getAccessToken()});
        return {chat_id: 'private-' + context.id};
      }
      async getWorkflowTransport() { return {transport:'websocket'}; }
      createWebSocketConnection(app, user, callbacks, workflow, chat, options) {
        window.channels[chat] = callbacks;
        window.sockets.push({chat,options,token:this.config.getAccessToken()});
        if (window.failNextSocket) {
          window.failNextSocket=false;
          queueMicrotask(() => callbacks.onClose({code:1006}));
        } else queueMicrotask(callbacks.onOpen);
        return {close(){ window.closedChats.push(chat); }};
      }
      async sendMessageToWorkflow(text) { window.sent.push({text,token:this.config.getAccessToken()}); return true; }
    }`,
    '../../pages/hooks': `export {useChatWebSocket} from ${JSON.stringify(path.join(ui,'pages/hooks/useChatWebSocket.js'))}`,
    '../../utils/resolveWorkflow': 'export default value => value;',
    '../../config': 'export default {get: () => null};',
    '../../platform/index.js': 'export default {getAccessToken: () => null};',
    '../../styles/brandAssets': 'export const getBrandLogoSrc=()=>""; export const applyBrandImageFallback=()=>{};',
    '../../session/chatSessionStorage': 'export const logChatPersistence=()=>{};',
    '../../core/ui/UIToolRenderer': 'export default () => null;',
    '../../core/ui/ShellUIToolRenderer': 'export default () => null;',
    'react-router-dom': 'export const useParams=()=>({}); export const useNavigate=()=>()=>{};',
  };
  const bundle = await build({
    stdin: {resolveDir: shell, loader: 'jsx', contents: `
      import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
      import WorkflowChat from ${JSON.stringify(path.join(ui,'components/chat/WorkflowChat.jsx'))};
      import ArtifactPreviewPane from ${JSON.stringify(path.join(ui,'components/preview/ArtifactPreviewPane.jsx'))};
      window.starts=[]; window.sent=[]; window.results=[]; window.candidates=[]; window.channels={}; window.closedChats=[]; window.sockets=[];
      function Example() {
        const [id,setId]=useState('first'); const [draft,setDraft]=useState('');
        const [token,setToken]=useState('test-scope');
        window.select=setId; window.rotateToken=setToken;
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
    bundle: true, write: false, jsx: 'automatic', loader: {'.js':'jsx', '.css':'empty', '.png':'dataurl'},
    alias: reactAlias,
    nodePaths: [path.join(shell, 'node_modules')], define: {'process.env.NODE_ENV':'"development"'},
    plugins: [{ name: 'transport', setup(builder) {
      builder.onResolve({filter:/.*/}, args => Object.hasOwn(boundaries,args.path) ? {path:args.path,namespace:'fixture'} : undefined);
      builder.onLoad({filter:/.*/,namespace:'fixture'}, args => ({contents:boundaries[args.path],resolveDir:shell,loader:'js'}));
    }}],
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.text().includes('while rendering a different component')) errors.push(msg.text()); });
  await page.route('http://fixture.test/**', route => route.fulfill({contentType:'text/html', body:'<div id="root"></div>'}));
  await page.goto('http://fixture.test/');
  await page.addScriptTag({content:bundle.outputFiles[0].text});
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
