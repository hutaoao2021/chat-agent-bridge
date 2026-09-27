import {conversationId} from './selectors.js';
let tab, conversation, task;
const el = id => document.getElementById(id);
async function send(type, extra = {}) {
  const result = await chrome.runtime.sendMessage({type, tabId: tab?.id, conversationId: conversation, ...extra});
  if (!result?.ok) throw new Error(result?.error || '无法连接');
  return result;
}
async function refresh() {
  [tab] = await chrome.tabs.query({active: true, currentWindow: true}); conversation = conversationId(tab?.url || '');
  const paired = (await chrome.storage.local.get('pairing')).pairing;
  if (paired) el('base').value = paired.base;
  if (!conversation) { el('status').textContent = '请打开已有 ChatGPT 对话。首次使用先正常发一条消息，使 URL 包含 /c/对话ID。'; return; }
  const state = await send('GET_STATUS'); task = state.settings.awaitingTask ? null : state.task;
  el('pairing-detail').textContent = state.paired ? '已连接；配对码留空正常。' + (state.pairingExpiresAt ? ' 有效至 ' + new Date(state.pairingExpiresAt * 1000).toLocaleDateString() : ' 旧配对期限未知，可继续使用。') : '仅首次安装、撤销配对或凭据过期时需要配对。';
  el('work-mode').textContent = state.workMode?.enabled ? '此对话工作模式已开启：以后直接发送需求，自动准备绑定。' : '此对话是普通聊天；启用一次后会记住工作模式。';
  el('diagnostics').textContent = state.settings.sendTrace?.length ? JSON.stringify(state.settings.sendTrace, null, 2) : '暂无记录';
  const uncertain = (state.job_cancellations || []).filter(job => ['unknown','cancel_unknown'].includes(job.status));
  el('status').textContent = `配对：${state.paired ? '已连接' : '未连接'}\n续接：${state.settings.enabled ? '启用' : '暂停'}\n状态：${task?.status || '尚未创建任务'}\n任务：${task?.id || '—'}\n续接次数（含人工跳过）：${task?.turn_count || 0}\n${state.settings.reason || ''}${uncertain.length ? '\n取消结果未确认：'+uncertain.map(job=>job.id).join(', ') : ''}`;
  el('cap').value = state.settings.maxTurns;
  el('approvals').replaceChildren();
  for (const approval of state.settings.awaitingTask ? [] : state.approvals || []) {
    const box = document.createElement('div'); box.className = 'approval';
    const text = document.createElement('div'); text.textContent = `${approval.decision}\n${approval.summary}`; box.append(text);
    if (approval.decision === 'pending') for (const [label, approved] of [['批准', true], ['拒绝', false]]) {
      const button = document.createElement('button'); button.textContent = label;
      button.onclick = () => run(async () => { await send('APPROVE', {approvalId: approval.id, approved}); await refresh(); }); box.append(button);
    }
    el('approvals').append(box);
  }
}
async function run(fn) { el('error').textContent = ''; try { await fn(); } catch (error) { el('error').textContent = error.message; } }
el('pair').onclick = () => run(async () => {
  el('pair').disabled = true;
  try {
    await send('PAIR', {base: el('base').value.trim(), code: el('code').value.trim()});
    el('code').value = ''; await refresh();
  } finally { el('pair').disabled = false; }
});
el('revoke').onclick = () => run(async () => { await send('REVOKE'); await refresh(); });
el('enable').onclick = () => run(async () => { await send('ENABLE_CHAT', {maxTurns: Number(el('cap').value)}); await refresh(); });
el('pause').onclick = () => run(async () => { await send('PAUSE_CHAT'); await refresh(); });
el('stop').onclick = () => run(async () => { if (!task) throw new Error('没有活动任务'); await send('STOP', {taskId: task.id}); await refresh(); });
el('reconcile').onclick = () => run(async () => { if (!task || !el('confirmed').checked) throw new Error('请先人工检查并勾选确认'); await send('RECONCILE', {taskId: task.id, humanConfirmed: true}); el('confirmed').checked = false; await refresh(); });
void run(refresh);
