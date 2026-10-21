import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test, { before, after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');
let browser, server, origin;

before(async () => {
  const stubs = {
    ChatUIContext: `import React,{createContext,useContext} from 'react';
      export const ChatContext=createContext(null); export const useChatUI=()=>useContext(ChatContext);`,
    useTheme: `export default ()=>({theme:{}});`,
    brandAssets: `export const getBrandLogoSrc=()=>''; export const applyBrandImageFallback=()=>{};`,
    ChatInterface: `import React,{useState} from 'react';
      export default function ChatInterface({messages,onSendMessage}) {
        const [text,setText]=useState('');
        return <><div role="log">{messages.filter(m=>!m.isThinking).map(m=><p key={m.id} data-id={m.id}>{m.content}</p>)}</div>
          {messages.some(m=>m.isThinking)&&<span data-testid="pending-response">Waiting for a response</span>}
          <form onSubmit={e=>{e.preventDefault();onSendMessage(text);setText('');}}>
            <input aria-label="Ask input" value={text} onChange={e=>setText(e.target.value)}/>
            <button>Send</button>
          </form></>;
      }`,
    api: `export const authFetch=async()=>({ok:true,json:async()=>({sessions:[],session_state:null})});`,
  };
  const bundle = await build({
    stdin: { contents: `
      import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
      import {BrowserRouter} from 'react-router-dom';
      import Widget from ${JSON.stringify(path.join(ui, 'components/chat/PersistentChatWidget.jsx'))};
      import {ChatContext} from ${JSON.stringify(path.join(ui, 'context/ChatUIContext'))};
      window.connections=[]; window.fetches=[]; window.sent=[];
      const api={
        createWebSocketConnection(app,user,callbacks,workflow,carrier,options) {
          const record={app,user,callbacks,carrier,options,closed:false};
          const conn={send:message=>{window.sent.push(message);return true;},close:()=>{record.closed=true;}};
          record.conn=conn;window.connections.push(record);return conn;
        },
        fetchGeneralChatTranscript(app,gid) {
          return new Promise(resolve=>window.fetches.push({app,gid,resolve}));
        },
      };
      function Fixture(){
        const [identity,setIdentity]=useState({app:'sample-app',user:'alice'});
        const [activeGeneralChatId,setActiveGeneralChatId]=useState(window.initialGeneralId === undefined ? 'saved' : window.initialGeneralId);
        const [askMessages,setAskMessages]=useState([]);
        const [unreadChatCount,setUnreadChatCount]=useState(0);
        const [isChatOverlayOpen,setIsChatOverlayOpen]=useState(false);
        const [mounted,setMounted]=useState(true);
        window.switchIdentity=setIdentity;window.selectConversation=setActiveGeneralChatId;
        window.unmountWidget=()=>setMounted(false);
        window.current={activeGeneralChatId,askMessages};
        const noop=()=>{};
        return <ChatContext.Provider value={{
          api,config:{appId:identity.app,appName:'Sample App'},user:{id:identity.user,app_id:identity.app},
          askMessages,setAskMessages,activeGeneralChatId,setActiveGeneralChatId,
          unreadChatCount,setUnreadChatCount,setConversationMode:noop,setActiveChatId:noop,setActiveWorkflowName:noop,
          isChatOverlayOpen,setIsChatOverlayOpen,
        }}>{mounted&&<Widget/>}</ChatContext.Provider>;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture/></BrowserRouter>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    alias: Object.fromEntries(['react', 'react-dom', 'react-router-dom'].map(name => [name, path.join(shell, 'node_modules', name)])),
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'external-boundaries', setup(builder) {
      builder.onResolve({ filter: /(?:ChatUIContext|useTheme|brandAssets|ChatInterface|adapters\/api)(?:\.[jt]sx?)?$/ }, args =>
        ({ path: path.basename(args.path).replace(/\.[jt]sx?$/, ''), namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args =>
        ({ contents: stubs[args.path], loader: 'jsx', resolveDir: shell }));
    } }],
  });
  server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text
      : '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
});

const transcript = (gid = 'saved', messages = [
  { event_id: 'user-1', sequence: 1, role: 'user', content: 'Remember my question' },
  { event_id: 'answer-1', sequence: 2, role: 'assistant', content: 'A saved answer' },
], changes = {}) => ({ app_id: 'sample-app', user_id: 'alice', chat_id: gid, found: true, messages, ...changes });

async function fixture(t, width = 1280, initialGeneralId = 'saved') {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.addInitScript(id => { window.initialGeneralId = id; }, initialGeneralId);
  await page.goto(origin);
  await page.getByRole('button', { name: 'Open assistant', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(1);
  await page.evaluate(() => window.connections[0].callbacks.onOpen());
  return page;
}
async function ack(page, gid = 'saved', connection = 0, type = 'chat.mode_changed') {
  await page.evaluate(({ gid, connection, type }) => window.connections[connection].callbacks.onMessage({
    type, data: { mode: 'general', general_chat_id: gid },
  }), { gid, connection, type });
}
async function resolve(page, payload = transcript(), index = 0) {
  await expect.poll(() => page.evaluate(() => window.fetches.length)).toBeGreaterThan(index);
  await page.evaluate(({ payload, index }) => window.fetches[index].resolve(payload), { payload, index });
}
async function send(page, text) {
  await page.getByRole('textbox', { name: 'Ask input' }).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}
const submissions = page => page.evaluate(() => window.sent.filter(event => event.type === 'user.input.submit'));

for (const width of [390, 1280]) {
  test(`restores acknowledged persisted transcript without sending a prompt (${width}px)`, async t => {
    const page = await fixture(t, width);
    await ack(page);
    await expect(page.getByText('Loading conversation…', { exact: true })).toBeVisible();
    await resolve(page);
    await expect(page.getByRole('log')).toHaveText('Remember my questionA saved answer');
    await expect(page.getByText('Ask anything', { exact: true })).toBeVisible();
    assert.deepEqual(await submissions(page), []);
    await page.getByTitle('Minimize', { exact: true }).click();
    await page.getByRole('button', { name: 'Open assistant', exact: true }).click();
    await ack(page);
    await expect(page.getByRole('log').locator('p')).toHaveCount(2);
    assert.equal(await page.evaluate(() => window.fetches.length), 1);
    assert.equal(await page.evaluate(() => window.connections.length), 1);
  });
}

test('queued optimistic inputs survive held hydration and send only after history is ready', async t => {
  const page = await fixture(t);
  await send(page, 'same text');
  await ack(page);
  await send(page, 'same text');
  assert.deepEqual(await submissions(page), []);
  await resolve(page);
  await expect(page.getByRole('log').locator('p')).toHaveCount(4);
  await expect.poll(async () => (await submissions(page)).length).toBe(2);
  assert.deepEqual((await submissions(page)).map(event => [event.text, event.context.general_chat_id]),
    [['same text', 'saved'], ['same text', 'saved']]);
});

test('live messages arriving during restore survive with ID overlap deduplication only', async t => {
  const page = await fixture(t);
  await ack(page);
  for (const general_message_id of ['answer-1', 'answer-2']) {
    await page.evaluate(general_message_id => window.connections[0].callbacks.onMessage({
      type: 'chat.stream_end', data: { content: 'A saved answer', metadata: { general_chat_id: 'saved', general_message_id } },
    }), general_message_id);
  }
  await resolve(page);
  await expect(page.getByRole('log').locator('p')).toHaveCount(3);
  await expect(page.getByRole('log').getByText('A saved answer', { exact: true })).toHaveCount(2);
  await page.evaluate(() => window.connections[0].callbacks.onMessage({
    type: 'chat.stream_end', data: { content: 'A saved answer', metadata: { general_chat_id: 'saved', general_message_id: 'answer-1' } },
  }));
  await expect(page.getByRole('log').locator('p')).toHaveCount(3);
});

test('accepted submission stays visibly pending through unscoped chunks until its scoped completion', async t => {
  const page = await fixture(t);
  await ack(page); await resolve(page);
  await send(page, 'another question');
  await expect(page.getByTestId('pending-response')).toBeVisible();
  await page.evaluate(() => window.connections[0].callbacks.onMessage({
    type: 'chat.stream_chunk', data: { content: 'ambiguous chunk', metadata: { source: 'general_agent' } },
  }));
  await expect(page.getByTestId('pending-response')).toBeVisible();
  await expect(page.getByRole('log')).not.toContainText('ambiguous chunk');
  await page.evaluate(() => window.connections[0].callbacks.onMessage({
    type: 'chat.stream_end', data: { content: 'Scoped final answer', metadata: { general_chat_id: 'saved', general_message_id: 'answer-3' } },
  }));
  await expect(page.getByTestId('pending-response')).toHaveCount(0);
  await expect(page.getByRole('log')).toContainText('Scoped final answer');
});

test('two accepted sends stay pending until two distinct scoped completions arrive', async t => {
  const page = await fixture(t);
  await ack(page); await resolve(page);
  await send(page, 'first'); await send(page, 'second');
  const complete = id => page.evaluate(id => window.connections[0].callbacks.onMessage({
    type: 'chat.stream_end', data: { content: 'Equal reply', metadata: { general_chat_id: 'saved', general_message_id: id } },
  }), id);
  await complete('answer-1');
  await expect(page.getByTestId('pending-response')).toBeVisible();
  await complete('first-reply');
  await expect(page.getByTestId('pending-response')).toBeVisible();
  await complete('first-reply');
  await expect(page.getByTestId('pending-response')).toBeVisible();
  await complete('second-reply');
  await expect(page.getByTestId('pending-response')).toHaveCount(0);
  await expect(page.getByRole('log').getByText('Equal reply', { exact: true })).toHaveCount(2);
});

for (const [label, payload] of [
  ['null', null], ['missing record', transcript('saved', [], { found: false })],
  ['wrong owner', transcript('saved', [], { user_id: 'bob' })],
  ['wrong app', transcript('saved', [], { app_id: 'other-app' })],
  ['wrong conversation', transcript('other')], ['malformed messages', transcript('saved', null)],
]) {
  test(`${label} history stays unavailable, then a real empty transcript can be retried`, async t => {
    const page = await fixture(t);
    await ack(page);
    await send(page, 'waiting input');
    await resolve(page, payload);
    await expect(page.getByRole('status')).toContainText('Couldn’t load this conversation');
    assert.deepEqual(await submissions(page), []);
    await page.getByRole('button', { name: 'Retry history' }).click();
    await resolve(page, transcript('saved', []), 1);
    await expect(page.getByText('Ask anything', { exact: true })).toBeVisible();
    await expect(page.getByRole('status')).toHaveCount(0);
    await expect(page.getByRole('log')).toHaveText('waiting input');
    await expect.poll(async () => (await submissions(page)).length).toBe(1);
  });
}

test('new conversation uses server acknowledgement and preserves queued input without old fetch replay', async t => {
  const page = await fixture(t);
  await ack(page);
  await page.getByRole('button', { name: /New conversation/ }).click();
  await send(page, 'for the new conversation');
  assert.equal(await page.evaluate(() => window.current.activeGeneralChatId), 'saved');
  assert.equal(await page.evaluate(() => window.sent.filter(event => event.type === 'chat.start_general_chat').length), 1);
  await resolve(page);
  await expect(page.getByRole('log')).toHaveText('for the new conversation');
  assert.deepEqual(await submissions(page), []);
  await ack(page, 'new-server-id', 0, 'chat.general_session_created');
  await resolve(page, transcript('new-server-id', []), 1);
  await expect.poll(() => page.evaluate(() => window.current.activeGeneralChatId)).toBe('new-server-id');
  await expect(page.getByRole('log')).toHaveText('for the new conversation');
  await expect.poll(async () => (await submissions(page)).length).toBe(1);
  assert.equal((await submissions(page))[0].context.general_chat_id, 'new-server-id');
  await page.evaluate(() => window.connections[0].callbacks.onMessage({
    type: 'chat.text', data: { content: 'late old answer', metadata: { general_chat_id: 'saved' } },
  }));
  await expect(page.getByRole('log')).not.toContainText('late old answer');
});

test('old history remains visible until a new conversation is acknowledged', async t => {
  const page = await fixture(t);
  await ack(page); await resolve(page);
  await expect(page.getByRole('log')).toContainText('A saved answer');
  await page.getByRole('button', { name: /New conversation/ }).click();
  await expect(page.getByRole('log')).toContainText('A saved answer');
  await ack(page, 'saved', 0, 'chat.general_session_created');
  await expect(page.getByRole('log')).toContainText('A saved answer');
  assert.equal(await page.evaluate(() => window.fetches.length), 1);
  await ack(page, 'new-server-id', 0, 'chat.general_session_created');
  await expect(page.getByRole('log').locator('p')).toHaveCount(0);
  await resolve(page, transcript('new-server-id', []), 1);
  assert.deepEqual(await submissions(page), []);
});

test('New conversation cannot move older queued input into a different conversation', async t => {
  const page = await fixture(t);
  await ack(page);
  await send(page, 'keep this in the original conversation');
  await expect(page.getByRole('button', { name: /New conversation/ })).toBeDisabled();
  await resolve(page);
  await expect.poll(async () => (await submissions(page)).length).toBe(1);
  assert.equal((await submissions(page))[0].context.general_chat_id, 'saved');
  await expect(page.getByRole('button', { name: /New conversation/ })).toBeEnabled();
  await page.getByRole('button', { name: /New conversation/ }).click();
  await ack(page, 'fresh', 0, 'chat.general_session_created');
  await resolve(page, transcript('fresh', []), 1);
  await expect(page.getByRole('log').locator('p')).toHaveCount(0);
  assert.equal((await submissions(page)).length, 1);
});

for (const identity of [{ app: 'sample-app', user: 'bob' }, { app: 'other-app', user: 'alice' }]) {
  test(`identity change to ${identity.app}/${identity.user} excludes old callbacks, pending input and carrier`, async t => {
    const page = await fixture(t);
    await ack(page);
    await send(page, 'old unsent message');
    await page.evaluate(identity => window.switchIdentity(identity), identity);
    await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
    await expect(page.getByRole('log').locator('p')).toHaveCount(0);
    await page.evaluate(() => {
      window.connections[0].callbacks.onMessage({ type: 'chat.text', data: { content: 'old private answer' } });
      window.connections[0].callbacks.onClose();
      window.connections[1].callbacks.onOpen();
    });
    await ack(page, 'identity-session', 1);
    await resolve(page);
    await resolve(page, transcript('identity-session', [], { app_id: identity.app, user_id: identity.user }), 1);
    await expect(page.getByText('Ask anything', { exact: true })).toBeVisible();
    await expect(page.getByRole('log').locator('p')).toHaveCount(0);
    assert.deepEqual(await submissions(page), []);
    const carriers = await page.evaluate(() => window.connections.map(record => record.carrier));
    assert.notEqual(carriers[0], carriers[1]);
  });
}

test('external selection isolates old fetches and preserves queued drafts until returning to their conversation', async t => {
  const page = await fixture(t);
  await ack(page);
  await send(page, 'unsent input for original conversation');
  await page.evaluate(() => window.selectConversation('selected'));
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
  await page.evaluate(() => window.connections[1].callbacks.onOpen());
  await ack(page, 'selected', 1);
  await resolve(page, transcript('selected', [{ event_id: 'selected-answer', role: 'assistant', content: 'Selected history' }]), 1);
  await resolve(page);
  await expect(page.getByRole('log')).toHaveText('Selected history');
  await expect(page.getByRole('button', { name: /New conversation/ })).toBeEnabled();
  assert.deepEqual(await submissions(page), []);
  assert.equal(await page.evaluate(() => window.connections[0].closed), true);
  await page.evaluate(() => window.selectConversation('saved'));
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(3);
  await page.evaluate(() => window.connections[2].callbacks.onOpen());
  await ack(page, 'saved', 2);
  await expect(page.getByRole('log')).toHaveText('unsent input for original conversation');
  assert.deepEqual(await submissions(page), []);
  await resolve(page, transcript(), 2);
  await expect.poll(async () => (await submissions(page)).length).toBe(1);
  assert.equal((await submissions(page))[0].context.general_chat_id, 'saved');
  await expect(page.getByRole('log')).toHaveText('Remember my questionA saved answerunsent input for original conversation');
});

test('connection retry creates a new socket for the last acknowledged ID and ignores its earlier unfinished fetch', async t => {
  const page = await fixture(t);
  await ack(page, 'server-selected');
  await page.evaluate(() => window.connections[0].callbacks.onClose());
  await page.getByRole('button', { name: 'Retry connection' }).click();
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
  await page.evaluate(() => window.connections[1].callbacks.onOpen());
  assert.equal(await page.evaluate(() => window.sent.at(-1).general_chat_id), 'server-selected');
  await ack(page, 'server-selected', 1);
  await resolve(page, transcript('server-selected', [{ event_id: 'latest', role: 'assistant', content: 'Latest history' }]), 1);
  await resolve(page, transcript('server-selected'));
  await expect(page.getByRole('log')).toHaveText('Latest history');
});

test('disconnected assistant only reports waiting messages when input is queued', async t => {
  const page = await fixture(t, 390);
  await page.evaluate(() => window.connections[0].callbacks.onClose());
  const notice = page.getByRole('status').filter({ hasText: 'The assistant is disconnected.' });
  await expect(notice).toBeVisible();
  await expect(notice).not.toContainText('Your messages are waiting.');
  await expect(notice.getByRole('button', { name: 'Retry connection' })).toBeVisible();

  await send(page, 'Question queued while offline');
  await expect(notice).toContainText('Your messages are waiting.');
  assert.deepEqual(await submissions(page), []);
});

test('unmount closes the connection and rejects pending fetch and socket callbacks', async t => {
  const page = await fixture(t);
  await ack(page);
  await page.evaluate(() => window.unmountWidget());
  await resolve(page);
  await page.evaluate(() => window.connections[0].callbacks.onMessage({
    type: 'chat.mode_changed', data: { mode: 'general', general_chat_id: 'late' },
  }));
  assert.equal(await page.evaluate(() => window.connections[0].closed), true);
  assert.equal(await page.evaluate(() => window.current.activeGeneralChatId), 'saved');
  assert.equal(await page.evaluate(() => window.fetches.length), 1);
});

test('selection before initial or replacement acknowledgement rejects both superseded sockets', async t => {
  const page = await fixture(t);
  await page.evaluate(() => window.selectConversation('second'));
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
  await page.evaluate(() => window.connections[1].callbacks.onOpen());
  await page.evaluate(() => window.selectConversation('third'));
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(3);
  await page.evaluate(() => window.connections[2].callbacks.onOpen());
  await ack(page, 'saved', 0);
  await ack(page, 'second', 1);
  assert.equal(await page.evaluate(() => window.fetches.length), 0);
  assert.equal(await page.evaluate(() => window.current.activeGeneralChatId), 'third');
  await ack(page, 'third', 2);
  await resolve(page, transcript('third', [{ event_id: 'third-answer', role: 'assistant', content: 'Third history' }]));
  await expect(page.getByRole('log')).toHaveText('Third history');
  assert.deepEqual(await page.evaluate(() => window.connections.map(record => record.closed)), [true, true, false]);
});

for (const initialGeneralId of ['saved', null]) {
  test(`draft before initial acknowledgement cannot migrate on selection (${initialGeneralId})`, async t => {
    const page = await fixture(t, 1280, initialGeneralId);
    await send(page, 'draft for original conversation');
    await page.evaluate(() => window.selectConversation('other'));
    await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
    await page.evaluate(() => window.connections[1].callbacks.onOpen());
    await ack(page, 'other', 1);
    await resolve(page, transcript('other', []));
    await expect(page.getByText('Ask anything', { exact: true })).toBeVisible();
    await expect(page.getByRole('log').locator('p')).toHaveCount(0);
    assert.deepEqual(await submissions(page), []);
    if (initialGeneralId) {
      await page.evaluate(() => window.selectConversation('saved'));
      await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(3);
      await page.evaluate(() => window.connections[2].callbacks.onOpen());
      await ack(page, 'saved', 2);
      await resolve(page, transcript(), 1);
      await expect.poll(async () => (await submissions(page)).length).toBe(1);
      assert.equal((await submissions(page))[0].context.general_chat_id, 'saved');
    }
  });
}

test('draft for an unacknowledged new conversation cannot migrate to an external selection', async t => {
  const page = await fixture(t);
  await ack(page); await resolve(page);
  await page.getByRole('button', { name: /New conversation/ }).click();
  await send(page, 'draft for pending new conversation');
  await page.evaluate(() => window.selectConversation('other'));
  await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
  await page.evaluate(() => window.connections[1].callbacks.onOpen());
  await ack(page, 'ignored-new', 0, 'chat.general_session_created');
  await ack(page, 'other', 1);
  await resolve(page, transcript('other', []), 1);
  await expect(page.getByText('Ask anything', { exact: true })).toBeVisible();
  await expect(page.getByRole('log').locator('p')).toHaveCount(0);
  assert.deepEqual(await submissions(page), []);
});

for (const fresh of [false, true]) {
  test(`connection retry preserves only its own pending ${fresh ? 'new' : 'initial'} conversation draft`, async t => {
    const page = await fixture(t, 1280, fresh ? 'saved' : null);
    if (fresh) {
      await ack(page); await resolve(page);
      await page.getByRole('button', { name: /New conversation/ }).click();
    }
    await send(page, 'draft survives retry');
    await page.evaluate(() => window.connections[0].callbacks.onClose());
    await page.getByRole('button', { name: 'Retry connection' }).click();
    await expect.poll(() => page.evaluate(() => window.connections.length)).toBe(2);
    await page.evaluate(() => window.connections[1].callbacks.onOpen());
    const expectedCommand = fresh ? 'chat.start_general_chat' : 'chat.enter_general_mode';
    assert.equal(await page.evaluate(() => window.sent.at(-1).type), expectedCommand);
    await ack(page, 'server-selected', 1, fresh ? 'chat.general_session_created' : 'chat.mode_changed');
    await resolve(page, transcript('server-selected', []), fresh ? 1 : 0);
    await expect.poll(async () => (await submissions(page)).length).toBe(1);
    assert.equal((await submissions(page))[0].context.general_chat_id, 'server-selected');
    await expect(page.getByRole('log')).toHaveText('draft survives retry');
  });
}
