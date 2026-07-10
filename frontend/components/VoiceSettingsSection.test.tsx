import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceSettingsSection } from "@/components/VoiceSettingsSection";

const apiMocks = vi.hoisted(() => ({
  getVoiceSettings: vi.fn(),
  updateVoiceSettings: vi.fn(),
  testVoiceSettings: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

beforeEach(() => {
  apiMocks.getVoiceSettings.mockResolvedValue({
    configured: true,
    enabled: true,
    account_sid: "AC123",
    auth_token_set: true,
    sms_from_number: "+15551111111",
    voice_from_number: "+15552222222",
    source: "database",
  });
  apiMocks.updateVoiceSettings.mockImplementation(async (body) => ({
    configured: true,
    enabled: body.enabled,
    account_sid: body.account_sid,
    auth_token_set: true,
    sms_from_number: body.sms_from_number,
    voice_from_number: body.voice_from_number,
    source: "database",
  }));
});

describe("VoiceSettingsSection", () => {
  it("masks the token and preserves it when saving without a replacement", async () => {
    render(<VoiceSettingsSection />);

    await waitFor(() => {
      const accountSid = screen.getByLabelText(
        "Account SID",
      ) as HTMLInputElement;
      expect(accountSid.value).toBe("AC123");
    });
    const authToken = screen.getByLabelText("Auth token") as HTMLInputElement;
    expect(authToken.placeholder).toBe("••• configured");

    fireEvent.change(screen.getByLabelText("SMS from number"), {
      target: { value: "+15553333333" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save calling settings" }));

    await waitFor(() =>
      expect(apiMocks.updateVoiceSettings).toHaveBeenCalledWith({
        enabled: true,
        account_sid: "AC123",
        auth_token: undefined,
        sms_from_number: "+15553333333",
        voice_from_number: "+15552222222",
      }),
    );
    expect(screen.getByText("Calling and SMS settings saved.")).toBeTruthy();
  });
});
