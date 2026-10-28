import type { ComponentType, ReactNode } from "react";
import { AiFillWechatWork, AiOutlineDingding } from "react-icons/ai";
import { BiLogoMicrosoftTeams } from "react-icons/bi";
import {
  SiAnsible,
  SiAnthropic,
  SiApple,
  SiArgo,
  SiAsana,
  SiBitbucket,
  SiCircleci,
  SiConfluence,
  SiDiscord,
  SiGitea,
  SiGithub,
  SiGitlab,
  SiGooglechat,
  SiGooglecloud,
  SiGoogledocs,
  SiHomeassistant,
  SiJenkins,
  SiJira,
  SiKubernetes,
  SiLinear,
  SiMailgun,
  SiMattermost,
  SiMatrix,
  SiNewrelic,
  SiNotion,
  SiOllama,
  SiOpenai,
  SiSentry,
  SiSignal,
  SiSplunk,
  SiStatuspage,
  SiTelegram,
  SiTerraform,
  SiTwilio,
  SiWechat,
  SiWhatsapp,
  SiZendesk,
} from "react-icons/si";
// AWS + Microsoft/Azure marks aren't in Simple Icons (trademark), so pull them
// from Font Awesome / the VS Code icon set.
import { FaAws } from "react-icons/fa6";
import { VscAzure, VscAzureDevops } from "react-icons/vsc";
import {
  Boxes,
  Globe,
  LifeBuoy,
  Mail,
  Phone,
  Plug,
  Puzzle,
  Zap,
} from "lucide-react";

// A brand glyph: a Simple-Icons component (with brand color where it reads well
// on both themes) or a neutral Lucide fallback for brands Simple Icons doesn't
// ship (Microsoft/Azure, AWS, …). Glyphs without a `color` inherit
// `currentColor` (we render them in fg-secondary) so near-black/near-white
// marks like GitHub, Notion, or OpenAI stay visible in both modes.
type BrandIconProps = {
  size?: number;
  color?: string;
  className?: string;
  "aria-hidden"?: boolean | "true" | "false";
};
type IconComponent = ComponentType<BrandIconProps>;
type Brand = { Icon: IconComponent; color?: string };

function ServiceNowIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={className}
      {...props}
    >
      <path
        fill="#81B5A1"
        fillRule="evenodd"
        d="M32.195 3.312A32.267 32.267 0 0 0 9.949 58.883a6.346 6.346 0 0 0 8.264.43 23.035 23.035 0 0 1 27.445 0 6.364 6.364 0 0 0 8.389-.43A32.267 32.267 0 0 0 32.195 3.312m-.18 48.275a15.632 15.632 0 0 1-16.133-16.026 16.044 16.044 0 1 1 32.07 0 15.614 15.614 0 0 1-16.026 16.026"
      />
    </svg>
  );
}

function SlackBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 256 256"
      className={className}
      {...props}
    >
      <path fill="#E01E5A" d="M53.841 161.32c0 14.832-11.987 26.82-26.819 26.82S.203 176.152.203 161.32c0-14.831 11.987-26.818 26.82-26.818H53.84zm13.41 0c0-14.831 11.987-26.818 26.819-26.818s26.819 11.987 26.819 26.819v67.047c0 14.832-11.987 26.82-26.82 26.82c-14.83 0-26.818-11.988-26.818-26.82z" />
      <path fill="#36C5F0" d="M94.07 53.638c-14.832 0-26.82-11.987-26.82-26.819S79.239 0 94.07 0s26.819 11.987 26.819 26.819v26.82zm0 13.613c14.832 0 26.819 11.987 26.819 26.819s-11.987 26.819-26.82 26.819H26.82C11.987 120.889 0 108.902 0 94.069c0-14.83 11.987-26.818 26.819-26.818z" />
      <path fill="#2EB67D" d="M201.55 94.07c0-14.832 11.987-26.82 26.818-26.82s26.82 11.988 26.82 26.82s-11.988 26.819-26.82 26.819H201.55zm-13.41 0c0 14.832-11.988 26.819-26.82 26.819c-14.831 0-26.818-11.987-26.818-26.82V26.82C134.502 11.987 146.489 0 161.32 0s26.819 11.987 26.819 26.819z" />
      <path fill="#ECB22E" d="M161.32 201.55c14.832 0 26.82 11.987 26.82 26.818s-11.988 26.82-26.82 26.82c-14.831 0-26.818-11.988-26.818-26.82V201.55zm0-13.41c-14.831 0-26.818-11.988-26.818-26.82c0-14.831 11.987-26.818 26.819-26.818h67.25c14.832 0 26.82 11.987 26.82 26.819s-11.988 26.819-26.82 26.819z" />
    </svg>
  );
}

function LarkBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 25.37"
      className={className}
      {...props}
    >
      <path fill="#4BC0AE" d="m16.59 13.32.08-.08.27-.27.32-.32.83-.81.73-.72.48-.47c.29-.28.59-.54.91-.78.64-.51 1.36-.93 2.12-1.28.52-.25 1.06-.45 1.61-.62A18.8 18.8 0 0 0 20.39.86 1.72 1.72 0 0 0 19.05 0H5.37a.26.26 0 0 0-.16.47 36.9 36.9 0 0 1 11.34 12.89z" />
      <path fill="#4C6EB5" d="M11.15 25.37c7.07 0 13.23-3.9 16.43-9.66.12-.2.23-.41.33-.61-.21.42-.47.81-.75 1.18a7.2 7.2 0 0 1-2.21 1.92 7.6 7.6 0 0 1-3.2.82c-.67.03-1.35-.04-2.01-.2l-2.05-.62a33 33 0 0 1-4.62-1.77A38.8 38.8 0 0 1 .45 7.58a.26.26 0 0 0-.45.18v13.06c0 .57.28 1.1.75 1.42a18.75 18.75 0 0 0 10.4 3.13" />
      <path fill="#214295" d="M31.92 8.34a11.24 11.24 0 0 0-7.99-.6 12.4 12.4 0 0 0-4.03 2.31l-3.32 3.27a17.2 17.2 0 0 1-3.9 2.76 34.4 34.4 0 0 0 7.06 2.74c1.32.33 2.7.28 3.99-.17a7.5 7.5 0 0 0 3.43-2.37c.28-.37.53-.76.75-1.17l1.83-3.65a11.3 11.3 0 0 1 2.18-3.12" />
    </svg>
  );
}

// Model vendor marks Simple Icons ships but react-icons does not yet: the
// Simple Icons paths (CC0), the same marks the product site shows.
function QwenBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      {...props}
    >
      <path fill="#6950EF" d="M23.919 14.545 20.817 9.17l1.47-2.544a.56.56 0 0 0 0-.566l-1.633-2.83a.57.57 0 0 0-.49-.283h-6.207L12.487.402a.57.57 0 0 0-.49-.284H8.732a.56.56 0 0 0-.49.284L5.139 5.775h-2.94a.56.56 0 0 0-.49.284L.077 8.887a.56.56 0 0 0 0 .567L3.18 14.83l-1.47 2.545a.56.56 0 0 0 0 .566l1.634 2.83a.57.57 0 0 0 .49.283h6.205l1.47 2.545a.57.57 0 0 0 .49.284h3.266a.57.57 0 0 0 .49-.284l3.104-5.375h2.94a.57.57 0 0 0 .49-.283l1.634-2.828a.55.55 0 0 0-.004-.568M8.733.686l1.634 2.828-1.634 2.828H21.8L20.164 9.17H7.425L5.63 6.06Zm1.306 19.801-6.205-.002 1.634-2.83h3.265L2.201 6.344h3.267q3.182 5.517 6.367 11.032zm10.124-5.66L18.53 12l-6.532 11.315-1.634-2.83c2.129-3.673 4.25-7.351 6.373-11.028h3.592l3.102 5.374z" />
    </svg>
  );
}

function DeepSeekBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      {...props}
    >
      <path fill="#4D6BFE" d="M23.748 4.651c-.254-.124-.364.113-.512.233-.051.04-.094.09-.137.137-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.155-.708-.311-.955-.65-.172-.24-.219-.509-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.094.172.187.129.323-.082.28-.18.553-.266.833-.055.179-.137.218-.328.14a5.5 5.5 0 0 1-1.737-1.179c-.857-.828-1.631-1.743-2.597-2.46a12 12 0 0 0-.689-.47c-.985-.957.13-1.743.387-1.836.27-.098.094-.433-.778-.428-.872.003-1.67.295-2.687.685a3 3 0 0 1-.465.136 9.6 9.6 0 0 0-2.883-.101c-1.885.21-3.39 1.1-4.497 2.622C.082 8.776-.231 10.854.152 13.02c.403 2.284 1.568 4.175 3.36 5.653 1.857 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.132-.284 4.994-1.86.47.234.962.328 1.78.398.629.058 1.235-.031 1.705-.129.735-.155.684-.836.418-.961-2.155-1.004-1.682-.595-2.112-.926 1.095-1.295 2.768-3.598 3.284-6.733.05-.346.115-.834.108-1.114-.004-.171.035-.238.23-.257a4.2 4.2 0 0 0 1.545-.475c1.397-.763 1.96-2.016 2.093-3.517.02-.23-.004-.467-.247-.588M11.58 18.168c-2.088-1.642-3.101-2.183-3.52-2.16-.39.024-.32.472-.234.763.09.288.207.487.371.74.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.168-1.361-.801-2.5-1.86-3.301-3.306-.775-1.393-1.225-2.888-1.299-4.482-.02-.385.094-.522.477-.592a4.7 4.7 0 0 1 1.53-.038c2.131.311 3.946 1.264 5.467 2.774.868.86 1.525 1.887 2.202 2.89.72 1.066 1.494 2.082 2.48 2.915.348.291.626.513.892.677-.802.09-2.14.109-3.055-.615zm1.001-6.44a.306.306 0 0 1 .415-.287.3.3 0 0 1 .113.074.3.3 0 0 1 .086.214c0 .17-.136.307-.308.307a.303.303 0 0 1-.306-.307m3.11 1.596c-.2.081-.4.151-.591.16a1.25 1.25 0 0 1-.798-.254c-.274-.23-.47-.358-.551-.758a1.7 1.7 0 0 1 .015-.588c.07-.327-.007-.537-.238-.727-.188-.156-.426-.199-.689-.199a.6.6 0 0 1-.254-.078.253.253 0 0 1-.114-.358 1 1 0 0 1 .192-.21c.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.392.451.462.576.685.915.176.264.336.536.446.848.066.194-.02.353-.25.45" />
    </svg>
  );
}

// Kimi's mark is near black; currentColor keeps it visible in both themes.
function KimiBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      {...props}
    >
      <path fill="currentColor" d="M21.765.351C22.998.351 24 1.353 24 2.586S22.998 4.82 21.765 4.82h-1.974c-.15 0-.26-.12-.26-.26V2.586A2.237 2.237 0 0 1 21.765.35M9.41 13.388l8.447-8.377c.16-.16.07-.471-.14-.471h-4.55s-.1.02-.14.06l-9.099 9.029c-.14.14-.35.02-.35-.21V4.81c0-.15-.1-.27-.221-.27H.22c-.12 0-.22.12-.22.27v18.57c0 .15.1.27.22.27h3.137c.12 0 .22-.12.22-.27v-3.79c0-.08.03-.16.08-.21l2.826-2.796c.07-.07.16-.08.241-.03l7.546 5.551a8.9 8.9 0 0 0 4.018 1.493c.12.01.23-.11.23-.27V19.76c0-.14-.08-.25-.19-.26a5.8 5.8 0 0 1-2.355-.942l-6.533-4.73c-.14-.09-.15-.32-.03-.441" />
    </svg>
  );
}

// LM Studio's mark, the same path the product site shows for it.
function LMStudioBrandIcon({ size, className, ...props }: BrandIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      {...props}
    >
      <path fill="#7A5CF5" d="M5.6 0A5.6 5.6 0 0 0 0 5.6v12.8A5.6 5.6 0 0 0 5.6 24h12.8a5.6 5.6 0 0 0 5.6-5.6V5.6A5.6 5.6 0 0 0 18.4 0zm0 2h12.8A3.6 3.6 0 0 1 22 5.6v12.8a3.6 3.6 0 0 1-3.6 3.6H5.6A3.6 3.6 0 0 1 2 18.4V5.6A3.6 3.6 0 0 1 5.6 2m-.4 2.8a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm-3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4zm3.2 4a1.2 1.2 0 0 0 0 2.4h10.4a1.2 1.2 0 0 0 0-2.4z" />
    </svg>
  );
}

const INTEGRATION_ICONS: Record<string, Brand> = {
  ansible: { Icon: SiAnsible },
  argocd: { Icon: SiArgo, color: "#EF7B4D" },
  asana: { Icon: SiAsana, color: "#F06A6A" },
  azure_devops: { Icon: VscAzureDevops, color: "#0078D4" },
  azure_pipelines: { Icon: VscAzureDevops, color: "#0078D4" },
  bitbucket: { Icon: SiBitbucket, color: "#2684FF" },
  circleci: { Icon: SiCircleci },
  confluence: { Icon: SiConfluence, color: "#2684FF" },
  custom: { Icon: Globe },
  freshservice: { Icon: LifeBuoy },
  gitea: { Icon: SiGitea, color: "#609926" },
  github: { Icon: SiGithub },
  gitlab: { Icon: SiGitlab, color: "#FC6D26" },
  google_docs: { Icon: SiGoogledocs, color: "#4285F4" },
  jenkins: { Icon: SiJenkins, color: "#D33833" },
  jira: { Icon: SiJira, color: "#2684FF" },
  kubernetes: { Icon: SiKubernetes, color: "#326CE5" },
  linear: { Icon: SiLinear, color: "#5E6AD2" },
  newrelic: { Icon: SiNewrelic, color: "#00AC69" },
  notion: { Icon: SiNotion },
  sentry: { Icon: SiSentry, color: "#8C5CF4" },
  servicenow: { Icon: ServiceNowIcon },
  splunk: { Icon: SiSplunk },
  statuspage: { Icon: SiStatuspage, color: "#2684FF" },
  terraform_cloud: { Icon: SiTerraform, color: "#7B42BC" },
  zendesk: { Icon: SiZendesk },
};

const PLATFORM_ICONS: Record<string, Brand> = {
  telegram: { Icon: SiTelegram, color: "#26A5E4" },
  signal: { Icon: SiSignal, color: "#3A76F0" },
  whatsapp: { Icon: SiWhatsapp, color: "#25D366" },
  slack: { Icon: SlackBrandIcon },
  discord: { Icon: SiDiscord, color: "#5865F2" },
  google_chat: { Icon: SiGooglechat, color: "#34A853" },
  teams: { Icon: BiLogoMicrosoftTeams, color: "#6264A7" },
  teams_workflows: { Icon: BiLogoMicrosoftTeams, color: "#6264A7" },
  mattermost: { Icon: SiMattermost, color: "#0058CC" },
  matrix: { Icon: SiMatrix },
  feishu: { Icon: LarkBrandIcon },
  dingtalk: { Icon: AiOutlineDingding, color: "#0089FF" },
  wecom: { Icon: AiFillWechatWork, color: "#07C160" },
  weixin: { Icon: SiWechat, color: "#07C160" },
  twilio: { Icon: SiTwilio, color: "#F22F46" },
  email: { Icon: SiMailgun, color: "#F06B66" },
  smtp: { Icon: Mail },
  homeassistant: { Icon: SiHomeassistant, color: "#18BCF2" },
  bluebubbles: { Icon: SiApple },
  eventbridge: { Icon: Zap },
  custom: { Icon: Puzzle },
  // Voice Call is a paging channel, not a chat platform, but it shows up
  // alongside them in routing summaries.
  voice: { Icon: Phone },
};

const PROVIDER_ICONS: Record<string, Brand> = {
  anthropic: { Icon: SiAnthropic, color: "#D97757" },
  openai: { Icon: SiOpenai },
  ollama: { Icon: SiOllama },
  lmstudio: { Icon: LMStudioBrandIcon },
  vertex_ai: { Icon: SiGooglecloud, color: "#4285F4" },
  azure_openai: { Icon: VscAzure, color: "#0078D4" },
  bedrock: { Icon: FaAws, color: "#FF9900" },
  openai_compatible: { Icon: Boxes },
  qwen: { Icon: QwenBrandIcon },
  deepseek: { Icon: DeepSeekBrandIcon },
  kimi: { Icon: KimiBrandIcon },
};

function glyph(brand: Brand | undefined, fallback: IconComponent, size: number): ReactNode {
  const Icon = brand?.Icon ?? fallback;
  // Brand glyphs are always decorative - the kind/platform/provider name is
  // rendered as adjacent text wherever these appear. react-icons emits
  // role="img" with no name (an axe svg-img-alt violation); hiding the svg
  // from the accessibility tree is the correct treatment.
  return (
    <Icon
      size={size}
      color={brand?.color}
      className={brand?.color ? "shrink-0" : "shrink-0 text-fg-secondary"}
      aria-hidden="true"
    />
  );
}

/** Brand glyph for an integration connector kind (e.g. "github", "jira"). */
export function integrationKindIcon(kind: string, size = 16): ReactNode {
  return glyph(INTEGRATION_ICONS[kind], Plug, size);
}

/** Brand glyph for a notification platform (e.g. "slack", "telegram", "voice"). */
export function notificationPlatformIcon(platform: string, size = 16): ReactNode {
  return glyph(PLATFORM_ICONS[platform], Plug, size);
}

/** Brand glyph for a model provider (e.g. "anthropic", "openai", "bedrock"). */
export function modelProviderIcon(provider: string, size = 16): ReactNode {
  return glyph(PROVIDER_ICONS[provider], Boxes, size);
}
