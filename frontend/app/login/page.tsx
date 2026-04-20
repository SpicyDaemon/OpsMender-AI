"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { Input, Label, FormError } from "@/components/ui/Input";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      router.push("/dashboard/incidents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex items-center justify-center gap-2 mb-8">
          <ShieldCheck size={32} className="text-accent" />
          <span className="text-xl font-bold text-fg-primary">AI Incident Manager</span>
        </div>

        <div className="rounded-xl bg-bg-panel shadow-sm ring-1 ring-border-subtle px-8 py-8">
          <h1 className="text-lg font-semibold text-fg-primary mb-6">Sign in to your account</h1>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
              />
            </div>

            <div>
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            {error && <FormError message={error} />}

            <Button type="submit" loading={loading} className="w-full justify-center">
              Sign in
            </Button>
          </form>
        </div>

        <p className="mt-4 text-center text-sm text-fg-secondary">
          No account?{" "}
          <Link href="/register" className="text-accent hover:underline font-medium">
            Register
          </Link>
        </p>
      </div>
    </div>
  );
}
