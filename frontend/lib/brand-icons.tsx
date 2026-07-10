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
  vertex_ai: { Icon: SiGooglecloud, color: "#4285F4" },
  azure_openai: { Icon: VscAzure, color: "#0078D4" },
  bedrock: { Icon: FaAws, color: "#FF9900" },
  openai_compatible: { Icon: Boxes },
};

function glyph(brand: Brand | undefined, fallback: IconComponent, size: number): ReactNode {
  const Icon = brand?.Icon ?? fallback;
  // Brand glyphs are always decorative — the kind/platform/provider name is
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
