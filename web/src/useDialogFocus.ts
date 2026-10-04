import { useEffect, type RefObject } from 'react';

/** Keep keyboard navigation inside an open modal and restore the user's previous control on close. */
export function useDialogFocus(
  ref: RefObject<HTMLElement | null>,
  open: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement;
    if (!dialog.contains(previous)) {
      const initial =
        dialog.querySelector<HTMLElement>('input, select, textarea') ||
        dialog.querySelector<HTMLElement>('button');
      (initial || dialog).focus();
    }
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, [ref, open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      } else if (event.key === 'Tab') {
        const controls = [
          ...dialog.querySelectorAll<HTMLElement>(
            'button, input, textarea, select, a[href], [tabindex]',
          ),
        ].filter(
          (element) =>
            element.tabIndex >= 0 &&
            element.getClientRects().length > 0 &&
            !element.matches(':disabled'),
        );
        const first = controls[0];
        const last = controls.at(-1);
        if (!first || !last) {
          event.preventDefault();
          dialog.focus();
        } else if (
          !dialog.contains(document.activeElement) ||
          (!event.shiftKey && document.activeElement === last)
        ) {
          event.preventDefault();
          first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
      }
    };
    document.addEventListener('keydown', handle);
    return () => document.removeEventListener('keydown', handle);
  }, [ref, open, onClose]);
}
