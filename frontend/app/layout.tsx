import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/auth";
import { BrandingProvider } from "@/context/branding";
import { ThemeProvider } from "@/context/theme";
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
    default: "OpsMender",
    template: "%s | OpsMender",
  },
  description:
    "Open-source incident response with tiered access controls for AI agents. Connect agents to your infrastructure through MCP servers.",
  icons: {
    icon: { url: "/OpsMender-Dark.png", type: "image/png", sizes: "605x588" },
    apple: { url: "/OpsMender-Dark.png", sizes: "180x180" },
  },
  openGraph: {
    title: "OpsMender",
    description:
      "Open-source incident response with tiered access controls for AI agents.",
    type: "website",
    images: [{ url: "/OpsMender-Dark.png", width: 605, height: 588, alt: "OpsMender logo" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "OpsMender",
    description:
      "Open-source incident response with tiered access controls for AI agents.",
    images: ["/OpsMender-Dark.png"],
  },
};

const THEME_INIT_SCRIPT = `
(() => {
  try {
    const stored = localStorage.getItem("opsmender:theme");
    const mode =
      stored === "light" || stored === "dark" || stored === "system"
        ? stored
        : "system";
    const resolved =
      mode === "system"
        ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
        : mode;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  } catch {
    document.documentElement.dataset.theme = "dark";
    document.documentElement.style.colorScheme = "dark";
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full`}
      suppressHydrationWarning
    >
      <body className="h-full">
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <ThemeProvider>
          <AuthProvider>
            <BrandingProvider>
              <ToastProvider>{children}</ToastProvider>
            </BrandingProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
