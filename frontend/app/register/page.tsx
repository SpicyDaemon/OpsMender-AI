"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { register } from "@/lib/api";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";

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
      router.push("/dashboard/incidents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-center gap-2 mb-8">
          <ShieldCheck size={32} className="text-accent" />
          <span className="text-xl font-bold text-fg-primary">AI Incident Manager</span>
        </div>

        <div className="rounded-xl bg-bg-panel shadow-sm ring-1 ring-border-subtle px-8 py-8">
          <h1 className="text-lg font-semibold text-fg-primary mb-6">Create an account</h1>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                type="text"
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
                required
                value={form.email}
                onChange={(e) => set("email", e.target.value)}
                placeholder="john@example.com"
              />
            </div>

            <div>
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                required
                minLength={8}
                value={form.password}
                onChange={(e) => set("password", e.target.value)}
                placeholder="••••••••"
              />
            </div>

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
        </div>

        <p className="mt-4 text-center text-sm text-fg-secondary">
          Already have an account?{" "}
          <Link href="/login" className="text-accent hover:underline font-medium">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
