import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium, expect} from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const root=path.dirname(shell);
const pages=path.join(root,'factory_app/app/admin/pages');
const registry={mrr:{unit:'currency_usd',label:'MRR'},arr:{unit:'currency_usd',label:'ARR'}};
const available=value=>({available:true,value});
const baseline={summary:{app:{name:'First app',description:'A real app description.',lifecycle_state:'building',
  current_build_run:{phase:'genesis',active_workflow_id:'ThemeCapture',updated_at:'2026-11-28T00:00:00Z'}}},
  usage:null,runs:null};

test('portfolio and overview distinguish unavailable data from zero at phone and desktop widths',async t=>{
  const bundle=await build({
    stdin:{contents:`import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
      import {BrowserRouter,Routes,Route} from 'react-router-dom';
      import Overview from ${JSON.stringify(path.join(pages,'AppOverviewPage.jsx'))};
      import Revenue from ${JSON.stringify(path.join(pages,'WorkspaceRevenuePage.jsx'))};
      import Integrations from ${JSON.stringify(path.join(pages,'PortfolioIntegrations.jsx'))};
      import Usage from ${JSON.stringify(path.join(pages,'WorkspaceUsagePage.jsx'))};
      window.mozaiksAuth={getAccessToken:()=> 'fixture-token'};
      function Fixture(){const [revision,setRevision]=useState(0);window.replaceFixture=value=>{window.fixture=value;setRevision(x=>x+1)};
        return <BrowserRouter><Routes key={revision}><Route path="/apps/:appId/overview" element={<Overview/>}/>
        <Route path="/revenue" element={<Revenue/>}/><Route path="/integrations" element={<Integrations/>}/>
        <Route path="/usage" element={<Usage/>}/></Routes></BrowserRouter>}
      createRoot(document.getElementById('root')).render(<Fixture/>);`,resolveDir:shell,loader:'jsx'},
    bundle:true,write:false,format:'esm',jsx:'automatic',loader:{'.js':'jsx','.png':'dataurl'},
    nodePaths:[path.join(shell,'node_modules')],
    alias:{'@mozaiks/chat-ui/ui':path.join(root,'chat-ui/src/ui/index.js'),'@mozaiks/chat-ui/admin':path.join(root,'chat-ui/src/admin')},
    plugins:[{name:'fixture-boundaries',setup(builder){
      builder.onResolve({filter:/^@mozaiks\/chat-ui\/workspace$|\/(useAppStudioData|useStudioAnalytics|useWorkspaceApps|useWorkspaceStudioData)\.js$/},args=>({path:args.path,namespace:'fixture'}));
      builder.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:
        args.path.includes('useAppStudioData')?'export const useAppStudioData=()=>({data:window.fixture.data,loading:false,error:null,dataMode:"live",refresh:()=>{}});':
        args.path.includes('useStudioAnalytics')?'export const useAppAnalytics=()=>({data:window.fixture.analytics});export const usePortfolioAnalytics=()=>({data:window.fixture.portfolio,loading:false,error:null,dataMode:window.fixture.dataMode||"live"});':
        args.path.includes('useWorkspaceApps')?'export const useWorkspaceApps=()=>({apps:window.fixture.apps,loading:false,error:null});':
        args.path.includes('useWorkspaceStudioData')?'export const useWorkspaceStudioData=()=>({apps:window.fixture.apps,workspaceRuns:[],workspaceUsage:window.fixture.usage,loading:false,error:null,dataMode:"live"});':
        'export const WorkspaceLayout=({children})=>children;'}));
    }}],
  });
  const stylesheet=path.join(shell,'styles.css');
  const styles=await postcss([tailwindcss()]).process((await fs.readFile(stylesheet,'utf8'))+'\n@source "'+pages.replaceAll('\\','/')+'";\n@source "'+path.join(root,'chat-ui/src/ui').replaceAll('\\','/')+'";',{from:stylesheet});
  const requests=[];
  const server=http.createServer((req,res)=>{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname.startsWith('/api/')){
      requests.push({app:url.searchParams.get('dashboard_app_id'),authorization:req.headers.authorization});
      res.setHeader('Content-Type','application/json');
      if(url.searchParams.get('dashboard_app_id')==='second'){res.statusCode=503;res.end('{}');}
      else res.end(JSON.stringify({connectors:[],declarations:[]}));
      return;
    }
    res.setHeader('Content-Type',url.pathname==='/fixture.js'?'text/javascript':'text/html');
    res.end(url.pathname==='/fixture.js'?bundle.outputFiles[0].text:'<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>'+styles.css+'</style><div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve)}));
  const browser=await chromium.launch();t.after(()=>browser.close());
  const origin='http://127.0.0.1:'+server.address().port;
  for(const width of [390,1280]) await t.test(width+'px',async()=>{
    const page=await browser.newPage({viewport:{width,height:844}});page.setDefaultTimeout(5000);
    const fixture={data:baseline,analytics:{registry,metrics:{mrr:available(0),active_users:{available:false,value:0}}},
      portfolio:{registry,apps:[{app_id:'factory-first',dashboard_app_id:'first',name:'First app',metrics:{mrr:available(0)}},
        {app_id:'factory-second',dashboard_app_id:'second',name:'Second app',metrics:{mrr:{available:false,value:0}}}]},
      apps:[{app_id:'first',name:'First app'},{app_id:'second',name:'Second app'}],usage:null};
    try{
      await page.addInitScript(value=>{window.fixture=value},fixture);
      await page.goto(origin+'/apps/first/overview');
      await expect(page.getByRole('link',{name:'Open Project: Genesis Build · Theme Capture',exact:true})).toHaveAttribute('href','/apps/first/building');
      await expect(page.getByText('MRR',{exact:true}).locator('..')).toContainText('$0.00');
      await expect(page.getByText('Runtime cost',{exact:true}).locator('..')).toContainText('Unavailable');
      await expect(page.getByText('Active users',{exact:true}).locator('..')).toContainText('Unavailable');
      await expect(page.getByText(/Next step|Readiness board|App intelligence/)).toHaveCount(0);
      await expect(page.getByRole('button',{name:'Add cover'})).toHaveCount(0);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      const noPhase={...fixture,data:{...baseline,summary:{app:{name:'First app',lifecycle_state:'draft'}}}};
      await page.evaluate(value=>window.replaceFixture(value),noPhase);
      await expect(page.getByRole('link',{name:/Open Project:/})).toHaveCount(0);
      await page.goto(origin+'/revenue');
      const first=page.getByRole('link',{name:'First app',exact:true});
      await expect(first).toHaveAttribute('href','/apps/first/revenue');
      const visibleRows=page.locator(width<768?'article':'tbody tr');
      await expect(visibleRows.filter({hasText:'First app'})).toContainText('$0.00');
      await expect(visibleRows.filter({hasText:'Second app'})).toContainText('Unavailable');
      await page.goto(origin+'/integrations');
      await expect(page.getByText('0 connections · 0 build requirements',{exact:true}).filter({visible:true})).toBeVisible();
      await expect(page.getByText('Unavailable',{exact:true}).filter({visible:true})).toBeVisible();
      await expect(page.getByRole('link',{name:'First app',exact:true})).toHaveAttribute('href','/apps/first/integrations');
      await page.goto(origin+'/usage');
      await expect(page.getByText('Usage records could not be loaded.',{exact:true})).toBeVisible();
      await expect(page.getByText('$0.00',{exact:true})).toHaveCount(0);
      const usage={cost_source:'mixed',pricing_health:{status:'unpriced_models'},totals:{llm_calls:2},
        by_run:[{app_id:'first',chat_id:'one',prompt_tokens:100,completion_tokens:20,estimated_cost_usd:2,llm_calls:1},
          {app_id:'second',chat_id:'two',prompt_tokens:10,completion_tokens:5,estimated_cost_usd:0,llm_calls:1}],
        events:[{app_id:'first',cost_source:'provider_supplied'},{app_id:'second',cost_source:'not_configured'}]};
      await page.evaluate(value=>window.replaceFixture(value),{...fixture,usage});
      const usageRows=page.locator(width<768?'article':'tbody tr');
      await expect(usageRows.filter({hasText:'First app'})).toContainText('$2.00');
      await expect(usageRows.filter({hasText:'Second app'})).toContainText('Unavailable');
      await page.goto(origin+'/revenue');
      await page.evaluate(value=>window.replaceFixture(value),{...fixture,dataMode:'demo'});
      await expect(page.getByText('Demo data',{exact:true})).toBeVisible();
      if(process.env.STUDIO_PORTFOLIO_EVIDENCE_DIR){
        await fs.mkdir(process.env.STUDIO_PORTFOLIO_EVIDENCE_DIR,{recursive:true});
        await page.goto(origin+'/apps/first/overview');
        await page.screenshot({path:path.join(process.env.STUDIO_PORTFOLIO_EVIDENCE_DIR,'overview-'+width+'.png'),fullPage:true});
      }
    }finally{await page.close()}
  });
  assert.ok(requests.length>=4);
  assert.ok(requests.every(req=>['first','second'].includes(req.app)&&req.authorization==='Bearer fixture-token'));
});
