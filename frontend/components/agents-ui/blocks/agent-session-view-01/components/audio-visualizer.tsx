'use client';

import React from 'react';
import { type MotionProps, motion } from 'motion/react';
import { useVoiceAssistant } from '@livekit/components-react';
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura';
import { AgentAudioVisualizerBar } from '@/components/agents-ui/agent-audio-visualizer-bar';
import { AgentAudioVisualizerGrid } from '@/components/agents-ui/agent-audio-visualizer-grid';
import { AgentAudioVisualizerRadial } from '@/components/agents-ui/agent-audio-visualizer-radial';
import { AgentAudioVisualizerWave } from '@/components/agents-ui/agent-audio-visualizer-wave';
import { cn } from '@/lib/shadcn/utils';

const MotionAgentAudioVisualizerAura = motion.create(AgentAudioVisualizerAura);
const MotionAgentAudioVisualizerBar = motion.create(AgentAudioVisualizerBar);
const MotionAgentAudioVisualizerGrid = motion.create(AgentAudioVisualizerGrid);
const MotionAgentAudioVisualizerRadial = motion.create(AgentAudioVisualizerRadial);
const MotionAgentAudioVisualizerWave = motion.create(AgentAudioVisualizerWave);

/**
 * Custom grid cell that paints a smooth diagonal rainbow: the hue is derived
 * from the cell's grid position (`data-lk-index`). Highlighted cells pop to
 * full opacity and scale up; idle cells stay as soft pastel dots. The whole
 * grid then slowly cycles hue via the `visualizer-rainbow` container class.
 */
function RainbowGridCell({
  'data-lk-index': index = 0,
  'data-lk-highlighted': highlighted = false,
  columnCount = 15,
  className,
  style,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  'data-lk-index'?: number;
  'data-lk-highlighted'?: boolean;
  columnCount?: number;
}) {
  const x = index % columnCount;
  const y = Math.floor(index / columnCount);
  const hue = ((x + y) * 360) / (columnCount * 2);

  return (
    <div
      {...props}
      data-lk-index={index}
      data-lk-highlighted={highlighted}
      className={cn(
        'h-2 w-2 place-self-center rounded-full transition-all ease-out',
        'opacity-20 data-[lk-highlighted=true]:scale-150 data-[lk-highlighted=true]:opacity-100',
        className
      )}
      style={{ ...style, background: `hsl(${hue} 95% 60%)` }}
    />
  );
}

interface AudioVisualizerProps extends MotionProps {
  themeMode?: 'dark' | 'light';
  isChatOpen: boolean;
  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura';
  audioVisualizerColor?: `#${string}`;
  audioVisualizerColorShift?: number;
  audioVisualizerWaveLineWidth?: number;
  audioVisualizerGridRowCount?: number;
  audioVisualizerGridColumnCount?: number;
  audioVisualizerRadialBarCount?: number;
  audioVisualizerRadialRadius?: number;
  audioVisualizerBarCount?: number;
  className?: string;
}

export function AudioVisualizer({
  themeMode,
  isChatOpen,
  audioVisualizerType = 'bar',
  audioVisualizerColor,
  audioVisualizerColorShift = 0.3,
  audioVisualizerBarCount = 5,
  audioVisualizerRadialRadius = 100,
  audioVisualizerRadialBarCount = 25,
  audioVisualizerGridRowCount = 15,
  audioVisualizerGridColumnCount = 15,
  audioVisualizerWaveLineWidth = 3,
  className,
  ...props
}: AudioVisualizerProps) {
  const { state, audioTrack } = useVoiceAssistant();

  switch (audioVisualizerType) {
    case 'aura': {
      return (
        <MotionAgentAudioVisualizerAura
          state={state}
          audioTrack={audioTrack}
          color={audioVisualizerColor}
          colorShift={audioVisualizerColorShift}
          themeMode={themeMode}
          className={cn('size-[300px] md:size-[450px]', className)}
          {...props}
        />
      );
    }
    case 'wave': {
      return (
        <motion.div className={className} {...props}>
          <MotionAgentAudioVisualizerWave
            state={state}
            audioTrack={audioTrack}
            color={audioVisualizerColor}
            colorShift={audioVisualizerColorShift}
            lineWidth={isChatOpen ? audioVisualizerWaveLineWidth * 2 : audioVisualizerWaveLineWidth}
            className="size-[300px] md:size-[450px]"
          />
        </motion.div>
      );
    }
    case 'grid': {
      const totalCount = audioVisualizerGridRowCount * audioVisualizerGridColumnCount;

      let size: 'icon' | 'sm' | 'md' | 'lg' | 'xl' = 'sm';
      if (totalCount < 100) {
        size = 'xl';
      } else if (totalCount < 200) {
        size = 'lg';
      } else if (totalCount < 300) {
        size = 'md';
      }

      return (
        <MotionAgentAudioVisualizerGrid
          size={size}
          state={state}
          color={audioVisualizerColor}
          audioTrack={audioTrack}
          rowCount={audioVisualizerGridRowCount}
          columnCount={audioVisualizerGridColumnCount}
          radius={Math.round(
            Math.min(audioVisualizerGridRowCount, audioVisualizerGridColumnCount) / 4
          )}
          className={cn(
            'visualizer-rainbow size-[350px] gap-0 p-8 *:place-self-center md:size-[450px]',
            className
          )}
          {...props}
        >
          <RainbowGridCell columnCount={audioVisualizerGridColumnCount} />
        </MotionAgentAudioVisualizerGrid>
      );
    }
    case 'radial': {
      return (
        <motion.div className={className} {...props}>
          <MotionAgentAudioVisualizerRadial
            size="xl"
            state={state}
            color={audioVisualizerColor}
            audioTrack={audioTrack}
            radius={audioVisualizerRadialRadius}
            barCount={audioVisualizerRadialBarCount}
            className="size-[450px]"
          />
        </motion.div>
      );
    }
    default: {
      let size: 'icon' | 'sm' | 'md' | 'lg' | 'xl' = 'icon';
      let sizedClassName = cn('size-[300px] md:size-[450px]', className);

      if (audioVisualizerBarCount <= 5) {
        size = 'xl';
        sizedClassName = cn('size-[450px] *:min-h-[64px] *:w-[64px] gap-4', className);
      } else if (audioVisualizerBarCount <= 10) {
        size = 'lg';
        sizedClassName = cn('size-[450px]', className);
      } else if (audioVisualizerBarCount <= 15) {
        size = 'md';
        sizedClassName = cn('size-[350px] md:size-[450px]', className);
      } else if (audioVisualizerBarCount <= 30) {
        size = 'sm';
        sizedClassName = cn('size-[300px] md:size-[450px]', className);
      }

      return (
        <MotionAgentAudioVisualizerBar
          size={size}
          state={state}
          color={audioVisualizerColor}
          audioTrack={audioTrack}
          barCount={audioVisualizerBarCount}
          className={sizedClassName}
          {...props}
        >
          <span className="min-h-2.5 w-2.5 rounded-full bg-current/10 transition-colors duration-250 ease-linear data-[lk-highlighted=true]:bg-current" />
        </MotionAgentAudioVisualizerBar>
      );
    }
  }
}
