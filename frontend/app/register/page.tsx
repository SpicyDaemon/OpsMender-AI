"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { register } from "@/lib/api";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { FormError, Input, Label, Select } from "@/components/ui/Input";
import { PasswordField } from "@/components/ui/PasswordField";

export default function RegisterPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({
    username: "",
    email: "",
    password: "",
    role: "viewer" as "admin" | "operator" | "viewer",
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
      await register(form.username, form.email, form.password, form.role);
      // Auto-login after registration
      await login(form.username, form.password);
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
        <div>
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            type="text"
            name="username"
            autoComplete="username"
            required
            minLength={3}
            value={form.username}
            onChange={(e) => set("username", e.target.value)}
            placeholder="johndoe"
          />
        </div>

        <div>
          <Label htmlFor="email">Email</Label>
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

        <div>
          <Label htmlFor="role">Role</Label>
          <Select
            id="role"
            value={form.role}
            onChange={(e) => set("role", e.target.value)}
          >
            <option value="viewer">Viewer</option>
            <option value="operator">Operator</option>
            <option value="admin">Admin</option>
          </Select>
        </div>

        {error && <FormError message={error} />}

        <Button type="submit" loading={loading} className="w-full justify-center">
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}
