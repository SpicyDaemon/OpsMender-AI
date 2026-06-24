"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { register } from "@/lib/api";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { FormAlert, Input, Label } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";

export default function RegisterPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({
    email: "",
    password: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function set(field: string, value: string) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await register(form.email, form.password);
      // Auto-login after registration
      await login(form.email, form.password);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Create an OpsMender account"
      description="Set up access for operators, reviewers, or admins and jump straight into the dashboard."
      footer={(
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </>
      )}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <FormAlert message={error} />}
        <div>
          <Label htmlFor="email" required>Email</Label>
          <Input
            id="email"
            type="email"
            name="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            placeholder="john@example.com"
          />
        </div>

        <PasswordField
          id="password"
          name="password"
          label="Password"
          autoComplete="new-password"
          required
          minLength={8}
          value={form.password}
          onChange={(e) => set("password", e.target.value)}
          placeholder="Minimum 8 characters"
        />

        <Button type="submit" loading={loading} className="w-full justify-center">
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}
