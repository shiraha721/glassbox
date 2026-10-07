import type { ProviderProfile } from '../../api/client';

export const templates = {
  openai: {
    name: 'OpenAI-compatible',
    base_url: 'https://api.openai.com/v1',
    max_output_tokens: 8192,
  },
  anthropic: {
    name: 'Anthropic',
    base_url: 'https://api.anthropic.com/v1',
    max_output_tokens: 32000,
  },
} as const;

/** Create a provider with a unique ID and a key name derived from it. */
export function newProvider(adapter: 'openai' | 'anthropic', existing: string[]): ProviderProfile {
  let id: string = adapter;
  for (let index = 2; existing.includes(id); index += 1) id = `${adapter}-${index}`;
  return {
    id,
    adapter,
    ...templates[adapter],
    credential_ref: `${id.toUpperCase().replaceAll('-', '_')}_API_KEY`,
    models: [],
    temperature: null,
    context_window: null,
  };
}
