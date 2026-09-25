const PROVIDER_BRANDS = {
  openai:        { label: "OpenAI",        color: "#10a37f", logo: null },
  anthropic:     { label: "Anthropic Compatible", color: "#d4a574", logo: null },
  google:        { label: "Google",        color: "#4285f4", logo: null },
  groq:          { label: "Groq",          color: "#f55036", logo: null },
  deepseek:      { label: "DeepSeek",      color: "#4d6bfe", logo: null },
  mistral:       { label: "Mistral",       color: "#ff7000", logo: null },
  cohere:        { label: "Cohere",        color: "#39594d", logo: null },
  ollama:        { label: "Ollama",        color: "#ffffff", logo: null },
  together:      { label: "Together AI",   color: "#6366f1", logo: null },
  fireworks:     { label: "Fireworks AI",  color: "#ff6b35", logo: null },
  perplexity:    { label: "Perplexity",    color: "#1a73e8", logo: null },
  xai:           { label: "xAI",           color: "#1d9bf0", logo: null },
  huggingface:   { label: "Hugging Face",  color: "#ff9d00", logo: null },
  replicate:     { label: "Replicate",     color: "#3b82f6", logo: null },
  stability:     { label: "Stability AI",  color: "#a855f7", logo: null },
  elevenlabs:    { label: "ElevenLabs",    color: "#000000", logo: null },
  openrouter:    { label: "OpenRouter",    color: "#6366f1", logo: null },
  opencode:      { label: "OpenCode",      color: "#fbbf24", logo: null },
  lmstudio:      { label: "LM Studio",    color: "#000000", logo: null },
  textgen:       { label: "TextGen",       color: "#22c55e", logo: null },
  jan:           { label: "Jan",           color: "#000000", logo: null },
  gpt4all:       { label: "GPT4All",       color: "#6366f1", logo: null },
  localai:       { label: "LocalAI",       color: "#22c55e", logo: null },
  vllm:          { label: "vLLM",          color: "#3b82f6", logo: null },
  tgi:           { label: "TGI",           color: "#ff6b35", logo: null },
  elevenlabs_s:  { label: "ElevenLabs",    color: "#000000", logo: null },
  google_tts:    { label: "Google TTS",    color: "#4285f4", logo: null },
  browserbase:   { label: "Browserbase",   color: "#000000", logo: null },
  brightdata:    { label: "Bright Data",   color: "#00a651", logo: null },
  scrapingbee:   { label: "ScrapingBee",   color: "#ff6b35", logo: null },
  alibaba:       { label: "DashScope",     color: "#ff6a00", logo: null },
  zhipuai:       { label: "Zhipu AI",      color: "#2b6cb0", logo: null },
  moonshot:      { label: "Moonshot",      color: "#6366f1", logo: null },
  minimax:       { label: "MiniMax",       color: "#8b5cf6", logo: null },
  baidu:         { label: "Qianfan",       color: "#2932e1", logo: null },
  xiaomi:        { label: "Xiaomi MIMO",   color: "#ff6900", logo: null },
  nvidia:        { label: "NVIDIA NIM",    color: "#76b900", logo: null },
  siliconflow:   { label: "SiliconFlow",   color: "#6366f1", logo: null },
  aihubmix:      { label: "AiHubMix",      color: "#3b82f6", logo: null },
  novita:        { label: "Novita AI",     color: "#f59e0b", logo: null },
  volcengine:    { label: "VolcEngine",    color: "#3370ff", logo: null },
  byteplus:      { label: "BytePlus",      color: "#3370ff", logo: null },
  stepfun:       { label: "Step Fun",      color: "#10b981", logo: null },
  longcat:       { label: "LongCat",       color: "#f97316", logo: null },
  antling:       { label: "Ant Ling",      color: "#ef4444", logo: null },
  skywork:       { label: "Skywork",       color: "#0ea5e9", logo: null },
  assemblyai:    { label: "AssemblyAI",    color: "#4f46e5", logo: null },
  custom:        { label: "Custom",        color: "#6b7280", logo: null },
};

const PROVIDER_ALIASES = {
  gemini: 'google',
  lm_studio: 'lmstudio',
  dashscope: 'alibaba',
  zhipu: 'zhipuai',
  qianfan: 'baidu',
  xiaomi_mimo: 'xiaomi',
  minimax_anthropic: 'minimax',
  volcengine_coding_plan: 'volcengine',
  byteplus_coding_plan: 'byteplus',
  ant_ling: 'antling',
  atomic_chat: 'localai',
  ovms: 'vllm',
  opencode_go: 'opencode',
  opencode_zen: 'opencode',
};

/** Il colore di una marca che la tabella non conosce: una tinta ricavata dal
 *  nome, saturazione e chiarezza fisse.
 *
 *  Era la regola del pallino nell'officina (`_brandColor`), mentre la casa
 *  leggeva la tabella: la stessa marca aveva due colori, e ogni marca che
 *  l'utente si aggiunge da se' — il motivo per cui quella schermata esiste —
 *  in casa era grigia. Ora le conosciute prendono il loro colore e le altre
 *  questa tinta, stabile fra un'apertura e l'altra, in tutti e due i gusci.
 *  Non passa dai token del tema: identifica una marca, non un ruolo. */
function colorFromName(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h}, 62%, 55%)`;
}

export function getProviderBrand(name) {
  if (!name) return { label: 'Unknown', color: '#888', logo: null };
  const normalized = name.toLowerCase().replace(/[\s-]+/g, '_');
  const aliased = PROVIDER_ALIASES[normalized] || normalized;
  return PROVIDER_BRANDS[aliased] || { label: name, color: colorFromName(name), logo: null };
}
