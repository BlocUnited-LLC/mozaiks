import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test, { after, before } from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');
let browser;
let server;
let origin;

before(async () => {
  const stubs = {
    ChatUIContext: `import React,{createContext,useContext} from 'react';
      export const ChatContext=createContext(null);
      export const useChatUI=()=>useContext(ChatContext);`,
    NavigationProvider: `import React,{createContext,useContext} from 'react';
      export const NavigationContext=createContext(null);
      export const useNavigation=()=>useContext(NavigationContext);`,
    componentRegistry: `import React from 'react';
      import {LoginPage,AuthCallbackPage} from ${JSON.stringify(path.join(ui, 'auth/AuthPages.jsx'))};
      import {PageFrame} from ${JSON.stringify(path.join(ui, 'ui/page-renderer/PageFrame.jsx'))};
      const ChatPage=()=> <h1>Full chat</h1>;
      const AppPage=({route})=> window.scenario.geometry
        ? <PageFrame name="geometry-fixture" layout="sidebar" className="fixture-frame" bodyClassName="fixture-body">
            <form className="fixture-content" onSubmit={event=>{event.preventDefault();window.submitted+=1;}}>
              <h1>{route.component}</h1><div className="fixture-space"/>
              <textarea aria-label="Draft"/><button type="submit">Send</button>
            </form>
          </PageFrame>
        : <h1>{route.component}</h1>;
      export const hasComponent=name=>['ChatPage','Dashboard','Focus','Report','LoginPage','AuthCallbackPage','CustomLogin'].includes(name);
      export const getComponent=name=>name==='ChatPage'?ChatPage
        :name==='LoginPage'||name==='CustomLogin'?LoginPage:name==='AuthCallbackPage'?AuthCallbackPage:AppPage;`,
    TransitionScreen: `import React from 'react';
      export const TransitionScreen=({transitionId})=><h1>Transition {transitionId}</h1>;`,
    useTheme: `export const useTheme=()=>({theme:window.scenario.theme||{},loading:false}); export default useTheme;`,
    brandAssets: `export const getChatBackgroundSrc=()=>null;
      export const getBrandLogoSrc=()=> 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E';
      export const applyBrandImageFallback=()=>{};`,
    useWidgetAskWS: `export const useWidgetAskWS=options=>{
      window.widgetOptions={enabled:options.enabled,pagePath:options.pagePath,pageContext:options.pageContext};
      return {send:()=>false,status:'disconnected',isAgentTyping:false,generalModeReady:false};
    };`,
    ChatInterface: `import React from 'react'; export default ()=> <div>Ask conversation</div>;`,
    api: `export const authFetch=async(url,options)=>{
      window.apiRequests.push({url,method:options?.method||'GET'});
      if(url==='/api/workflows/trigger') return {ok:true,json:async()=>({chat_id:'started-chat',workflow_id:'Example'})};
      return {ok:true,json:async()=>({session_state:window.scenario.sessionState||null,sessions:window.scenario.sessions||[]})};
    };`,
    Header: 'export default ()=>null;',
    Footer: 'export default ()=>null;',
    MobileBottomBar: 'export default ()=>null;',
  };
  const bundle = await build({
    stdin: { contents: `
      import React,{useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {BrowserRouter,useNavigate} from 'react-router-dom';
      import RouteRenderer from ${JSON.stringify(path.join(ui, 'components/RouteRenderer.jsx'))};
      import GlobalChatWidgetWrapper from ${JSON.stringify(path.join(ui, 'widget/GlobalChatWidgetWrapper.jsx'))};
      import {ChatContext} from ${JSON.stringify(path.join(ui, 'context/ChatUIContext'))};
      import {NavigationContext} from ${JSON.stringify(path.join(ui, 'providers/NavigationProvider'))};
      const identity={user:window.scenario.authenticated===false?null:{id:'alice',app_id:'sample-app',roles:['user']},
        config:{appId:'sample-app',appName:window.scenario.appName},
        auth:{login:async()=>{},handleCallback:()=>new Promise(()=>{})},api:{}};
      function Fixture(){
        const [navigation,setNavigation]=useState(window.scenario);
        const [isInWidgetMode,setIsInWidgetMode]=useState(false);
        const [isWidgetVisible,setIsWidgetVisible]=useState(false);
        const [isChatOverlayOpen,setIsChatOverlayOpen]=useState(false);
        const [askMessages,setAskMessages]=useState([]);
        const [unreadChatCount,setUnreadChatCount]=useState(0);
        const navigate=useNavigate();
        window.updateNavigation=changes=>setNavigation(value=>({...value,...changes}));
        window.setUnread=setUnreadChatCount;
        window.go=navigate;
        const noop=()=>{};
        const chat={isInWidgetMode,setIsInWidgetMode,isWidgetVisible,setIsWidgetVisible,
          isChatOverlayOpen,setIsChatOverlayOpen,
          askMessages,setAskMessages,unreadChatCount,setUnreadChatCount,
          setConversationMode:noop,setActiveChatId:noop,setActiveWorkflowName:noop,setActiveGeneralChatId:noop,
          activeChatId:null,activeWorkflowName:null,conversationMode:'ask',loading:navigation.authLoading||false,
          ...identity};
        return <ChatContext.Provider value={chat}><NavigationContext.Provider value={navigation}>
          <RouteRenderer isAuthenticated={navigation.authenticated!==false}/>
          {navigation.bottomBar && <div className="shell-mobile-bottom-bar fixture-bottom-bar">
            <button onClick={()=>setIsChatOverlayOpen(true)}>Bottom bar assistant</button>
          </div>}
          <GlobalChatWidgetWrapper/>
        </NavigationContext.Provider></ChatContext.Provider>;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture/></BrowserRouter>);
    `, loader: 'jsx', resolveDir: shell },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    alias: Object.fromEntries(['react', 'react-dom', 'react-router-dom'].map(name => [name, path.join(shell, 'node_modules', name)])),
    nodePaths: [path.join(shell, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-boundaries', setup(builder) {
      builder.onResolve({ filter: /(?:ChatUIContext|NavigationProvider|componentRegistry|TransitionScreen|useTheme|brandAssets|useWidgetAskWS|ChatInterface|adapters\/api|layout\/(?:Header|Footer|MobileBottomBar))(?:\.[jt]sx?)?$/ }, args => {
        const name = path.basename(args.path).replace(/\.[jt]sx?$/, '');
        return { path: name, namespace: 'fixture-boundary' };
      });
      builder.onLoad({ filter: /.*/, namespace: 'fixture-boundary' }, args => ({ contents: stubs[args.path], loader: 'jsx', resolveDir: shell }));
    } }],
  });
  // Real shared CSS and real components. The fixture only supplies a flex-filled
  // custom page and synthetic identity/transport; it is not live-host acceptance.
  const styles = await postcss([tailwindcss()]).process(
    await fs.readFile(path.join(shell, 'styles.css'), 'utf8')
      + `\n@source "${ui.replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const mobileStyles = await fs.readFile(path.join(ui, 'styles/mobile.css'), 'utf8');
  const fixtureStyles = `
    .fixture-frame { height: 100dvh; }
    .fixture-frame > div { height: 100%; }
    .fixture-body { display: flex; flex: 1; }
    .fixture-content { display: flex; flex-direction: column; gap: .5rem; width: 100%; min-height: 0; }
    .fixture-space { flex: 1; }
    .fixture-content textarea { height: 4rem; flex-shrink: 0; border: 1px solid; }
    .fixture-content button { align-self: flex-end; min-height: 44px; padding: .5rem 1rem; border: 1px solid; }
    .fixture-bottom-bar { position: fixed; bottom: 0; width: 100%; height: 4.5rem; }
    @media (min-width: 768px) { .fixture-bottom-bar { display: none; } }
  `;
  server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text
      : `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles.css}\n${mobileStyles}\n${fixtureStyles}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
});

const appPage = (path, component = 'Dashboard', meta = {}) => ({ path, component, meta: { appShell: false, ...meta } });

async function fixture(t, scenario, pathname = '/', width = 1280) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.addInitScript(scenario => {
    window.scenario = scenario; window.apiRequests = []; window.submitted = 0;
    for (const [key, value] of Object.entries(scenario.storage || {})) localStorage.setItem(key, value);
  }, { pages: [], navigation: { assistant_available: true }, appName: 'Sample App', ...scenario });
  await page.goto(origin + pathname);
  return page;
}

async function framePadding(page) {
  return page.locator('[data-page="geometry-fixture"] > div').evaluate(element =>
    parseFloat(getComputedStyle(element).paddingBottom));
}

async function assertClearControls(page, label) {
  const geometry = await page.evaluate(() => {
    const launcher = document.querySelector('.widget-floating-launcher');
    const button = launcher.querySelector('button');
    const rect = element => {
      const { left, top, right, bottom, width, height } = element.getBoundingClientRect();
      return { left, top, right, bottom, width, height };
    };
    // Include the unread badge, which deliberately extends above the tab.
    const obstacles = [button, ...button.querySelectorAll('span')].map(rect);
    const controls = [...document.querySelectorAll('.fixture-content textarea, .fixture-content button')].map(element => {
      const bounds = rect(element);
      const uncovered = [.02, .5, .98].every(x => [.02, .5, .98].every(y =>
        element.contains(document.elementFromPoint(bounds.left + x * bounds.width, bounds.top + y * bounds.height))));
      const overlaps = obstacles.some(other => bounds.left < other.right && bounds.right > other.left
        && bounds.top < other.bottom && bounds.bottom > other.top);
      return { bounds, uncovered, overlaps };
    });
    return { controls, launcher: rect(button),
      launcherSize: [button.clientWidth, button.scrollWidth, button.clientHeight, button.scrollHeight],
      overflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
      launcherContentFits: button.scrollHeight <= button.clientHeight && button.scrollWidth <= button.clientWidth };
  });
  assert.equal(geometry.controls.length, 2);
  for (const control of geometry.controls) {
    assert.equal(control.overlaps, false, `${label}: full rectangle intersects launcher: ${JSON.stringify(geometry)}`);
    assert.equal(control.uncovered, true, `${label}: control edge/interior is covered: ${JSON.stringify(geometry)}`);
  }
  assert.equal(geometry.overflow, false, `${label}: viewport overflow`);
  assert.equal(geometry.launcherContentFits, true, `${label}: launcher content overflows: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.launcher.width >= 44 && geometry.launcher.height >= 44, `${label}: touch target`);
  await expect(page.getByRole('button', { name: 'Open assistant', exact: true })).toHaveCount(1);
  return geometry;
}

for (const width of [390, 1280]) {
  test(`PageFrame clears the entire composer and launcher lifecycle (${width}px)`, async t => {
    const page = await fixture(t, { geometry: true, authLoading: true, pages: [appPage('/')] }, '/', width);
    await expect(page.getByLabel('Draft')).toBeVisible();
    const baseline = await framePadding(page);
    await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
    await page.getByLabel('Draft').fill('Preserve this draft across assistant transitions');
    await page.evaluate(() => window.updateNavigation({ authLoading: false }));
    await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
    if (process.env.MOZAIKS_TEST_SCREENSHOTS) {
      await fs.mkdir(process.env.MOZAIKS_TEST_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.MOZAIKS_TEST_SCREENSHOTS, `widget-clearance-${width}.png`) });
    }
    const initial = await assertClearControls(page, `${width}px late mount`);
    assert.ok(await framePadding(page) > baseline, 'late launcher must reserve space');
    assert.equal(initial.launcher.height, width < 768 ? 60 : 104, 'default visual height preserved');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    assert.equal(await page.evaluate(() => window.submitted), 1);
    await page.evaluate(() => window.setUnread(9));
    await assertClearControls(page, `${width}px unread badge`);
    await page.getByRole('button', { name: 'Open assistant' }).click();
    await expect(page.getByText('Ask conversation', { exact: true })).toBeVisible();
    assert.equal(await framePadding(page), baseline, 'expanded overlay does not leave launcher padding');
    await page.getByTitle('Minimize', { exact: true }).click();
    await assertClearControls(page, `${width}px collapsed`);
    await expect(page.getByLabel('Draft')).toHaveValue('Preserve this draft across assistant transitions');
    await expect.poll(() => page.evaluate(() => window.widgetOptions.enabled)).toBe(true);
    await page.evaluate(() => window.go('/chat'));
    await expect(page.getByRole('heading', { name: 'Full chat', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).getPropertyValue('--shell-page-assistant-clearance').trim()), '');
    await page.evaluate(() => window.go('/'));
    await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
    await assertClearControls(page, `${width}px route return`);
  });
}

test('PageFrame clearance follows breakpoints, bottom-bar ownership, safe areas and 200% text size', async t => {
  const page = await fixture(t, { geometry: true, pages: [appPage('/')] }, '/', 390);
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
  for (const width of [390, 767, 768, 1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await assertClearControls(page, `${width}px resize`);
  }
  await page.evaluate(() => window.updateNavigation({ bottomBar: true }));
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeHidden();
  assert.equal(await framePadding(page), 32, 'mobile bottom bar removes the floating reservation');
  await page.getByRole('button', { name: 'Bottom bar assistant' }).click();
  await expect(page.getByTitle('Minimize', { exact: true })).toBeVisible();
  await page.getByTitle('Minimize', { exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 900 });
  await assertClearControls(page, 'desktop bottom bar hidden by media query');
  await page.setViewportSize({ width: 390, height: 900 });
  await page.evaluate(() => window.updateNavigation({ bottomBar: false }));
  await assertClearControls(page, 'mobile bottom bar removed');
  const beforeInset = await framePadding(page);
  await page.evaluate(() => document.documentElement.style.setProperty('--safe-area-bottom', '24px'));
  await assertClearControls(page, '24px safe-area inset');
  assert.equal(await framePadding(page), beforeInset + 24);
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => { document.documentElement.style.fontSize = '32px'; window.setUnread(99); });
    await assertClearControls(page, `${width}px 200% text size`);
  }
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '';
    document.documentElement.style.setProperty('--shell-page-padding-y-base', '12rem');
    document.documentElement.style.setProperty('--shell-page-padding-y-md', '12rem');
  });
  assert.equal(await framePadding(page), 192, 'larger app theme padding remains authoritative');
  await assertClearControls(page, 'larger theme padding');
  const stable = await framePadding(page);
  // Several frames without a mutation prove the clearance is not accumulating.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await framePadding(page), stable);
});

for (const width of [390, 1280]) {
  test(`custom root renders its page and one working launcher without a workflow (${width}px)`, async t => {
    const page = await fixture(t, { pages: [appPage('/', 'Focus', { ai_context: 'Focus tasks' })] }, '/', width);
    await expect(page.getByRole('heading', { name: 'Focus', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Full chat' })).toHaveCount(0);
    const launcher = page.getByRole('button', { name: 'Open assistant', exact: true });
    await expect(launcher).toHaveCount(1);
    await expect.poll(() => page.evaluate(() => window.widgetOptions)).toEqual({ enabled: false, pagePath: '/', pageContext: 'Focus tasks' });
    await launcher.click();
    await expect(page.getByText('Ask conversation', { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.widgetOptions.enabled)).toBe(true);
    await page.getByTitle('Minimize', { exact: true }).click();
    await expect(launcher).toBeVisible();
    assert.ok((await page.evaluate(() => window.apiRequests)).every(request => request.method === 'GET'));
  });
}

for (const [name, pages, pathname] of [
  ['fallback root', [], '/'],
  ['non-routable root declaration', [{ path: '/', meta: { title: 'Not a page' } }], '/'],
  ['declared chat root', [appPage('/', 'ChatPage')], '/'],
  ['declared full-chat alias', [appPage('/inbox', 'ChatPage')], '/inbox'],
  ['chat route', [], '/chat'],
  ['workflow chat route', [], '/chat/sample-app/Example'],
  ['short app route', [], '/app'],
  ['app workflow route', [], '/app/sample-app/Example'],
  ['reserved wildcard override', [appPage('/app/*')], '/app/sample-app/Example'],
]) {
  test(`${name} renders full chat without a second launcher`, async t => {
    const page = await fixture(t, { pages }, pathname);
    await expect(page.getByRole('heading', { name: 'Full chat', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
  });
}

for (const pathname of ['/chat/help', '/app/settings']) {
  test(`static app page ${pathname} outranks the core wildcard and retains its launcher`, async t => {
    const page = await fixture(t, { pages: [appPage(pathname)] }, pathname);
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
  });
}

test('launcher page context follows React Router ranking and wildcard matching', async t => {
  const page = await fixture(t, { pages: [
    appPage('/reports/:id', 'Report', { ai_context: 'Single report' }),
    appPage('/reports/latest', 'Dashboard', { ai_context: 'Latest reports' }),
    appPage('/archive/*', 'Report', { ai_context: 'Archive' }),
  ] }, '/reports/latest');
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.widgetOptions.pageContext)).toBe('Latest reports');
  await page.evaluate(() => window.go('/archive/2026/10'));
  await expect(page.getByRole('heading', { name: 'Report' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.widgetOptions.pagePath)).toBe('/archive/*');
  await page.evaluate(() => window.go('/chat'));
  await expect(page.getByRole('heading', { name: 'Full chat' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
});

test('non-root landing redirect keeps precedence over a declared root', async t => {
  const page = await fixture(t, { landing_spot: '/dashboard', pages: [appPage('/', 'Focus'), appPage('/dashboard')] });
  await expect(page).toHaveURL(origin + '/dashboard');
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Focus' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
});

test('a declared root transition takes precedence over its component without rendering full chat', async t => {
  const page = await fixture(t, { pages: [{ ...appPage('/', 'ChatPage'), transition: 'choose_project' }] });
  await expect(page.getByRole('heading', { name: 'Transition choose_project' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Full chat' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
});

test('navigation loading cannot reveal a launcher for an unresolved route', async t => {
  const page = await fixture(t, { loading: true, pages: [appPage('/dashboard')] }, '/dashboard');
  await expect(page.getByText('Loading…', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
  await page.evaluate(() => window.updateNavigation({ loading: false }));
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
});

test('a workflow root enters the workflow once and hides the launcher on the resulting chat', async t => {
  const page = await fixture(t, { pages: [{ ...appPage('/', 'Dashboard'), workflow: 'Example' }] });
  await expect(page).toHaveURL(origin + '/chat?mode=workflow&workflow=Example&chat_id=started-chat');
  await expect(page.getByRole('heading', { name: 'Full chat' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
  assert.equal((await page.evaluate(() => window.apiRequests)).filter(request => request.url === '/api/workflows/trigger').length, 1);
});

test('custom root retains the existing unauthenticated login redirect', async t => {
  const page = await fixture(t, { authenticated: false, pages: [
    appPage('/', 'Focus'), appPage('/login', 'LoginPage', { requiresAuth: false }),
  ] });
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).pathname, '/login');
  await expect(page.getByRole('heading', { name: 'Focus' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
});

test('custom root preserves the existing role authorization boundary', async t => {
  const page = await fixture(t, { pages: [appPage('/', 'Focus', { requiresRole: 'admin' })] });
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Focus' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Full chat' })).toHaveCount(0);
});

for (const [pathname, component] of [['/sign-in', 'CustomLogin'], ['/complete-sign-in', 'AuthCallbackPage']]) {
  test(`declared authentication surface ${pathname} has no assistant launcher`, async t => {
    const page = await fixture(t, { authenticated: false,
      navigation: { auth: { contract: { routes: { login: '/sign-in', callback: '/complete-sign-in' } } } },
      pages: [appPage(pathname, component, { requiresAuth: false })],
    }, pathname);
    await expect(page.getByRole('heading', { name: pathname === '/sign-in' ? 'Sign in' : 'Completing sign-in', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
  });
}

test('public app page retains its launcher without an authenticated user', async t => {
  const page = await fixture(t, { authenticated: false, pages: [appPage('/', 'Focus', { requiresAuth: false })] });
  await expect(page.getByRole('heading', { name: 'Focus' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open assistant' })).toBeVisible();
});

for (const [appName, theme, expectedName] of [
  ['Sample App', { branding: { name: 'Different theme name' } }, 'Sample App'],
  [null, { branding: { name: 'Theme App' } }, 'Theme App'],
  [null, {}, 'Assistant'],
]) {
  test(`assistant heading uses ${expectedName} and omits unsupported workflow access`, async t => {
    const page = await fixture(t, { appName, theme, pages: [appPage('/')] });
    await page.getByRole('button', { name: 'Open assistant' }).click();
    await expect(page.getByTitle('Open full Ask chat')).toContainText(expectedName);
    await expect(page.getByRole('button', { name: /^Go to / })).toHaveCount(0);
    await expect(page.getByText('mozaiksai', { exact: true })).toHaveCount(0);
  });
}

test('workflow access goes to the declared fresh-start route when no sessions exist', async t => {
  const page = await fixture(t, { pages: [appPage('/'), appPage('/new', 'Report', { freshStart: true })] });
  await page.getByRole('button', { name: 'Open assistant' }).click();
  await page.getByRole('button', { name: 'Go to workflows', exact: true }).click();
  await expect(page).toHaveURL(origin + '/new');
  await expect(page.getByRole('heading', { name: 'Report' })).toBeVisible();
});

test('workflow access resumes the actual server-owned session', async t => {
  const page = await fixture(t, { pages: [appPage('/')], sessions: [{ workflow_name: 'Example', chat_id: 'owned-chat' }] });
  await page.getByRole('button', { name: 'Open assistant' }).click();
  await page.getByRole('button', { name: 'Go to Example', exact: true }).click();
  const target = new URL(page.url());
  assert.equal(target.pathname, '/chat');
  assert.equal(target.searchParams.get('workflow'), 'Example');
  assert.equal(target.searchParams.get('chat_id'), 'owned-chat');
});

test('multiple owned workflows retain the picker and resume the selected session', async t => {
  const page = await fixture(t, { pages: [appPage('/')], sessions: [
    { workflow_name: 'FirstExample', chat_id: 'first-chat' },
    { workflow_name: 'SecondExample', chat_id: 'second-chat' },
  ] });
  await page.getByRole('button', { name: 'Open assistant' }).click();
  await page.getByRole('button', { name: 'Go to a workflow (2 running)', exact: true }).click();
  await expect(page).toHaveURL(origin + '/');
  await page.getByRole('button', { name: /SecondExample.*Resume/ }).click();
  await expect(page).toHaveURL(origin + '/chat?mode=workflow&chat_id=second-chat&workflow=SecondExample');
});

test('unscoped stale workflow storage does not create a workflow destination', async t => {
  const page = await fixture(t, { pages: [appPage('/')], storage: {
    'mozaiks.current_workflow_name': 'OtherAppWorkflow', 'mozaiks.current_chat_id': 'other-app-chat',
  } });
  await page.getByRole('button', { name: 'Open assistant' }).click();
  await expect(page.getByText('Ask conversation')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Go to / })).toHaveCount(0);
});

for (const owner of ['alice', 'bob']) {
  test(`saved workflow destination respects the current user scope (${owner})`, async t => {
    const page = await fixture(t, { pages: [appPage('/')], storage: {
      'mozaiks.current_workflow_name': 'Example',
      [`mozaiks.workflow_chat_id.sample-app.${owner}.Example`]: 'saved-chat',
    } });
    await page.getByRole('button', { name: 'Open assistant' }).click();
    if (owner !== 'alice') {
      await expect(page.getByRole('button', { name: /^Go to / })).toHaveCount(0);
      return;
    }
    await page.getByRole('button', { name: 'Go to workflows', exact: true }).click();
    await expect(page).toHaveURL(origin + '/chat?mode=workflow&chat_id=saved-chat&workflow=Example');
  });
}

for (const available of [false, undefined]) {
  test(`app without configured assistant hides launcher (${available})`, async t => {
    const page = await fixture(t, { pages: [appPage('/notes')], navigation: { assistant_available: available } }, '/notes');
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);
  });
}
