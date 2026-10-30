/**
 * M1-116 (F6): a live stream offers its session token in the WebSocket
 * handshake's subprotocol list and never in the URL, so the access log and
 * any proxy see a plain path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  STREAM_BEARER_PREFIX,
  STREAM_SUBPROTOCOL,
  connectNotificationStream,
  connectSessionStream,
  setToken,
  streamProtocols,
} from "@/lib/api";

const opened: { url: string; protocols: string[] | undefined }[] = [];

class FakeSocket {
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string, protocols?: string | string[]) {
    opened.push({ url, protocols: Array.isArray(protocols) ? protocols : protocols ? [protocols] : undefined });
  }
}

beforeEach(() => {
  opened.length = 0;
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  });
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("stream token transport", () => {
  it("offers the token as a subprotocol and keeps it out of the URL", () => {
    setToken("unit.session.jwt");

    connectSessionStream("11111111-1111-4111-8111-111111111111", () => {});
    connectNotificationStream({ onNotification: () => {} });

    expect(opened).toHaveLength(2);
    for (const socket of opened) {
      expect(socket.url).not.toContain("token");
      expect(socket.url).not.toContain("?");
      expect(socket.protocols).toEqual([STREAM_SUBPROTOCOL, `${STREAM_BEARER_PREFIX}unit.session.jwt`]);
    }
    expect(opened[0].url).toMatch(/\/sessions\/11111111-1111-4111-8111-111111111111\/stream$/);
    expect(opened[1].url).toMatch(/\/notifications\/stream$/);
  });

  it("offers no subprotocol when nobody is signed in", () => {
    connectNotificationStream({ onNotification: () => {} });

    expect(opened[0].protocols).toBeUndefined();
    expect(opened[0].url).not.toContain("token");
    expect(streamProtocols(null)).toBeUndefined();
  });
});
