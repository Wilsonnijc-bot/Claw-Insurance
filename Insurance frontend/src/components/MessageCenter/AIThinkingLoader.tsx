import React, { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { thinkingStates } from '../../types';
import { AnimatedEllipsis } from '../common/AnimatedEllipsis';

interface AIThinkingLoaderProps {
  className?: string;
}

export const AIThinkingLoader: React.FC<AIThinkingLoaderProps> = ({ className = '' }) => {
  const [currentPhase, setCurrentPhase] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentPhase((prev) => ((prev + 1) % 4) as 0 | 1 | 2 | 3);
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  const currentState = thinkingStates[currentPhase];

  return (
    <div
      className={`flex items-start gap-3 p-4 bg-gradient-to-r from-surface-warm to-surface rounded-xl border border-border-subtle border-l-[3px] border-l-deep-trust ${className}`}
      aria-live="polite"
      aria-busy="true"
    >
      {/* Animated Pulse Icon */}
      <div className="relative flex-shrink-0">
        <div className="absolute inset-0 bg-deep-trust/15 rounded-full animate-pulse-soft" />
        <div className="relative w-8 h-8 flex items-center justify-center bg-gradient-to-br from-deep-trust/10 to-deep-trust/5 rounded-full">
          <Sparkles className="w-4 h-4 text-deep-trust animate-pulse-soft" strokeWidth={1.5} />
        </div>
      </div>

      {/* Text Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-deep-slate">
            {currentState.text}
          </span>
          <AnimatedEllipsis className="text-deep-trust" />
        </div>
        <p className="text-xs text-medium-gray mt-0.5">
          {currentState.subtext}
        </p>
      </div>

      {/* Progress Indicator */}
      <div className="flex items-center gap-1">
        {thinkingStates.map((_, index) => (
          <div
            key={index}
            className={`w-1.5 h-1.5 rounded-full transition-colors duration-300 ${
              index === currentPhase ? 'bg-deep-trust' : 'bg-border-light'
            }`}
          />
        ))}
      </div>
    </div>
  );
};

export default AIThinkingLoader;
