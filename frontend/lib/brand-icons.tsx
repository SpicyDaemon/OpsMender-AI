import type { ComponentType, ReactNode } from "react";
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
  SiSlack,
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
  Cloud,
  Globe,
  LifeBuoy,
  Mail,
  MessageSquare,
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
type IconComponent = ComponentType<{
  size?: number;
  color?: string;
  className?: string;
  "aria-hidden"?: boolean | "true" | "false";
}>;
type Brand = { Icon: IconComponent; color?: string };

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
  servicenow: { Icon: Cloud },
  splunk: { Icon: SiSplunk },
  statuspage: { Icon: SiStatuspage, color: "#2684FF" },
  terraform_cloud: { Icon: SiTerraform, color: "#7B42BC" },
  zendesk: { Icon: SiZendesk },
};

const PLATFORM_ICONS: Record<string, Brand> = {
  telegram: { Icon: SiTelegram, color: "#26A5E4" },
  signal: { Icon: SiSignal, color: "#3A76F0" },
  whatsapp: { Icon: SiWhatsapp, color: "#25D366" },
  slack: { Icon: SiSlack },
  discord: { Icon: SiDiscord, color: "#5865F2" },
  google_chat: { Icon: SiGooglechat, color: "#34A853" },
  teams: { Icon: MessageSquare },
  mattermost: { Icon: SiMattermost, color: "#0058CC" },
  matrix: { Icon: SiMatrix },
  feishu: { Icon: MessageSquare },
  dingtalk: { Icon: MessageSquare },
  wecom: { Icon: MessageSquare },
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
