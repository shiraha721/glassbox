import { X } from 'lucide-react';
import type { Attachment } from '../../api/client';
import { imageUrl } from './attachments';

/** Thumbnails of the images attached to the next task, each removable. */
export function AttachmentStrip({
  items,
  onRemove,
}: {
  items: Attachment[];
  onRemove: (item: Attachment) => void;
}) {
  if (!items.length) return null;
  return (
    <ul className="attachment-strip" aria-label="Attached images">
      {items.map((item, index) => (
        <li key={item.hash}>
          <img src={imageUrl(item)} alt={`Attached image ${index + 1}`} />
          <button
            className="icon-button"
            aria-label={`Remove image ${index + 1}`}
            onClick={() => onRemove(item)}
          >
            <X size={12} />
          </button>
        </li>
      ))}
    </ul>
  );
}
