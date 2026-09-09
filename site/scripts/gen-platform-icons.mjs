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
// interactive actions (see backend/bots/capabilities.py).
const platforms = [
  { id: 'slack',        name: 'Slack',            note: 'Rich card with buttons to act on',           svg: SLACK, buttons: true },
  { id: 'teams',        name: 'Microsoft Teams',  note: 'Rich card with buttons to act on',           svg: render(bi.BiLogoMicrosoftTeams, '#6264A7'), buttons: true },
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


// Model providers, from backend/llm/factory.py. Icons mirror PROVIDER_ICONS
// in frontend/lib/brand-icons.tsx.
const providers = [
  { id: 'anthropic',         name: 'Anthropic',    note: 'Claude',                       svg: render(si.SiAnthropic, '#D97757') },
  { id: 'openai',            name: 'OpenAI',       note: 'GPT',                          svg: render(si.SiOpenai) },
  { id: 'azure_openai',      name: 'Azure OpenAI', note: 'Your Azure deployment',        svg: render(vsc.VscAzure, '#0078D4') },
  { id: 'bedrock',           name: 'AWS Bedrock',  note: 'Models in your AWS account',   svg: render(fa.FaAws, '#FF9900') },
  { id: 'vertex_ai',         name: 'Vertex AI',    note: 'Google Cloud',                 svg: render(si.SiGooglecloud, '#4285F4') },
  { id: 'ollama',            name: 'Ollama',       note: 'Local, nothing leaves the box', svg: render(si.SiOllama) },
  { id: 'openai_compatible', name: 'Any OpenAI compatible endpoint', note: 'vLLM, LM Studio, your own gateway', svg: glyph('Boxes') },
];

const dataDir = resolve(here, '../src/data');
mkdirSync(dataDir, { recursive: true });
writeFileSync(resolve(dataDir, 'platforms.json'), JSON.stringify(platforms, null, 2) + '\n');
writeFileSync(resolve(dataDir, 'sources.json'), JSON.stringify(sources, null, 2) + '\n');
writeFileSync(resolve(dataDir, 'providers.json'), JSON.stringify(providers, null, 2) + '\n');
console.log(`wrote ${platforms.length} platforms, ${sources.length} sources, ${providers.length} providers to ${dataDir}`);
