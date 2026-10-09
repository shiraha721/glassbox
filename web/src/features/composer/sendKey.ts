import { useSyncExternalStore } from 'react';

/** Which key runs the task: Enter (Shift+Enter adds a line) or Ctrl/Cmd+Enter (Enter adds one). */
export type SendKey = 'enter' | 'mod-enter';

const storageKey = 'glassbox.sendKey';
const listeners = new Set<() => void>();

function stored(): SendKey {
  try {
    return localStorage.getItem(storageKey) === 'mod-enter' ? 'mod-enter' : 'enter';
  } catch {
    return 'enter';
  }
}

let current: SendKey = stored();

/** Remember the choice in this browser and update every composer showing it. */
export function setSendKey(value: SendKey) {
  current = value;
  try {
    localStorage.setItem(storageKey, value);
  } catch {
    // Without storage the choice lasts until the page reloads.
  }
  listeners.forEach((listener) => listener());
}

/** The per-browser send key preference, shared by Settings and the task box. */
export function useSendKey(): SendKey {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}

type KeyPress = {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  isComposing?: boolean;
};

/** Whether a key press in the task box runs the task instead of inserting a line break.
 *
 * Ctrl/Cmd+Enter always runs. Enter alone runs only in Enter mode. A key press that confirms
 * an input method composition (Chinese, Japanese, Korean input) never runs anything. */
export function sendsTask(event: KeyPress, mode: SendKey): boolean {
  if (event.key !== 'Enter' || event.isComposing) return false;
  if (event.ctrlKey || event.metaKey) return true;
  return mode === 'enter' && !event.shiftKey && !event.altKey;
}

/** The keyboard hint for the current mode, for button titles and the composer footer. */
export function sendKeyLabel(mode: SendKey): string {
  return mode === 'enter' ? 'Enter' : 'Ctrl+Enter';
}
