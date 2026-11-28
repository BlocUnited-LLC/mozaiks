import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');

test('generated app menu uses its server-admitted entry and clears it on account change', async t => {
  const stubs = {
    '../../providers/NavigationProvider': 'export const useNavigation=()=>window.shellNavigation;',
    '../../navigation/useNavigationActions': `import {useNavigate} from 'react-router-dom'; export const useNavigationActions=()=>{const go=useNavigate();return item=>go(item.path);};`,
    '../../context/ChatUIContext': 'export const useChatUI=()=>({login:()=>{},logout:()=>{}});',
    '../../ui/hooks/useAppEventBus.js': 'export const useAppEventBus=()=>{};',
    './notificationApi.js': 'export const fetchNotificationCount=async()=>0; export const clearNotifications=async()=>{};',
  };
  const bundle = await build({
    stdin:{contents:`import React from 'react';import {createRoot} from 'react-dom/client';
      import {BrowserRouter,useLocation} from 'react-router-dom';
      import Header from ${JSON.stringify(path.join(ui,'components/layout/Header.js'))};
      window.shellNavigation={landing_spot:'/home',pages:[],headerPages:[],header:{actions:[]},
        profile:{menu:[{id:'admin-portal',label:'Admin',path:'/admin',requiresRole:'admin'}]},
        notifications:{show:false},auth:{runtime:{enabled:true}}};
      function App(){const loc=useLocation();const [actor,setActor]=React.useState({id:'staff',name:'App Staff',roles:['user'],
        admin_entry:new URLSearchParams(window.location.search).get('entry')||'/admin/support'});
        window.switchActor=setActor;return <><Header user={actor}/><h1>{loc.pathname}</h1></>;}
      createRoot(document.getElementById('root')).render(<BrowserRouter><App/></BrowserRouter>);`,resolveDir:shell,loader:'jsx'},
    alias:{react:path.join(shell,'node_modules/react'),'react-dom':path.join(shell,'node_modules/react-dom'),'react-router-dom':path.join(shell,'node_modules/react-router-dom')},
    bundle:true,write:false,format:'esm',jsx:'automatic',loader:{'.js':'jsx','.css':'empty','.png':'dataurl'},
    plugins:[{name:'fixtures',setup(builder){
      builder.onResolve({filter:/.*/},args=>args.path in stubs?{path:args.path,namespace:'fixture'}:null);
      builder.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:stubs[args.path],loader:'js',resolveDir:shell}));
    }}],
  });
  const server=http.createServer((req,res)=>{
    const script=req.url==='/fixture.js';res.setHeader('Content-Type',script?'text/javascript':'text/html');
    res.end(script?bundle.outputFiles[0].text:'<div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const origin='http://127.0.0.1:'+server.address().port;
  for(const width of [390,1440]){
    const page=await browser.newPage({viewport:{width,height:900}});
    await page.goto(origin+'/home');await page.getByTitle('App Staff',{exact:true}).click();
    await page.getByRole('button',{name:'Admin',exact:true}).click();
    await expect(page.getByRole('heading',{name:'/admin/support',exact:true})).toBeVisible();
    await page.evaluate(()=>window.switchActor({id:'customer',name:'Customer',roles:['user']}));
    await page.getByTitle('Customer',{exact:true}).click();
    await expect(page.getByRole('button',{name:'Admin',exact:true})).toHaveCount(0);
    for(const entry of ['https://other.example/admin','/admin/../settings','/apps/foreign']){
      await page.goto(origin+'/home?entry='+encodeURIComponent(entry));
      await page.getByTitle('App Staff',{exact:true}).click();
      assert.equal(await page.getByRole('button',{name:'Admin',exact:true}).count(),0);
    }
    await page.close();
  }
});
