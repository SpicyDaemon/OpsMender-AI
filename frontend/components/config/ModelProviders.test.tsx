import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  ProviderModelsListResponse,
} from "@/lib/types";

// GET /models as the server answers it when none of the vendor keys is set:
// the provider select is built from this list, not from a frontend table.
const GET_MODELS: ProviderModelsListResponse = {
  items: [
    {
      provider: "deepseek",
      label: "DeepSeek",
      default_model_id: "deepseek-flash",
      default_api_key_env_var: "DEEPSEEK_API_KEY",
      requires_api_key: true,
      requires_base_url: false,
      requires_api_version: false,
      available: false,
      models: [],
      error: "DEEPSEEK_API_KEY environment variable is not set.",
    },
    {
      provider: "kimi",
      label: "Kimi",
      default_model_id: "kimi-k3",
      default_api_key_env_var: "MOONSHOT_API_KEY",
      requires_api_key: true,
      requires_base_url: false,
      requires_api_version: false,
      available: false,
      models: [],
      error: "MOONSHOT_API_KEY environment variable is not set.",
    },
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
    {
      provider: "qwen",
      label: "Qwen",
      default_model_id: "qwen-plus",
      default_api_key_env_var: "DASHSCOPE_API_KEY",
      requires_api_key: true,
      requires_base_url: false,
      requires_api_version: false,
      available: false,
      models: [],
      error: "DASHSCOPE_API_KEY environment variable is not set.",
    },
  ],
  total: 4,
};

const BOOTSTRAP: ModelBootstrapStatusResponse = {
  needs_setup: true,
  has_configs: false,
  has_default: false,
  default_config: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.createModelConfig.mockResolvedValue({ config: {}, warnings: [] });
});

describe("Model provider select", () => {
  it("lists Qwen, DeepSeek and Kimi when the server reports them", async () => {
    render(
      <ModelSection
        bootstrap={BOOTSTRAP}
        providers={GET_MODELS.items}
        configs={[]}
        onReload={async () => {}}
        canEdit
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /new model/i }));
    fireEvent.click(screen.getByLabelText("Provider"));

    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "DeepSeek",
      "Kimi",
      "Ollama",
      "Qwen",
    ]);
    for (const option of options) {
      expect(option.querySelector("svg"), option.textContent ?? "").not.toBeNull();
    }

    // Picking a vendor fills its model and its own key variable; no base URL
    // field is needed, since the server knows the vendor's URL.
    fireEvent.click(within(screen.getByRole("listbox")).getByText("DeepSeek"));
    expect(screen.getByLabelText("Model ID")).toHaveProperty(
      "value",
      "deepseek-flash",
    );
    expect(screen.getByLabelText(/API Key Env Var/)).toHaveProperty(
      "value",
      "DEEPSEEK_API_KEY",
    );
    expect(screen.queryByLabelText("Base URL")).toBeNull();

    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "deepseek-primary" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create config/i }));

    await waitFor(() =>
      expect(apiMocks.createModelConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "deepseek-primary",
          provider: "deepseek",
          model_id: "deepseek-flash",
          api_key_env_var: "DEEPSEEK_API_KEY",
          base_url: undefined,
        }),
      ),
    );
  });
});
