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

const DEFAULT_DOCUMENT_TITLE = "OpsMender — OpsMender AI";
const DEFAULT_FAVICON_HREF = "/OpsMender-Dark.png";

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
      clearBranding();
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
    if (config.company_name) {
      // Note: This only works for the current tab title,
      // Next.js Metadata might override it on page transitions.
      // But it's a good fallback for the initial load.
      document.title = `${config.company_name} | OpsMender`;
    } else {
      document.title = DEFAULT_DOCUMENT_TITLE;
    }

    if (config.favicon_url) {
      setFavicon(config.favicon_url);
    } else {
      setFavicon(DEFAULT_FAVICON_HREF);
    }
  };

  const clearBranding = () => {
    document.title = DEFAULT_DOCUMENT_TITLE;
    setFavicon(DEFAULT_FAVICON_HREF);
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

function setFavicon(href: string) {
  let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = href;
}
