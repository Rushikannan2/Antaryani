// Same-origin proxy for the local Srilatha dashboard API (aiohttp on
// 127.0.0.1:8787). The Python API only exists while the agent runs, so a
// stopped or still-booting agent must not surface as a raw `ECONNREFUSED`
// stack trace from the Next server (which is what the previous blind
// next.config rewrite produced). This handler forwards the request unchanged
// but converts a connection failure into a clean JSON 503 with `Retry-After`;
// the dashboard renders that as its offline banner with slower polling and
// recovers on its own once the agent is back.
import { NextResponse } from 'next/server';

const DEFAULT_BACKEND = 'http://127.0.0.1:8787';
// JSON endpoints answer in milliseconds; media streams are exempt so large
// screen recordings are never aborted mid-download.
const JSON_TIMEOUT_MS = 10_000;
const OFFLINE_ERROR =
  'Local dashboard API is offline — start the agent with `lk agent dev`, or run `uv run python src/dashboard_api.py`.';

type RouteContext = { params: Promise<{ path: string[] }> };

function backendOrigin(): string {
  const configured = process.env.SRILATHA_DASHBOARD_URL?.trim();
  return (configured || DEFAULT_BACKEND).replace(/\/+$/, '');
}

function describeFailure(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  if (error.name === 'TimeoutError') return 'timed out waiting for the dashboard API';
  const cause = error.cause;
  if (cause instanceof Error) {
    // Dual-stack connects surface an AggregateError whose own message is
    // empty; the per-address errors carry the useful detail.
    const inners =
      'errors' in cause && Array.isArray(cause.errors)
        ? cause.errors.filter((entry): entry is Error => entry instanceof Error)
        : [cause];
    const detail = inners.map((entry) => entry.message).find(Boolean);
    if (detail) return `${error.message}: ${detail}`;
  }
  return error.message;
}

function offline(error: unknown): NextResponse {
  return NextResponse.json(
    { ok: false, error: OFFLINE_ERROR, reason: describeFailure(error) },
    { status: 503, headers: { 'Retry-After': '5', 'Cache-Control': 'no-store' } }
  );
}

async function proxy(request: Request, segments: string[]): Promise<Response> {
  const target = new URL(`${backendOrigin()}/${segments.map(encodeURIComponent).join('/')}`);
  target.search = new URL(request.url).search;

  // Forward only what the API needs: JSON bodies for actions and the Range
  // header so <video>/<img> seeking keeps working through the proxy.
  const headers = new Headers();
  for (const name of ['content-type', 'range', 'accept']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const init: RequestInit = { method: request.method, headers, cache: 'no-store' };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = await request.arrayBuffer();
  }
  if (segments[0] !== 'media') {
    init.signal = AbortSignal.timeout(JSON_TIMEOUT_MS);
  }

  let upstream: globalThis.Response;
  try {
    upstream = await fetch(target, init);
  } catch (error) {
    // Connection refused/reset or timeout: the API is down, not broken.
    return offline(error);
  }

  // Relay status, body, and the handful of headers the client relies on;
  // everything streams through untouched (no buffering of recordings).
  // content-length is skipped when the upstream body was encoded, because
  // fetch transparently decompresses and the original length would not match.
  const responseHeaders = new Headers();
  for (const name of ['content-type', 'content-range', 'accept-ranges', 'cache-control', 'etag']) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (!upstream.headers.get('content-encoding')) {
    const length = upstream.headers.get('content-length');
    if (length) responseHeaders.set('content-length', length);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  return proxy(request, path);
}
