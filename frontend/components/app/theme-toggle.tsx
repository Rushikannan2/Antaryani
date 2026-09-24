'use client';

import { useTheme } from 'next-themes';
import { MoonIcon, SunIcon } from '@phosphor-icons/react';
import { cn } from '@/lib/shadcn/utils';

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { setTheme, resolvedTheme } = useTheme();

  // Determine which icon to show based on current theme
  const isDark = resolvedTheme === 'dark';
  const isSystem = resolvedTheme === 'system';

  return (
    <div
      className={cn(
        'bg-border flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium',
        className
      )}
    >
      <span className="sr-only">Toggle color scheme</span>
      <button
        type="button"
        onClick={() => setTheme(isDark ? 'light' : 'dark')}
        className="hover:bg-border/50 flex size-3.5 items-center justify-center rounded-md transition-colors"
        aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {isDark ? (
          <SunIcon size={12} className="text-primary" />
        ) : (
          <MoonIcon size={12} className="text-primary" />
        )}
      </button>
      <span className="truncate text-[10px] opacity-60">
        {isSystem ? 'System' : isDark ? 'Dark' : 'Light'}
      </span>
    </div>
  );
}
