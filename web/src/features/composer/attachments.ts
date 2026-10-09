import { upload, type Attachment } from '../../api/client';

export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
export const MAX_IMAGES = 10;

/** The image files among pasted, dropped or picked files. */
export function imageFiles(files: FileList | File[] | null | undefined): File[] {
  return Array.from(files ?? []).filter((file) => IMAGE_TYPES.includes(file.type));
}

/** Upload images one at a time; the server verifies each and stores it by its hash. */
export async function uploadImages(files: File[]): Promise<Attachment[]> {
  const stored: Attachment[] = [];
  for (const file of files) stored.push(await upload<Attachment>('/attachments', file));
  return stored;
}

/** Add new images to the list, skipping repeats and keeping the per-task limit. */
export function addImages(current: Attachment[], added: Attachment[]): Attachment[] {
  const seen = new Set(current.map((item) => item.hash));
  const next = [...current];
  for (const item of added) {
    if (!seen.has(item.hash) && next.length < MAX_IMAGES) {
      seen.add(item.hash);
      next.push(item);
    }
  }
  return next;
}

/** Where Glassbox serves a stored image. */
export function imageUrl(item: Attachment): string {
  return `/api/v1/attachments/${item.hash}`;
}
