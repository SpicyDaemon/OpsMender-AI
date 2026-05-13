import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/auth";
import { BrandingProvider } from "@/context/branding";
import { ToastProvider } from "@/components/ui/Toast";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Opsmender — Opsmender AI",
    template: "%s | Opsmender",
  },
  description:
    "Open-source AI-powered incident response framework with tiered access controls. Connect AI agents to infrastructure via MCP servers.",
  icons: {
    icon: { url: "/opsmender-v2.png", type: "image/png", sizes: "512x512" },
    apple: { url: "/opsmender-v2.png", sizes: "180x180" },
  },
  openGraph: {
    title: "Opsmender — Opsmender AI",
    description:
      "Open-source AI-powered incident response framework with tiered access controls.",
    type: "website",
    images: [{ url: "/og-image.png", width: 1200, height: 630, alt: "Opsmender — Opsmender AI" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Opsmender — Opsmender AI",
    description:
      "Open-source AI-powered incident response framework with tiered access controls.",
    images: ["/og-image.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full`}
    >
      <body className="h-full">
        <AuthProvider>
          <BrandingProvider>
            <ToastProvider>{children}</ToastProvider>
          </BrandingProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
