'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  BatteryCharging,
  Camera,
  Check,
  ChevronRight,
  CircleStop,
  Clock3,
  Cpu,
  Database,
  ExternalLink,
  FileImage,
  FolderOpen,
  Gauge,
  HardDrive,
  History,
  ListVideo,
  LockKeyhole,
  Mic,
  Monitor,
  Pause,
  Play,
  Radio,
  RefreshCw,
  ScreenShare,
  ShieldCheck,
  Sparkles,
  Square,
  Video,
  VideoOff,
  Wifi,
  WifiOff,
  X,
  Zap,
} from 'lucide-react';
import {
  useAgent,
  useChat,
  useLocalParticipant,
  useSessionContext,
  useSessionMessages,
} from '@livekit/components-react';
import { AgentControlBar } from '@/components/agents-ui/agent-control-bar';
import { ThemeToggle } from '@/components/app/theme-toggle';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/shadcn/utils';

const API_ROOT = '/api/dashboard';

type MetricValue = {
  available?: boolean;
  detail?: string;
  percent?: number | null;
  cores?: number | null;
  threads?: number | null;
  frequency_ghz?: number | null;
  total_gb?: number | null;
  used_gb?: number | null;
  available_gb?: number | null;
  free_gb?: number | null;
  drive?: string;
  name?: string;
  vram_used_gb?: number | null;
  vram_total_gb?: number | null;
  temperature_c?: number | null;
  connected?: boolean;
  interfaces?: string[];
  upload_mbps?: number | null;
  download_mbps?: number | null;
  percent_value?: number | null;
  charging?: boolean;
  ac_connected?: boolean;
  status?: string;
  os?: string;
  hostname?: string;
  uptime_hours?: number | null;
  date?: string;
  time?: string;
  timezone?: string;
};

type SystemMetrics = Record<string, MetricValue>;
type RecordingState = 'idle' | 'recording' | 'paused' | 'stopping' | 'completed' | 'error';
type RecordingStatus = {
  state: RecordingState;
  elapsed_seconds: number;
  frames?: number;
  error?: string | null;
  last?: { path?: string; filename?: string; size_bytes?: number; frames?: number } | null;
};
type ActivityEntry = {
  id?: string;
  timestamp?: string;
  kind: string;
  status: string;
  detail: string;
  session_id?: string | null;
};
type SessionSummary = {
  id: string;
  start_time: string;
  end_time?: string | null;
  duration: number;
  status: string;
  language?: string | null;
  summary?: string;
  actions?: ActivityEntry[];
  media?: ActivityEntry[];
  confirmations?: number;
  errors?: ActivityEntry[];
  events?: ActivityEntry[];
};

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) {
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return body;
}

async function apiGet<T>(path: string): Promise<T> {
  return readJson<T>(await fetch(`${API_ROOT}${path}`, { cache: 'no-store' }));
}

async function apiPost<T>(path: string, body: object): Promise<T> {
  return readJson<T>(
    await fetch(`${API_ROOT}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  );
}

// The header clock and every formatted timestamp render on both the server and
// the client, so they must not depend on the ambient locale (Node defaults to
// en-US -> "04:46 PM", a browser set to en-IN -> "04:46 pm", which breaks
// hydration). One explicit locale keeps server and client output identical.
const CLOCK_LOCALE = 'en-US';

function formatTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString(CLOCK_LOCALE, { hour: '2-digit', minute: '2-digit', hour12: true });
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(CLOCK_LOCALE, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
}

function formatDuration(seconds?: number | null) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return [hours, minutes, secs].map((part) => String(part).padStart(2, '0')).join(':');
}

function formatBytes(value?: number | null) {
  if (value === null || value === undefined) return '—';
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function valueOrDash(value: number | string | null | undefined, suffix = '') {
  if (value === null || value === undefined || value === '') return '—';
  return `${value}${suffix}`;
}

function Progress({
  value,
  tone = 'cyan',
}: {
  value?: number | null;
  tone?: 'cyan' | 'violet' | 'amber' | 'emerald';
}) {
  const safe = Math.max(0, Math.min(100, value ?? 0));
  const tones = {
    cyan: 'bg-cyan-400',
    violet: 'bg-violet-400',
    amber: 'bg-amber-400',
    emerald: 'bg-emerald-400',
  };
  return (
    <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
      <div
        className={cn('h-full rounded-full transition-all duration-700', tones[tone])}
        style={{ width: `${safe}%` }}
      />
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
  percent,
  tone = 'cyan',
  unavailable = false,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  detail: string;
  percent?: number | null;
  tone?: 'cyan' | 'violet' | 'amber' | 'emerald';
  unavailable?: boolean;
}) {
  return (
    <div className="border-border/70 bg-card/65 hover:border-primary/40 rounded-2xl border p-4 shadow-[0_10px_35px_rgb(15_23_42/_.04)] transition-colors">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="bg-primary/10 text-primary flex size-9 items-center justify-center rounded-xl">
          <Icon className="size-4" />
        </div>
        {percent !== null && percent !== undefined && !unavailable ? (
          <span className="text-muted-foreground font-mono text-[11px]">
            {Math.round(percent)}%
          </span>
        ) : null}
      </div>
      <p className="text-muted-foreground text-[11px] font-semibold tracking-[0.16em] uppercase">
        {label}
      </p>
      <p
        className={cn(
          'mt-1 truncate text-xl font-semibold tracking-tight',
          unavailable && 'text-muted-foreground text-sm'
        )}
      >
        {value}
      </p>
      <p className="text-muted-foreground mt-1 truncate text-xs">{detail}</p>
      {percent !== null && percent !== undefined && !unavailable ? (
        <div className="mt-3">
          <Progress value={percent} tone={tone} />
        </div>
      ) : null}
    </div>
  );
}

function SectionHeading({
  eyebrow,
  title,
  action,
}: {
  eyebrow: string;
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <p className="text-primary mb-1 text-[10px] font-bold tracking-[0.2em] uppercase">
          {eyebrow}
        </p>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      </div>
      {action}
    </div>
  );
}

function formatAgentState(
  agentState: string,
  connected: boolean,
  executing = false,
  waitingForConfirmation = false
) {
  if (!connected) return 'DISCONNECTED';
  if (waitingForConfirmation) return 'WAITING FOR CONFIRMATION';
  if (executing) return 'EXECUTING';
  if (agentState === 'listening') return 'LISTENING';
  if (agentState === 'thinking') return 'THINKING';
  if (agentState === 'speaking') return 'SPEAKING';
  if (agentState === 'initializing') return 'CONNECTING';
  return 'CONNECTED';
}

function stateTone(state: string) {
  if (state === 'recording') return 'text-red-400';
  if (state === 'paused') return 'text-amber-400';
  if (state === 'completed') return 'text-emerald-400';
  if (state === 'error') return 'text-red-400';
  return 'text-muted-foreground';
}

export function SrilathaDashboard({ isVideoInputSupported }: { isVideoInputSupported: boolean }) {
  const session = useSessionContext();
  const agent = useAgent();
  const { messages } = useSessionMessages(session);
  const { send } = useChat();
  const { isScreenShareEnabled, localParticipant, cameraTrack, isCameraEnabled } =
    useLocalParticipant();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [recording, setRecording] = useState<RecordingStatus>({
    state: 'idle',
    elapsed_seconds: 0,
  });
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [screenshot, setScreenshot] = useState<{ filename?: string; path?: string } | null>(null);
  const [confirmationRequired, setConfirmationRequired] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [text, setText] = useState('');
  const [isCameraOn, setIsCameraOn] = useState<boolean | null>(null);

  const loadSystem = useCallback(async () => {
    try {
      const data = await apiGet<SystemMetrics>('/system');
      setMetrics(data);
      setBackendOnline(true);
    } catch {
      setBackendOnline(false);
    }
  }, []);

  const loadRecording = useCallback(async () => {
    try {
      const data = await apiGet<RecordingStatus>('/recording');
      setRecording(data);
      setBackendOnline(true);
    } catch {
      setBackendOnline(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const [sessionData, activityData] = await Promise.all([
        apiGet<{ sessions: SessionSummary[] }>('/sessions'),
        apiGet<{ activity: ActivityEntry[] }>('/activity'),
      ]);
      setSessions(sessionData.sessions || []);
      setActivity(activityData.activity || []);
      setBackendOnline(true);
    } catch {
      setBackendOnline(false);
    }
  }, []);

  useEffect(() => {
    // Back off polling while the local dashboard API is unreachable so the
    // Next.js proxy does not spam connection errors while the agent restarts.
    const offline = backendOnline === false;
    void loadSystem();
    void loadRecording();
    void loadHistory();
    const systemTimer = window.setInterval(() => void loadSystem(), offline ? 30_000 : 10_000);
    const recordingTimer = window.setInterval(() => void loadRecording(), offline ? 5_000 : 1_000);
    const historyTimer = window.setInterval(() => void loadHistory(), offline ? 15_000 : 5_000);
    return () => {
      window.clearInterval(systemTimer);
      window.clearInterval(recordingTimer);
      window.clearInterval(historyTimer);
    };
  }, [loadHistory, loadRecording, loadSystem, backendOnline]);

  // Keep the camera state truthful even if the track changes elsewhere
  // (control bar, device errors, or disconnect auto-disable).
  useEffect(() => {
    setIsCameraOn(isCameraEnabled);
  }, [isCameraEnabled]);

  // Attach the local camera track to the self-view preview element.
  useEffect(() => {
    const video = videoRef.current;
    const track = cameraTrack?.track;
    if (!video || !track) return;
    video.muted = true;
    track.attach(video);
    return () => {
      track.detach(video);
    };
  }, [cameraTrack?.track]);

  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    const onConfirmation = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean }>).detail;
      setConfirmationRequired(detail?.active === true);
    };
    window.addEventListener('srilatha:confirmation', onConfirmation);
    return () => window.removeEventListener('srilatha:confirmation', onConfirmation);
  }, []);

  const activeSession = useMemo(
    () => sessions.find((item) => item.status === 'active'),
    [sessions]
  );
  const previousSessions = useMemo(
    () => sessions.filter((item) => item.status !== 'active'),
    [sessions]
  );
  const agentState = formatAgentState(
    agent.state,
    session.isConnected,
    busyAction !== null,
    confirmationRequired
  );
  const displayTime = now.toLocaleTimeString(CLOCK_LOCALE, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
  const displayDate = now.toLocaleDateString(CLOCK_LOCALE, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
  const statusLabel =
    backendOnline === false ? 'OFFLINE' : session.isConnected ? 'ONLINE' : 'READY';
  const statusColor =
    backendOnline === false ? 'bg-red-400' : session.isConnected ? 'bg-emerald-400' : 'bg-cyan-400';

  async function runAction(name: string, action: () => Promise<void>) {
    setBusyAction(name);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'The action could not be completed.');
    } finally {
      setBusyAction(null);
    }
  }

  async function takeScreenshot() {
    await runAction('screenshot', async () => {
      const result = await apiPost<{ filename?: string; path?: string }>('/screenshot', {});
      setScreenshot(result);
      setNotice(`Screenshot saved as ${result.filename || 'a verified PNG'}.`);
      await loadHistory();
    });
  }

  async function toggleCamera() {
    if (!session.isConnected) return;
    await runAction('camera', async () => {
      const enable = !isCameraOn;
      await localParticipant.setCameraEnabled(enable);
      setIsCameraOn(enable);
      setNotice(enable ? 'Camera on — your live self-view is now visible.' : 'Camera turned off.');
    });
  }

  async function recordingAction(action: 'start' | 'pause' | 'resume' | 'stop') {
    await runAction(`recording-${action}`, async () => {
      if (action === 'start' && (recording.state === 'completed' || recording.state === 'error')) {
        await apiPost<RecordingStatus>('/recording', { action: 'reset' });
      }
      const result = await apiPost<RecordingStatus>('/recording', { action });
      setRecording(result);
      await loadHistory();
    });
  }

  async function openFiles() {
    await runAction('files', async () => {
      await apiPost('/action', { action: 'open_files' });
      setNotice("File Explorer opened through Srilatha's allowlisted Windows tool.");
      await loadHistory();
    });
  }

  async function refreshSystem() {
    await runAction('system', async () => {
      await loadSystem();
      setNotice('System status refreshed.');
    });
  }

  async function selectSession(item: SessionSummary) {
    try {
      const detail = await apiGet<SessionSummary>(`/sessions/${encodeURIComponent(item.id)}`);
      setSelectedSession(detail);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Session details are unavailable.');
    }
  }

  async function sendText(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    setText('');
    try {
      await send(value);
    } catch {
      setNotice('The message could not be sent yet.');
    }
  }

  const cpu = metrics?.cpu;
  const ram = metrics?.ram;
  const gpu = metrics?.gpu;
  const storage = metrics?.storage;
  const battery = metrics?.battery;
  const network = metrics?.network;
  const system = metrics?.system;
  const recordingLabel =
    recording.state === 'recording'
      ? 'Recording'
      : recording.state === 'paused'
        ? 'Paused'
        : recording.state === 'completed'
          ? 'Saved'
          : recording.state === 'error'
            ? 'Error'
            : 'Not recording';
  const recordingDot =
    recording.state === 'recording'
      ? 'bg-red-400 animate-pulse'
      : recording.state === 'paused'
        ? 'bg-amber-400'
        : recording.state === 'completed'
          ? 'bg-emerald-400'
          : 'bg-slate-500';

  return (
    <div className="bg-background text-foreground relative min-h-svh overflow-hidden">
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
        <div className="bg-primary/10 absolute -top-48 left-1/3 size-[30rem] rounded-full blur-3xl" />
        <div className="absolute top-1/3 -right-40 size-[30rem] rounded-full bg-cyan-400/5 blur-3xl" />
        <div className="absolute inset-0 bg-[linear-gradient(rgba(148,163,184,.035)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,.035)_1px,transparent_1px)] [mask-image:linear-gradient(to_bottom,black,transparent_70%)] bg-[size:48px_48px]" />
      </div>

      <div className="relative mx-auto max-w-[1500px] px-4 py-5 sm:px-6 lg:px-10 lg:py-8">
        <header className="border-border/70 bg-background/80 mb-8 rounded-2xl border border-t px-6 py-4 shadow-sm backdrop-blur-xl">
          <div className="flex max-w-2xl items-center gap-3">
            <div className="bg-primary/10 text-primary shadow-primary/20 relative flex size-10 items-center justify-center overflow-hidden rounded-2xl">
              <Sparkles className="animate-spin-reverse size-5" />
              <Sparkles className="absolute -top-1 -left-1 size-3 opacity-30" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Srilatha</h1>
              <p className="text-muted-foreground tracked text-xs uppercase">Personal AI</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Status dot and label */}
            <div className="border-border/70 bg-background/60 flex items-center gap-2 rounded-xl border px-3 py-2">
              <span className={cn('size-2.5 rounded-full', statusColor)} aria-hidden="true" />
              <span className="text-[10px] font-bold tracking-[0.14em] whitespace-nowrap uppercase">
                {statusLabel}
              </span>
            </div>

            {/* Time and date: suppressHydrationWarning because the live clock can
                tick between the server render and client hydration. */}
            <div className="hidden items-center gap-2 sm:flex">
              <p
                suppressHydrationWarning
                className="text-primary font-mono text-sm font-semibold tracking-wide"
              >
                {displayTime}
              </p>
              <p
                suppressHydrationWarning
                className="text-muted-foreground text-[10px] font-bold tracking-[0.12em] uppercase"
              >
                {displayDate}
              </p>
            </div>

            {/* Confirmation and theme */}
            <div className="flex items-center gap-2">
              {confirmationRequired ? (
                <LockKeyhole className="size-3.5 text-amber-400" />
              ) : (
                <ShieldCheck className="size-3.5 text-emerald-400" />
              )}
              <span className="text-[10px] font-bold tracking-[0.12em] whitespace-nowrap uppercase">
                {confirmationRequired ? 'Confirmation required' : 'Protected'}
              </span>
            </div>

            <ThemeToggle />
          </div>
        </header>

        {notice ? (
          <div
            className="border-primary/20 bg-primary/5 text-primary mb-5 flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-sm"
            role="status"
          >
            <span>{notice}</span>
            <button type="button" aria-label="Dismiss notice" onClick={() => setNotice(null)}>
              <X className="size-4" />
            </button>
          </div>
        ) : null}

        <main className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(350px,.85fr)]">
            <section className="border-border/70 bg-card/75 relative overflow-hidden rounded-3xl border p-5 shadow-[0_20px_70px_rgb(15_23_42/_.08)] sm:p-7">
              <div className="bg-primary/10 pointer-events-none absolute -top-24 -right-20 size-72 rounded-full blur-3xl" />
              <div className="relative flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="text-primary mb-3 flex items-center gap-2 text-[10px] font-bold tracking-[0.2em] uppercase">
                    <span className="bg-primary size-1.5 rounded-full" /> Voice workspace
                  </div>
                  <h2 className="text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">
                    Good day, Rushi Sir.
                  </h2>
                  <p className="text-muted-foreground mt-2 max-w-lg text-sm leading-6">
                    Srilatha is ready to listen, understand, and take care of the details.
                  </p>
                </div>
                <div className="border-border/70 bg-background/60 flex items-center gap-2 rounded-full border px-3 py-1.5">
                  <span
                    className={cn(
                      'size-2 rounded-full',
                      session.isConnected ? 'animate-pulse bg-emerald-400' : 'bg-slate-400'
                    )}
                  />
                  <span className="text-muted-foreground text-[10px] font-bold tracking-[0.16em] uppercase">
                    {agentState}
                  </span>
                </div>
              </div>

              <div className="border-primary/20 bg-background/35 relative mt-8 flex min-h-48 flex-col items-center justify-center rounded-2xl border border-dashed px-4 py-6 text-center">
                <div
                  className="mb-4 flex h-16 w-32 items-center justify-center gap-1.5"
                  aria-hidden="true"
                >
                  {[18, 30, 44, 26, 52, 34, 20, 40, 24, 32, 18, 28].map((height, index) => (
                    <span
                      key={index}
                      className={cn(
                        'w-1.5 rounded-full transition-all duration-500',
                        session.isConnected ? 'bg-primary' : 'bg-muted-foreground/40'
                      )}
                      style={{
                        height: `${session.isConnected ? height : Math.max(10, height / 2)}px`,
                        animationDelay: `${index * 70}ms`,
                      }}
                    />
                  ))}
                </div>
                <p className="text-sm font-medium">
                  {session.isConnected ? 'Srilatha is listening' : 'Start a secure voice session'}
                </p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {session.isConnected
                    ? 'Speak naturally in English or your preferred language.'
                    : 'Your microphone is used only for this LiveKit session.'}
                </p>
                {!session.isConnected ? (
                  <Button className="mt-5 rounded-full px-6" onClick={() => void session.start()}>
                    <Mic className="size-4" /> Start call
                  </Button>
                ) : null}
                {session.isConnected ? (
                  <div
                    aria-label={
                      isCameraOn === true ? 'Camera preview: live' : 'Camera preview: off'
                    }
                    className="border-primary/40 absolute top-3 right-3 w-32 overflow-hidden rounded-xl border bg-slate-950 shadow-lg sm:w-44"
                  >
                    <video
                      ref={videoRef}
                      autoPlay
                      playsInline
                      muted
                      className={cn(
                        'aspect-video w-full -scale-x-100 bg-slate-950 object-cover',
                        isCameraOn !== true && 'hidden'
                      )}
                    />
                    {isCameraOn === true ? (
                      <div className="absolute top-1.5 left-1.5 flex items-center gap-1.5 rounded-full bg-black/65 px-2 py-0.5">
                        <span className="size-1.5 animate-pulse rounded-full bg-red-500" />
                        <span className="text-[9px] font-bold tracking-[0.14em] text-white uppercase">
                          Live
                        </span>
                      </div>
                    ) : (
                      <div className="flex aspect-video w-full flex-col items-center justify-center gap-1.5 bg-slate-950">
                        <VideoOff className="size-5 text-slate-400" />
                        <span className="text-[9px] font-bold tracking-[0.14em] text-slate-400 uppercase">
                          Camera off
                        </span>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>

              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <div className="border-border/70 bg-background/45 rounded-xl border p-3">
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.15em] uppercase">
                    Session
                  </p>
                  <p className="mt-1 truncate text-sm font-medium">
                    {activeSession ? 'Active now' : 'No active session'}
                  </p>
                </div>
                <div className="border-border/70 bg-background/45 rounded-xl border p-3">
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.15em] uppercase">
                    Screen
                  </p>
                  <p className="mt-1 flex items-center gap-1.5 truncate text-sm font-medium">
                    {isScreenShareEnabled ? (
                      <ScreenShare className="text-primary size-3.5" />
                    ) : (
                      <Monitor className="text-muted-foreground size-3.5" />
                    )}
                    {isScreenShareEnabled ? 'Shared' : 'Not shared'}
                  </p>
                </div>
                <div className="border-border/70 bg-background/45 rounded-xl border p-3">
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.15em] uppercase">
                    Security
                  </p>
                  <p
                    className={cn(
                      'mt-1 flex items-center gap-1.5 truncate text-sm font-medium',
                      confirmationRequired && 'text-amber-500'
                    )}
                  >
                    {confirmationRequired ? (
                      <LockKeyhole className="size-3.5" />
                    ) : (
                      <ShieldCheck className="size-3.5 text-emerald-400" />
                    )}
                    {confirmationRequired ? 'Awaiting confirmation' : 'Protected'}
                  </p>
                </div>
              </div>

              {isScreenShareEnabled ? (
                <div className="border-primary/20 bg-primary/5 text-primary mt-4 flex items-center gap-2 rounded-xl border px-3 py-2 text-xs">
                  <EyeIcon /> Screen shared — Srilatha can observe what you show, but will not act
                  without your instruction.
                </div>
              ) : null}

              {session.isConnected ? (
                <div className="border-border/70 bg-background/45 mt-5 rounded-2xl border p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <ListVideo className="text-primary size-4" />
                      <span className="text-sm font-semibold">Current conversation</span>
                    </div>
                    <span className="text-muted-foreground font-mono text-[10px] uppercase">
                      {messages.length} messages
                    </span>
                  </div>
                  <div className="max-h-56 space-y-3 overflow-y-auto pr-1">
                    {messages.length === 0 ? (
                      <p className="text-muted-foreground py-5 text-center text-xs">
                        Your live transcript will appear here.
                      </p>
                    ) : (
                      messages.slice(-8).map((message) => {
                        const isUser = message.from?.isLocal === true;
                        return (
                          <div
                            key={message.id}
                            className={cn('flex', isUser ? 'justify-end' : 'justify-start')}
                          >
                            <div
                              className={cn(
                                'max-w-[88%] rounded-2xl px-3 py-2 text-sm',
                                isUser ? 'bg-primary text-primary-foreground' : 'bg-muted/70'
                              )}
                            >
                              <p className="mb-0.5 text-[9px] font-bold tracking-[0.14em] uppercase opacity-60">
                                {isUser ? 'Rushi Sir' : 'Srilatha'}
                              </p>
                              <p>{message.message}</p>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                  <form onSubmit={sendText} className="mt-3 flex gap-2">
                    <input
                      value={text}
                      onChange={(event) => setText(event.target.value)}
                      placeholder="Type a message to Srilatha…"
                      className="border-border bg-background focus:border-primary min-w-0 flex-1 rounded-xl border px-3 py-2 text-sm outline-none"
                    />
                    <Button
                      type="submit"
                      size="icon"
                      aria-label="Send message"
                      disabled={!text.trim()}
                    >
                      <Zap className="size-4" />
                    </Button>
                    {/* Camera ON/OFF toggle with live self-view */}
                    <Button
                      type="button"
                      variant={isCameraOn === true ? 'default' : 'outline'}
                      size="icon"
                      aria-label={isCameraOn === true ? 'Turn off camera' : 'Turn on camera'}
                      aria-pressed={isCameraOn === true}
                      title={
                        !isVideoInputSupported
                          ? 'No camera detected'
                          : isCameraOn === true
                            ? 'Camera on — click to turn off'
                            : 'Camera off — click to turn on'
                      }
                      onClick={() => void toggleCamera()}
                      disabled={busyAction === 'camera' || !isVideoInputSupported}
                    >
                      {isCameraOn === true ? (
                        <Video className="size-4" />
                      ) : (
                        <VideoOff className="size-4" />
                      )}
                    </Button>
                  </form>
                  <div className="mt-3 flex gap-2">
                    <AgentControlBar
                      controls={{
                        leave: true,
                        microphone: true,
                        // Camera ON/OFF lives beside the chat input as the single control.
                        camera: false,
                        screenShare: isVideoInputSupported,
                        chat: true,
                      }}
                      isConnected={session.isConnected}
                      onDisconnect={session.end}
                    />
                  </div>
                </div>
              ) : null}
            </section>

            <section className="border-border/70 bg-card/75 rounded-3xl border p-5 shadow-[0_20px_70px_rgb(15_23_42/_.08)] sm:p-6">
              <SectionHeading
                eyebrow="Local telemetry"
                title="System status"
                action={
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Refresh system status"
                    onClick={() => void refreshSystem()}
                    disabled={busyAction === 'system'}
                  >
                    <RefreshCw
                      className={cn('size-4', busyAction === 'system' && 'animate-spin')}
                    />
                  </Button>
                }
              />
              {backendOnline === false ? (
                <div className="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-500">
                  Dashboard API offline — start the agent with{' '}
                  <code className="font-mono font-semibold">lk agent dev</code> to stream live
                  metrics.
                </div>
              ) : null}
              <div className="grid grid-cols-2 gap-3">
                <MetricCard
                  icon={Cpu}
                  label="CPU"
                  value={valueOrDash(cpu?.percent, '%')}
                  detail={cpu?.threads ? `${cpu.threads} threads` : 'Utilization unavailable'}
                  percent={cpu?.percent}
                  tone="cyan"
                  unavailable={cpu?.available === false}
                />
                <MetricCard
                  icon={Gauge}
                  label="RAM"
                  value={valueOrDash(ram?.percent, '%')}
                  detail={
                    ram?.total_gb
                      ? `${ram.used_gb?.toFixed(1) || '—'} / ${ram.total_gb.toFixed(1)} GB`
                      : 'Memory unavailable'
                  }
                  percent={ram?.percent}
                  tone="violet"
                  unavailable={ram?.available === false}
                />
                <MetricCard
                  icon={Monitor}
                  label="GPU"
                  value={gpu?.available === false ? 'Unavailable' : valueOrDash(gpu?.percent, '%')}
                  detail={
                    gpu?.available === false
                      ? 'GPU metrics unavailable'
                      : gpu?.name || 'Graphics adapter'
                  }
                  percent={gpu?.percent}
                  tone="violet"
                  unavailable={gpu?.available === false}
                />
                <MetricCard
                  icon={HardDrive}
                  label="Storage"
                  value={valueOrDash(storage?.percent, '%')}
                  detail={
                    storage?.total_gb
                      ? `${storage.free_gb?.toFixed(1) || '—'} GB free`
                      : 'System drive'
                  }
                  percent={storage?.percent}
                  tone="amber"
                  unavailable={storage?.available === false}
                />
                <MetricCard
                  icon={BatteryCharging}
                  label="Battery"
                  value={
                    battery?.available === false
                      ? 'Unavailable'
                      : valueOrDash(battery?.percent, '%')
                  }
                  detail={
                    battery?.available === false
                      ? 'No battery reported'
                      : battery?.status || 'Power status'
                  }
                  unavailable={battery?.available === false}
                />
                <MetricCard
                  icon={network?.connected === false ? WifiOff : Wifi}
                  label="Network"
                  value={network?.connected === false ? 'Offline' : 'Connected'}
                  detail={
                    network?.interfaces?.[0] ||
                    (network?.available === false ? 'Status unavailable' : 'Local network')
                  }
                  unavailable={network?.available === false}
                />
              </div>
              <div className="border-border/70 bg-background/45 mt-4 grid grid-cols-2 gap-3 rounded-2xl border p-3 text-xs">
                <div>
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.14em] uppercase">
                    Operating system
                  </p>
                  <p className="mt-1 truncate font-medium">{system?.os || 'Windows'}</p>
                </div>
                <div>
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.14em] uppercase">
                    Host
                  </p>
                  <p className="mt-1 truncate font-medium">{system?.hostname || '—'}</p>
                </div>
                <div>
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.14em] uppercase">
                    Uptime
                  </p>
                  <p className="mt-1 font-medium">
                    {system?.uptime_hours ? `${system.uptime_hours.toFixed(1)} hours` : '—'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground text-[10px] font-bold tracking-[0.14em] uppercase">
                    Timezone
                  </p>
                  <p className="mt-1 truncate font-medium">
                    {metrics?.time?.timezone || 'Local time'}
                  </p>
                </div>
              </div>
            </section>
          </div>

          <section className="border-border/70 bg-card/70 rounded-3xl border p-5 shadow-sm sm:p-6">
            <SectionHeading
              eyebrow="Real capabilities"
              title="Quick actions"
              action={<span className="text-muted-foreground text-xs">No simulated controls</span>}
            />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <QuickAction
                icon={Camera}
                label="Take screenshot"
                detail="Save verified PNG"
                busy={busyAction === 'screenshot'}
                onClick={() => void takeScreenshot()}
              />
              <QuickAction
                icon={isVideoInputSupported ? ScreenShare : Monitor}
                label={recording.state === 'recording' ? 'Recording active' : 'Start recording'}
                detail="Real desktop capture"
                busy={busyAction === 'recording-start'}
                onClick={() => void recordingAction('start')}
                disabled={recording.state === 'recording' || recording.state === 'paused'}
              />
              <QuickAction
                icon={FolderOpen}
                label="Open files"
                detail="Allowlisted File Explorer"
                busy={busyAction === 'files'}
                onClick={() => void openFiles()}
              />
              <QuickAction
                icon={Activity}
                label="System status"
                detail="Read local metrics"
                busy={busyAction === 'system'}
                onClick={() => void refreshSystem()}
              />
            </div>
            {screenshot?.filename ? (
              <div className="border-border/70 bg-background/45 mt-4 flex items-center gap-3 rounded-2xl border p-3">
                <div className="bg-primary/10 text-primary flex size-9 items-center justify-center rounded-xl">
                  <FileImage className="size-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium">Latest screenshot</p>
                  <p className="text-muted-foreground truncate text-xs">{screenshot.filename}</p>
                </div>
                <a
                  className="text-primary ml-auto inline-flex items-center gap-1 text-xs font-medium hover:underline"
                  href={`${API_ROOT}/media?kind=image&file=${encodeURIComponent(screenshot.filename || '')}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  View <ExternalLink className="size-3" />
                </a>
              </div>
            ) : null}
          </section>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,.85fr)]">
            <section className="border-border/70 bg-card/70 rounded-3xl border p-5 shadow-sm sm:p-6">
              <SectionHeading
                eyebrow="Timeline"
                title="Recent sessions"
                action={<History className="text-muted-foreground size-4" />}
              />
              <div className="space-y-2">
                {sessions.length === 0 ? (
                  <p className="text-muted-foreground rounded-xl border border-dashed p-5 text-center text-xs">
                    No sessions recorded yet.
                  </p>
                ) : null}
                {activeSession ? (
                  <SessionRow
                    session={activeSession}
                    current
                    onClick={() => void selectSession(activeSession)}
                  />
                ) : null}
                {previousSessions.slice(0, 5).map((item) => (
                  <SessionRow
                    key={item.id}
                    session={item}
                    onClick={() => void selectSession(item)}
                  />
                ))}
              </div>
              {selectedSession ? (
                <div className="border-primary/20 bg-primary/5 mt-4 rounded-2xl border p-4">
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <div>
                      <p className="text-primary text-[10px] font-bold tracking-[0.16em] uppercase">
                        Session detail
                      </p>
                      <p className="mt-1 text-sm font-semibold">
                        {selectedSession.summary || 'Voice session'}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSelectedSession(null)}
                      aria-label="Close session details"
                    >
                      <X className="text-muted-foreground size-4" />
                    </button>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    {formatDate(selectedSession.start_time)} ·{' '}
                    {formatDuration(selectedSession.duration)} ·{' '}
                    {selectedSession.confirmations || 0} confirmation events
                  </p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <div>
                      <p className="text-muted-foreground text-[10px] uppercase">Actions</p>
                      <p className="mt-1 text-xs">
                        {selectedSession.actions?.length || 0} recorded
                      </p>
                    </div>
                    <div>
                      <p className="text-muted-foreground text-[10px] uppercase">Media</p>
                      <p className="mt-1 text-xs">{selectedSession.media?.length || 0} files</p>
                    </div>
                  </div>
                </div>
              ) : null}
            </section>

            <section className="border-border/70 bg-card/70 rounded-3xl border p-5 shadow-sm sm:p-6">
              <SectionHeading
                eyebrow="Audit trail"
                title="Recent activity"
                action={<Database className="text-muted-foreground size-4" />}
              />
              <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
                {activity.length === 0 ? (
                  <p className="text-muted-foreground rounded-xl border border-dashed p-5 text-center text-xs">
                    Actions will appear here as Srilatha works.
                  </p>
                ) : (
                  activity
                    .slice(0, 8)
                    .map((item, index) => (
                      <ActivityRow key={item.id || `${item.timestamp}-${index}`} item={item} />
                    ))
                )}
              </div>
            </section>
          </div>

          <section className="border-border/70 bg-card/70 rounded-3xl border p-5 shadow-sm sm:p-6">
            <SectionHeading
              eyebrow="Capture controls"
              title="Screen recording"
              action={
                <span
                  className={cn(
                    'flex items-center gap-2 text-xs font-semibold',
                    stateTone(recording.state)
                  )}
                >
                  <span className={cn('size-2 rounded-full', recordingDot)} />
                  {recordingLabel}
                </span>
              }
            />
            <div className="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-center">
              <div>
                <div className="flex items-end gap-3">
                  <span className="font-mono text-4xl font-semibold tracking-tight">
                    {formatDuration(recording.elapsed_seconds)}
                  </span>
                  {recording.frames ? (
                    <span className="text-muted-foreground mb-1 text-xs">
                      {recording.frames} frames captured
                    </span>
                  ) : null}
                </div>
                <p className="text-muted-foreground mt-2 text-xs">
                  Recordings are finalized and verified before they are reported as saved.
                </p>
                {recording.last?.filename ? (
                  <p className="text-primary mt-2 flex items-center gap-1.5 text-xs">
                    <Check className="size-3.5" /> Last saved: {recording.last.filename} (
                    {formatBytes(recording.last.size_bytes)})
                  </p>
                ) : null}
                {recording.error ? (
                  <p className="mt-2 text-xs text-red-400">{recording.error}</p>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-2">
                {recording.state === 'recording' ? (
                  <>
                    <Button
                      variant="outline"
                      onClick={() => void recordingAction('pause')}
                      disabled={busyAction === 'recording-pause'}
                    >
                      <Pause className="size-4" /> Pause
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={() => void recordingAction('stop')}
                      disabled={busyAction === 'recording-stop'}
                    >
                      <Square className="size-4" /> Stop
                    </Button>
                  </>
                ) : null}
                {recording.state === 'paused' ? (
                  <>
                    <Button
                      onClick={() => void recordingAction('resume')}
                      disabled={busyAction === 'recording-resume'}
                    >
                      <Play className="size-4" /> Resume
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={() => void recordingAction('stop')}
                      disabled={busyAction === 'recording-stop'}
                    >
                      <Square className="size-4" /> Stop
                    </Button>
                  </>
                ) : null}
                {recording.state === 'idle' ||
                recording.state === 'completed' ||
                recording.state === 'error' ? (
                  <Button
                    onClick={() => void recordingAction('start')}
                    disabled={busyAction === 'recording-start'}
                  >
                    <CircleStop className="size-4" /> Start recording
                  </Button>
                ) : null}
              </div>
            </div>
          </section>
        </main>

        <footer className="text-muted-foreground mt-7 flex flex-wrap items-center justify-between gap-3 px-1 text-[11px]">
          <span>
            Local dashboard · metrics update periodically · no secrets stored in the browser
          </span>
          <a
            className="hover:text-foreground inline-flex items-center gap-1.5"
            href="https://docs.livekit.io/agents"
            target="_blank"
            rel="noreferrer"
          >
            Built with <span className="font-semibold">LiveKit Agents</span>
            <ExternalLink className="size-3" />
          </a>
        </footer>
      </div>
    </div>
  );
}

function QuickAction({
  icon: Icon,
  label,
  detail,
  busy,
  disabled,
  onClick,
}: {
  icon: typeof Camera;
  label: string;
  detail: string;
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className="border-border/70 bg-background/45 hover:border-primary/40 hover:bg-primary/5 group flex items-center gap-3 rounded-2xl border p-3 text-left transition-all disabled:cursor-not-allowed disabled:opacity-50"
    >
      <div className="bg-primary/10 text-primary flex size-9 shrink-0 items-center justify-center rounded-xl group-hover:scale-105">
        <Icon className={cn('size-4', busy && 'animate-pulse')} />
      </div>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{busy ? 'Working…' : label}</span>
        <span className="text-muted-foreground block truncate text-xs">{detail}</span>
      </span>
      <ChevronRight className="text-muted-foreground ml-auto size-4 shrink-0" />
    </button>
  );
}

function SessionRow({
  session,
  current,
  onClick,
}: {
  session: SessionSummary;
  current?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="border-border/70 bg-background/40 hover:border-primary/35 flex w-full items-center gap-3 rounded-xl border p-3 text-left transition-colors"
    >
      <div
        className={cn(
          'flex size-8 shrink-0 items-center justify-center rounded-lg',
          current ? 'bg-emerald-400/10 text-emerald-400' : 'bg-muted text-muted-foreground'
        )}
      >
        {current ? <Radio className="size-4" /> : <Clock3 className="size-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {session.summary || (current ? 'Current voice session' : 'Voice session')}
        </p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {current
            ? 'Active now'
            : `${formatDate(session.start_time)} · ${formatTime(session.start_time)}`}
        </p>
      </div>
      <span className="text-muted-foreground font-mono text-[10px]">
        {current ? 'LIVE' : formatDuration(session.duration)}
      </span>
    </button>
  );
}

function ActivityRow({ item }: { item: ActivityEntry }) {
  const failed = item.status === 'error' || item.status === 'failed';
  const confirmation = item.kind === 'confirmation';
  return (
    <div className="flex gap-3 rounded-xl px-2 py-2.5">
      <div
        className={cn(
          'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg',
          failed
            ? 'bg-red-400/10 text-red-400'
            : confirmation
              ? 'bg-amber-400/10 text-amber-400'
              : 'bg-emerald-400/10 text-emerald-400'
        )}
      >
        {confirmation ? (
          <LockKeyhole className="size-3.5" />
        ) : failed ? (
          <X className="size-3.5" />
        ) : (
          <Check className="size-3.5" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">
          {confirmation
            ? 'Confirmation required'
            : item.kind === 'screenshot'
              ? 'Screenshot saved'
              : item.kind === 'recording'
                ? 'Recording activity'
                : item.kind === 'application'
                  ? 'Application opened'
                  : 'Action completed'}
        </p>
        <p className="text-muted-foreground mt-0.5 truncate text-[11px]">{item.detail}</p>
      </div>
      <time className="text-muted-foreground shrink-0 text-[10px]">
        {formatTime(item.timestamp)}
      </time>
    </div>
  );
}

function EyeIcon() {
  return (
    <span className="border-primary/60 flex size-4 items-center justify-center rounded-full border">
      <span className="bg-primary size-1.5 rounded-full" />
    </span>
  );
}
