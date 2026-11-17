import assert from 'node:assert/strict';
import test from 'node:test';

let revision = 0;
async function fixture() {
  const listeners = new Set();
  const data = new Map();
  const storage = {getItem:key=>data.get(key) ?? null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  const popup = {closed:false,sessionStorage:storage,close(){this.closed=true;},location:{replace(url){this.url=url;}}};
  globalThis.window = {location:new URL('https://app.test/auth/callback?code=code&state=owned'),
    sessionStorage:storage,open:()=>popup,
    addEventListener:(_name,listener)=>listeners.add(listener),removeEventListener:(_name,listener)=>listeners.delete(listener),
    setInterval,clearInterval,setTimeout,clearTimeout};
  const module = await import(`../../chat-ui/src/auth/browserAuthorizationTransport.js?test=${++revision}`);
  return {module,popup,listeners,storage};
}

test('web transport binds popup source, origin and transaction before returning a callback', async()=>{
  const {module,popup,listeners,storage}=await fixture();
  const transport=module.createBrowserAuthorizationTransport();
  transport.prepare();
  assert.equal(popup.location.url,undefined);
  assert.throws(()=>transport.prepare(),/already in progress/);
  const callbackUri='https://app.test/auth/callback';
  const pending=transport.open({url:'https://idp.test/authorize?state=owned',callbackUri});
  assert.equal(popup.location.url,'https://idp.test/authorize?state=owned');
  assert.equal(JSON.parse(storage.getItem('mozaiks.oidc.popup')).callbackUri,callbackUri);
  let settled=false; pending.then(()=>{settled=true;});
  const send=(source,origin,url)=>[...listeners].forEach(listener=>listener({source,origin,data:{type:'mozaiks.oidc.callback',url}}));
  send({},'https://app.test',callbackUri+'?state=owned&code=code');
  send(popup,'https://attacker.test',callbackUri+'?state=owned&code=code');
  send(popup,'https://app.test',callbackUri+'?state=wrong&code=code');
  send(popup,'https://app.test','https://app.test/other?state=owned&code=code');
  await Promise.resolve(); assert.equal(settled,false);
  send(popup,'https://app.test',callbackUri+'?state=owned&code=code');
  assert.equal(await pending,callbackUri+'?state=owned&code=code');
  assert.equal(popup.closed,true); assert.equal(listeners.size,0);
});

test('cancellation and blocked popups leave no pending listener',async()=>{
  const {module,listeners}=await fixture();
  const transport=module.createBrowserAuthorizationTransport(); transport.prepare();
  const pending=transport.open({url:'https://idp.test/authorize?state=owned',callbackUri:'https://app.test/auth/callback'});
  transport.cancel(); await assert.rejects(pending,/cancelled/); assert.equal(listeners.size,0);
  window.open=()=>null;
  assert.throws(()=>transport.prepare(),/Allow the sign-in popup/);
});

test('ordinary popup callbacks with an unrelated opener follow normal authentication',async()=>{
  const {module}=await fixture(); let calls=0;
  window.opener={postMessage(){calls++;}};
  assert.equal(module.relayBrowserAuthorization('/auth/callback'),false);
  assert.equal(calls,0);
});

test('only a fresh transport-owned matching callback is relayed once',async()=>{
  const {module,storage}=await fixture(); const messages=[];
  window.opener={postMessage:(...args)=>messages.push(args)};
  storage.setItem('mozaiks.oidc.popup',JSON.stringify({callbackUri:'https://app.test/auth/callback',state:'wrong',createdAt:Date.now()}));
  assert.equal(module.relayBrowserAuthorization('/auth/callback'),false);
  storage.setItem('mozaiks.oidc.popup',JSON.stringify({callbackUri:'https://app.test/auth/callback',state:'owned',createdAt:Date.now()-16*60*1000}));
  assert.equal(module.relayBrowserAuthorization('/auth/callback'),false);
  storage.setItem('mozaiks.oidc.popup',JSON.stringify({callbackUri:'https://app.test/auth/callback',state:'owned',createdAt:Date.now()}));
  assert.equal(module.relayBrowserAuthorization('/auth/callback'),true);
  assert.equal(module.relayBrowserAuthorization('/auth/callback'),true);
  assert.equal(messages.length,1); assert.equal(messages[0][1],'https://app.test');
  assert.equal(storage.getItem('mozaiks.oidc.popup'),null);
});
