import { describe, expect, it } from 'vitest';
import { addImages, imageFiles, MAX_IMAGES } from './attachments';

const image = (hash: string) => ({ hash, media_type: 'image/png' as const, size: 1 });

describe('attachments', () => {
  it('keeps only supported image files', () => {
    const files = [
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['x'], 'b.svg', { type: 'image/svg+xml' }),
      new File(['x'], 'c.txt', { type: 'text/plain' }),
    ];
    expect(imageFiles(files).map((file) => file.name)).toEqual(['a.png']);
  });

  it('skips repeats and stops at the limit', () => {
    const many = Array.from({ length: MAX_IMAGES + 2 }, (_, index) => image(`h${index}`));
    expect(addImages([image('h0')], many)).toHaveLength(MAX_IMAGES);
    expect(addImages([image('a')], [image('a'), image('b')]).map((item) => item.hash)).toEqual([
      'a',
      'b',
    ]);
  });
});
