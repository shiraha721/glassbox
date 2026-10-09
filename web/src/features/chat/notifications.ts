import { useEffect, useRef, useState } from 'react';
import type { Run } from '../../api/client';

// States worth interrupting someone for: the Run needs a decision, or it stopped.
const notable: Record<string, string> = {
  waiting_approval: 'needs your approval',
  succeeded: 'finished',
  failed: 'failed',
  interrupted: 'was interrupted',
};

const storageKey = 'glassbox.notify';

/** Decide whether a status change deserves a notification. The first sighting of a Run (for
 * example after a page load) never notifies; only a change into a notable state does. */
export function runNotice(
  previous: string | undefined,
  run: Pick<Run, 'status' | 'input'>,
  conversation: string,
): { title: string; body: string } | null {
  if (previous === undefined || previous === run.status) return null;
  const phrase = notable[run.status];
  if (!phrase) return null;
  const task = run.input.length > 120 ? `${run.input.slice(0, 117)}…` : run.input;
  return { title: `Run ${phrase}`, body: `${conversation}: ${task}` };
}

/** The per-browser notification preference, which also asks for the browser's permission. */
export function useNotificationSetting() {
  const supported = typeof Notification !== 'undefined';
  const [enabled, setEnabled] = useState(() => {
    try {
      return (
        supported &&
        Notification.permission === 'granted' &&
        localStorage.getItem(storageKey) === 'on'
      );
    } catch {
      return false;
    }
  });
  const remember = (on: boolean) => {
    setEnabled(on);
    try {
      localStorage.setItem(storageKey, on ? 'on' : 'off');
    } catch {
      // Without storage the choice lasts until the page reloads.
    }
  };
  const toggle = async () => {
    if (!supported) return;
    if (enabled) return remember(false);
    const permission =
      Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
    remember(permission === 'granted');
  };
  return {
    supported,
    enabled,
    blocked: supported && Notification.permission === 'denied',
    toggle,
  };
}

/** Notify while the page is in the background when the followed Run needs approval or ends. */
export function useRunNotifications(run: Run | undefined, conversation: string, enabled: boolean) {
  const seen = useRef(new Map<string, string>());
  useEffect(() => {
    if (!run) return;
    const previous = seen.current.get(run.id);
    seen.current.set(run.id, run.status);
    if (!enabled || !document.hidden) return;
    const notice = runNotice(previous, run, conversation);
    if (!notice) return;
    const shown = new Notification(notice.title, { body: notice.body, tag: run.id });
    shown.onclick = () => {
      window.focus();
      shown.close();
    };
  }, [run, conversation, enabled]);
}
