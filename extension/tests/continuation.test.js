import test from 'node:test';
import assert from 'node:assert/strict';
import { decideContinuation, continuationMessage, continuationPauseReason } from '../continuation.js';
const page={conversationId:'c',generating:false,errorKind:null,composerReady:true,draftEmpty:true,settled:true};
const task={id:'task',conversation_id:'c',status:'waiting_for_model',turn_count:0};
const settings={enabled:true,maxTurns:3,phase:'idle'};
test('pause diagnostics distinguish unavailable composer, draft, and pending send',()=>{
  assert.match(continuationPauseReason({...page,composerReady:false,composerCount:2},task,settings),/输入框.*2/);
  assert.match(continuationPauseReason({...page,draftEmpty:false,draftLength:12},task,settings),/草稿.*12/);
  assert.match(continuationPauseReason(page,task,{...settings,phase:'sending'}),/sending/);
  assert.equal(continuationPauseReason(page,task,settings),null);
});
test('pause for errors, navigation, cap, drafts and ambiguous refresh',()=>{
  for(const p of [{errorKind:'quota'},{conversationId:'other'},{composerReady:false},{draftEmpty:false}]) assert.equal(decideContinuation({...page,...p},task,settings,{id:'l'}),'pause');
  assert.equal(decideContinuation(page,task,{...settings,phase:'sending'},{id:'l'}),'pause');
  assert.equal(decideContinuation(page,{...task,turn_count:3},settings,{id:'l'}),'pause');
  for(const status of ['completed','blocked','cancelled']) assert.equal(decideContinuation(page,{...task,status},settings,{id:'l'}),'pause');
});
test('wait for streaming, approvals, jobs and missing leases',()=>{
  assert.equal(decideContinuation({...page,generating:true},task,settings,{id:'l'}),'wait');
  assert.equal(decideContinuation(page,task,settings,null),'wait');
  for(const status of ['waiting_for_job','waiting_for_approval']) assert.equal(decideContinuation(page,{...task,status},settings,{id:'l'}),'wait');
  assert.equal(decideContinuation(page,task,settings,{id:'l'}),'send');
});
test('continuation includes original task and unique lease marker',()=>{
  const text=continuationMessage(task,{id:'lease-1'});
  assert.match(text,/task/);assert.match(text,/lease-1/);assert.match(text,/task_get/);
});
