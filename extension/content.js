(async () => {
  const {selectConversation, fillComposer, normalizedText, renderedText, COMPOSER_SELECTOR} = await import(chrome.runtime.getURL('selectors.js'));
  const {decideContinuation, continuationMessage, continuationPauseReason} = await import(chrome.runtime.getURL('continuation.js'));
  const badge = document.createElement('div');
  badge.id = 'chat-agent-bridge-status';
  Object.assign(badge.style, {position: 'fixed', right: '16px', bottom: '90px', zIndex: '2147483647', padding: '8px 12px', background: '#17232b', color: '#fff', fontSize: '12px', borderRadius: '8px', maxWidth: '300px'});
  badge.textContent = 'Bridge：暂停'; document.documentElement.append(badge);
  let conversation = null, busy = false, settleSince = Date.now(), previousAssistant = '', lastUserText = null, taskSeen = false;
  let workMode = false, submitBusy = false, invalidated = false, pollTimer = null;
  const bindingPrefix = code => `[Bridge binding ${code}]\n请先调用 task_begin，binding_code 为 ${code}，goal 根据下面的需求填写，idempotency_key 使用新任务的固定标识。每轮结束前保存 task_checkpoint，需求完成后调用 task_finish。\n\n`;
  async function message(type, extra = {}) {
    if (invalidated) throw new Error('扩展已更新，请刷新 ChatGPT 页面');
    let response;
    try { response = await chrome.runtime.sendMessage({type, conversationId: conversation, ...extra}); }
    catch (error) {
      if (/extension context invalidated/i.test(error.message || '')) {
        invalidated = true; workMode = false;
        if (pollTimer != null) clearInterval(pollTimer);
        badge.textContent = 'Bridge：扩展已更新，请刷新 ChatGPT 页面';
      }
      throw error;
    }
    if (!response?.ok) throw new Error(response?.error || '扩展服务未响应');
    return response;
  }
  async function pause(reason) {
    if (invalidated) { badge.textContent = 'Bridge：扩展已更新，请刷新 ChatGPT 页面'; return; }
    badge.textContent = `Bridge：暂停 · ${reason}`;
    if (conversation) await message('PAUSE_CHAT', {reason}).catch(() => {});
  }
  async function traceSend(stage, page, fingerprint = '') {
    const controls = [...document.querySelectorAll('button[aria-label]')]
      .map(node => node.getAttribute('aria-label'))
      .filter(label => /^(send|stop|停止|发送)/i.test(label || '')).slice(0, 8);
    await message('TRACE_SEND', {snapshot: {stage, at: Date.now(),
      users: page?.userCount ?? -1, assistants: page?.assistantCount ?? -1,
      draftLength: page?.draftLength ?? normalizedText(renderedText(page?.composer)).length,
      generating: !!page?.generating, sendEnabled: !!page?.sendButton && !page.sendButton.disabled,
      errorKind: page?.errorKind || '', echoMatches: !!fingerprint && page?.lastUserText === fingerprint,
      controls}}).catch(() => {});
  }
  chrome.runtime.onMessage.addListener((request, sender, respond) => {
    if (request.type !== 'PREFIX_BINDING') return;
    try {
      const page = selectConversation(document, location);
      if (!page || page.conversationId !== request.conversationId || !page.composer) throw new Error('对话或输入框已变化');
      const draft = renderedText(page.composer);
      if (draft.includes('[Bridge binding ')) throw new Error('输入框已有绑定说明，请先发送或删除');
      const prefix = bindingPrefix(request.bindingCode);
      fillComposer(page.composer, prefix + draft); respond({ok: true});
    } catch (error) { respond({ok: false, error: error.message}); }
  });
  document.addEventListener('beforeinput', event => {
    if (event.isTrusted && taskSeen && event.target.closest?.(COMPOSER_SELECTOR)) { taskSeen = false; void pause('检测到手动输入'); }
  }, true);
  async function prepareUserSubmit(page) {
    const chat = page.conversationId, draft = renderedText(page.composer);
    const assistantText = page.assistantText, previousUserText = page.lastUserText;
    let clicked = false, pending = false, fingerprint;
    submitBusy = true;
    try {
      const prepared = await message('AUTO_PREPARE');
      let current = selectConversation(document, location);
      if (!current || current.conversationId !== chat || conversation !== chat || renderedText(current.composer) !== draft || current.generating) throw new Error('准备任务时对话或草稿已变化，请重新发送');
      const prefix = prepared.bindingCode ? bindingPrefix(prepared.bindingCode) :
        `[Bridge task ${prepared.taskId}]\n先调用 task_get 核对当前任务，再处理下面的需求；每轮保存 task_checkpoint，全部完成后调用 task_finish。\n\n`;
      const outgoing = prefix + draft;
      fingerprint = normalizedText(outgoing);
      await message('PREPARE_USER_SEND', {fingerprint, assistantText}); pending = true;
      fillComposer(current.composer, outgoing);
      await new Promise(resolve => setTimeout(resolve, 300));
      const {allowed} = await message('VALIDATE_USER_SEND', {fingerprint});
      current = selectConversation(document, location);
      if (!allowed || !current || current.conversationId !== chat || conversation !== chat || current.generating || current.errorKind || !current.sendButton || current.sendButton.disabled || normalizedText(renderedText(current.composer)) !== fingerprint) throw new Error('发送前工作模式或页面状态已变化');
      taskSeen = false; lastUserText = null; settleSince = Date.now();
      clicked = true;
      current.sendButton.click();
      const deadline = Date.now() + 12000; let confirmed = false;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 250));
        current = selectConversation(document, location);
        if (!current || current.conversationId !== chat || conversation !== chat || current.errorKind) break;
        if (current.draftEmpty && current.lastUserText === fingerprint && previousUserText !== fingerprint) { confirmed = true; break; }
      }
      if (!confirmed) throw new Error('任务消息发送结果未确认，禁止自动重发');
      await message('ACK_USER_SEND', {fingerprint});
      lastUserText = current.lastUserText; settleSince = Date.now();
    } catch (error) {
      if (pending && !clicked) await message('CANCEL_USER_SEND', {fingerprint}).catch(() => {});
      await pause(error.message);
    }
    finally { submitBusy = false; }
  }
  function interceptSubmit(event, keyboard) {
    if (invalidated || !event.isTrusted || !workMode) return;
    const page = selectConversation(document, location);
    if (!page || page.conversationId !== conversation || !page.composer || page.draftEmpty) return;
    if (keyboard) {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing || !event.target.closest?.(COMPOSER_SELECTOR)) return;
    } else if (event.target !== page.sendButton && !page.sendButton?.contains?.(event.target)) return;
    if (submitBusy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    if (/^\[Bridge (?:binding|continuation|task) /.test(renderedText(page.composer))) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (!submitBusy) void prepareUserSubmit(page);
  }
  document.addEventListener('click', event => interceptSubmit(event, false), true);
  document.addEventListener('keydown', event => interceptSubmit(event, true), true);
  async function tick() {
    if (invalidated || busy || submitBusy) return; busy = true;
    try {
      let page = selectConversation(document, location);
      if (!page) { if (conversation) await pause('已离开对话'); conversation = null; workMode = false; return; }
      if (conversation !== page.conversationId) {
        if (conversation) await pause('对话已切换');
        workMode = false;
        conversation = page.conversationId; taskSeen = false; lastUserText = null; settleSince = Date.now(); previousAssistant = '';
        await message('HELLO');
      }
      const state = await message('GET_STATUS');
      workMode = !!state.workMode?.enabled;
      badge.textContent = workMode && state.task?.status === 'completed' ? 'Bridge：工作模式 · 已完成，直接输入新需求' : `Bridge：${state.settings.enabled ? '运行' : '暂停'} · ${state.settings.awaitingTask ? '等待新任务绑定' : state.task?.status || state.settings.reason}`;
      if (!state.settings.enabled) return;
      if (state.settings.awaitingTask) {
        taskSeen = false; lastUserText = null;
        return;
      }
      if (!state.task) return;
      if (!taskSeen) { taskSeen = true; lastUserText = page.lastUserText; settleSince = Date.now(); }
      if (page.lastUserText !== lastUserText) { await pause('检测到新的手动消息或页面变化'); return; }
      if (page.generating || page.assistantText !== previousAssistant) { settleSince = Date.now(); previousAssistant = page.assistantText; }
      page.settled = Date.now() - settleSince >= 3500 && !!page.assistantText;
      const decision = decideContinuation(page, state.task, state.settings, null);
      if (decision === 'pause') { await pause(continuationPauseReason(page, state.task, state.settings)); return; }
      if (!state.continuation_ready) return;
      if (state.settings.lastAssistantText != null && page.assistantText === state.settings.lastAssistantText) return;
      if (state.settings.lastAssistantText == null && state.settings.lastAssistantCount != null && page.assistantCount <= state.settings.lastAssistantCount) return;
      if (!page.settled || page.generating || !['active', 'waiting_for_model'].includes(state.task.status)) return;
      const {lease} = await message('CLAIM', {taskId: state.task.id});
      if (!lease) { await pause('续接资格已被占用或过期，请检查其他标签页'); return; }
      // Re-observe after the network round trip, before touching the composer.
      page = selectConversation(document, location);
      if (!page || page.conversationId !== conversation || page.generating || !page.draftEmpty || page.errorKind) { await pause('发送前页面状态已变化'); return; }
      await traceSend('before_fill', page);
      const text = continuationMessage(state.task, lease); const fingerprint = normalizedText(text);
      // A unique lease marker must not already exist in the latest user message.
      const previousUserText = page.lastUserText;
      if (previousUserText === fingerprint) { await pause('续接消息已存在，禁止重复发送'); return; }
      await message('PREPARE_SEND', {leaseId: lease.id, fingerprint, assistantCount: page.assistantCount, assistantText: page.assistantText});
      fillComposer(page.composer, text);
      await new Promise(resolve => setTimeout(resolve, 300));
      page = selectConversation(document, location);
      await traceSend('before_click', page, fingerprint);
      const {allowed} = await message('FINAL_SEND', {leaseId: lease.id, fingerprint, taskId: state.task.id});
      page = selectConversation(document, location);
      if (!allowed || !page || page.conversationId !== conversation || page.generating || page.errorKind || !page.composerReady || !page.sendButton || page.sendButton.disabled || normalizedText(renderedText(page.composer)) !== fingerprint) { await pause('发送前授权或页面状态已变化'); return; }
      page.sendButton.click();
      const deadline = Date.now() + 12000; let confirmed = false;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 250));
        page = selectConversation(document, location);
        if (!page || page.conversationId !== conversation || page.errorKind) break;
        // Virtualized history can replace old messages while keeping its count fixed.
        if (page.draftEmpty && page.lastUserText === fingerprint && previousUserText !== fingerprint) { confirmed = true; break; }
      }
      if (!confirmed) { await traceSend('echo_missing', page, fingerprint); await pause('发送结果未确认，禁止自动重发'); return; }
      await traceSend('echo_confirmed', page, fingerprint);
      await message('ACK', {leaseId: lease.id, fingerprint});
      await traceSend('acknowledged', page, fingerprint);
      lastUserText = page.lastUserText; settleSince = Date.now();
    } catch (error) { await pause(error.message); }
    finally { busy = false; }
  }
  pollTimer = setInterval(() => void tick(), 1500);
  void tick();
})();
