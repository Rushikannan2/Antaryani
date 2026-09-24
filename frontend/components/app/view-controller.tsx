'use client';

import { SrilathaDashboard } from '@/components/app/srilatha-dashboard';

interface ViewControllerProps {
  isVideoInputSupported: boolean;
}

export function ViewController({ isVideoInputSupported }: ViewControllerProps) {
  return <SrilathaDashboard isVideoInputSupported={isVideoInputSupported} />;
}
