import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {decideContinuation, continuationMessage, continuationPauseReason} from '../continuation.js';
import {normalizedText, renderedText, COMPOSER_SELECTOR} from '../selectors.js';
const flush = async () => { for(let i=0;i<80;i++) await Promise.resolve(); };

async function harness(interrupt, overrides = {}) {
  let clock=0, tick, clicks=0, beforeInput, runtimeCalls=0;const listeners={};const badge={style:{}};
  const settings={enabled:true,phase:'idle',maxTurns:10,...overrides.settings};
  const task={id:'t',conversation_id:'c',status:'waiting_for_model',turn_count:0,...overrides.task};
  const composer=overrides.richComposer ? {innerText:'',textContent:''} : {value:'',textContent:''};
  const rendered = () => composer.value ?? composer.innerText;
  const page={conversationId:'c',composer,composerReady:true,draftEmpty:true,generating:false,errorKind:null,assistantText:'old reply',assistantCount:1,userCount:1,lastUserText:'initial task'};
  page.sendButton={disabled:false,click(){clicks++;if(overrides.missingEcho)return;if(!overrides.virtualized)page.userCount++;else page.assistantCount--;page.lastUserText=normalizedText(rendered());if(overrides.richComposer){composer.innerText='';composer.textContent='';}else composer.value='';page.draftEmpty=true;}};
  const selectors={normalizedText,renderedText,COMPOSER_SELECTOR,selectConversation:()=>({...page,draftEmpty:!normalizedText(rendered())}),fillComposer:(node,text)=>{if(overrides.richComposer){node.innerText=text;node.textContent=text.replace(/\n/g,'');}else node.value=text;}};
  let pending='';
  const chrome={runtime:{getURL:x=>x,onMessage:{addListener(){}},async sendMessage(m){
    runtimeCalls++;if(overrides.runtimeInvalidated)throw Error("Extension context invalidated.");
    if(m.type==='PAUSE_CHAT'){settings.enabled=false;return {ok:true};}
    if(m.type==='TRACE_SEND'){(settings.sendTrace ??= []).push(m.snapshot);return {ok:true};}
    if(m.type==='GET_STATUS'||m.type==='HELLO')return {ok:true,settings:{...settings},workMode:{enabled:!!overrides.workMode},task:{...task},continuation_ready:true};
    if(m.type==='AUTO_PREPARE'){if(overrides.prepareFailure)throw Error('pairing required');settings.enabled=true;settings.awaitingTask=!overrides.reuseTask;return {ok:true,settings:{...settings},bindingCode:overrides.reuseTask?undefined:'fresh-binding',taskId:overrides.reuseTask?'t':null};}
    if(m.type==='PREPARE_USER_SEND'){settings.phase='user_sending';settings.pendingMessage=m.fingerprint;settings.lastAssistantText=m.assistantText;return {ok:true};}
    if(m.type==='VALIDATE_USER_SEND')return {ok:true,allowed:settings.enabled&&settings.phase==='user_sending'&&!!overrides.workMode};
    if(m.type==='ACK_USER_SEND'||m.type==='CANCEL_USER_SEND'){settings.phase='idle';delete settings.pendingMessage;return {ok:true};}
    if(m.type==='CLAIM'){settings.phase='leased';return {ok:true,lease:{id:`lease${task.turn_count}`}};}
    if(m.type==='PREPARE_SEND'){settings.phase='sending';pending=m.fingerprint;settings.lastAssistantCount=m.assistantCount;settings.lastAssistantText=m.assistantText;return {ok:true};}
    if(m.type==='FINAL_SEND')return {ok:true,allowed:settings.enabled&&page.conversationId==='c'&&task.status==='waiting_for_model'};
    if(m.type==='ACK'){settings.phase='idle';task.turn_count++;return {ok:true};}
    throw Error(m.type);
  }}};
  let source=fs.readFileSync(new URL('../content.js',import.meta.url),'utf8');
  source=source.replace(/await import\(chrome\.runtime\.getURL\('selectors.js'\)\)/,'dependencies.selectors').replace(/await import\(chrome\.runtime\.getURL\('continuation.js'\)\)/,'dependencies.continuation');
  vm.runInNewContext(source,{dependencies:{selectors,continuation:{decideContinuation,continuationMessage,continuationPauseReason}},chrome,
    document:{querySelectorAll:()=>[],createElement:()=>badge,documentElement:{append(){}},addEventListener(type,fn){listeners[type]=fn;if(type==='beforeinput')beforeInput=fn;}},location:{},Date:{now:()=>clock},
    clearInterval(){},setInterval:fn=>{tick=fn;return 1;},setTimeout:(fn,ms)=>{clock+=ms;if(ms===300&&interrupt)interrupt(settings,page,task);fn();}});
  await flush();
  return {async enter(shiftKey=false){let prevented=false;listeners.keydown?.({isTrusted:true,key:'Enter',shiftKey,target:{closest:()=>composer},preventDefault(){prevented=true;},stopImmediatePropagation(){}});await flush();return prevented;},async submit(){let prevented=false;listeners.click?.({isTrusted:true,target:page.sendButton,preventDefault(){prevented=true;},stopImmediatePropagation(){}});await flush();return prevented;},async tick(){clock+=4000;tick();await flush();},async manualInput(){beforeInput({isTrusted:true,target:{closest:selector=>selector.includes('[data-composer-markdown]') ? composer : null}});await flush();},get clicks(){return clicks;},get runtimeCalls(){return runtimeCalls;},get badgeText(){return badge.textContent;},settings,page,task};
}

test('work mode prepares a fresh binding on normal Send after a completed task',async()=>{
  const h=await harness(null,{workMode:true,task:{status:'completed'}});
  h.page.composer.value='修改 hello.py 输出你好呀';
  assert.equal(await h.submit(),true);
  assert.equal(h.clicks,1);
  assert.match(h.page.lastUserText,/Bridge binding fresh-binding/);
  assert.match(h.page.lastUserText,/修改 hello.py 输出你好呀/);
});

test('ordinary chats retain their original Send behavior',async()=>{
  const h=await harness(null,{workMode:false});h.page.composer.value='普通聊天';
  assert.equal(await h.submit(),false);assert.equal(h.clicks,0);
});

test('work mode cannot send if task preparation fails',async()=>{
  const h=await harness(null,{workMode:true,prepareFailure:true});h.page.composer.value='my request';
  await h.submit();assert.equal(h.clicks,0);assert.equal(h.page.composer.value,'my request');
});
test('Enter prepares work requests while Shift+Enter remains a newline',async()=>{
  const h=await harness(null,{workMode:true,task:{status:'completed'}});h.page.composer.value='next task';
  assert.equal(await h.enter(true),false);assert.equal(h.clicks,0);
  assert.equal(await h.enter(),true);assert.equal(h.clicks,1);
});
test('draft changes during automatic preparation prevent the replayed send',async()=>{
  const h=await harness((s,page)=>{page.composer.value='edited request';},{workMode:true,task:{status:'completed'}});
  h.page.composer.value='initial request';await h.submit();
  assert.equal(h.clicks,0);assert.equal(h.page.composer.value,'edited request');
});
test('active-task user requests wait for a new assistant reply before automatic continuation',async()=>{
  const h=await harness(null,{workMode:true,reuseTask:true});h.page.composer.value='additional requirement';
  await h.submit();assert.equal(h.clicks,1);assert.equal(h.settings.phase,'idle');
  await h.tick();await h.tick();assert.equal(h.clicks,1);
});

test('uncertain work request sends retain pending intent and cannot automatically repeat',async()=>{
  const h=await harness(null,{workMode:true,reuseTask:true,missingEcho:true});
  h.page.composer.value='additional requirement';await h.submit();await flush();
  assert.equal(h.clicks,1);assert.equal(h.settings.phase,'user_sending');assert.equal(h.settings.enabled,false);
  await h.tick();assert.equal(h.clicks,1);
});

test('manual typing in the current ID-less composer pauses continuation',async()=>{
  const h=await harness();await h.manualInput();assert.equal(h.settings.enabled,false);await h.tick();assert.equal(h.clicks,0);
});
test('rich composer paragraph boundaries do not prevent an authorized send',async()=>{
  const h=await harness(null,{richComposer:true});await h.tick();
  assert.equal(h.clicks,1);assert.equal(h.task.turn_count,1);
  assert.deepEqual(h.settings.sendTrace.map(x=>x.stage),['before_fill','before_click','echo_confirmed','acknowledged']);
  assert.equal(JSON.stringify(h.settings.sendTrace).includes('continue task'),false);
});
test('unique full message echo confirms send when virtualization keeps user count unchanged',async()=>{
  const h=await harness(null,{virtualized:true,richComposer:true});
  await h.tick();
  assert.equal(h.clicks,1);assert.equal(h.page.userCount,1);
  assert.equal(h.task.turn_count,1);
  assert.equal(h.settings.sendTrace.at(-1).stage,'acknowledged');
  await h.tick();assert.equal(h.clicks,1);
});
test('later replies can continue when rendered history counts shrink',async()=>{
  const h=await harness(null,{virtualized:true,richComposer:true});await h.tick();
  h.page.userCount=0;h.page.assistantText='new verified reply';
  await h.tick();await h.tick();
  assert.equal(h.clicks,2);assert.equal(h.task.turn_count,2);
});

test('new binding waits for task_begin instead of stopping on the previous completed task',async()=>{
  const h=await harness(null,{settings:{awaitingTask:true},task:{status:'completed'}});
  await h.tick();assert.equal(h.settings.enabled,true);assert.equal(h.clicks,0);
});
test('generation must stop and settle before any draft insertion or send',async()=>{
  const h=await harness();h.page.generating=true;
  await h.tick();await h.tick();
  assert.equal(h.clicks,0);assert.equal(h.page.composer.value,'');
  assert.equal(h.settings.sendTrace,undefined);
  h.page.generating=false;await h.tick();
  assert.equal(h.clicks,1);assert.equal(h.task.turn_count,1);
});

test('background retains pending binding until a different task is registered',async()=>{
  const values={pairing:{base:'http://127.0.0.1:8900',token:'token'}};let listener;
  let current={id:'old',status:'completed'};
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{get:async()=>({id:1,url:'https://chatgpt.com/c/c'}),sendMessage:async()=>({ok:true}),onRemoved:{addListener(){}}},
    storage:{local:{get:async key=>({[key]:values[key]}),set:async obj=>Object.assign(values,obj)}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,
    fetch:async url=>({ok:true,json:async()=>new URL(url).pathname.endsWith('/bind')?{binding_code:'one-use'}:{task:current,approvals:[]}})});
  const invoke=type=>new Promise(resolve=>listener({type,tabId:1,conversationId:'c',maxTurns:3},{},resolve));
  const enabled=await invoke('ENABLE_CHAT');assert.equal(enabled.settings.awaitingTask,true);assert.equal(enabled.settings.taskId,null);
  const pending=await invoke('GET_STATUS');assert.equal(pending.settings.awaitingTask,true);
  current={id:'new',status:'active'};
  const started=await invoke('GET_STATUS');assert.equal(started.settings.awaitingTask,false);assert.equal(started.settings.taskId,'new');
});

test('Pause during preparation prevents click',async()=>{
  const h=await harness(settings=>{settings.enabled=false;});await h.tick();assert.equal(h.clicks,0);
});
test('navigation during preparation prevents click',async()=>{
  const h=await harness((s,page)=>{page.conversationId='different';});await h.tick();assert.equal(h.clicks,0);
});
test('echoed send cannot reuse old assistant reply while generation is delayed',async()=>{
  const h=await harness();await h.tick();assert.equal(h.clicks,1);await h.tick();await h.tick();assert.equal(h.clicks,1);
});

test('actual background HELLO after Send before ACK pauses persisted intent',async()=>{
  const values={pairing:{base:'http://127.0.0.1:8900',token:'token'},'chat:1:c':{enabled:true,phase:'sending',maxTurns:10,leaseId:'l',pendingMessage:'sent'}};
  let listener;
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{get:async()=>({id:1,url:'https://chatgpt.com/c/c'}),onRemoved:{addListener(){}}},
    storage:{local:{get:async key=>key===null?{...values}:{[key]:values[key]},set:async obj=>Object.assign(values,obj),remove:async key=>{delete values[key];}}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,
    fetch:async()=>({ok:true,json:async()=>({task:null,approvals:[]})})});
  const result=await new Promise(resolve=>listener({type:'HELLO',conversationId:'c'},{tab:{id:1,url:'https://chatgpt.com/c/c'}},resolve));
  assert.equal(result.settings.enabled,false);assert.match(result.settings.reason,/暂停/);
});

test('repeated pairing click keeps the token from the successful first request',async()=>{
  const values={};let listener,requests=0;
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{onRemoved:{addListener(){}}},storage:{local:{get:async key=>({[key]:values[key]}),set:async obj=>Object.assign(values,obj)}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,
    fetch:async()=>{requests++;return requests===1?{ok:true,json:async()=>({token:'successful-token'})}:{ok:false,status:403,json:async()=>({error:'invalid or expired pairing code'})};}});
  const invoke=()=>new Promise(resolve=>listener({type:'PAIR',code:'one-use-code',base:'http://127.0.0.1:8900'},{},resolve));
  const results=await Promise.all([invoke(),invoke()]);
  assert.equal(results[0].ok,true);assert.equal(results[1].ok,false);
  assert.equal(values.pairing.token,'successful-token');
});

test('failed pairing leaves an existing connection unchanged',async()=>{
  const values={pairing:{base:'http://127.0.0.1:8900',token:'existing-token'}};let listener;
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{onRemoved:{addListener(){}}},storage:{local:{get:async key=>({[key]:values[key]}),set:async obj=>Object.assign(values,obj)}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,
    fetch:async()=>({ok:false,status:403,json:async()=>({error:'invalid or expired pairing code'})})});
  const result=await new Promise(resolve=>listener({type:'PAIR',code:'used-code',base:'http://127.0.0.1:9000'},{},resolve));
  assert.equal(result.ok,false);assert.equal(values.pairing.token,'existing-token');assert.equal(values.pairing.base,'http://127.0.0.1:8900');
});

test('send diagnostics retain only bounded metadata for the sending tab',async()=>{
  const values={};let listener;
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{onRemoved:{addListener(){}}},storage:{local:{get:async key=>({[key]:values[key]}),set:async obj=>Object.assign(values,obj)}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal});
  const sender={tab:{id:1,url:'https://chatgpt.com/c/c'}};
  for(let i=0;i<10;i++) {
    const result=await new Promise(resolve=>listener({type:'TRACE_SEND',conversationId:'c',snapshot:{stage:'before_click',users:i,text:'private text',token:'private token',controls:['Send','Stop streaming','private label']}},sender,resolve));
    assert.equal(result.ok,true);
  }
  const trace=values['chat:1:c'].sendTrace;
  assert.equal(trace.length,8);assert.equal(trace[0].users,2);
  assert.equal(JSON.stringify(trace).includes('private'),false);
});

async function backgroundHarness(values, task) {
  let listener,binds=0;
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{get:async id=>({id,url:'https://chatgpt.com/c/c'}),sendMessage:async()=>({ok:true}),onRemoved:{addListener(){}}},
    storage:{local:{get:async key=>key===null?{...values}:{[key]:values[key]},set:async obj=>Object.assign(values,obj),remove:async key=>{delete values[key];}}}};
  vm.runInNewContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,
    fetch:async url=>({ok:true,json:async()=>new URL(url).pathname.endsWith('/bind')?(binds++,{binding_code:'new-binding'}):{task,approvals:[]}})});
  return {values,get binds(){return binds;},invoke:(type,tabId=2,extra={})=>new Promise(resolve=>listener({type,conversationId:'c',...extra},{tab:{id:tabId,url:'https://chatgpt.com/c/c'}},resolve)),ui:type=>new Promise(resolve=>listener({type,tabId:2,conversationId:'c'},{},resolve))};
}

test('conversation work mode survives a new tab and prepares another task',async()=>{
  const h=await backgroundHarness({'work:c':{enabled:true,maxTurns:8}}, {id:'old',status:'completed'});
  const result=await h.invoke('AUTO_PREPARE');
  assert.equal(result.ok,true);assert.equal(result.bindingCode,'new-binding');
  assert.equal(result.settings.awaitingTask,true);assert.equal(result.settings.maxTurns,8);
});

test('automatic preparation does not enable unrelated chats or abandon pending sends',async()=>{
  const plain=await backgroundHarness({},null);
  assert.equal((await plain.invoke('AUTO_PREPARE')).ok,false);assert.equal(plain.binds,0);
  const pending=await backgroundHarness({'work:c':{enabled:true,maxTurns:8},'chat:2:c':{phase:'sending',enabled:false}}, {id:'old',status:'completed'});
  assert.equal((await pending.invoke('AUTO_PREPARE')).ok,false);assert.equal(pending.binds,0);
});

test('existing enabled chats migrate to persistent work mode and explicit Pause disables it',async()=>{
  const h=await backgroundHarness({pairing:{token:'t'},'chat:2:c':{enabled:false,phase:'idle',taskId:'old',maxTurns:5,reason:'任务已结束：completed'}}, {id:'old',status:'completed'});
  const result=await h.invoke('GET_STATUS');assert.equal(result.workMode.enabled,true);
  await h.ui('PAUSE_CHAT');assert.equal(h.values['work:c'].enabled,false);
  assert.equal((await h.invoke('AUTO_PREPARE')).ok,false);
});
test('active tasks are reused without generating another binding',async()=>{
  const h=await backgroundHarness({'work:c':{enabled:true,maxTurns:8}}, {id:'active-task',status:'waiting_for_model'});
  const result=await h.invoke('AUTO_PREPARE');
  assert.equal(result.taskId,'active-task');assert.equal(result.bindingCode,undefined);assert.equal(h.binds,0);
});
test('explicit Pause applies to every open tab of the same conversation',async()=>{
  const h=await backgroundHarness({pairing:{token:'t'},'work:c':{enabled:true,maxTurns:8},
    'chat:2:c':{enabled:true,phase:'idle',maxTurns:8},'chat:3:c':{enabled:true,phase:'idle',maxTurns:8}}, {id:'active-task',status:'waiting_for_model'});
  await h.ui('PAUSE_CHAT');
  const other=await h.invoke('GET_STATUS',3);
  assert.equal(other.settings.enabled,false);
  assert.equal((await h.invoke('CLAIM',3,{taskId:'active-task'})).lease,null);
});

test('work request intent survives refresh and only its matching echo can acknowledge it',async()=>{
  const h=await backgroundHarness({pairing:{token:'t'},'work:c':{enabled:true,maxTurns:8},
    'chat:2:c':{enabled:true,phase:'idle',maxTurns:8}}, {id:'active-task',status:'waiting_for_model'});
  assert.equal((await h.invoke('PREPARE_USER_SEND',2,{fingerprint:'request',assistantText:'old reply'})).ok,true);
  assert.equal((await h.invoke('VALIDATE_USER_SEND',2,{fingerprint:'wrong'})).allowed,false);
  assert.equal((await h.invoke('ACK_USER_SEND',2,{fingerprint:'wrong'})).ok,false);
  const refreshed=await h.invoke('HELLO');assert.equal(refreshed.settings.enabled,false);
  assert.equal(refreshed.settings.phase,'user_sending');
  assert.equal((await h.invoke('AUTO_PREPARE')).ok,false);
  assert.equal((await h.invoke('ACK_USER_SEND',2,{fingerprint:'request'})).ok,true);
  assert.equal(h.values['chat:2:c'].lastAssistantText,'old reply');
  assert.equal(h.values['chat:2:c'].phase,'idle');
});

 test("invalidated extension context stops polling and requests page refresh",async()=>{
 const h=await harness(null,{runtimeInvalidated:true});await h.tick();const attempts=h.runtimeCalls;await h.tick();assert.equal(h.runtimeCalls,attempts);assert.match(h.badgeText,/刷新.*ChatGPT/);
});
