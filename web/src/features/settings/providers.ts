import type { Config, ProviderProfile } from '../../api/client';

/** The request formats Glassbox speaks, in the order Settings lists them. */
export const formats = ['openai', 'responses', 'anthropic'] as const;
export type Format = (typeof formats)[number];

/** How each request format is labelled in Settings. */
export const formatLabels: Record<Format, string> = {
  openai: 'Chat Completions',
  responses: 'Responses API',
  anthropic: 'Messages API',
};

/** The path each format's requests add to its base URL. */
export const formatPaths: Record<Format, string> = {
  openai: '/chat/completions',
  responses: '/responses',
  anthropic: '/messages',
};

/** A model service Settings can add with its documented endpoints filled in.
 *
 * Gateways that serve each model in one format list `formatPrefixes`: model ID prefixes with
 * the format their models need (the longest match wins), applied when models are added. They
 * follow the service's documentation as of 2026-10-09; Settings → Models can change any. */
export type Service = {
  id: string;
  name: string;
  description: string;
  adapter: Format;
  base_url: string;
  endpoints?: Partial<Record<Format, string>>;
  credential_ref: string;
  max_output_tokens: number;
  keyUrl?: string;
  formatPrefixes?: Record<string, Format>;
  /** Signs in with ChatGPT instead of using a key. */
  auth?: 'chatgpt';
};

/** Where ChatGPT shows and limits what apps use of the plan. */
export const usageUrl = 'https://chatgpt.com/settings/usage';

export const services: Service[] = [
  {
    id: 'chatgpt',
    name: 'ChatGPT plan',
    description:
      'GPT models on your ChatGPT plan instead of an API key, with Sign in with ChatGPT.',
    adapter: 'responses',
    base_url: 'https://api.openai.com/v1',
    credential_ref: '',
    max_output_tokens: 32000,
    auth: 'chatgpt',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    description: 'GPT models with an OpenAI API key.',
    adapter: 'responses',
    base_url: 'https://api.openai.com/v1',
    endpoints: { openai: 'https://api.openai.com/v1' },
    credential_ref: 'OPENAI_API_KEY',
    max_output_tokens: 32000,
    keyUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    description: 'Claude models with a Claude Console API key.',
    adapter: 'anthropic',
    base_url: 'https://api.anthropic.com/v1',
    credential_ref: 'ANTHROPIC_API_KEY',
    max_output_tokens: 32000,
    keyUrl: 'https://platform.claude.com/settings/keys',
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    description: 'Models from many labs with one key. Claude models use the Messages API.',
    adapter: 'openai',
    base_url: 'https://openrouter.ai/api/v1',
    endpoints: {
      responses: 'https://openrouter.ai/api/v1',
      anthropic: 'https://openrouter.ai/api/v1',
    },
    credential_ref: 'OPENROUTER_API_KEY',
    max_output_tokens: 8192,
    keyUrl: 'https://openrouter.ai/settings/keys',
    formatPrefixes: { 'anthropic/': 'anthropic' },
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    description: 'DeepSeek models through Chat Completions or the Messages API.',
    adapter: 'openai',
    base_url: 'https://api.deepseek.com',
    endpoints: { anthropic: 'https://api.deepseek.com/anthropic/v1' },
    credential_ref: 'DEEPSEEK_API_KEY',
    max_output_tokens: 8192,
    keyUrl: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'modelscope',
    name: 'ModelScope',
    description: 'Open models on ModelScope API-Inference with an access token.',
    adapter: 'openai',
    base_url: 'https://api-inference.modelscope.cn/v1',
    endpoints: { anthropic: 'https://api-inference.modelscope.cn/v1' },
    credential_ref: 'MODELSCOPE_API_KEY',
    max_output_tokens: 8192,
    keyUrl: 'https://modelscope.cn/my/myaccesstoken',
  },
  {
    id: 'opencode-zen',
    name: 'OpenCode Zen',
    description: 'Pay-as-you-go models selected for coding agents.',
    adapter: 'openai',
    base_url: 'https://opencode.ai/zen/v1',
    endpoints: { responses: 'https://opencode.ai/zen/v1', anthropic: 'https://opencode.ai/zen/v1' },
    credential_ref: 'OPENCODE_API_KEY',
    max_output_tokens: 8192,
    keyUrl: 'https://opencode.ai/auth',
    formatPrefixes: {
      'claude-': 'anthropic',
      'qwen3.5-': 'anthropic',
      'qwen3.6-': 'anthropic',
      'qwen3.7-': 'anthropic',
      'qwen3.8-flash': 'anthropic',
      'gpt-': 'responses',
      'grok-': 'responses',
      'muse-': 'responses',
    },
  },
  {
    id: 'opencode-go',
    name: 'OpenCode Go',
    description: 'A subscription for open coding models.',
    adapter: 'openai',
    base_url: 'https://opencode.ai/zen/go/v1',
    endpoints: {
      responses: 'https://opencode.ai/zen/go/v1',
      anthropic: 'https://opencode.ai/zen/go/v1',
    },
    credential_ref: 'OPENCODE_API_KEY',
    max_output_tokens: 8192,
    keyUrl: 'https://opencode.ai/auth',
    formatPrefixes: {
      'claude-': 'anthropic',
      'minimax-': 'anthropic',
      qwen: 'anthropic',
      'gpt-': 'responses',
      'grok-': 'responses',
      'muse-': 'responses',
    },
  },
];

/** Endpoints for services not listed: any server that speaks one of the formats. */
export const customEndpoints: Record<Format, { name: string; base_url: string }> = {
  openai: { name: 'OpenAI-compatible', base_url: 'https://api.openai.com/v1' },
  responses: { name: 'Responses API', base_url: 'https://api.openai.com/v1' },
  anthropic: { name: 'Anthropic-compatible', base_url: 'https://api.anthropic.com/v1' },
};

/** A unique provider ID based on a preferred one. */
function uniqueId(preferred: string, existing: string[]): string {
  let id = preferred;
  for (let index = 2; existing.includes(id); index += 1) id = `${preferred}-${index}`;
  return id;
}

const blank = {
  models: [],
  model_formats: {},
  temperature: null,
  context_window: null,
  prices: {},
  show_reasoning: false,
} satisfies Partial<ProviderProfile>;

/** Add a known service under a unique ID. Its key name is the service's own, so two
 * providers of one service share a key unless one is renamed. */
export function serviceProvider(service: Service, existing: string[]): ProviderProfile {
  return {
    ...blank,
    id: uniqueId(service.id, existing),
    name: service.name,
    service: service.id,
    auth: service.auth ?? 'key',
    adapter: service.adapter,
    base_url: service.base_url,
    endpoints: { ...service.endpoints },
    credential_ref: service.credential_ref,
    max_output_tokens: service.max_output_tokens,
  };
}

/** Add an endpoint of one format, with a unique ID and a key name derived from it. */
export function newProvider(adapter: Format, existing: string[]): ProviderProfile {
  const id = uniqueId(adapter, existing);
  return {
    ...blank,
    id,
    adapter,
    ...customEndpoints[adapter],
    service: null,
    auth: 'key',
    endpoints: {},
    credential_ref: `${id.toUpperCase().replaceAll('-', '_')}_API_KEY`,
    max_output_tokens: adapter === 'anthropic' ? 32000 : 8192,
  };
}

/** The service a provider was added as, if it is a known one. */
export function serviceOf(profile: ProviderProfile): Service | undefined {
  return services.find((service) => service.id === profile.service);
}

/** The formats a provider offers, each with its base URL, in Settings order. */
export function offered(profile: ProviderProfile): [Format, string][] {
  return formats.flatMap((format): [Format, string][] => {
    const url = format === profile.adapter ? profile.base_url : profile.endpoints?.[format];
    return url === undefined ? [] : [[format, url]];
  });
}

/** The format a model of this provider is called with. */
export function formatOf(profile: ProviderProfile, model: string): Format {
  return profile.model_formats?.[model] ?? profile.adapter;
}

/** Offer a format at a URL, or stop offering it with an empty URL. The default format keeps
 * its URL field even when emptied; models that used a dropped format return to the default. */
export function setFormatUrl(
  profile: ProviderProfile,
  format: Format,
  url: string,
): ProviderProfile {
  if (format === profile.adapter) return { ...profile, base_url: url };
  const endpoints = { ...profile.endpoints };
  if (url) endpoints[format] = url;
  else delete endpoints[format];
  const model_formats = url
    ? profile.model_formats
    : Object.fromEntries(
        Object.entries(profile.model_formats ?? {}).filter(([, value]) => value !== format),
      );
  return { ...profile, endpoints, model_formats };
}

/** Make an offered format the default. Models without their own format follow it. */
export function setDefaultFormat(profile: ProviderProfile, format: Format): ProviderProfile {
  const url = profile.endpoints?.[format];
  if (format === profile.adapter || url === undefined) return profile;
  const endpoints: Record<string, string> = {
    ...profile.endpoints,
    [profile.adapter]: profile.base_url,
  };
  delete endpoints[format];
  return { ...profile, adapter: format, base_url: url, endpoints };
}

/** Assign a model one of the provider's formats; the default format needs no entry. */
export function setModelFormat(
  profile: ProviderProfile,
  model: string,
  format: Format,
): ProviderProfile {
  const model_formats = { ...profile.model_formats };
  if (format === profile.adapter) delete model_formats[model];
  else model_formats[model] = format;
  return { ...profile, model_formats };
}

/** The format a known gateway documents for a model, when the provider offers it. */
export function suggestedFormat(profile: ProviderProfile, model: string): Format | undefined {
  const prefixes = Object.entries(serviceOf(profile)?.formatPrefixes ?? {})
    .filter(([prefix]) => model.startsWith(prefix))
    .sort(([left], [right]) => right.length - left.length);
  const format = prefixes[0]?.[1];
  return format && offered(profile).some(([value]) => value === format) ? format : undefined;
}

/** Add model IDs to a provider; new ones get the format their gateway documents. */
export function addModels(profile: ProviderProfile, models: string[]): ProviderProfile {
  const added = models.filter((model) => !profile.models.includes(model));
  let next: ProviderProfile = {
    ...profile,
    models: [...new Set([...profile.models, ...added])].sort(),
  };
  for (const model of added) {
    const format = suggestedFormat(next, model);
    if (format && !next.model_formats?.[model]) next = setModelFormat(next, model, format);
  }
  return next;
}

/** Remove a model and its format. */
export function removeModel(profile: ProviderProfile, model: string): ProviderProfile {
  return {
    ...profile,
    models: profile.models.filter((item) => item !== model),
    model_formats: Object.fromEntries(
      Object.entries(profile.model_formats ?? {}).filter(([key]) => key !== model),
    ),
  };
}

/** Keep the default model's connection in step with its provider, as the server resolves it.
 * The server checks the format, and a ChatGPT sign-in only goes with its own endpoint, so a
 * provider or format change must not leave the previous provider's values behind. */
export function withDefaultConnection(config: Config): Config {
  const current = config.provider;
  const profile = config.providers.find((item) => item.id === current.profile_id);
  let next: Config['provider'] = { ...current, auth: 'key' };
  if (profile) {
    const adapter = formatOf(profile, current.model);
    next = {
      ...current,
      adapter,
      base_url: adapter === profile.adapter ? profile.base_url : profile.endpoints?.[adapter] || '',
      auth: profile.auth ?? 'key',
      credential_ref: profile.credential_ref ?? '',
    };
  }
  return JSON.stringify(next) === JSON.stringify(current) ? config : { ...config, provider: next };
}
