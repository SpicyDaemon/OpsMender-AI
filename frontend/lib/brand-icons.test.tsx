import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { integrationKindIcon, notificationPlatformIcon } from "./brand-icons";

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

  it("renders the corrected brand marks", () => {
    expect(markup(integrationKindIcon("servicenow"))).toContain("#81B5A1");
    expect(markup(notificationPlatformIcon("slack"))).toContain("#36C5F0");
    expect(markup(notificationPlatformIcon("teams"))).toContain("#6264A7");
    expect(markup(notificationPlatformIcon("feishu"))).toContain("#4BC0AE");
    expect(markup(notificationPlatformIcon("dingtalk"))).toContain("#0089FF");
    expect(markup(notificationPlatformIcon("wecom"))).toContain("#07C160");
  });
});
