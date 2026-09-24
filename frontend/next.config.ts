import type { NextConfig } from 'next';

const dashboardBackend = process.env.SRILATHA_DASHBOARD_URL || 'http://127.0.0.1:8787';

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/dashboard/:path*',
        destination: `${dashboardBackend}/:path*`,
      },
    ];
  },
};

export default nextConfig;
