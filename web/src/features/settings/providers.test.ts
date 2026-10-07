import { describe, expect, it } from 'vitest';
import { adapterLabels, newProvider } from './providers';

describe('newProvider', () => {
  it('creates a Responses API provider with a unique ID and key name', () => {
    const provider = newProvider('responses', ['responses']);
    expect(provider).toMatchObject({
      id: 'responses-2',
      adapter: 'responses',
      base_url: 'https://api.openai.com/v1',
      credential_ref: 'RESPONSES_2_API_KEY',
    });
    expect(adapterLabels[provider.adapter]).toBe('Responses API');
  });
});
