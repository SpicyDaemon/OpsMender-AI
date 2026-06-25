"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Bell, ImageUp, Save, Trash2, UserCircle } from "lucide-react";
import { useAuth } from "@/context/auth";
import {
  changeMyPassword,
  deleteMyAvatar,
  updateMe,
  uploadMyAvatar,
} from "@/lib/api";
import {
  Avatar,
  AVATAR_COLOR_KEYS,
  AVATAR_PALETTE,
} from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { MFASettings } from "@/components/MFASettings";

export default function ProfileSettingsPage() {
  const { user, refresh } = useAuth();
  const toast = useToast();

  const [form, setForm] = useState({
    username: "",
    email: "",
    first_name: "",
    last_name: "",
    avatar_color: "" as string,
    phone: "",
  });
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");

  const avatarInputRef = useRef<HTMLInputElement>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);

  const [pw, setPw] = useState({ current_password: "", new_password: "", confirm: "" });
  const [savingPw, setSavingPw] = useState(false);
  const [pwError, setPwError] = useState("");

  // Initialize the form once per user identity. Keying on user.id (not the
  // whole object) avoids a re-sync loop if the context hands back a new user
  // reference, and preserves in-progress edits after refresh().
  useEffect(() => {
    if (!user) return;
    setForm({
      username: user.username,
      email: user.email,
      first_name: user.first_name ?? "",
      last_name: user.last_name ?? "",
      avatar_color: user.avatar_color ?? "",
      phone: user.phone ?? "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  if (!user) {
    return <div className="py-12 text-center text-fg-muted">Loading…</div>;
  }

  const previewUser = {
    username: form.username || user.username,
    first_name: form.first_name,
    last_name: form.last_name,
    avatar_color: form.avatar_color || null,
    avatar_url: user.avatar_url ?? null,
  };

  async function saveProfile() {
    if (!form.username.trim() || !form.email.trim()) {
      setProfileError("Username and email are required.");
      return;
    }
    setSavingProfile(true);
    setProfileError("");
    try {
      await updateMe({
        username: form.username.trim(),
        email: form.email.trim(),
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        avatar_color: form.avatar_color || null,
        phone: form.phone.trim() || null,
      });
      await refresh();
      toast.success("Profile updated");
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingProfile(false);
    }
  }

  async function savePassword() {
    if (pw.new_password.length < 8) {
      setPwError("New password must be at least 8 characters.");
      return;
    }
    if (pw.new_password !== pw.confirm) {
      setPwError("New password and confirmation don't match.");
      return;
    }
    setSavingPw(true);
    setPwError("");
    try {
      await changeMyPassword({
        current_password: pw.current_password,
        new_password: pw.new_password,
      });
      setPw({ current_password: "", new_password: "", confirm: "" });
      toast.success("Password changed");
    } catch (err) {
      setPwError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingPw(false);
    }
  }

  async function onAvatarSelected(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setAvatarBusy(true);
    try {
      await uploadMyAvatar(file);
      await refresh();
      toast.success("Profile picture updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setAvatarBusy(false);
    }
  }

  async function removeAvatar() {
    setAvatarBusy(true);
    try {
      await deleteMyAvatar();
      await refresh();
      toast.success("Profile picture removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setAvatarBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="Profile & Settings"
        subtitle="Manage your account details, avatar, and password."
        icon={<UserCircle size={18} />}
      />

      {/* Profile */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
        <div className="mb-5 flex items-center gap-4">
          <Avatar user={previewUser} size={56} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-base font-semibold text-fg-primary">
              {`${form.first_name} ${form.last_name}`.trim() || form.username || user.username}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <Badge variant="default">{user.role}</Badge>
              <span className="text-xs text-fg-muted">{user.auth_source || "local"}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input
                ref={avatarInputRef}
                type="file"
                accept=".png,.jpg,.jpeg,.gif,.bmp,.ico,.tiff,.tif,image/*"
                className="hidden"
                onChange={onAvatarSelected}
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => avatarInputRef.current?.click()}
                loading={avatarBusy}
              >
                <ImageUp size={14} /> {user.has_avatar ? "Change photo" : "Upload photo"}
              </Button>
              {user.has_avatar && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={removeAvatar}
                  disabled={avatarBusy}
                >
                  <Trash2 size={14} /> Remove
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-fg-muted">
              PNG, JPG, GIF, BMP, ICO, or TIFF up to 5 MB — resized to fit
              200×200.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="pf-first">First name</Label>
            <Input
              id="pf-first"
              value={form.first_name}
              onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              placeholder="Ada"
            />
          </div>
          <div>
            <Label htmlFor="pf-last">Last name</Label>
            <Input
              id="pf-last"
              value={form.last_name}
              onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              placeholder="Lovelace"
            />
          </div>
          <div>
            <Label htmlFor="pf-username" required>Username</Label>
            <Input
              id="pf-username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor="pf-email" required>Email</Label>
            <Input
              id="pf-email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="pf-phone">Phone (for SMS / Voice Call paging)</Label>
            <Input
              id="pf-phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              value={form.phone}
              // Accept only digits and a single leading "+". Strip anything else
              // as it is typed so the stored value is already clean.
              onChange={(e) => {
                const raw = e.target.value;
                const plus = raw.trimStart().startsWith("+") ? "+" : "";
                const digits = raw.replace(/[^0-9]/g, "");
                setForm({ ...form, phone: `${plus}${digits}` });
              }}
              placeholder="+14155550100"
            />
            <p className="mt-1 text-xs text-fg-muted">
              Optional. Digits and an optional leading “+” only. Used to reach
              you by SMS or an automated phone call when your paging routing
              includes those channels.
            </p>
          </div>
        </div>

        <div className="mt-4">
          <Label>Avatar color</Label>
          <div className="mt-2 flex flex-wrap gap-2">
            {AVATAR_COLOR_KEYS.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setForm({ ...form, avatar_color: key })}
                aria-label={`Avatar color ${key}`}
                title={key}
                className={`h-7 w-7 rounded-md ring-offset-2 ring-offset-bg-panel transition ${
                  form.avatar_color === key ? "ring-2 ring-accent" : "hover:opacity-80"
                }`}
                style={{ backgroundColor: AVATAR_PALETTE[key] }}
              />
            ))}
            <button
              type="button"
              onClick={() => setForm({ ...form, avatar_color: "" })}
              className={`h-7 rounded-md border border-border-strong px-2 text-xs text-fg-secondary transition hover:text-fg-primary ${
                form.avatar_color === "" ? "ring-2 ring-accent" : ""
              }`}
              title="Auto (derived from username)"
            >
              Auto
            </button>
          </div>
        </div>

        {profileError && <div className="mt-3"><FormError message={profileError} /></div>}

        <div className="mt-5 flex justify-end">
          <Button onClick={saveProfile} loading={savingProfile}>
            <Save size={14} /> Save profile
          </Button>
        </div>
      </section>

      {/* Password */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
        <h2 className="text-sm font-semibold text-fg-primary">Change password</h2>
        <p className="mt-0.5 text-sm text-fg-secondary">
          Use a strong password of at least 8 characters.
        </p>
        <div className="mt-4 space-y-4">
          <PasswordField
            label="Current password"
            value={pw.current_password}
            onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
            autoComplete="current-password"
          />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <PasswordField
              label="New password"
              value={pw.new_password}
              onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
              autoComplete="new-password"
            />
            <PasswordField
              label="Confirm new password"
              value={pw.confirm}
              onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
              autoComplete="new-password"
            />
          </div>
        </div>
        {pwError && <div className="mt-3"><FormError message={pwError} /></div>}
        <div className="mt-5 flex justify-end">
          <Button
            onClick={savePassword}
            loading={savingPw}
            disabled={!pw.current_password || !pw.new_password}
          >
            <Save size={14} /> Update password
          </Button>
        </div>
      </section>

      <MFASettings />

      {/* Notifications link */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm sm:p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-fg-primary">Notification preferences</h2>
            <p className="mt-0.5 text-sm text-fg-secondary">
              Choose how you're paged and which channels you receive.
            </p>
          </div>
          <Link
            href="/dashboard/paging/notifications"
            className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-surface px-3 py-2 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
          >
            <Bell size={14} /> Open
          </Link>
        </div>
      </section>
    </div>
  );
}
