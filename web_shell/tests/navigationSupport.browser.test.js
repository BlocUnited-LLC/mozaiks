import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium, expect} from '@playwright/test';
const shell=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const provider=path.resolve(shell,'../chat-ui/src/providers/NavigationProvider.jsx');
for(const supplied of [false,true]){
  test(`support binding survives NavigationProvider ${supplied?'supplied':'fetched'} config`,async t=>{
    const bundle=await build({
      stdin:{contents:`import React from 'react'; import {createRoot} from 'react-dom/client';
        import {NavigationProvider,useNavigation} from ${JSON.stringify(provider)};
        const config={support_module:'support',assistant_available:true,pages:[]};
        window.fetch=async()=>Response.json(config);
        function Consumer(){const {navigation,loading}=useNavigation();return <output>{loading?'loading':navigation.support_module}</output>;}
        createRoot(document.getElementById('root')).render(<NavigationProvider config={${supplied?'config':'null'}}><Consumer/></NavigationProvider>);`,resolveDir:shell,loader:'jsx'},
      bundle:true,write:false,format:'esm',jsx:'automatic',
      alias:{react:path.join(shell,'node_modules/react'),'react-dom':path.join(shell,'node_modules/react-dom')},
    });
    const server=http.createServer((req,res)=>{
      res.setHeader('Content-Type',req.url==='/fixture.js'?'text/javascript':'text/html');
      res.end(req.url==='/fixture.js'?bundle.outputFiles[0].text:'<div id="root"></div><script type="module" src="/fixture.js"></script>');
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    t.after(()=>new Promise(resolve=>server.close(resolve)));
    const browser=await chromium.launch({headless:true});
    t.after(()=>browser.close());
    const page=await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await expect(page.locator('output')).toHaveText('support');
  });
}
