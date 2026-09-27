const ORIGIN = `chrome-extension://${chrome.runtime.id}`;
const keyFor = (tabId, conversation) => `chat:${tabId}:${conversation}`;
let queue = Promise.resolve();
function serial(fn) { const result = queue.then(fn, fn); queue = result.catch(() => {}); return result; }
async function get(key) { return (await chrome.storage.local.get(key))[key]; }
async function put(key, value) { await chrome.storage.local.set({[key]: value}); }
async function disableWorkConversation(conversation, maxTurns, reason) {
  await put(`work:${conversation}`, {enabled: false, maxTurns});
  const all = await chrome.storage.local.get(null);
  for (const [key, value] of Object.entries(all)) {
    if (key.startsWith('chat:') && key.endsWith(`:${conversation}`)) await put(key, {...value, enabled: false, reason});
  }
}
async function api(action, data, method = 'POST', pairingOverride = null) {
  const pairing = pairingOverride ?? await get('pairing');
  const base = pairing?.base || 'http://127.0.0.1:8900';
  const url = new URL(`/local/v1/${action}`, base);
  if (method === 'GET') for (const [key, value] of Object.entries(data)) url.searchParams.set(key, value);
  const response = await fetch(url, {method, cache: 'no-store', signal: AbortSignal.timeout(15000),
    headers: {'Content-Type': 'application/json', 'X-Bridge-Origin': ORIGIN, ...(pairing?.token ? {Authorization: `Bearer ${pairing.token}`} : {})},
    ...(method === 'POST' ? {body: JSON.stringify(data)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Local service: ${response.status}`);
  return result;
}
function validPage(tab, conversation) {
  if (!tab?.id || !/^https:\/\/chatgpt\.com\/(?:.*\/)?c\//.test(tab.url || '') || !conversation) throw new Error('请先打开已有 ChatGPT 对话');
}
async function handle(message, sender) {
  const {type} = message;
  if (type === 'PAIR') {
    if (sender.tab) throw new Error('请在扩展弹窗配对');
    const base = message.base || 'http://127.0.0.1:8900';
    if (!/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(base)) throw new Error('只支持 127.0.0.1 本地服务');
    const result = await api('pair', {code: message.code}, 'POST', {base});
    await put('pairing', {base, token: result.token, expiresAt: result.expires_at});
    return {paired: true};
  }
  if (type === 'REVOKE') {
    if (sender.tab) throw new Error('请在弹窗解除配对');
    await api('revoke', {}); await chrome.storage.local.remove('pairing');
    const all = await chrome.storage.local.get(null);
    for (const [key, value] of Object.entries(all)) if (key.startsWith('chat:')) await put(key, {...value, enabled: false, reason: '配对已撤销'});
    for (const [key, value] of Object.entries(all)) if (key.startsWith('work:')) await put(key, {...value, enabled: false});
    return {paired: false};
  }
  const tab = sender.tab || await chrome.tabs.get(message.tabId);
  const conversation = message.conversationId;
  validPage(tab, conversation);
  const key = keyFor(tab.id, conversation);
  let settings = await get(key) || {enabled: false, maxTurns: 10, phase: 'idle', reason: '尚未启用'};
  const workKey = `work:${conversation}`;
  let workMode = await get(workKey);
  if (!workMode && (settings.taskId || settings.awaitingTask) && !['用户暂停','任务已停止','配对已撤销'].includes(settings.reason)) {
    workMode = {enabled: true, maxTurns: settings.maxTurns}; await put(workKey, workMode);
  }
  if (type === 'AUTO_PREPARE') {
    if (!sender.tab || !workMode?.enabled) throw new Error('此对话未启用工作模式');
    if (settings.phase !== 'idle') throw new Error('上一轮发送状态不确定，请先人工确认恢复');
    const status = await api('status', {conversation_id: conversation}, 'GET');
    let bindingCode;
    if (!status.task || ['completed','blocked','cancelled'].includes(status.task.status)) {
      bindingCode = (await api('bind', {conversation_id: conversation, tab_id: String(tab.id)})).binding_code;
      settings = {enabled: true, phase: 'idle', maxTurns: workMode.maxTurns, awaitingTask: true,
        previousTaskId: status.task?.id ?? null, taskId: null, reason: '新需求已自动绑定，等待 task_begin'};
    } else {
      settings.enabled = true; settings.awaitingTask = false; settings.taskId = status.task.id;
      settings.maxTurns = workMode.maxTurns; settings.reason = '继续当前任务';
    }
    await put(key, settings);
    return {settings, bindingCode, taskId: bindingCode ? null : status.task.id};
  }
  if (type === 'TRACE_SEND') {
    if (!sender.tab) throw new Error('发送诊断仅允许页面记录');
    const input = message.snapshot || {}, snapshot = {};
    for (const field of ['stage','at','users','assistants','draftLength','generating','sendEnabled','errorKind','echoMatches']) {
      if (['string','number','boolean'].includes(typeof input[field])) snapshot[field] = typeof input[field] === 'string' ? input[field].slice(0,80) : input[field];
    }
    snapshot.controls = (Array.isArray(input.controls) ? input.controls : []).filter(x=>typeof x==='string' && /^(send|stop|停止|发送)/i.test(x)).slice(0,8).map(x=>x.slice(0,60));
    settings.sendTrace = [...(settings.sendTrace || []), snapshot].slice(-8);
    await put(key, settings); return {ok: true};
  }
  if (type === 'PREPARE_USER_SEND') {
    if (!sender.tab || !workMode?.enabled || !settings.enabled || settings.phase !== 'idle' || typeof message.fingerprint !== 'string' || !message.fingerprint || typeof message.assistantText !== 'string') throw new Error('任务消息发送资格已失效');
    settings.phase = 'user_sending'; settings.pendingMessage = message.fingerprint;
    settings.lastAssistantText = message.assistantText;
    await put(key, settings); return {ok: true};
  }
  if (type === 'VALIDATE_USER_SEND') {
    return {allowed: !!sender.tab && !!workMode?.enabled && settings.enabled && settings.phase === 'user_sending' && settings.pendingMessage === message.fingerprint};
  }
  if (type === 'ACK_USER_SEND' || type === 'CANCEL_USER_SEND') {
    if (!sender.tab || settings.phase !== 'user_sending' || settings.pendingMessage !== message.fingerprint) throw new Error('任务消息发送确认不匹配');
    settings.phase = 'idle'; delete settings.pendingMessage;
    await put(key, settings); return {ok: true};
  }
  if (type === 'ENABLE_CHAT') {
    if (sender.tab) throw new Error('请在弹窗启用');
    const maxTurns = Number(message.maxTurns);
    if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 100) throw new Error('续接上限需为 1–100');
    const status = await api('status', {conversation_id: conversation}, 'GET');
    let bindingCode;
    if (!status.task || ['completed', 'blocked', 'cancelled'].includes(status.task.status)) {
      bindingCode = (await api('bind', {conversation_id: conversation, tab_id: String(tab.id)})).binding_code;
      const result = await chrome.tabs.sendMessage(tab.id, {type: 'PREFIX_BINDING', conversationId: conversation, bindingCode});
      if (!result?.ok) throw new Error(result?.error || '无法写入绑定说明');
    } else if (settings.phase !== 'idle') throw new Error('上一轮发送状态不确定；请手动发消息继续，避免重复发送');
    settings = {enabled: true, maxTurns, phase: 'idle', reason: bindingCode ? '请手动发送第一条任务消息，等待 task_begin' : '已启用',
      awaitingTask: !!bindingCode, previousTaskId: bindingCode ? status.task?.id ?? null : null,
      taskId: bindingCode ? null : status.task?.id};
    await put(key, settings);
    await put(workKey, {enabled: true, maxTurns});
    return {settings};
  }
  if (type === 'PAUSE_CHAT') {
    settings.enabled = false; settings.reason = message.reason || '用户暂停'; await put(key, settings);
    if (!sender.tab) await disableWorkConversation(conversation, settings.maxTurns, settings.reason);
    return {settings};
  }
  if (type === 'HELLO' && settings.phase !== 'idle') {
    settings.enabled = false; settings.reason = '页面刷新时发送状态不确定，已暂停'; await put(key, settings);
  }
  if (['GET_STATUS', 'HELLO'].includes(type)) {
    const pairing = await get('pairing');
    if (!pairing?.token) return {settings, workMode: workMode || {enabled: false}, paired: false, task: null, approvals: []};
    const status = await api('status', {conversation_id: conversation}, 'GET');
    if (settings.awaitingTask && status.task && status.task.id !== settings.previousTaskId) {
      settings.awaitingTask = false; settings.taskId = status.task.id; settings.reason = '任务已绑定';
      delete settings.previousTaskId; await put(key, settings);
    }
    return {...status, settings, workMode: workMode || {enabled: false}, paired: true, pairingExpiresAt: pairing.expiresAt};
  }
  if (type === 'APPROVE' || type === 'STOP' || type === 'RECONCILE') {
    if (sender.tab) throw new Error('人工控制只允许在弹窗操作');
    if (type === 'APPROVE') return api('approve', {approval_id: message.approvalId, approved: message.approved});
    if (type === 'RECONCILE') {
      if (message.humanConfirmed !== true) throw new Error('请先检查最后一条消息并手动发送恢复说明');
      if (settings.phase !== 'user_sending') await api('reconcile', {task_id: message.taskId, conversation_id: conversation, human_confirmed: true});
      settings = {enabled: false, phase: 'idle', maxTurns: settings.maxTurns, reason: '已人工确认，请再次启用'};
      await put(key, settings); return {settings};
    }
    settings.enabled = false; settings.reason = '任务已停止'; await put(key, settings);
    await disableWorkConversation(conversation, settings.maxTurns, settings.reason);
    return api('stop', {task_id: message.taskId});
  }
  if (type === 'CLAIM') {
    if (!settings.enabled || settings.phase !== 'idle') return {lease: null};
    const status = await api('status', {conversation_id: conversation}, 'GET');
    const task = status.task;
    if (!task || task.id !== message.taskId || task.turn_count >= settings.maxTurns) return {lease: null};
    const result = await api('claim', {task_id: task.id, conversation_id: conversation, tab_id: String(tab.id), expected_turn: task.turn_count});
    if (result.lease) { settings.phase = 'leased'; settings.leaseId = result.lease.id; await put(key, settings); }
    return result;
  }
  if (type === 'PREPARE_SEND') {
    if (settings.phase !== 'leased' || settings.leaseId !== message.leaseId || !settings.enabled) throw new Error('发送资格已失效');
    settings.phase = 'sending'; settings.pendingMessage = message.fingerprint; settings.lastAssistantCount = message.assistantCount;
    if (typeof message.assistantText === 'string') settings.lastAssistantText = message.assistantText;
    await put(key, settings);
    return {ok: true};
  }
  if (type === 'FINAL_SEND') {
    if (!settings.enabled || settings.phase !== 'sending' || settings.leaseId !== message.leaseId || settings.pendingMessage !== message.fingerprint) return {allowed: false};
    const result = await api('validate_send', {lease_id: message.leaseId, task_id: message.taskId, conversation_id: conversation, tab_id: String(tab.id)});
    return {allowed: result.allowed};
  }
  if (type === 'ACK') {
    if (settings.phase !== 'sending' || settings.leaseId !== message.leaseId || settings.pendingMessage !== message.fingerprint) throw new Error('发送确认不匹配');
    await api('ack', {lease_id: message.leaseId, message_fingerprint: message.fingerprint});
    settings.phase = 'idle'; delete settings.pendingMessage; delete settings.leaseId; settings.reason = '已确认发送，等待回复'; await put(key, settings);
    return {ok: true};
  }
  throw new Error('未知扩展消息');
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  serial(() => handle(message, sender)).then(result => respond({ok: true, ...result}), error => respond({ok: false, error: error.message}));
  return true;
});
chrome.tabs.onRemoved.addListener(tabId => {
  serial(async () => {
    const all = await chrome.storage.local.get(null);
    for (const key of Object.keys(all)) if (key.startsWith(`chat:${tabId}:`)) await chrome.storage.local.remove(key);
  });
});
