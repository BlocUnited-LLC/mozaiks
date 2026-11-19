import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source=fs.readFileSync(new URL('../../chat-ui/src/pages/ChatPage.js',import.meta.url),'utf8');
const expandBody=source.match(/onExpand=\{\(\) => \{([\s\S]*?)\n\s*\}\}/)?.[1];
const restoreBody=source.match(/const restoreViewSnapshot = useCallback\(\(\) => \{([\s\S]*?)\n  \}, \[/)?.[1];
assert.ok(expandBody,'ChatPage expansion callback must be present');
assert.ok(restoreBody,'ChatPage restore callback must be present');

function fixture(){
  const state={layout:'split',open:true,messages:['artifact-A']};
  const ref={current:null};
  const setLayoutMode=value=>{state.layout=value;};
  const setOpen=value=>{state.open=value;};
  const setMessages=value=>{state.messages=value;};
  const expand=()=>new Function('viewArtifactSnapshotRef','layoutMode','isSidePanelOpen','currentArtifactMessages','setLayoutMode','setIsSidePanelOpen',expandBody)(
    ref,state.layout,state.open,state.messages,setLayoutMode,setOpen);
  const restore=()=>new Function('viewArtifactSnapshotRef','setLayoutMode','setIsSidePanelOpen','setCurrentArtifactMessages',restoreBody)(
    ref,setLayoutMode,setOpen,setMessages);
  return {state,ref,expand,restore};
}

for(const reason of ['another chat is selected','a new artifact version arrives']){
  test(`presentation expansion retains current artifact when ${reason}`,()=>{
    const {state,ref,expand,restore}=fixture();
    expand();assert.equal(state.layout,'view');
    assert.equal(Object.hasOwn(ref.current,'messages'),false);
    state.messages=['artifact-B'];
    restore();
    assert.deepEqual(state.messages,['artifact-B']);
    assert.equal(state.layout,'split');assert.equal(state.open,true);
    assert.equal(ref.current,null);
  });
}

test('existing replacement-view snapshots retain their message restoration behavior',()=>{
  const {state,ref,restore}=fixture();
  ref.current={layoutMode:'split',isOpen:true,messages:['original-artifact']};
  state.messages=['temporary-view'];
  restore();assert.deepEqual(state.messages,['original-artifact']);
});
