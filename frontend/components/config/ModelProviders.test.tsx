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
  ProviderModelsResponse,
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
      default_base_url: "https://api.deepseek.com",
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
      default_base_url: "https://api.moonshot.ai/v1",
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
      default_base_url: null,
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
      default_base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
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

    // Picking a vendor fills its model and its own key variable; the Base URL
    // field stays empty with the vendor's URL as its hint, since the server
    // knows it.
    fireEvent.click(within(screen.getByRole("listbox")).getByText("DeepSeek"));
    expect(screen.getByLabelText("Model ID")).toHaveProperty(
      "value",
      "deepseek-flash",
    );
    expect(screen.getByLabelText(/API Key Env Var/)).toHaveProperty(
      "value",
      "DEEPSEEK_API_KEY",
    );
    expect(screen.getByLabelText("Base URL")).toHaveProperty("value", "");
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "placeholder",
      "https://api.deepseek.com",
    );

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

// GET /models with LM Studio not running: the server reports it unavailable
// with the reason, and the form still offers it with its local address.
const LM_STUDIO_DOWN: ProviderModelsResponse = {
  provider: "lmstudio",
  label: "LM Studio",
  default_model_id: "openai/gpt-oss-20b",
  default_api_key_env_var: null,
  default_base_url: "http://localhost:1234/v1",
  requires_api_key: false,
  requires_base_url: false,
  requires_api_version: false,
  available: false,
  models: [],
  error: "LM Studio list_models failed: Connection error.",
};

const ANTHROPIC: ProviderModelsResponse = {
  provider: "anthropic",
  label: "Anthropic",
  default_model_id: "sonnet-4",
  default_api_key_env_var: "ANTHROPIC_API_KEY",
  default_base_url: null,
  requires_api_key: true,
  requires_base_url: false,
  requires_api_version: false,
  available: true,
  models: ["sonnet-4"],
  error: null,
};

const QWEN = GET_MODELS.items.find((item) => item.provider === "qwen")!;

function openNewModelForm(providers: ProviderModelsResponse[]) {
  render(
    <ModelSection
      bootstrap={BOOTSTRAP}
      providers={providers}
      configs={[]}
      onReload={async () => {}}
      canEdit
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /new model/i }));
}

function pickProvider(label: string) {
  fireEvent.click(screen.getByLabelText("Provider"));
  fireEvent.click(within(screen.getByRole("listbox")).getByText(label));
}

describe("Base URL field", () => {
  it("shows a provider's default URL as the placeholder and hides the field without one", () => {
    openNewModelForm([ANTHROPIC, LM_STUDIO_DOWN, QWEN]);

    // Anthropic (the first provider) has neither a required nor a default URL.
    expect(screen.queryByLabelText("Base URL")).toBeNull();

    pickProvider("LM Studio");
    const lmStudioUrl = screen.getByLabelText("Base URL");
    expect(lmStudioUrl).toHaveProperty("value", "");
    expect(lmStudioUrl).toHaveProperty("placeholder", "http://localhost:1234/v1");
    // No key needed, and the help names no other runtime.
    expect(screen.getByLabelText(/API Key Env Var/)).toHaveProperty("value", "");
    expect(
      screen.getByText(
        "Store the secret in `.env`; this field saves the variable name only. Leave blank unless the endpoint requires a key.",
      ),
    ).toBeTruthy();

    pickProvider("Qwen");
    expect(screen.getByLabelText("Base URL")).toHaveProperty(
      "placeholder",
      "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    );

    pickProvider("Anthropic");
    expect(screen.queryByLabelText("Base URL")).toBeNull();
  });

  it("saves an LM Studio config that nothing answers for and shows the warning", async () => {
    const warning =
      "Could not verify provider connectivity during validation: LM Studio list_models failed: Connection error. Saving anyway because explicit bootstrap inputs are allowed.";
    apiMocks.createModelConfig.mockResolvedValue({
      config: {},
      warnings: [{ code: "provider_unverified", message: warning }],
    });
    openNewModelForm([ANTHROPIC, LM_STUDIO_DOWN]);

    pickProvider("LM Studio");
    // Nothing listed, so the model is typed; it starts at the default.
    expect(screen.getByLabelText("Model ID")).toHaveProperty(
      "value",
      "openai/gpt-oss-20b",
    );
    fireEvent.change(screen.getByLabelText("Model ID"), {
      target: { value: "qwen/qwen3-8b" },
    });
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "lm-studio" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create config/i }));

    await waitFor(() =>
      expect(apiMocks.createModelConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "lm-studio",
          provider: "lmstudio",
          model_id: "qwen/qwen3-8b",
          api_key_env_var: undefined,
          base_url: undefined,
        }),
      ),
    );
    expect(await screen.findByText(warning)).toBeTruthy();
  });

  it("starts a reachable LM Studio on the first model it lists", async () => {
    openNewModelForm([
      ANTHROPIC,
      {
        ...LM_STUDIO_DOWN,
        available: true,
        models: ["qwen/qwen3-8b", "google/gemma-3-12b"],
        error: null,
      },
    ]);

    pickProvider("LM Studio");
    expect(screen.getByLabelText("Model ID")).toHaveProperty(
      "value",
      "qwen/qwen3-8b",
    );
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "lm-studio" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create config/i }));

    await waitFor(() =>
      expect(apiMocks.createModelConfig).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "lmstudio", model_id: "qwen/qwen3-8b" }),
      ),
    );
  });
});

// GET /models for the generic endpoint with no OPSMENDER_OPENAI_COMPATIBLE_*
// set: it needs a typed URL and takes a key only when the endpoint asks.
const OPENAI_COMPATIBLE: ProviderModelsResponse = {
  provider: "openai_compatible",
  label: "OpenAI-compatible",
  default_model_id: "gpt-4o",
  default_api_key_env_var: null,
  default_base_url: null,
  requires_api_key: false,
  requires_base_url: true,
  requires_api_version: false,
  available: false,
  models: [],
  error: "openai_compatible provider requires a base_url",
};

const DEEPSEEK = GET_MODELS.items.find((item) => item.provider === "deepseek")!;

const KEY_HELP =
  "Store the secret in `.env`; this field saves the variable name only.";

describe("API key help", () => {
  it("names vLLM and llama.cpp in the key help for the compatible provider only", () => {
    openNewModelForm([ANTHROPIC, OPENAI_COMPATIBLE, LM_STUDIO_DOWN]);

    pickProvider("OpenAI-compatible");
    expect(
      screen.getByText(
        `${KEY_HELP} Leave blank for an endpoint that does not require auth, such as a local vLLM or llama.cpp server.`,
      ),
    ).toBeTruthy();

    pickProvider("LM Studio");
    expect(screen.queryByText(/vLLM|llama\.cpp/)).toBeNull();
  });

  it("offers no blank key in the help for a provider that needs one", () => {
    openNewModelForm([ANTHROPIC, DEEPSEEK, LM_STUDIO_DOWN]);

    pickProvider("DeepSeek");
    expect(screen.getByText(KEY_HELP)).toBeTruthy();
    expect(screen.queryByText(/Leave blank/)).toBeNull();
  });
});
