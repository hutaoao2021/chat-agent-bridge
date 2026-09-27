import test from 'node:test';
import assert from 'node:assert/strict';
import { conversationId, classifyError, normalizedText, selectConversation } from '../selectors.js';
test('only real Chat conversation URLs qualify',()=>{
  assert.equal(conversationId('https://chatgpt.com/c/abc-123'),'abc-123');
  assert.equal(conversationId('https://chatgpt.com/g/g-id/c/abc'),'abc');
  for(const url of ['https://evil.test/c/abc','https://chatgpt.com/','https://chatgpt.com/?c=abc']) assert.equal(conversationId(url),null);
});

function editor(attributes, text = '') {
  return {textContent: text, getAttribute: name => attributes[name] ?? null,
    getClientRects: () => [1], matches: selector =>
      (selector === '#prompt-textarea' && attributes.id === 'prompt-textarea') ||
      (selector === '[data-composer-markdown][contenteditable="true"]' &&
        'data-composer-markdown' in attributes && attributes.contenteditable === 'true')};
}
function pageWith(editors) {
  return selectConversation({querySelectorAll(selector) {
    return editors.filter(node => selector.split(',').some(part => node.matches(part.trim())));
  }}, {href: 'https://chatgpt.com/c/test-chat'});
}
function messagePage(roleNodes, legacyNodes = {}, bubbles = []) {
  const composer = editor({contenteditable:'true','data-composer-markdown':''});
  return selectConversation({querySelectorAll(selector) {
    if (selector.includes('bubble')) return bubbles.filter(node=>selector.split(',').some(part=>part.trim()===node.selector));
    if (selector.includes('data-conversation-role')) return roleNodes.filter(node => selector.includes(`"${node.role}"`));
    if (selector.includes('data-message-author-role')) return legacyNodes[selector.includes('"assistant"')?'assistant':'user'] || [];
    return composer.matches(selector.split(',').at(-1).trim()) ? [composer] : [];
  }}, {href:'https://chatgpt.com/c/test-chat'});
}
test('user bubbles without role headings provide sent message counts and echo',()=>{
  const bubbles=[{selector:'[data-user-message-bubble]',innerText:'initial task'},{selector:'[data-user-message-bubble]',innerText:'[Bridge continuation lease]\ncontinue task'}];
  const page=messagePage([modernMessage('assistant','reply')],{},bubbles);
  assert.equal(page.userCount,2);
  assert.equal(page.lastUserText,'[Bridge continuation lease] continue task');
  assert.equal(page.assistantCount,1);
});

test('user bubble source does not double count legacy message wrappers',()=>{
  const bubble={selector:'[data-usermessage-bubble]',innerText:'[Bridge continuation lease]\ncontinue task'};
  const page=messagePage([],{user:[{textContent:'wrapper controls'}]},[bubble]);
  assert.equal(page.userCount,1);
  assert.equal(page.lastUserText,'[Bridge continuation lease] continue task');
});
function modernMessage(role, text) {
  const body = {textContent:text, matches:selector=>selector === '[data-chatgpt-selection-message-id]', getClientRects:()=>[1]};
  return {role, nextElementSibling:body};
}
test('modern role headings identify message bodies rather than hidden labels',()=>{
  const page = messagePage([modernMessage('user','initial task'),modernMessage('assistant','first reply'),
    modernMessage('user','[Bridge continuation lease]'),modernMessage('assistant','second reply')]);
  assert.equal(page.assistantCount,2);assert.equal(page.assistantText,'second reply');
  assert.equal(page.userCount,2);assert.equal(page.lastUserText,'[Bridge continuation lease]');
});
test('message echo preserves rendered paragraph boundaries',()=>{
  const user = modernMessage('user','[Bridge continuation lease]continue task');
  user.nextElementSibling.innerText='[Bridge continuation lease]\ncontinue task';
  assert.equal(messagePage([user]).lastUserText,'[Bridge continuation lease] continue task');
});
test('composer draft length follows rendered paragraphs',()=>{
  const composer=editor({contenteditable:'true','data-composer-markdown':''},'markercontinue');
  composer.innerText='marker\ncontinue';
  assert.equal(pageWith([composer]).draftLength,'marker continue'.length);
});
test('modern messages are not double counted when legacy attributes also exist',()=>{
  const modern = modernMessage('assistant','verified reply');
  const page = messagePage([modern],{assistant:[modern.nextElementSibling]});
  assert.equal(page.assistantCount,1);assert.equal(page.assistantText,'verified reply');
});
test('role headings without a message sibling do not qualify as replies',()=>{
  const page = messagePage([{role:'assistant',nextElementSibling:{matches:()=>false,textContent:'unrelated'}}]);
  assert.equal(page.assistantCount,0);
});
test('current composer without ID is selected among reply code editors', () => {
  const code = () => editor({contenteditable:'true', role:'textbox', 'aria-label':'Edit code', class:'cm-content'});
  const composer = editor({contenteditable:'true', role:'textbox', 'data-composer-markdown':'', class:'ProseMirror'}, 'continue task');
  const page = pageWith([code(), code(), composer]);
  assert.equal(page.composer, composer);
  assert.equal(page.composerReady, true);
  assert.equal(page.draftEmpty, false);
});
function sendPage(buttons) {
  const composer = editor({contenteditable:'true','data-composer-markdown':''});
  return selectConversation({querySelectorAll(selector) {
    return [composer,...buttons].filter(node=>selector.split(',').some(part=>node.matches(part.trim())));
  }},{href:'https://chatgpt.com/c/test-chat'});
}
function sendControl(kind='composer') {
  return {disabled:false,getClientRects:()=>[1],matches:selector=>
    kind==='composer' && selector==='button[type="submit"][aria-label="Send"].size-token-button-composer'};
}
test('current submit Send button without test ID is recognized',()=>{
  const button=sendControl();assert.equal(sendPage([button]).sendButton,button);
});
test('visible Stop control without a test ID keeps the page generating',()=>{
  const stop={getClientRects:()=>[1],matches:selector=>selector==='button[aria-label="Stop"]'};
  assert.equal(sendPage([stop]).generating,true);
  stop.hidden=true;
  assert.equal(sendPage([stop]).generating,false);
});
test('unrelated Send controls and ambiguous composer submit buttons are rejected',()=>{
  assert.equal(sendPage([sendControl('unrelated')]).sendButton,null);
  assert.equal(sendPage([sendControl(),sendControl()]).sendButton,null);
});
test('reply code editors cannot become the composer', () => {
  assert.equal(pageWith([editor({contenteditable:'true', role:'textbox', 'aria-label':'Edit code'})]).composer, null);
});
test('ambiguous modern composers prevent insertion', () => {
  const attributes = {contenteditable:'true', 'data-composer-markdown':''};
  assert.equal(pageWith([editor(attributes), editor(attributes)]).composer, null);
});
test('legacy composer and a composer matching both selectors remain supported', () => {
  for (const attributes of [{id:'prompt-textarea'}, {id:'prompt-textarea',contenteditable:'true','data-composer-markdown':''}]) {
    const composer = editor(attributes);
    assert.equal(pageWith([composer]).composer, composer);
  }
});
test('quota login refusal and generic alerts pause',()=>{
  assert.equal(classifyError('You have reached your limit'),'quota');
  assert.equal(classifyError('请重新登录'),'login');
  assert.equal(classifyError("I can't assist with that request"),'safety');
  assert.equal(classifyError('Something went wrong'),'page');
  assert.equal(normalizedText(' a \n b '),'a b');
});
