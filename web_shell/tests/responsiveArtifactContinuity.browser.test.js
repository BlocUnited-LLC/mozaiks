import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.join(path.dirname(shell), 'chat-ui/src');

test('ChatPage retains one live artifact and conversation across responsive layouts', async (t) => {
  // Exercise ChatPage's actual composition with local transport-free children.
  // The production layout, drawer, artifact renderer, effects and reducer run.
  const source = (await fs.readFile(path.join(ui, 'pages/ChatPage.js'), 'utf8')).replaceAll('\r\n', '\n');
  const renderStart = source.lastIndexOf('  return (\n    <div\n      className="relative flex flex-col h-screen min-h-screen overflow-hidden"');
  assert.notEqual(renderStart, -1, 'ChatPage render boundary must be explicit');
  const render = source.slice(renderStart, source.lastIndexOf('\n};'));
  const artifactToggle = source.slice(source.indexOf('  const artifactToggleHandler ='),source.indexOf('  const artifactToggleLabel ='));
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, {useState, useEffect, useLayoutEffect, useRef, useReducer, useCallback} from 'react';
      import {createRoot} from 'react-dom/client';
      import FluidChatLayout from ${JSON.stringify(path.join(ui, 'components/chat/FluidChatLayout.jsx'))};
      import MobileArtifactDrawer from ${JSON.stringify(path.join(ui, 'components/chat/MobileArtifactDrawer.jsx'))};
      import ArtifactPanel from ${JSON.stringify(path.join(ui, 'components/chat/ArtifactPanel.jsx'))};
      import {useChatArtifactLayoutEffects} from ${JSON.stringify(path.join(ui, 'hooks/useChatArtifactLayoutEffects.js'))};
      import {createInitialSurfaceState, uiSurfaceReducer} from ${JSON.stringify(path.join(ui, 'state/uiSurfaceReducer.js'))};
      const noop = () => {};
      const ErrorBoundary = ({children}) => children;
      const ArtifactErrorFallback = () => null;
      const Footer = () => <footer style={{height:24}}>Workspace footer</footer>;
      const AskHistorySidebar = () => <aside>Saved conversations</aside>;
      const MobileAskHistoryDrawer = () => null;
      const Header = () => <header style={{height:56,position:'absolute',top:0,left:0,right:0,zIndex:60,background:'#101827'}}>
        <button onClick={() => window.setTestLayout('split')}>Open artifact</button>
        <button onClick={() => window.setTestLayout('full')}>Close artifact</button>
      </header>;
      window.lifecycle = {mounts:0,active:0,maximum:0,chatMounts:0};
      window.ArtifactProbe = function ArtifactProbe() {
        useEffect(() => {
          const counts = window.lifecycle;
          counts.mounts += 1; counts.active += 1; counts.maximum = Math.max(counts.maximum,counts.active);
          return () => { counts.active -= 1; };
        }, []);
        return <section aria-label="Live artifact" style={{display:'flex',flexDirection:'column',gap:12,height:'100%'}}>
          <h1>Draft app preview</h1><button>Artifact action</button>
          <iframe title="Live app preview" src="/preview" style={{width:'100%',flex:1,minHeight:180,border:0,background:'white'}} />
        </section>;
      };
      function Conversation() {
        const [draft,setDraft] = useState('');
        useEffect(() => { window.lifecycle.chatMounts += 1; }, []);
        return <section aria-label="Conversation" style={{display:'flex',flexDirection:'column',height:'100%',padding:16,background:'#17263a'}}>
          <h1>Build conversation</h1><p style={{flex:1}}>Review your draft app.</p>
          <label>Message<textarea aria-label="Message" value={draft} onChange={event => setDraft(event.target.value)} /></label>
          <button>Send message</button>
        </section>;
      }
      function Fixture() {
        const [surface,dispatch] = useReducer(uiSurfaceReducer,undefined,createInitialSurfaceState);
        const layoutMode = surface.layoutMode;
        const isSidePanelOpen = surface.artifact.panelOpen;
        const setLayoutMode = useCallback(mode => dispatch({type:'SET_LAYOUT_MODE',mode}),[]);
        const setIsSidePanelOpen = useCallback(open => dispatch({type:'SET_ARTIFACT_PANEL_OPEN',open}),[]);
        const [isMobileView,setIsMobileView] = useState(false);
        const [mobileDrawerState,setMobileDrawerState] = useState('peek');
        const [forceOverlay,setForceOverlay] = useState(false);
        const [hasUnseenArtifact,setHasUnseenArtifact] = useState(false);
        const [hasUnseenChat,setHasUnseenChat] = useState(false);
        const artifactRestoredOnceRef = useRef(false);
        const [hasArtifact,setHasArtifact] = useState(true);
        const conversationMode = surface.conversationMode;
        const effectiveLayoutMode = conversationMode === 'ask' ? 'full' : layoutMode;
        const isViewMode = effectiveLayoutMode === 'view';
        const exitViewMode = () => setLayoutMode('split');
        const closeMobileArtifact = () => {setMobileDrawerState('peek');setIsSidePanelOpen(false);};
        const toggleSidePanel = () => setIsSidePanelOpen(!isSidePanelOpen);
        const isInWidgetMode = false, handleReturnToChat = noop;
        ${artifactToggle}
        window.toggleTestArtifact = artifactToggleHandler;
        window.setTestLayout = mode => {
          setLayoutMode(mode);
          if (isMobileView) setMobileDrawerState(mode === 'full' ? 'peek' : 'expanded');
        };
        window.clearTestArtifact = () => setHasArtifact(false);
        window.setTestConversation = mode => dispatch({type:'SET_CONVERSATION_MODE',mode});
        useLayoutEffect(() => {
          window.testState = {layoutMode,isMobileView,mobileDrawerState,isSidePanelOpen,conversationMode};
        }, [layoutMode,isMobileView,mobileDrawerState,isSidePanelOpen,conversationMode]);
        useChatArtifactLayoutEffects({connectionStatus:'disconnected',currentChatId:'chat-1',chatExists:false,
          artifactRestoredOnceRef,conversationMode,currentWorkflowName:'Review',restoreStoredArtifactForChat:noop,
          layoutMode,setLayoutMode,setIsMobileView,setForceOverlay,widgetOverlayOpen:false,setWidgetOverlayOpen:noop,
          isSidePanelOpen,setIsSidePanelOpen,isMobileView,mobileDrawerState,setMobileDrawerState,
          setHasUnseenArtifact,hasUnseenChat,setHasUnseenChat,forceOverlay,isInWidgetMode:false,
          widgetChatMinimized:false,setWidgetChatMinimized:noop});
        const chatInterface = <Conversation />;
        const currentArtifactMessages = hasArtifact ? [{id:'artifact-1',toolCall:{tool_name:'Preview',payload:{}}}] : [];
        const mainPaddingClass = 'pt-14 md:pt-16';
        const mainContentStyle = isMobileView ? {paddingTop:'calc(env(safe-area-inset-top, 0px) + 3.5rem)'} : undefined;
        const chatPageShellStyle = isMobileView ? {height:'100vh',minHeight:'100dvh'} : undefined;
        const mobileChatTopMarginClass = 'mt-0';
        const mobileChatPaddingBottomClass = 'pb-[calc(env(safe-area-inset-bottom,0px)+0.5rem)]';
        const showInitSpinner = false, pendingTransitionId = null, chatBackgroundSrc = null;
        const user = null, chatTheme = null, themeLoading = false;
        const handleNotificationClick = noop, handleHeaderAction = noop;
        const artifactPanelLoading = false, currentChatId = 'chat-1', currentWorkflowName = 'Review';
        const sendArtifactAction = noop, actionStatusMap = {}, viewWidget = null;
        const showMobileHistoryMenu = false, showAskHistorySidebar = conversationMode === 'ask' && !isMobileView;
        const generalChatSessions = [], activeGeneralChatId = null, generalSessionsLoading = false;
        const handleSelectGeneralChat = noop, handleStartGeneralChat = noop;
        const handleRefreshGeneralSessions = noop, handleClearGeneralSessions = noop, handleDeleteGeneralSession = noop;
        ${render}
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle:true,write:false,outfile:'fixture.js',format:'esm',jsx:'automatic',loader:{'.js':'jsx','.png':'dataurl'},
    nodePaths:[path.join(shell,'node_modules')],
    alias:{react:path.join(shell,'node_modules/react'),'react-dom':path.join(shell,'node_modules/react-dom')},
    plugins:[{name:'artifact-tool-boundary',setup(builder) {
      builder.onResolve({filter:/UIToolRenderer$/},()=>({path:'artifact',namespace:'fixture'}));
      builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:
        'import React from "react"; export default function Renderer() {return React.createElement(window.ArtifactProbe);}',loader:'js'}));
    }}],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell,'styles.css'),'utf8'))
      + ['pages/ChatPage.js','components/chat/FluidChatLayout.jsx','components/chat/MobileArtifactDrawer.jsx','components/chat/ArtifactPanel.jsx']
        .map(file => `\n@source "${path.join(ui,file).replaceAll('\\','/')}";`).join(''),
    {from:path.join(shell,'styles.css')},
  );
  let previewRequests = 0;
  const server = http.createServer((request,response) => {
    if (request.url === '/fixture.js') {response.setHeader('Content-Type','text/javascript');response.end(bundle.outputFiles.find(file=>file.path.endsWith('.js')).text);return;}
    response.setHeader('Content-Type','text/html');
    if (request.url === '/preview') {
      previewRequests += 1;
      response.end('<style>body{font:16px Arial;padding:16px}input{display:block;margin-top:16px;max-width:90%}</style><h2>Project board</h2><button onclick="this.textContent=\'Item added\'">Add item</button><input aria-label="Preview note">');return;
    }
    response.end(`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles.css}
      ${bundle.outputFiles.find(file=>file.path.endsWith('.css'))?.text || ''}
      :root{--color-primary:#22d3ee;--color-primary-rgb:34,211,238;--color-primary-light-rgb:103,232,249;--shell-header-height:3.5rem}
      body{margin:0;background:#08111f;color:white;font:16px Arial}button{padding:8px}textarea{display:block;width:100%;background:white;color:#111}
    </style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const browser = await chromium.launch({headless:true});
  t.after(()=>browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const lifecycle = () => page.evaluate(()=>window.lifecycle);
  const expectState = state => expect.poll(()=>page.evaluate(()=>window.testState)).toMatchObject(state);
  const setLayout = async mode => {
    await page.evaluate(mode=>window.setTestLayout(mode),mode);
    await expectState({layoutMode:mode,isSidePanelOpen:mode!=='full'});
    if (page.viewportSize().width < 768) {
      await expectState({mobileDrawerState:mode==='full'?'peek':'expanded'});
    }
  };
  const resize = async viewport => {
    await page.setViewportSize(viewport);
    await expectState({isMobileView:viewport.width<768});
  };
  await expect(page.getByLabel('Message',{exact:true})).toBeVisible();
  await page.getByLabel('Message',{exact:true}).fill('Keep this unsent draft');
  await expect(page.locator('iframe')).toHaveCount(0);
  await page.getByRole('button',{name:'Open artifact',exact:true}).click();
  await expectState({layoutMode:'split',isSidePanelOpen:true});
  const frame = page.frameLocator('iframe[title="Live app preview"]');
  await expect(frame.getByRole('button',{name:'Add item',exact:true})).toBeVisible();
  await frame.getByRole('button',{name:'Add item',exact:true}).click();
  await frame.getByLabel('Preview note').fill('Keep this app state');
  await page.evaluate(()=>{window.originalFrame=document.querySelector('iframe');window.originalComposer=document.querySelector('textarea');});

  await t.test('open desktop preview crosses 768px in both directions without remounting',async()=>{
    for (const width of [767,390,768,1280]) {
      await resize({width,height:900});
      await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
      await expect(frame.getByLabel('Preview note')).toHaveValue('Keep this app state');
      await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
      assert.equal(await page.evaluate(()=>window.originalFrame===document.querySelector('iframe')),true);
      assert.equal(await page.evaluate(()=>window.originalComposer===document.querySelector('textarea')),true);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true);
      if (width < 768) {
        assert.equal(await page.locator('textarea').evaluate(element=>Boolean(element.closest('[inert]'))),true);
        await page.locator('textarea').evaluate(element=>element.focus());
        await expect(page.locator('textarea')).not.toBeFocused();
      }
    }
    assert.deepEqual(await lifecycle(),{mounts:1,active:1,maximum:1,chatMounts:1});
    assert.equal(previewRequests,1);
  });

  await t.test('full, minimized, view and closed mobile states preserve content and hide interaction',async()=>{
    for (const mode of ['minimized','view','split','full']) {
      await setLayout(mode);
      await expect(page.locator('iframe')).toHaveCount(1);
      await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
      if (mode==='full') await expect(page.locator('iframe')).toBeHidden();
      else await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
      if (mode==='minimized') {
        const expand = page.getByRole('button',{name:'Expand conversation',exact:true});
        await expect(expand).toBeVisible();
        await expand.focus();
        await page.locator('textarea').evaluate(element=>element.focus());
        await expect(expand).toBeFocused();
        await expand.click();
        await expectState({layoutMode:'split'});
        await expect(page.getByLabel('Message',{exact:true})).toBeVisible();
        await expect(page.getByLabel('Message',{exact:true})).toHaveValue('Keep this unsent draft');
      }
    }
    await page.getByLabel('Message',{exact:true}).focus();
    await page.locator('button').filter({hasText:'Artifact action'}).evaluate(button=>button.focus());
    await expect(page.getByLabel('Message',{exact:true})).toBeFocused();
    assert.equal(await page.locator('iframe').evaluate(element=>element.closest('[inert]')?.getAttribute('aria-hidden')), 'true');
    await resize({width:390,height:900});
    await expect(page.locator('iframe')).toBeHidden();
    await page.getByRole('button',{name:'Open artifact',exact:true}).click();
    await expectState({layoutMode:'split',mobileDrawerState:'expanded'});
    await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Collapse artifact workspace',exact:true}).click();
    await expectState({layoutMode:'full',isSidePanelOpen:false,mobileDrawerState:'peek'});
    await expect(page.locator('iframe')).toBeHidden();
    await expect(page.getByLabel('Message',{exact:true})).toBeVisible();
    const composerBox = await page.getByLabel('Message',{exact:true}).boundingBox();
    assert.ok(composerBox.y >= 0 && composerBox.y + composerBox.height <= 900,'The mobile composer fits the viewport');
    await page.getByLabel('Message',{exact:true}).focus();
    await frame.getByLabel('Preview note').evaluate(element=>element.focus());
    await expect(page.getByLabel('Message',{exact:true})).toBeFocused();
    await resize({width:1280,height:900});
    await expect(page.locator('iframe')).toBeHidden();
    await page.getByRole('button',{name:'Open artifact',exact:true}).click();
    await expectState({layoutMode:'split',isSidePanelOpen:true});
    await expect(frame.getByLabel('Preview note')).toHaveValue('Keep this app state');
    assert.deepEqual(await lifecycle(),{mounts:1,active:1,maximum:1,chatMounts:1});
    assert.equal(previewRequests,1);
  });

  await t.test('mobile drawer moves keyboard focus out of inert content and returns it on close',async()=>{
    await resize({width:390,height:844});
    await setLayout('full');
    const composer = page.getByLabel('Message',{exact:true});
    const collapse = page.getByRole('button',{name:'Collapse artifact workspace',exact:true});
    await composer.focus();
    await page.evaluate(()=>window.setTestLayout('split'));
    await expectState({layoutMode:'split',mobileDrawerState:'expanded'});
    await expect(collapse).toBeFocused();
    assert.equal(await page.evaluate(()=>Boolean(document.activeElement?.closest('[inert]'))),false);
    assert.equal(await composer.evaluate(element=>Boolean(element.closest('[inert]'))),true);
    await collapse.press('Enter');
    await expectState({layoutMode:'full',mobileDrawerState:'peek'});
    await expect(composer).toBeFocused();
    assert.equal(await page.evaluate(()=>Boolean(document.activeElement?.closest('[inert]'))),false);

    const trigger = page.getByRole('button',{name:'Open artifact',exact:true});
    await trigger.focus();
    await trigger.press('Enter');
    await expectState({layoutMode:'split',mobileDrawerState:'expanded'});
    await expect(collapse).toBeFocused();
    await collapse.press('Enter');
    await expectState({layoutMode:'full',mobileDrawerState:'peek'});
    await expect(trigger).toBeFocused();
    assert.equal(await page.evaluate(()=>Boolean(document.activeElement?.closest('[inert]'))),false);
    await expect(composer).toHaveValue('Keep this unsent draft');
    await expect(frame.getByLabel('Preview note')).toHaveValue('Keep this app state');
  });

  await t.test('view and minimized layouts retain their state across narrow and short viewports',async()=>{
    for (const mode of ['view','minimized']) {
      await setLayout(mode);
      for (const viewport of [{width:390,height:844},{width:900,height:430},{width:1280,height:900}]) {
        await resize(viewport);
        await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
        assert.equal(await page.evaluate(()=>window.testState.layoutMode),mode);
        await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
      }
    }
    await setLayout('split');
    assert.deepEqual(await lifecycle(),{mounts:1,active:1,maximum:1,chatMounts:1});
    assert.equal(previewRequests,1);
  });

  await t.test('mobile artifact toggle preserves an explicit close through desktop resize',async()=>{
    await resize({width:390,height:900});
    await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
    await page.evaluate(()=>window.toggleTestArtifact());
    await expectState({layoutMode:'full',isSidePanelOpen:false,mobileDrawerState:'peek'});
    await expect(page.locator('iframe')).toBeHidden();
    await resize({width:1280,height:900});
    await expect(page.locator('iframe')).toBeHidden();
    await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
    await page.getByRole('button',{name:'Open artifact',exact:true}).click();
    await expectState({layoutMode:'split',isSidePanelOpen:true});
    await expect(frame.getByLabel('Preview note')).toHaveValue('Keep this app state');
    assert.equal(previewRequests,1);
  });

  await t.test('Ask history sidebar insertion and mobile presentation retain the composer',async()=>{
    await page.evaluate(()=>window.setTestConversation('ask'));
    await expectState({conversationMode:'ask',layoutMode:'full',isSidePanelOpen:false});
    await expect(page.getByText('Saved conversations',{exact:true})).toBeVisible();
    await expect(page.locator('iframe')).toBeHidden();
    await resize({width:390,height:900});
    await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
    await resize({width:1280,height:900});
    await expect(page.locator('textarea')).toHaveValue('Keep this unsent draft');
    assert.equal((await lifecycle()).chatMounts,1);
    await page.evaluate(()=>window.setTestConversation('workflow'));
    await expectState({conversationMode:'workflow'});
    await setLayout('split');
    await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
  });

  await t.test('artifact chrome and iframe fill their allocated pane at tablet breakpoints',async()=>{
    for (const width of [767,768,900,1280]) {
      await resize({width,height:900});
      await expect.poll(()=>page.getByRole('region',{name:'Artifact output stream'}).evaluate(region=>{
        const chrome = region.parentElement;
        return Math.abs(chrome.getBoundingClientRect().width - chrome.parentElement.getBoundingClientRect().width);
      })).toBeLessThanOrEqual(2);
      const geometry = await page.locator('iframe').evaluate(iframe=>({
        frame:iframe.getBoundingClientRect().width,
        pane:iframe.closest('[aria-label="Artifact output stream"]').getBoundingClientRect().width,
      }));
      assert.ok(geometry.frame >= geometry.pane - 64,`Preview iframe fills its pane at ${width}px`);
    }
  });

  const screenshotDir = process.env.RESPONSIVE_ARTIFACT_QA_DIR;
  if (screenshotDir) {
    await fs.mkdir(screenshotDir,{recursive:true});
    const settleLayout = () => page.locator('.chat-pane-transition').evaluate(element=>
      Promise.all(element.getAnimations().map(animation=>animation.finished.catch(()=>{}))));
    await settleLayout();
    await page.screenshot({path:path.join(screenshotDir,'desktop-artifact.png')});
    await resize({width:390,height:844});
    await expect(frame.getByRole('button',{name:'Item added',exact:true})).toBeVisible();
    await settleLayout();
    await page.screenshot({path:path.join(screenshotDir,'mobile-artifact.png')});
    await page.getByRole('button',{name:'Collapse artifact workspace',exact:true}).click();
    await expectState({layoutMode:'full',isSidePanelOpen:false,mobileDrawerState:'peek'});
    await expect(page.getByLabel('Message',{exact:true})).toBeVisible();
    await settleLayout();
    await page.screenshot({path:path.join(screenshotDir,'mobile-composer.png')});
  }
  await page.evaluate(()=>window.clearTestArtifact());
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect.poll(async()=>(await lifecycle()).active).toBe(0);
  assert.deepEqual(errors,[]);
});
