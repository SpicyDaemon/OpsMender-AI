import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  createModelConfig: vi.fn(),
  updateModelConfigById: vi.fn(),
  deleteModelConfig: vi.fn(),
  setDefaultModelConfig: vi.fn(),
  testModelConfig: vi.fn(),
  toggleModelConfigActive: vi.fn(),
  listProvidersWithParams: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import { ModelSection } from "@/components/config/ConfigSections";
import type {
  ModelBootstrapStatusResponse,
  ProviderModelsResponse,
} from "@/lib/types";

const PROVIDERS: ProviderModelsResponse[] = [
  {
    provider: "ollama",
    label: "Ollama",
    default_model_id: "llama3.2",
    default_api_key_env_var: null,
    requires_api_key: false,
    requires_base_url: false,
    requires_api_version: false,
    available: true,
    models: ["llama3.2"],
    error: null,
  },
];

const BOOTSTRAP: ModelBootstrapStatusResponse = {
  needs_setup: true,
  has_configs: false,
  has_default: false,
  default_config: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.createModelConfig.mockResolvedValue({
    config: {},
    warnings: [],
  });
});

describe("Model config capacity", () => {
  it("saves a per-model concurrent-session cap and explains zero", async () => {
    render(
      <ModelSection
        bootstrap={BOOTSTRAP}
        providers={PROVIDERS}
        configs={[]}
        onReload={async () => {}}
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /add model config/i }));
    expect(screen.getByText(/0 means unlimited/i)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "local-primary" },
    });
    fireEvent.change(screen.getByLabelText("Concurrent Sessions"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create config/i }));

    await waitFor(() =>
      expect(apiMocks.createModelConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "local-primary",
          model_id: "llama3.2",
          max_concurrent_sessions: 3,
        }),
      ),
    );
  });
});
