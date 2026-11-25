import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');

test('an early workflow message is recoverable in its own chat and resends only on user action', async t => {
  // Exercise the full-page send and receipt handlers with a private socket and
  // chat-creation endpoint. The factory shell and AG2 each have their own tests.
  const pageSource = await readFile(path.join(ui, 'pages/ChatPage.js'), 'utf8');
  const section = (start, end) => {
    const first = pageSource.indexOf(start);
    const last = pageSource.indexOf(end, first + start.length);
    assert.ok(first >= 0 && last > first, start);
    return pageSource.slice(first, last);
  };
  const sendHandler = section('  const sendMessage = async (messageContent)', '  const sendWsMessage =');
  const composerState = section('  const composerScope =', '  const workflowSocket =');
  const incoming = section("      case 'error': {", "      case 'resume_boundary':");
  const snapshotMerger = section('  const mergeWorkflowTranscriptMessages =', '  const persistWorkflowTranscriptSnapshot =');
  const admissionGate = section('  const workflowSendDisabled =', '  useEffect(');
  const { outputFiles } = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useCallback, useEffect, useRef, useState } from 'react';
      import { flushSync } from 'react-dom';
      import { createRoot } from 'react-dom/client';
      import {WebSocketApiAdapter, RestApiAdapter} from ${JSON.stringify(path.join(ui, 'adapters/api.js'))};
      import ChatInterface from ${JSON.stringify(path.join(ui, 'components/chat/ChatInterface.jsx'))};
      import {buildComposerArtifactContext} from ${JSON.stringify(path.join(ui, 'components/chat/inlineSurfaces.js'))};
      import useWorkflowSubmission, {withoutWorkflowSubmission} from ${JSON.stringify(path.join(ui, 'pages/hooks/useWorkflowSubmission.js'))};
      import {readStoredWorkflowTranscriptSnapshot,writeStoredWorkflowTranscriptSnapshot} from ${JSON.stringify(path.join(ui, 'session/chatSessionStorage.js'))};
      const ws = new WebSocket('ws://workflow.test/workflow');
      const noop = () => {};
      const resolveKnownWorkflowName = name => name;
      const sanitizeVisibleWorkflowMessages = rows => rows;
      const findPendingComposerInputRequestToolCall = () => null;
      const setChatStatusUsage = noop, setChatStatusProgress = noop;
      const currentArtifactContext = { artifact_id: 'survey-map' }, activeGeneralChatId = null, urlWorkflowName = null;
      const queryContext = {}, queryTriggerSource = null, queryFreshStart = false;
      const socketApi = new WebSocketApiAdapter({baseUrl:location.origin});
      const restApi = new RestApiAdapter({baseUrl:location.origin});
      const api = {
        startChat: () => window.startChat(),
        sendMessageToWorkflow: (...args) => {
          if (window.useHttp) return restApi.sendMessageToWorkflow(...args);
          socketApi._chatConnections.set(args[4], {send: frame => {ws.send(JSON.stringify(frame)); return true;}});
          return socketApi.sendMessageToWorkflow(...args);
        },
      };
      function Room() {
        const [scope, setScope] = useState(['workflow', 'app', 'owner', 'Study', 'chat-a']);
        const [isWorking, setLoading] = useState(false);
        const [pendingWorkflowReply, setPendingWorkflowReply] = useState(null);
        const [conversationMode,currentAppId,currentUserId,currentWorkflowName,currentChatId] = scope;
        const setCurrentChatId = chatId => setScope(previous => [...previous.slice(0, 4), chatId]);
        const setActiveChatId = noop, setCurrentWorkflowName = noop, setActiveWorkflowName = noop;
        const setConversationMode = noop, navigate = noop;
        const [messages, setMessagesWithLogging] = useState([]);
        const messagesRef = useRef(messages); messagesRef.current = messages;
        const workflowMessagesCacheRef = useRef([]), workflowMessagesSharedRef = useRef([]);
        const setWorkflowMessages = noop;
        const waitingForInput = Boolean(pendingWorkflowReply);
        const lastErrorIdRef = useRef(null);
        ${composerState}
        ${sendHandler}
        ${snapshotMerger}
        ${admissionGate}
        useEffect(() => {
          if (!currentChatId) return;
          const merged = mergeWorkflowTranscriptMessages(workflowMessagesCacheRef.current, messages);
          workflowMessagesCacheRef.current = merged;
          writeStoredWorkflowTranscriptSnapshot(currentChatId, {messages:merged});
        }, [currentChatId, messages, mergeWorkflowTranscriptMessages]);
        window.switchRoom = next => {
          const rows = readStoredWorkflowTranscriptSnapshot(next[4])?.messages || [];
          workflowMessagesCacheRef.current = rows;
          // Complete fixture-driven navigation before the browser types into the destination.
          flushSync(() => {
            setScope(next); setMessagesWithLogging(rows); setLoading(false); setPendingWorkflowReply(null);
          });
        };
        useEffect(() => {
          ws.onmessage = event => {
            const data = JSON.parse(event.data);
            window.receipts = (window.receipts || 0) + 1;
            switch(data.type.replace(/^chat\\./, '')) {
              ${incoming}
              case 'awaiting_reply': setLoading(false); setPendingWorkflowReply({}); return;
            }
          };
          return () => { ws.onmessage = null; };
        });
        return <ChatInterface messages={messages} onSendMessage={sendMessage} loading={false}
          workflowName={scope[3]} connectionStatus="connected" transcriptLabel="Conversation" plainContainer conversationMode={scope[0]}
          sendDisabled={workflowSendDisabled} composerValue={composerDraft}
          onComposerChange={changeDraft} rejectedDraft={recoveredWorkflowDraft} />;
      }
      createRoot(document.getElementById('root')).render(<Room />);
    ` },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl', '.css': 'empty' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"', 'process.env': '{}' },
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
  await page.addScriptTag({ type:'module', content: outputFiles[0].text });
  const input = page.getByRole('textbox');
  await expect.poll(async () => errors[0] || ((await input.count()) ? 'Ready' : '' )).toBe('Ready');
  const send = page.getByRole('button', { name: 'Send message' });
  const text = '  Compare cohorts\nincluding the last screen.  ';
  const transcript = page.getByRole('region', {name:'Conversation'});
  const message = transcript.getByText(/including the last screen\./);
  let receipts = 0;
  const receipt = async (attempt, accepted = false, chatId = attempt.chat_id, errorCode = 'CHAT_BUSY') => {
    socket.send(JSON.stringify({
      type: accepted ? 'chat.input_ack' : 'chat.error',
      data: { chat_id: chatId, client_message_id: attempt.context.client_message_id,
        ...(accepted ? {} : {error_code:errorCode}) },
    }));
    await expect.poll(() => page.evaluate(() => window.receipts)).toBe(++receipts);
  };
  const ready = async (chatId = 'chat-a') => {
    socket.send(JSON.stringify({ type:'chat.awaiting_reply', data:{chat_id:chatId} }));
    await expect.poll(() => page.evaluate(() => window.receipts)).toBe(++receipts);
  };

  await input.fill(text);
  await send.click();
  await expect.poll(() => attempts.length).toBe(1);
  assert.equal(attempts[0].text, text);
  assert.equal(attempts[0].context.artifact_context.artifact_id, 'survey-map');
  await expect(message).toHaveCount(1);
  await receipt(attempts[0], false, 'other-chat');
  await expect(input).toHaveValue('');
  await receipt(attempts[0]);
  await expect(input).toHaveValue(text);
  await expect(message).toHaveCount(0);
  await expect(send).toBeDisabled();
  await input.press('Enter');
  assert.equal(attempts.length, 1);
  await ready();
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
  await receipt({chat_id:'chat-a',context:{}}, false, 'chat-a', 'WORKFLOW_MODEL_UNAVAILABLE');
  await expect(input).toHaveValue('');
  await expect(message).toHaveCount(1);

  await input.fill('Another question');
  await expect(send).toBeDisabled();
  await ready();
  await input.press('Enter');
  await expect.poll(() => attempts.length).toBe(3);
  await input.fill('A newer draft');
  await receipt(attempts[2], false, 'chat-a', 'CHAT_LOCK_BUSY');
  await expect(input).toHaveValue('A newer draft');
  await expect(send).toBeEnabled();
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
  await expect(transcript.getByText('Another question', {exact:true})).toHaveCount(0);
  await expect(message).toHaveCount(1);
  assert.equal(attempts.length, 3);
  await input.fill('Test a retired model');
  await send.click();
  await expect.poll(() => attempts.length).toBe(4);
  await receipt(attempts[3], false, 'chat-a', 'WORKFLOW_MODEL_UNAVAILABLE');
  await expect(input).toHaveValue('Test a retired model');
  await expect(transcript.getByText('Test a retired model', {exact:true})).toHaveCount(0);

  const unstarted = ['workflow','app','owner','Study',null];
  for (const outcome of ['error', 'missing-id']) {
    await page.evaluate(({unstarted, outcome}) => {
      window.switchRoom(unstarted);
      window.startChat = async () => {
        if (outcome === 'error') throw new Error('Chat creation unavailable');
        return {};
      };
    }, {unstarted, outcome});
    const draft = '  First question before opening a chat  ';
    await input.fill(draft);
    await send.click();
    await expect(input).toHaveValue(draft);
    await expect(transcript.getByText(draft.trim(), {exact:true})).toHaveCount(0);
    assert.equal(attempts.length, 4);
  }
  await page.evaluate(() => {
    window.startChat = () => new Promise((_, reject) => { window.rejectStart = reject; });
  });
  await input.fill('Pending creation');
  await send.click();
  await page.waitForFunction(() => typeof window.rejectStart === 'function');
  await page.evaluate(() => window.switchRoom(['workflow','app','owner','Study','chat-b']));
  await expect(input).toHaveValue('');
  await page.evaluate(() => window.rejectStart(new Error('Failed previous room creation')));
  await expect(input).toHaveValue('');
  await expect(transcript.getByText('Pending creation', {exact:true})).toHaveCount(0);

  await page.evaluate(unstarted => {
    window.switchRoom(unstarted);
    window.startChat = () => new Promise(resolve => { window.completeStart = resolve; });
  }, unstarted);
  await input.fill('First turn in a new study');
  await send.click();
  await page.waitForFunction(() => typeof window.completeStart === 'function');
  await input.fill('A follow-up I am still writing');
  await page.evaluate(() => window.completeStart({chat_id:'new-study'}));
  await expect.poll(() => attempts.length).toBe(5);
  await expect(input).toHaveValue('A follow-up I am still writing');
  await receipt(attempts[4], false, 'new-study', 'CHAT_LOCK_BUSY');
  await expect(input).toHaveValue('A follow-up I am still writing');
  await page.getByText('View unsent message').click();
  await expect(page.getByText('First turn in a new study', {exact:true})).toHaveCount(1);
  await page.evaluate(() => window.switchRoom(['workflow','app','owner','Study','chat-b']));
  await expect(input).toHaveValue('');
  await page.evaluate(() => { window.useHttp = true; });
  const requests = [];
  await page.route('**/chat/app/chat-b/owner/input', route => { requests.push(route); });
  for (const runStatus of ['paused','completed']) {
    const count = requests.length;
    const prompt = 'REST turn ' + runStatus;
    await input.fill(prompt);
    await send.click();
    await expect.poll(() => requests.length).toBe(count + 1);
    await input.fill('Next turn');
    await ready('chat-b');
    await expect(send).toBeDisabled();
    await input.press('Enter');
    assert.equal(requests.length, count + 1);
    const request = requests[count];
    assert.equal(request.request().postDataJSON().message, prompt);
    await request.fulfill({ json:{ delivery_state:'accepted', result:{run_status:runStatus} } });
    await expect(send).toBeEnabled();
    await expect(input).toHaveValue('Next turn');
    await expect(transcript.getByText(prompt, {exact:true})).toHaveCount(1);
  }
  assert.deepEqual(errors, []);
});
