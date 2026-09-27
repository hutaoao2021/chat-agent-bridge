export function conversationId(href) {
  try {
    const url = new URL(href);
    if (url.origin !== 'https://chatgpt.com') return null;
    return url.pathname.match(/(?:^|\/)c\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1] || null;
  } catch { return null; }
}
export const normalizedText = text => (text || '').replace(/\s+/g, ' ').trim();
// innerText retains visible paragraph separators; textContent concatenates them.
export const renderedText = node => node?.value ?? node?.innerText ?? node?.textContent ?? '';
export function classifyError(text) {
  if (/reached.{0,30}limit|usage limit|too many requests|额度|达到.{0,8}上限/i.test(text)) return 'quota';
  if (/sign in|log in|重新登录|请登录/i.test(text)) return 'login';
  if (/can't (?:assist|help) with|cannot (?:assist|help) with|不能协助|无法协助|不能帮助.{0,10}请求/i.test(text)) return 'safety';
  return text?.trim() ? 'page' : null;
}
function visible(node) { return !!node && !node.hidden && node.getClientRects().length > 0; }
export const COMPOSER_SELECTOR = '#prompt-textarea, [data-composer-markdown][contenteditable="true"]';
function messagesForRole(document, role) {
  // Current user messages have a bubble marker, but no role heading.
  if (role === 'user') {
    const bubbles = [...document.querySelectorAll('[data-user-message-bubble], [data-usermessage-bubble]')];
    if (bubbles.length) return [...new Set(bubbles)];
  }
  // Current ChatGPT places a hidden role heading immediately before the body.
  const modern = [...document.querySelectorAll(`[data-conversation-role="${role}"]`)]
    .map(label => label.nextElementSibling)
    .filter(body => body?.matches('[data-chatgpt-selection-message-id]'));
  if (modern.length) return [...new Set(modern)];
  return [...document.querySelectorAll(`[data-message-author-role="${role}"]`)];
}
export function selectConversation(document, location) {
  const id = conversationId(location.href);
  if (!id) return null;
  const composers = [...document.querySelectorAll(COMPOSER_SELECTOR)].filter(visible);
  const composer = composers.length === 1 ? composers[0] : null;
  const sendRoot = composer?.closest?.('form') || document;
  const sendButtons = [...sendRoot.querySelectorAll('[data-testid="send-button"], [data-testid="composer-submit-button"], button[type="submit"][aria-label="Send"].size-token-button-composer')].filter(visible);
  const sendButton = sendButtons.length === 1 ? sendButtons[0] : null;
  const generating = !![...document.querySelectorAll('[data-testid="stop-button"], [data-testid="stop-generating-button"], button[aria-label="Stop"]')].find(visible);
  const alerts = [...document.querySelectorAll('[role="alert"]')].filter(visible).map(n => n.textContent).join('\n');
  const assistants = messagesForRole(document, 'assistant');
  const assistant = assistants.at(-1);
  const safety = assistant && /can't (?:assist|help) with|cannot (?:assist|help) with|无法协助|不能协助/i.test(assistant.textContent) ? 'safety' : null;
  const users = messagesForRole(document, 'user');
  return {conversationId: id, composer, composerCount: composers.length,
    draftLength: normalizedText(renderedText(composer)).length,
    sendButton, generating, errorKind: classifyError(alerts) || safety,
    composerReady: !!composer && !composer.disabled && composer.getAttribute('aria-disabled') !== 'true',
    draftEmpty: !normalizedText(renderedText(composer)),
    lastUserText: normalizedText(renderedText(users.at(-1))), userCount: users.length,
    assistantText: normalizedText(renderedText(assistant)), assistantCount: assistants.length};
}
export function fillComposer(composer, text) {
  composer.focus();
  if ('value' in composer) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
    if (!setter) throw new Error('unsupported composer');
    setter.call(composer, text);
    composer.dispatchEvent(new Event('input', {bubbles: true}));
  } else {
    const selection = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(composer); selection.removeAllRanges(); selection.addRange(range);
    if (!document.execCommand('insertText', false, text)) throw new Error('composer insertion failed');
    composer.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));
  }
}
