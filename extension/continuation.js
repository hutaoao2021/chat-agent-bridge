export function continuationPauseReason(page, task, settings) {
  if (!settings.enabled) return '续接未启用';
  if (page.errorKind) return `页面提示：${page.errorKind}`;
  if (!page.composerReady) return `输入框不可用（识别到 ${page.composerCount ?? '?'} 个候选）`;
  if (!page.draftEmpty) return `输入框存在草稿（${page.draftLength ?? '?'} 字）`;
  if (!task) return null;
  if (page.conversationId !== task.conversation_id) return '当前对话与任务不匹配';
  if (['sending', 'leased', 'user_sending'].includes(settings.phase)) return `上一轮发送状态待确认：${settings.phase}`;
  if (task.turn_count >= settings.maxTurns) return `已达到续接上限 ${settings.maxTurns}`;
  if (['completed', 'blocked', 'cancelled'].includes(task.status)) return `任务已结束：${task.status}`;
  return null;
}
export function decideContinuation(page, task, settings, lease) {
  if (continuationPauseReason(page, task, settings)) return 'pause';
  if (!task) return 'wait';
  if (page.generating || !page.settled || !lease) return 'wait';
  return ['active', 'waiting_for_model'].includes(task.status) ? 'send' : 'wait';
}

export function continuationMessage(task, lease) {
  return `[Bridge continuation ${lease.id}]\n继续已授权任务 ${task.id}。先调用 task_get 读取最新状态和检查点，继续未完成步骤并验证结果。不要重复已执行的操作。遇到待审批、额度限制或无法确认的结果就暂停。结束本轮前调用 task_checkpoint 保存已验证进度；只有需求全部完成才调用 task_finish。`;
}
