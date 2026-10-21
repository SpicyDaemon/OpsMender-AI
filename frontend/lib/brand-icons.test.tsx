import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import {
  integrationKindIcon,
  modelProviderIcon,
  notificationPlatformIcon,
} from "./brand-icons";

const INTEGRATION_KINDS = [
  "github", "gitlab", "gitea", "bitbucket", "azure_devops", "jira",
  "confluence", "servicenow", "linear", "notion", "google_docs", "jenkins",
  "circleci", "azure_pipelines", "terraform_cloud", "argocd", "ansible",
  "statuspage", "sentry", "newrelic", "splunk", "kubernetes", "zendesk",
  "freshservice", "asana",
];

const NOTIFICATION_PLATFORMS = [
  "telegram", "signal", "whatsapp", "slack", "discord", "google_chat",
  "teams", "mattermost", "matrix", "feishu", "dingtalk", "wecom", "weixin",
  "twilio", "email", "smtp", "homeassistant", "bluebubbles", "eventbridge",
];

const MODEL_PROVIDERS = [
  "anthropic", "openai", "azure_openai", "bedrock", "vertex_ai", "ollama",
  "qwen", "deepseek", "kimi",
];

function markup(node: ReactNode): string {
  return renderToStaticMarkup(<>{node}</>);
}

describe("brand icon registry", () => {
  it("provides a non-fallback icon for every integration option", () => {
    const fallback = markup(integrationKindIcon("not-a-kind"));
    for (const kind of INTEGRATION_KINDS) {
      expect(markup(integrationKindIcon(kind)), kind).not.toBe(fallback);
    }
  });

  it("provides a non-fallback icon for every notification platform", () => {
    const fallback = markup(notificationPlatformIcon("not-a-platform"));
    for (const platform of NOTIFICATION_PLATFORMS) {
      expect(markup(notificationPlatformIcon(platform)), platform).not.toBe(fallback);
    }
  });

  it("provides a non-fallback icon for every named model provider", () => {
    const fallback = markup(modelProviderIcon("not-a-provider"));
    for (const provider of MODEL_PROVIDERS) {
      expect(markup(modelProviderIcon(provider)), provider).not.toBe(fallback);
    }
  });

  it("draws the Qwen, DeepSeek and Kimi marks", () => {
    const qwen = markup(modelProviderIcon("qwen", 20));
    expect(qwen).toContain('fill="#6950EF"');
    expect(qwen).toContain('d="M23.919 14.545');
    expect(qwen).toContain('width="20"');
    expect(qwen).toContain('aria-hidden="true"');

    const deepseek = markup(modelProviderIcon("deepseek"));
    expect(deepseek).toContain('fill="#4D6BFE"');
    expect(deepseek).toContain('d="M23.748 4.651');

    // Kimi's near-black mark follows the text color in both themes.
    const kimi = markup(modelProviderIcon("kimi"));
    expect(kimi).toContain('fill="currentColor"');
    expect(kimi).toContain('d="M21.765.351');
    expect(kimi).toContain("text-fg-secondary");
  });

  it("renders the corrected brand marks", () => {
    expect(markup(integrationKindIcon("servicenow"))).toContain("#81B5A1");
    expect(markup(notificationPlatformIcon("slack"))).toContain("#36C5F0");
    expect(markup(notificationPlatformIcon("teams"))).toContain("#6264A7");
    expect(markup(notificationPlatformIcon("feishu"))).toContain("#4BC0AE");
    expect(markup(notificationPlatformIcon("dingtalk"))).toContain("#0089FF");
    expect(markup(notificationPlatformIcon("wecom"))).toContain("#07C160");
  });
});
