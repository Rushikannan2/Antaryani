'use client';

import * as React from 'react';
import { RoomEvent } from 'livekit-client';
import { useLocalParticipant, useRoomContext } from '@livekit/components-react';

const CONFIRMATION_TOPIC = 'srilatha.confirmation';
const CODE_PATTERN = /^\d{6}$/;

interface ConfirmationPayload {
  type?: unknown;
  token?: unknown;
  code?: unknown;
  description?: unknown;
  expires_in?: unknown;
}

interface PendingCode {
  code: string;
  description: string;
  expiresAt: number;
}

/**
 * Shows the six-digit confirmation code out of band, on the genuine user's
 * screen only: it arrives as a room data message on a dedicated topic, never
 * through speech or the transcript. The code is masked whenever this user is
 * screen sharing, so a shared screen never leaks it either.
 */
export function ConfirmationBanner() {
  const room = useRoomContext();
  const { isScreenShareEnabled } = useLocalParticipant();
  const [pending, setPending] = React.useState<PendingCode | null>(null);
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    const onMessage = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic !== CONFIRMATION_TOPIC) {
        return;
      }
      let data: ConfirmationPayload;
      try {
        data = JSON.parse(new TextDecoder().decode(payload)) as ConfirmationPayload;
      } catch {
        return;
      }
      if (data.type !== 'confirmation' || typeof data.code !== 'string') {
        return;
      }
      if (!CODE_PATTERN.test(data.code)) {
        return;
      }
      const ttl =
        typeof data.expires_in === 'number' && data.expires_in > 0 ? data.expires_in : 120;
      setPending({
        code: data.code,
        description: typeof data.description === 'string' ? data.description : '',
        expiresAt: Date.now() + ttl * 1000,
      });
    };

    room.on(RoomEvent.DataReceived, onMessage);
    return () => {
      room.off(RoomEvent.DataReceived, onMessage);
    };
  }, [room]);

  // Tick once a second so the countdown runs and the banner clears itself
  // when the staged confirmation expires.
  React.useEffect(() => {
    if (!pending) {
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);

  React.useEffect(() => {
    if (pending && now >= pending.expiresAt) {
      setPending(null);
    }
  }, [pending, now]);

  React.useEffect(() => {
    window.dispatchEvent(
      new CustomEvent('srilatha:confirmation', { detail: { active: pending !== null } })
    );
    return () => {
      window.dispatchEvent(new CustomEvent('srilatha:confirmation', { detail: { active: false } }));
    };
  }, [pending]);

  if (!pending) {
    return null;
  }

  const secondsLeft = Math.max(0, Math.ceil((pending.expiresAt - now) / 1000));

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-border bg-card text-card-foreground fixed inset-x-0 bottom-0 z-50 border-t p-4 shadow-[0_-2px_10px_rgb(0_0_0_/_0.25)]"
    >
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {pending.description || 'Confirmation required'}
          </p>
          <p className="text-muted-foreground text-xs">
            Read this code back to Srilatha to authorize the action.
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-bold tracking-widest">
            {isScreenShareEnabled ? '••••••' : pending.code}
          </p>
          <p className="text-muted-foreground text-xs">{secondsLeft}s left</p>
        </div>
      </div>
    </div>
  );
}
