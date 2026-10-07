import { describe, expect, it } from 'vitest';
import { effectiveConfig, modelLabel } from './choices';
import type { Config, Tool } from '../../api/client';

const config = {
  sandbox_mode: 'workspace-write',
  tools: { read_text: 'allow', write_text: 'allow', bash: 'allow', echo: 'ask', mcp_x: 'allow' },
} as unknown as Config;
const tools = [{ name: 'mcp_x', server_id: 'server' }] as unknown as Tool[];

describe('run choices', () => {
  it('labels models with their effort and the simulator by name', () => {
    expect(modelLabel({ provider_id: 'openai', model: 'gpt-5', reasoning_effort: 'high' })).toBe(
      'gpt-5 · High',
    );
    expect(modelLabel({ provider_id: 'simulated', model: 'simulated' })).toBe('Simulated model');
    expect(modelLabel({ provider_id: 'openai', model: '' })).toBe('Choose a model');
  });

  it('applies workspace access the way the server does', () => {
    const ask = effectiveConfig(config, tools, 'ask');
    expect(ask.tools).toEqual({
      read_text: 'allow',
      write_text: 'ask',
      bash: 'ask',
      echo: 'ask',
      mcp_x: 'ask',
    });
    expect(effectiveConfig(config, tools, 'read-only').sandbox_mode).toBe('read-only');
    expect(effectiveConfig(config, tools, 'workspace-write').tools).toBe(config.tools);
  });
});
