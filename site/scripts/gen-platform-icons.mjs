// Renders the notification platform marks the app itself uses
// (frontend/lib/brand-icons.tsx) into static SVG for the showcase site, so
// the site never drifts from the audited registry. Run from anywhere:
//
//   node site/scripts/gen-platform-icons.mjs
//
// Output: site/src/data/platforms.json
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const req = createRequire(resolve(here, '../../frontend/package.json'));
const React = req('react');
const { renderToStaticMarkup } = req('react-dom/server');
const si = req('react-icons/si');
const bi = req('react-icons/bi');
const ai = req('react-icons/ai');
const fa = req('react-icons/fa6');
const vsc = req('react-icons/vsc');
const lucide = req('lucide-react');

const render = (Icon, color) =>
  renderToStaticMarkup(React.createElement(Icon, { size: 28, color: color ?? 'currentColor', 'aria-hidden': true }));

// Hand drawn marks, byte for byte the ones in brand-icons.tsx.
const SLACK = `<svg width="28" height="28" viewBox="0 0 256 256" aria-hidden="true"><path fill="#E01E5A" d="M53.841 161.32c0 14.832-11.987 26.82-26.819 26.82S.203 176.152.203 161.32c0-14.831 11.987-26.818 26.82-26.818H53.84zm13.41 0c0-14.831 11.987-26.818 26.819-26.818s26.819 11.987 26.819 26.819v67.047c0 14.832-11.987 26.82-26.82 26.82c-14.83 0-26.818-11.988-26.818-26.82z"/><path fill="#36C5F0" d="M94.07 53.638c-14.832 0-26.82-11.987-26.82-26.819S79.239 0 94.07 0s26.819 11.987 26.819 26.819v26.82zm0 13.613c14.832 0 26.819 11.987 26.819 26.819s-11.987 26.819-26.82 26.819H26.82C11.987 120.889 0 108.902 0 94.069c0-14.83 11.987-26.818 26.819-26.818z"/><path fill="#2EB67D" d="M201.55 94.07c0-14.832 11.987-26.82 26.818-26.82s26.82 11.988 26.82 26.82s-11.988 26.819-26.82 26.819H201.55zm-13.41 0c0 14.832-11.988 26.819-26.82 26.819c-14.831 0-26.818-11.987-26.818-26.82V26.82C134.502 11.987 146.489 0 161.32 0s26.819 11.987 26.819 26.819z"/><path fill="#ECB22E" d="M161.32 201.55c14.832 0 26.82 11.987 26.82 26.818s-11.988 26.82-26.82 26.82c-14.831 0-26.818-11.988-26.818-26.82V201.55zm0-13.41c-14.831 0-26.818-11.988-26.818-26.82c0-14.831 11.987-26.818 26.819-26.818h67.25c14.832 0 26.82 11.987 26.82 26.819s-11.988 26.819-26.82 26.819z"/></svg>`;
const LARK = `<svg width="28" height="28" viewBox="0 0 32 25.37" aria-hidden="true"><path fill="#4BC0AE" d="m16.59 13.32.08-.08.27-.27.32-.32.83-.81.73-.72.48-.47c.29-.28.59-.54.91-.78.64-.51 1.36-.93 2.12-1.28.52-.25 1.06-.45 1.61-.62A18.8 18.8 0 0 0 20.39.86 1.72 1.72 0 0 0 19.05 0H5.37a.26.26 0 0 0-.16.47 36.9 36.9 0 0 1 11.34 12.89z"/><path fill="#4C6EB5" d="M11.15 25.37c7.07 0 13.23-3.9 16.43-9.66.12-.2.23-.41.33-.61-.21.42-.47.81-.75 1.18a7.2 7.2 0 0 1-2.21 1.92 7.6 7.6 0 0 1-3.2.82c-.67.03-1.35-.04-2.01-.2l-2.05-.62a33 33 0 0 1-4.62-1.77A38.8 38.8 0 0 1 .45 7.58a.26.26 0 0 0-.45.18v13.06c0 .57.28 1.1.75 1.42a18.75 18.75 0 0 0 10.4 3.13"/><path fill="#214295" d="M31.92 8.34a11.24 11.24 0 0 0-7.99-.6 12.4 12.4 0 0 0-4.03 2.31l-3.32 3.27a17.2 17.2 0 0 1-3.9 2.76 34.4 34.4 0 0 0 7.06 2.74c1.32.33 2.7.28 3.99-.17a7.5 7.5 0 0 0 3.43-2.37c.28-.37.53-.76.75-1.17l1.83-3.65a11.3 11.3 0 0 1 2.18-3.12"/></svg>`;

// Order is the order on the page. `buttons` marks platforms with verified
// interactive actions (see backend/bots/capabilities.py). `rebuilding` marks a
// platform that sends nothing until its rebuild lands; the page leaves it out
// of the platform marquee and the platform count.
const platforms = [
  { id: 'slack',        name: 'Slack',            note: 'Rich card with buttons to act on',           svg: SLACK, buttons: true },
  { id: 'teams',        name: 'Microsoft Teams',  note: 'Being rebuilt, sends nothing for now', svg: render(bi.BiLogoMicrosoftTeams, '#6264A7'), rebuilding: true },
  { id: 'discord',      name: 'Discord',          note: 'Rich card with buttons to act on',           svg: render(si.SiDiscord, '#5865F2'), buttons: true },
  { id: 'telegram',     name: 'Telegram',         note: 'Channel or direct message',   svg: render(si.SiTelegram, '#26A5E4') },
  { id: 'signal',       name: 'Signal',           note: 'Direct message',              svg: render(si.SiSignal, '#3A76F0') },
  { id: 'whatsapp',     name: 'WhatsApp',         note: 'Direct message',              svg: render(si.SiWhatsapp, '#25D366') },
  { id: 'matrix',       name: 'Matrix',           note: 'Room or direct message',      svg: render(si.SiMatrix) },
  { id: 'mattermost',   name: 'Mattermost',       note: 'Channel or direct message',   svg: render(si.SiMattermost, '#0058CC') },
  { id: 'google_chat',  name: 'Google Chat',      note: 'Space, updated in place',     svg: render(si.SiGooglechat, '#34A853') },
  { id: 'feishu',       name: 'Lark / Feishu',    note: 'Channel or direct message',   svg: LARK },
  { id: 'dingtalk',     name: 'DingTalk',         note: 'Group',                        svg: render(ai.AiOutlineDingding, '#0089FF') },
  { id: 'wecom',        name: 'WeCom',            note: 'Group or direct message',     svg: render(ai.AiFillWechatWork, '#07C160') },
  { id: 'weixin',       name: 'WeChat',           note: 'Official account',            svg: render(si.SiWechat, '#07C160') },
  { id: 'bluebubbles',  name: 'iMessage',         note: 'Through BlueBubbles',         svg: render(si.SiApple) },
  { id: 'voice',        name: 'Phone call',       note: 'Press 1 acknowledge, 2 escalate, 3 resolve', svg: render(lucide.Phone) },
  { id: 'sms',          name: 'SMS',              note: 'Alert and incident link',     svg: render(si.SiTwilio, '#F22F46') },
  { id: 'email',        name: 'Email',            note: 'SMTP or Mailgun',             svg: render(lucide.Mail) },
  { id: 'eventbridge',  name: 'AWS EventBridge',  note: 'Lifecycle events for your own automation', svg: render(fa.FaAws, '#FF9900') },
  { id: 'homeassistant',name: 'Home Assistant',   note: 'For the truly on call',       svg: render(si.SiHomeassistant, '#18BCF2') },
  { id: 'custom',       name: 'Any webhook',      note: 'Bring something we have not met', svg: render(lucide.Puzzle) },
];

// Inbound alert sources: one entry per adapter in backend/ingest/adapters.
// Simple Icons does not ship Oracle, AppDynamics, Honeycomb, or Bugsnag, so
// Oracle gets a hand drawn pill and the other three get neutral glyphs, the
// same fallback rule the app registry uses.
const ORACLE = `<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="7" width="20" height="10" rx="5" fill="none" stroke="#C74634" stroke-width="2.6"/></svg>`;
const glyph = (name, color) => (lucide[name] ? render(lucide[name], color) : render(lucide.Globe, color));

const sources = [
  { id: 'cloudwatch',      name: 'AWS CloudWatch',          note: 'Alarms',                    svg: render(fa.FaAws, '#FF9900') },
  { id: 'azure_monitor',   name: 'Azure Monitor',           note: 'Alerts',                    svg: render(vsc.VscAzure, '#0078D4') },
  { id: 'gcp_monitoring',  name: 'Google Cloud Monitoring', note: 'Alerting policies',         svg: render(si.SiGooglecloud, '#4285F4') },
  { id: 'oci_monitoring',  name: 'OCI Monitoring',          note: 'Alarms',                    svg: ORACLE },
  { id: 'sentry',          name: 'Sentry',                  note: 'Issues',                    svg: render(si.SiSentry, '#8C5CF4') },
  { id: 'newrelic',        name: 'New Relic',               note: 'Alerts',                    svg: render(si.SiNewrelic, '#00AC69') },
  { id: 'dynatrace',       name: 'Dynatrace',               note: 'Problems',                  svg: render(si.SiDynatrace, '#1496FF') },
  { id: 'splunk',          name: 'Splunk',                  note: 'Alert actions',             svg: render(si.SiSplunk) },
  { id: 'loki',            name: 'Grafana Loki',            note: 'Ruler alerts',              svg: render(si.SiGrafana, '#F46800') },
  { id: 'elastic_watcher', name: 'Elastic Watcher',         note: 'Watch actions',             svg: render(si.SiElastic, '#00BFB3') },
  { id: 'appdynamics',     name: 'AppDynamics',             note: 'Health rule violations',    svg: glyph('Activity') },
  { id: 'honeycomb',       name: 'Honeycomb',               note: 'Triggers',                  svg: glyph('Hexagon') },
  { id: 'bugsnag',         name: 'Bugsnag',                 note: 'Errors',                    svg: glyph('Bug') },
  { id: 'rollbar',         name: 'Rollbar',                 note: 'Occurrences',               svg: render(si.SiRollbar) },
  { id: 'generic',         name: 'Generic webhook',         note: 'Any JSON payload',          svg: glyph('Webhook') },
  { id: 'auto',            name: 'Auto detect',             note: 'Any webhook. Fields are learned once, then cached', svg: glyph('Radar') },
];


// Model vendor marks from Simple Icons (CC0) that react-icons does not ship
// yet, and LM Studio's mark. Qwen, DeepSeek, Kimi and LM Studio match
// brand-icons.tsx byte for byte.
const QWEN = `<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="#6950EF" d="M23.919 14.545 20.817 9.17l1.47-2.544a.56.56 0 0 0 0-.566l-1.633-2.83a.57.57 0 0 0-.49-.283h-6.207L12.487.402a.57.57 0 0 0-.49-.284H8.732a.56.56 0 0 0-.49.284L5.139 5.775h-2.94a.56.56 0 0 0-.49.284L.077 8.887a.56.56 0 0 0 0 .567L3.18 14.83l-1.47 2.545a.56.56 0 0 0 0 .566l1.634 2.83a.57.57 0 0 0 .49.283h6.205l1.47 2.545a.57.57 0 0 0 .49.284h3.266a.57.57 0 0 0 .49-.284l3.104-5.375h2.94a.57.57 0 0 0 .49-.283l1.634-2.828a.55.55 0 0 0-.004-.568M8.733.686l1.634 2.828-1.634 2.828H21.8L20.164 9.17H7.425L5.63 6.06Zm1.306 19.801-6.205-.002 1.634-2.83h3.265L2.201 6.344h3.267q3.182 5.517 6.367 11.032zm10.124-5.66L18.53 12l-6.532 11.315-1.634-2.83c2.129-3.673 4.25-7.351 6.373-11.028h3.592l3.102 5.374z"/></svg>`;
const DEEPSEEK = `<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4D6BFE" d="M23.748 4.651c-.254-.124-.364.113-.512.233-.051.04-.094.09-.137.137-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.155-.708-.311-.955-.65-.172-.24-.219-.509-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.094.172.187.129.323-.082.28-.18.553-.266.833-.055.179-.137.218-.328.14a5.5 5.5 0 0 1-1.737-1.179c-.857-.828-1.631-1.743-2.597-2.46a12 12 0 0 0-.689-.47c-.985-.957.13-1.743.387-1.836.27-.098.094-.433-.778-.428-.872.003-1.67.295-2.687.685a3 3 0 0 1-.465.136 9.6 9.6 0 0 0-2.883-.101c-1.885.21-3.39 1.1-4.497 2.622C.082 8.776-.231 10.854.152 13.02c.403 2.284 1.568 4.175 3.36 5.653 1.857 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.132-.284 4.994-1.86.47.234.962.328 1.78.398.629.058 1.235-.031 1.705-.129.735-.155.684-.836.418-.961-2.155-1.004-1.682-.595-2.112-.926 1.095-1.295 2.768-3.598 3.284-6.733.05-.346.115-.834.108-1.114-.004-.171.035-.238.23-.257a4.2 4.2 0 0 0 1.545-.475c1.397-.763 1.96-2.016 2.093-3.517.02-.23-.004-.467-.247-.588M11.58 18.168c-2.088-1.642-3.101-2.183-3.52-2.16-.39.024-.32.472-.234.763.09.288.207.487.371.74.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.168-1.361-.801-2.5-1.86-3.301-3.306-.775-1.393-1.225-2.888-1.299-4.482-.02-.385.094-.522.477-.592a4.7 4.7 0 0 1 1.53-.038c2.131.311 3.946 1.264 5.467 2.774.868.86 1.525 1.887 2.202 2.89.72 1.066 1.494 2.082 2.48 2.915.348.291.626.513.892.677-.802.09-2.14.109-3.055-.615zm1.001-6.44a.306.306 0 0 1 .415-.287.3.3 0 0 1 .113.074.3.3 0 0 1 .086.214c0 .17-.136.307-.308.307a.303.303 0 0 1-.306-.307m3.11 1.596c-.2.081-.4.151-.591.16a1.25 1.25 0 0 1-.798-.254c-.274-.23-.47-.358-.551-.758a1.7 1.7 0 0 1 .015-.588c.07-.327-.007-.537-.238-.727-.188-.156-.426-.199-.689-.199a.6.6 0 0 1-.254-.078.253.253 0 0 1-.114-.358 1 1 0 0 1 .192-.21c.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.392.451.462.576.685.915.176.264.336.536.446.848.066.194-.02.353-.25.45"/></svg>`;
const KIMI = `<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.765.351C22.998.351 24 1.353 24 2.586S22.998 4.82 21.765 4.82h-1.974c-.15 0-.26-.12-.26-.26V2.586A2.237 2.237 0 0 1 21.765.35M9.41 13.388l8.447-8.377c.16-.16.07-.471-.14-.471h-4.55s-.1.02-.14.06l-9.099 9.029c-.14.14-.35.02-.35-.21V4.81c0-.15-.1-.27-.221-.27H.22c-.12 0-.22.12-.22.27v18.57c0 .15.1.27.22.27h3.137c.12 0 .22-.12.22-.27v-3.79c0-.08.03-.16.08-.21l2.826-2.796c.07-.07.16-.08.241-.03l7.546 5.551a8.9 8.9 0 0 0 4.018 1.493c.12.01.23-.11.23-.27V19.76c0-.14-.08-.25-.19-.26a5.8 5.8 0 0 1-2.355-.942l-6.533-4.73c-.14-.09-.15-.32-.03-.441"/></svg>`;
const LMSTUDIO = `<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path fill="#7A5CF5" d="M5.6 0A5.6 5.6 0 0 0 0 5.6v12.8A5.6 5.6 0 0 0 5.6 24h12.8a5.6 5.6 0 0 0 5.6-5.6V5.6A5.6 5.6 0 0 0 18.4 0zm0 2h12.8A3.6 3.6 0 0 1 22 5.6v12.8a3.6 3.6 0 0 1-3.6 3.6H5.6A3.6 3.6 0 0 1 2 18.4V5.6A3.6 3.6 0 0 1 5.6 2m-.4 2.8a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm-3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4z"/></svg>`;

// Model providers, from backend/llm/registry.py. Icons mirror PROVIDER_ICONS
// in frontend/lib/brand-icons.tsx.
const providers = [
  { id: 'anthropic',         name: 'Anthropic',    note: 'Claude',                       svg: render(si.SiAnthropic, '#D97757') },
  { id: 'openai',            name: 'OpenAI',       note: 'GPT',                          svg: render(si.SiOpenai) },
  { id: 'azure_openai',      name: 'Azure OpenAI', note: 'Your Azure deployment',        svg: render(vsc.VscAzure, '#0078D4') },
  { id: 'bedrock',           name: 'AWS Bedrock',  note: 'Models in your AWS account',   svg: render(fa.FaAws, '#FF9900') },
  { id: 'vertex_ai',         name: 'Vertex AI',    note: 'Google Cloud',                 svg: render(si.SiGooglecloud, '#4285F4') },
  { id: 'ollama',            name: 'Ollama',       note: 'Local, nothing leaves the box', svg: render(si.SiOllama) },
  { id: 'lmstudio',          name: 'LM Studio',    note: 'Local, on your own machine',   svg: LMSTUDIO },
  { id: 'qwen',              name: 'Qwen',         note: 'With your Qwen API key',       svg: QWEN },
  { id: 'deepseek',          name: 'DeepSeek',     note: 'With your DeepSeek API key',   svg: DEEPSEEK },
  { id: 'kimi',              name: 'Kimi',         note: 'With your Kimi API key',       svg: KIMI },
  { id: 'openai_compatible', name: 'Any OpenAI compatible endpoint', note: 'vLLM, llama.cpp, your own gateway', svg: glyph('Boxes') },
];

const dataDir = resolve(here, '../src/data');
mkdirSync(dataDir, { recursive: true });
writeFileSync(resolve(dataDir, 'platforms.json'), JSON.stringify(platforms, null, 2) + '\n');
writeFileSync(resolve(dataDir, 'sources.json'), JSON.stringify(sources, null, 2) + '\n');
writeFileSync(resolve(dataDir, 'providers.json'), JSON.stringify(providers, null, 2) + '\n');
console.log(`wrote ${platforms.length} platforms, ${sources.length} sources, ${providers.length} providers to ${dataDir}`);
