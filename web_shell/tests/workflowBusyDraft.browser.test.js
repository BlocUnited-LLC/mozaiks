import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');

test('an early workflow message is recoverable in its own chat and resends only on user action', async t => {
  const { outputFiles } = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useCallback, useEffect, useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import ChatInterface from ${JSON.stringify(path.join(ui, 'components/chat/ChatInterface.jsx'))};
      import useWorkflowSubmission from ${JSON.stringify(path.join(ui, 'pages/hooks/useWorkflowSubmission.js'))};
      const ws = new WebSocket('ws://workflow.test/workflow');
      let sequence = 0;
      function Room() {
        const [scope, setScope] = useState(['workflow', 'app', 'owner', 'Study', 'chat-a']);
        const [working, setWorking] = useState(false);
        const [messages, setMessages] = useState([]);
        const remove = useCallback(id => setMessages(rows => rows.filter(row => row.id !== id)), []);
        const key = JSON.stringify(scope);
        const composer = useWorkflowSubmission(key, remove);
        const chatId = scope[4];
        window.switchRoom = next => { setScope(next); setMessages([]); setWorking(false); };
        useEffect(() => {
          ws.onmessage = event => {
            const frame = JSON.parse(event.data);
            if (frame.type === 'chat.input_ack') composer.handleAdmission(true, frame);
            if (frame.type === 'chat.error' && frame.data.error_code === 'CHAT_BUSY' && composer.handleAdmission(false, frame)) setWorking(true);
            if (frame.type === 'chat.awaiting_reply') setWorking(false);
            window.receipts = (window.receipts || 0) + 1;
          };
          return () => { ws.onmessage = null; };
        }, [composer.handleAdmission]);
        function send(message) {
          const row = {...message, id: String(++sequence)};
          composer.remember(key, chatId, row);
          setMessages(rows => [...rows, row]);
          ws.send(JSON.stringify({type:'user.input.submit', chat_id:chatId, text:row.content, context:{client_message_id:row.id}}));
        }
        return <ChatInterface messages={messages} onSendMessage={send} loading={false}
          workflowName={scope[3]} connectionStatus="connected" transcriptLabel="Conversation" plainContainer conversationMode={scope[0]}
          sendDisabled={working && scope[0] === 'workflow'} composerValue={composer.text}
          onComposerChange={composer.changeDraft} rejectedDraft={composer.refused} />;
      }
      createRoot(document.getElementById('root')).render(<Room />);
    ` },
    bundle: true, write: false, jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl', '.css': 'empty' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-boundaries', setup(builder) {
      const stubs = {
        'react-router-dom': 'export const useNavigate = () => () => {}; export const useParams = () => ({});',
        '../../core/ui/UIToolRenderer': 'export default function UIToolRenderer() { return null; }',
        '../../styles/brandAssets': 'export const getBrandLogoSrc = () => ""; export const applyBrandImageFallback = () => {};',
        '../../session/chatSessionStorage': 'export const logChatPersistence = () => {};',
      };
      builder.onResolve({ filter: /.*/ }, args => Object.hasOwn(stubs, args.path)
        ? { path: args.path, namespace: 'fixture' } : undefined);
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents: stubs[args.path], loader: 'js' }));
    } }],
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  const attempts = [];
  let socket;
  page.on('pageerror', error => errors.push(error.message));
  await page.routeWebSocket('**/workflow', ws => {
    socket = ws;
    ws.onMessage(payload => attempts.push(JSON.parse(payload)));
  });
  await page.route('http://workflow.test/', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><div id="root"></div>',
  }));
  await page.goto('http://workflow.test/');
  await page.addScriptTag({ content: outputFiles[0].text });
  const input = page.getByRole('textbox');
  const send = page.getByRole('button', { name: 'Send message' });
  const text = '  Compare cohorts\nincluding the last screen.  ';
  const transcript = page.getByRole('region', {name:'Conversation'});
  const message = transcript.getByText(/including the last screen\./);
  let receipts = 0;
  const receipt = async (attempt, accepted = false, chatId = attempt.chat_id) => {
    socket.send(JSON.stringify({
      type: accepted ? 'chat.input_ack' : 'chat.error',
      data: { chat_id: chatId, client_message_id: attempt.context.client_message_id,
        ...(accepted ? {} : {error_code:'CHAT_BUSY'}) },
    }));
    await expect.poll(() => page.evaluate(() => window.receipts)).toBe(++receipts);
  };
  const ready = () => socket.send(JSON.stringify({ type:'chat.awaiting_reply', data:{chat_id:'chat-a'} }));

  await input.fill(text);
  await send.click();
  await expect.poll(() => attempts.length).toBe(1);
  assert.equal(attempts[0].text, text);
  await expect(message).toHaveCount(1);
  await receipt(attempts[0], false, 'other-chat');
  await expect(input).toHaveValue('');
  await receipt(attempts[0]);
  await expect(input).toHaveValue(text);
  await expect(message).toHaveCount(0);
  await expect(send).toBeDisabled();
  await input.press('Enter');
  assert.equal(attempts.length, 1);
  ready();
  receipts++;
  await expect(send).toBeEnabled();
  assert.equal(attempts.length, 1);
  await send.click();
  await expect.poll(() => attempts.length).toBe(2);
  assert.equal(attempts[1].text, text);
  assert.notEqual(attempts[0].context.client_message_id, attempts[1].context.client_message_id);
  await receipt(attempts[1], true);
  await receipt(attempts[1]); // An accepted turn cannot be revoked by a late busy frame.
  await expect(input).toHaveValue('');
  await expect(message).toHaveCount(1);

  await input.fill('Another question');
  await input.press('Enter');
  await expect.poll(() => attempts.length).toBe(3);
  await input.fill('A newer draft');
  await receipt(attempts[2]);
  await expect(input).toHaveValue('A newer draft');
  await page.getByText('View unsent message').click();
  await expect(page.getByText('Another question', { exact: true })).toHaveCount(1);
  for (const scope of [
    ['workflow', 'app', 'owner', 'Study', 'chat-b'],
    ['workflow', 'other-app', 'owner', 'Study', 'chat-a'],
    ['workflow', 'app', 'other-owner', 'Study', 'chat-a'],
    ['workflow', 'app', 'owner', 'Other', 'chat-a'],
    ['ask', 'app', 'owner', 'Study', 'chat-a'],
    ['workflow', 'app', 'owner', 'Study', 'chat-a'],
  ]) {
    await page.evaluate(next => window.switchRoom(next), scope);
    await receipt(attempts[2]);
    await expect(input).toHaveValue('');
    await expect(page.getByText('View unsent message')).toHaveCount(0);
  }
  assert.equal(attempts.length, 3);
  assert.deepEqual(errors, []);
});
