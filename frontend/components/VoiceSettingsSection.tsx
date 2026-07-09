"use client";

import { useEffect, useState } from "react";
import { getVoiceSettings, updateVoiceSettings } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Input";
import { Toggle } from "@/components/ui/Toggle";
import type { VoiceSettingsResponse } from "@/lib/types";

type VoiceForm = {
  enabled: boolean;
  account_sid: string;
  auth_token: string;
  sms_from_number: string;
  voice_from_number: string;
};

function emptyForm(): VoiceForm {
  return {
    enabled: true,
    account_sid: "",
    auth_token: "",
    sms_from_number: "",
    voice_from_number: "",
  };
}

export function VoiceSettingsSection() {
  const [form, setForm] = useState<VoiceForm>(() => emptyForm());
  const [configured, setConfigured] = useState(false);
  const [authTokenSet, setAuthTokenSet] = useState(false);
  const [source, setSource] = useState<VoiceSettingsResponse["source"]>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  function applySettings(settings: VoiceSettingsResponse) {
    setForm({
      enabled: settings.enabled,
      account_sid: settings.account_sid,
      auth_token: "",
      sms_from_number: settings.sms_from_number,
      voice_from_number: settings.voice_from_number ?? "",
    });
    setConfigured(settings.configured);
    setAuthTokenSet(settings.auth_token_set);
    setSource(settings.source);
  }

  useEffect(() => {
    getVoiceSettings()
      .then(applySettings)
      .catch(() => {
        setConfigured(false);
        setSource(null);
      });
  }, []);

  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const saved = await updateVoiceSettings({
        enabled: form.enabled,
        account_sid: form.account_sid || null,
        auth_token: form.auth_token || undefined,
        sms_from_number: form.sms_from_number || null,
        voice_from_number: form.voice_from_number || null,
      });
      applySettings(saved);
      setNotice("Voice & SMS calling saved.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-fg-primary">
            Voice &amp; SMS calling
          </h2>
          {configured ? (
            <span className="inline-flex items-center gap-1 rounded-pill border border-status-low-border bg-status-low-bg px-2 py-0.5 text-[11px] font-medium text-status-low">
              Configured · {source === "database" ? "saved here" : "from environment"}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-muted">
              Not configured
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-fg-secondary">
          Controls SMS delivery and automated voice-call routing for this
          workspace. Matching OPSMENDER_TWILIO_* environment variables still
          seed fresh instances.
        </p>
      </div>

      <div className="flex items-center justify-between gap-4 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3">
        <div>
          <Label htmlFor="voice-enabled">Calling enabled</Label>
          <p className="text-xs text-fg-muted">
            When off, saved database settings are ignored and env bootstrap can
            still apply.
          </p>
        </div>
        <Toggle
          id="voice-enabled"
          checked={form.enabled}
          aria-label="Calling enabled"
          onChange={(enabled) => setForm({ ...form, enabled })}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <Label htmlFor="voice-account-sid">Account SID</Label>
          <Input
            id="voice-account-sid"
            value={form.account_sid}
            onChange={(e) => setForm({ ...form, account_sid: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="voice-auth-token">Auth token</Label>
          <Input
            id="voice-auth-token"
            type="password"
            value={form.auth_token}
            placeholder={authTokenSet ? "••• configured" : ""}
            onChange={(e) => setForm({ ...form, auth_token: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="voice-sms-from">SMS from number</Label>
          <Input
            id="voice-sms-from"
            value={form.sms_from_number}
            onChange={(e) =>
              setForm({ ...form, sms_from_number: e.target.value })
            }
            placeholder="+15551234567"
          />
        </div>
        <div>
          <Label htmlFor="voice-call-from">Voice from number</Label>
          <Input
            id="voice-call-from"
            value={form.voice_from_number}
            onChange={(e) =>
              setForm({ ...form, voice_from_number: e.target.value })
            }
            placeholder="Falls back to the SMS number"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={save}
          loading={saving}
          disabled={!form.account_sid || !form.sms_from_number}
        >
          Save calling settings
        </Button>
        {notice && <p className="text-sm text-fg-secondary">{notice}</p>}
      </div>
    </section>
  );
}
