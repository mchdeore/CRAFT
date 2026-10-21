import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import './typescript-loader.mjs';
registerHooks({load(url,context,next){const loaded=next(url,context);return url.endsWith('/src/lib/api-client.ts')?{...loaded,source:String(loaded.source).replace(/import\.meta\.env\b/g,JSON.stringify({VITE_API_ORIGIN:'https://api.generated.example'}))}:loaded;}});
const {fromAgUiMessages,toAgUiMessages}=await import('@assistant-ui/react-ag-ui');
const {createSessionHistory}=await import('../src/lib/agent-history.ts');
const conversation='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const reasoning=(id)=>({id,role:'reasoning',content:'',encryptedValue:'generated-opaque-'+id});
const assistant=(id,content)=>({id,role:'assistant',content});
async function load(messages){const original=globalThis.fetch;globalThis.fetch=async()=>new Response(JSON.stringify({id:conversation,messages}),{headers:{'Content-Type':'application/json'}});try{return(await createSessionHistory(conversation).load()).messages.map(entry=>entry.message);}finally{globalThis.fetch=original;}}
const protectedValues=(wire)=>wire.filter(m=>m.role==='reasoning').map(m=>({id:m.id,encryptedValue:m.encryptedValue}));
test('history merge preserves both opaque reasoning records through actual SDK export',async()=>{
 const wire=[{id:'user',role:'user',content:'Generated request'},reasoning('first'),assistant('step','Generated step'),reasoning('later'),assistant('answer','Generated answer')];
 const expected=protectedValues(wire);assert.deepEqual(protectedValues(toAgUiMessages(fromAgUiMessages(wire))),expected,'installed SDK must retain both before the application merge');
 const loaded=await load(wire);assert.equal(loaded.filter(m=>m.role==='assistant').length,1);assert.deepEqual(protectedValues(toAgUiMessages(loaded)),expected);
});
test('history keeps opaque reasoning in separate turns across user boundary',async()=>{
 const wire=[{id:'user1',role:'user',content:'Generated request'},reasoning('first'),assistant('step','Generated step'),{id:'user2',role:'user',content:'Generated next'},reasoning('later'),assistant('answer','Generated answer')];
 const loaded=await load(wire);assert.equal(loaded.filter(m=>m.role==='assistant').length,2);assert.deepEqual(protectedValues(toAgUiMessages(loaded)),protectedValues(wire));
});
test('plain consecutive assistant messages retain ordered content without opaque data',async()=>{
 const loaded=await load([assistant('step','Generated step'),assistant('answer','Generated answer')]);assert.equal(loaded.length,1);assert.deepEqual(loaded[0].content.map(p=>p.text),['Generated step','Generated answer']);assert.deepEqual(protectedValues(toAgUiMessages(loaded)),[]);
});
