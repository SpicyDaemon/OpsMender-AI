"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  connectNotificationStream,
  listApprovals,
  type Notification,
} from "@/lib/api";

export const LIVE_EVENT_DEBOUNCE_MS = 500;
const APPROVAL_COUNT_POLL_MS = 60_000;
const RECONNECT_INITIAL_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

type LiveEventHandler = (notification: Notification) => void;

type LiveEventSubscription = {
  categories: Set<string>;
  handler: LiveEventHandler;
  pending: Notification | null;
  timer: ReturnType<typeof setTimeout> | null;
};

export function createLiveEventDispatcher({
  debounceMs = LIVE_EVENT_DEBOUNCE_MS,
}: { debounceMs?: number } = {}) {
  let nextId = 1;
  const subscriptions = new Map<number, LiveEventSubscription>();

  function subscribe(
    categories: string[],
    handler: LiveEventHandler,
  ): () => void {
    const id = nextId++;
    subscriptions.set(id, {
      categories: new Set(categories),
      handler,
      pending: null,
      timer: null,
    });

    return () => {
      const sub = subscriptions.get(id);
      if (sub?.timer) clearTimeout(sub.timer);
      subscriptions.delete(id);
    };
  }

  function publish(notification: Notification) {
    for (const sub of subscriptions.values()) {
      if (!sub.categories.has(notification.category)) continue;
      sub.pending = notification;
      if (sub.timer) clearTimeout(sub.timer);
      sub.timer = setTimeout(() => {
        const pending = sub.pending;
        sub.pending = null;
        sub.timer = null;
        if (pending) sub.handler(pending);
      }, debounceMs);
    }
  }

  function clear() {
    for (const sub of subscriptions.values()) {
      if (sub.timer) clearTimeout(sub.timer);
    }
    subscriptions.clear();
  }

  return {
    clear,
    publish,
    subscribe,
    subscriptionCount: () => subscriptions.size,
  };
}

type LiveEventsContextValue = {
  subscribe: (categories: string[], handler: LiveEventHandler) => () => void;
};

const LiveEventsContext = createContext<LiveEventsContextValue | null>(null);

export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const dispatcherRef = useRef(createLiveEventDispatcher());
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef(RECONNECT_INITIAL_MS);
  const activeRef = useRef(false);

  useEffect(() => {
    activeRef.current = true;

    const clearReconnect = () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (!activeRef.current || reconnectTimerRef.current) return;
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(
        reconnectDelayRef.current * 2,
        RECONNECT_MAX_MS,
      );
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delay);
    };

    const connect = () => {
      if (!activeRef.current || socketRef.current) return;
      try {
        socketRef.current = connectNotificationStream({
          onNotification: (notification) => {
            dispatcherRef.current.publish(notification);
          },
          onClose: () => {
            socketRef.current = null;
            scheduleReconnect();
          },
        });
        reconnectDelayRef.current = RECONNECT_INITIAL_MS;
      } catch {
        scheduleReconnect();
      }
    };

    connect();

    return () => {
      activeRef.current = false;
      clearReconnect();
      dispatcherRef.current.clear();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  const subscribe = useCallback(
    (categories: string[], handler: LiveEventHandler) =>
      dispatcherRef.current.subscribe(categories, handler),
    [],
  );

  const value = useMemo<LiveEventsContextValue>(
    () => ({ subscribe }),
    [subscribe],
  );

  return (
    <LiveEventsContext.Provider value={value}>
      {children}
    </LiveEventsContext.Provider>
  );
}

export function useLiveEvents(
  categories: string[],
  handler: LiveEventHandler,
) {
  const context = useContext(LiveEventsContext);
  const handlerRef = useRef(handler);
  const categoryKey = categories.join("\u0000");

  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    if (!context || categories.length === 0) return;
    const stableCategories = categoryKey ? categoryKey.split("\u0000") : [];
    return context.subscribe(stableCategories, (notification) => {
      handlerRef.current(notification);
    });
  }, [categoryKey, context]);
}

export function usePendingApprovalsCount(enabled = true) {
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const res = await listApprovals({ status: "pending", limit: 1 });
      setCount(res.total);
    } catch {
      setCount(0);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    void Promise.resolve().then(refresh);
    const interval = window.setInterval(
      () => void refresh(),
      APPROVAL_COUNT_POLL_MS,
    );
    return () => window.clearInterval(interval);
  }, [enabled, refresh]);

  useLiveEvents(enabled ? ["approval"] : [], () => {
    void refresh();
  });

  return enabled ? count : 0;
}
