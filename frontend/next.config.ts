import type { NextConfig } from 'next';

// /api/dashboard/* is handled by app/api/dashboard/[...path]/route.ts, which
// proxies the local Python API and returns a clean JSON 503 when it is down —
// a plain rewrite surfaced raw ECONNREFUSED stack traces instead.
const nextConfig: NextConfig = {};

export default nextConfig;
