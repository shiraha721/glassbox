import { describe, expect, it } from 'vitest';
import type { Config } from '../../api/client';
import {
  addModels,
  formatLabels,
  formatOf,
  newProvider,
  offered,
  removeModel,
  serviceProvider,
  services,
  setDefaultFormat,
  setFormatUrl,
  setModelFormat,
  withDefaultConnection,
} from './providers';

const service = (id: string) => services.find((item) => item.id === id)!;

describe('providers', () => {
  it('creates a custom endpoint with a unique ID and key name', () => {
    const provider = newProvider('responses', ['responses']);
    expect(provider).toMatchObject({
      id: 'responses-2',
      adapter: 'responses',
      base_url: 'https://api.openai.com/v1',
      credential_ref: 'RESPONSES_2_API_KEY',
      endpoints: {},
    });
    expect(formatLabels[provider.adapter]).toBe('Responses API');
  });

  it('adds a known service with every format it documents', () => {
    const deepseek = serviceProvider(service('deepseek'), []);
    expect(deepseek).toMatchObject({ id: 'deepseek', service: 'deepseek' });
    expect(offered(deepseek)).toEqual([
      ['openai', 'https://api.deepseek.com'],
      ['anthropic', 'https://api.deepseek.com/anthropic/v1'],
    ]);
    expect(serviceProvider(service('deepseek'), ['deepseek']).id).toBe('deepseek-2');
  });

  it('moves the default between offered formats and drops formats without a URL', () => {
    let profile = serviceProvider(service('deepseek'), []);
    profile = setModelFormat(profile, 'deepseek-flash', 'anthropic');
    profile = setDefaultFormat(profile, 'anthropic');
    expect(profile).toMatchObject({
      adapter: 'anthropic',
      base_url: 'https://api.deepseek.com/anthropic/v1',
      endpoints: { openai: 'https://api.deepseek.com' },
    });
    // A format nobody offers cannot become the default.
    expect(setDefaultFormat(profile, 'responses')).toBe(profile);
    profile = setFormatUrl(setModelFormat(profile, 'other', 'openai'), 'openai', '');
    expect(profile.endpoints).toEqual({});
    expect(formatOf(profile, 'other')).toBe('anthropic');
  });

  it('gives new models of a gateway the format it documents', () => {
    const go = addModels(serviceProvider(service('opencode-go'), []), [
      'glm-5.3',
      'claude-haiku-5-5',
      'qwen3.8-max',
      'gpt-6-luna',
    ]);
    expect(go.model_formats).toEqual({
      'claude-haiku-5-5': 'anthropic',
      'qwen3.8-max': 'anthropic',
      'gpt-6-luna': 'responses',
    });
    expect(formatOf(go, 'glm-5.3')).toBe('openai');
    // A choice made in Settings survives later refreshes.
    const chosen = addModels(setModelFormat(go, 'gpt-6-luna', 'openai'), ['gpt-6-luna']);
    expect(formatOf(chosen, 'gpt-6-luna')).toBe('openai');
    expect(removeModel(chosen, 'claude-haiku-5-5').model_formats).not.toHaveProperty(
      'claude-haiku-5-5',
    );
  });

  it('keeps the default model in step with its provider', () => {
    const openrouter = addModels(serviceProvider(service('openrouter'), []), [
      'anthropic/claude-sonnet-5',
    ]);
    const plan = serviceProvider(service('chatgpt'), []);
    const config = {
      providers: [openrouter, plan],
      provider: {
        profile_id: 'openrouter',
        adapter: 'openai',
        auth: 'chatgpt',
        base_url: 'https://api.openai.com/v1',
        credential_ref: '',
        model: 'anthropic/claude-sonnet-5',
      },
    } as unknown as Config;
    expect(withDefaultConnection(config).provider).toMatchObject({
      adapter: 'anthropic',
      auth: 'key',
      base_url: 'https://openrouter.ai/api/v1',
      credential_ref: 'OPENROUTER_API_KEY',
    });
    const onPlan = { ...config, provider: { ...config.provider, profile_id: 'chatgpt' } };
    expect(withDefaultConnection(onPlan).provider).toMatchObject({
      adapter: 'responses',
      auth: 'chatgpt',
      base_url: 'https://api.openai.com/v1',
    });
    // A removed provider leaves the simulated model, which has no sign-in.
    const removed = { ...onPlan, providers: [openrouter] };
    expect(withDefaultConnection(removed).provider.auth).toBe('key');
  });
});
