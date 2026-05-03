"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "./auth";
import { getOrganization } from "@/lib/api";
import type { BrandingConfig } from "@/lib/types";

interface BrandingContextValue {
  branding: BrandingConfig | null;
  loading: boolean;
}

const BrandingContext = createContext<BrandingContextValue | null>(null);

export function BrandingProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [branding, setBranding] = useState<BrandingConfig | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!user?.primary_org_id) {
      setBranding(null);
      return;
    }

    setLoading(true);
    getOrganization(user.primary_org_id)
      .then((org) => {
        setBranding(org.branding || null);
        if (org.branding) {
          applyBranding(org.branding);
        } else {
          clearBranding();
        }
      })
      .catch((err) => {
        console.error("Failed to load organization branding:", err);
        clearBranding();
      })
      .finally(() => setLoading(false));
  }, [user?.primary_org_id]);

  const applyBranding = (config: BrandingConfig) => {
    const root = document.documentElement;
    if (config.primary_color) {
      root.style.setProperty("--primary", config.primary_color);
      // Generate hover/focus variants if needed, or just let CSS do its thing
    }
    if (config.secondary_color) {
      root.style.setProperty("--secondary", config.secondary_color);
    }
    
    // Update Document Title
    if (config.company_name) {
      // Note: This only works for the current tab title, 
      // Next.js Metadata might override it on page transitions.
      // But it's a good fallback for the initial load.
      document.title = `${config.company_name} | AIM`;
    }

    // Update Favicon
    if (config.favicon_url) {
      let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.getElementsByTagName('head')[0].appendChild(link);
      }
      link.href = config.favicon_url;
    }
  };

  const clearBranding = () => {
    const root = document.documentElement;
    root.style.removeProperty("--primary");
    root.style.removeProperty("--secondary");
    setBranding(null);
  };

  return (
    <BrandingContext.Provider value={{ branding, loading }}>
      {children}
    </BrandingContext.Provider>
  );
}

export function useBranding() {
  const ctx = useContext(BrandingContext);
  if (!ctx) throw new Error("useBranding must be used inside <BrandingProvider>");
  return ctx;
}
