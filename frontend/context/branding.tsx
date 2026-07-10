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
    let cancelled = false;
    const organizationId = user?.primary_org_id;
    if (!organizationId) {
      clearBrandingDocument();
      queueMicrotask(() => {
        if (!cancelled) {
          setBranding(null);
          setLoading(false);
        }
      });
      return () => {
        cancelled = true;
      };
    }

    queueMicrotask(() => {
      if (!cancelled) setLoading(true);
    });
    getOrganization(organizationId)
      .then((org) => {
        if (cancelled) return;
        setBranding(org.branding || null);
        if (org.branding) {
          applyBrandingDocument(org.branding);
        } else {
          clearBrandingDocument();
        }
      })
      .catch((err) => {
        if (cancelled) return;
        console.warn("Organization branding unavailable:", err);
        clearBrandingDocument();
        setBranding(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user?.primary_org_id]);

  return (
    <BrandingContext.Provider value={{ branding, loading }}>
      {children}
    </BrandingContext.Provider>
  );
}

function applyBrandingDocument(config: BrandingConfig) {
  document.title = config.company_name
    ? `${config.company_name} | OpsMender`
    : DEFAULT_DOCUMENT_TITLE;
  setFavicon(config.favicon_url || DEFAULT_FAVICON_HREF);
}

function clearBrandingDocument() {
  document.title = DEFAULT_DOCUMENT_TITLE;
  setFavicon(DEFAULT_FAVICON_HREF);
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
