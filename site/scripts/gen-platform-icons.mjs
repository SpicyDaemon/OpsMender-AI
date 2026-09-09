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
const lucide = req('lucide-react');

const render = (Icon, color) =>
  renderToStaticMarkup(React.createElement(Icon, { size: 28, color: color ?? 'currentColor', 'aria-hidden': true }));

// Hand drawn marks, byte for byte the ones in brand-icons.tsx.
const SLACK = `<svg width="28" height="28" viewBox="0 0 256 256" aria-hidden="true"><path fill="#E01E5A" d="M53.841 161.32c0 14.832-11.987 26.82-26.819 26.82S.203 176.152.203 161.32c0-14.831 11.987-26.818 26.82-26.818H53.84zm13.41 0c0-14.831 11.987-26.818 26.819-26.818s26.819 11.987 26.819 26.819v67.047c0 14.832-11.987 26.82-26.82 26.82c-14.83 0-26.818-11.988-26.818-26.82z"/><path fill="#36C5F0" d="M94.07 53.638c-14.832 0-26.82-11.987-26.82-26.819S79.239 0 94.07 0s26.819 11.987 26.819 26.819v26.82zm0 13.613c14.832 0 26.819 11.987 26.819 26.819s-11.987 26.819-26.82 26.819H26.82C11.987 120.889 0 108.902 0 94.069c0-14.83 11.987-26.818 26.819-26.818z"/><path fill="#2EB67D" d="M201.55 94.07c0-14.832 11.987-26.82 26.818-26.82s26.82 11.988 26.82 26.82s-11.988 26.819-26.82 26.819H201.55zm-13.41 0c0 14.832-11.988 26.819-26.82 26.819c-14.831 0-26.818-11.987-26.818-26.82V26.82C134.502 11.987 146.489 0 161.32 0s26.819 11.987 26.819 26.819z"/><path fill="#ECB22E" d="M161.32 201.55c14.832 0 26.82 11.987 26.82 26.818s-11.988 26.82-26.82 26.82c-14.831 0-26.818-11.988-26.818-26.82V201.55zm0-13.41c-14.831 0-26.818-11.988-26.818-26.82c0-14.831 11.987-26.818 26.819-26.818h67.25c14.832 0 26.82 11.987 26.82 26.819s-11.988 26.819-26.82 26.819z"/></svg>`;
const LARK = `<svg width="28" height="28" viewBox="0 0 32 25.37" aria-hidden="true"><path fill="#4BC0AE" d="m16.59 13.32.08-.08.27-.27.32-.32.83-.81.73-.72.48-.47c.29-.28.59-.54.91-.78.64-.51 1.36-.93 2.12-1.28.52-.25 1.06-.45 1.61-.62A18.8 18.8 0 0 0 20.39.86 1.72 1.72 0 0 0 19.05 0H5.37a.26.26 0 0 0-.16.47 36.9 36.9 0 0 1 11.34 12.89z"/><path fill="#4C6EB5" d="M11.15 25.37c7.07 0 13.23-3.9 16.43-9.66.12-.2.23-.41.33-.61-.21.42-.47.81-.75 1.18a7.2 7.2 0 0 1-2.21 1.92 7.6 7.6 0 0 1-3.2.82c-.67.03-1.35-.04-2.01-.2l-2.05-.62a33 33 0 0 1-4.62-1.77A38.8 38.8 0 0 1 .45 7.58a.26.26 0 0 0-.45.18v13.06c0 .57.28 1.1.75 1.42a18.75 18.75 0 0 0 10.4 3.13"/><path fill="#214295" d="M31.92 8.34a11.24 11.24 0 0 0-7.99-.6 12.4 12.4 0 0 0-4.03 2.31l-3.32 3.27a17.2 17.2 0 0 1-3.9 2.76 34.4 34.4 0 0 0 7.06 2.74c1.32.33 2.7.28 3.99-.17a7.5 7.5 0 0 0 3.43-2.37c.28-.37.53-.76.75-1.17l1.83-3.65a11.3 11.3 0 0 1 2.18-3.12"/></svg>`;

// Order is the order on the page. `buttons` marks platforms with verified
// interactive actions (see backend/bots/capabilities.py).
const platforms = [
  { id: 'slack',        name: 'Slack',            note: 'Buttons to act on',           svg: SLACK, buttons: true },
  { id: 'teams',        name: 'Microsoft Teams',  note: 'Buttons to act on',           svg: render(bi.BiLogoMicrosoftTeams, '#6264A7'), buttons: true },
  { id: 'discord',      name: 'Discord',          note: 'Buttons to act on',           svg: render(si.SiDiscord, '#5865F2'), buttons: true },
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

const out = resolve(here, '../src/data/platforms.json');
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(platforms, null, 2) + '\n');
console.log(`wrote ${platforms.length} platforms to ${out}`);
