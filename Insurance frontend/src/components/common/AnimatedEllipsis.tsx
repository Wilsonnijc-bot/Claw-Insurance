import React from 'react';

interface AnimatedEllipsisProps {
  className?: string;
}

export const AnimatedEllipsis: React.FC<AnimatedEllipsisProps> = ({ className = '' }) => {
  return (
    <span className={`inline-flex items-center ${className}`} aria-hidden="true">
      <span
        className="inline-block w-1 h-1 bg-current rounded-full animate-bounce-dot"
        style={{ animationDelay: '0ms' }}
      />
      <span
        className="inline-block w-1 h-1 bg-current rounded-full animate-bounce-dot ml-0.5"
        style={{ animationDelay: '150ms' }}
      />
      <span
        className="inline-block w-1 h-1 bg-current rounded-full animate-bounce-dot ml-0.5"
        style={{ animationDelay: '300ms' }}
      />
    </span>
  );
};

export default AnimatedEllipsis;
