import React, { useState } from 'react';
import { AlertTriangle, XCircle, RefreshCw } from 'lucide-react';

interface FloatingStatusNoticeProps {
  title: string;
  message: string;
  severity?: 'warning' | 'error';
  action?: {
    label: string;
    onClick: () => Promise<void> | void;
  };
}

export const FloatingStatusNotice: React.FC<FloatingStatusNoticeProps> = ({
  title,
  message,
  severity = 'warning',
  action,
}) => {
  const [busy, setBusy] = useState(false);

  const isError = severity === 'error';

  const handleAction = async () => {
    if (!action || busy) return;
    setBusy(true);
    try {
      await action.onClick();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pointer-events-none fixed right-4 top-[4.5rem] z-50 w-[min(360px,calc(100vw-2rem))] animate-slide-in-right">
      <div className={`pointer-events-auto overflow-hidden rounded-2xl border shadow-elevated ${
        isError
          ? 'border-safety-red/30 bg-[#fff0f0]'
          : 'border-warning/30 bg-[#fff8e6]'
      }`}>
        <div className="flex items-start gap-3 px-4 py-3.5">
          <div className={`mt-0.5 rounded-2xl p-2 ${
            isError
              ? 'bg-safety-red/10 text-safety-red'
              : 'bg-warning/15 text-[#9a6700]'
          }`}>
            {isError
              ? <XCircle className="h-4 w-4" strokeWidth={1.75} />
              : <AlertTriangle className="h-4 w-4" strokeWidth={1.75} />
            }
          </div>
          <div className="min-w-0 flex-1">
            <p className={`text-sm font-semibold tracking-tight ${
              isError ? 'text-safety-red' : 'text-[#8a5a00]'
            }`}>{title}</p>
            <p className={`mt-1 text-xs leading-5 ${
              isError ? 'text-safety-red/80' : 'text-[#946200]'
            }`}>{message}</p>
            {action && (
              <button
                onClick={handleAction}
                disabled={busy}
                className={`mt-2 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                  isError
                    ? 'bg-safety-red/10 text-safety-red hover:bg-safety-red/20 disabled:opacity-50'
                    : 'bg-warning/15 text-[#8a5a00] hover:bg-warning/25 disabled:opacity-50'
                }`}
              >
                <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} strokeWidth={2} />
                {busy ? '执行中…' : action.label}
              </button>
            )}
          </div>
        </div>
        <div className={`h-1 w-full ${
          isError
            ? 'bg-gradient-to-r from-safety-red/50 via-safety-red/80 to-safety-red/50'
            : 'bg-gradient-to-r from-warning/65 via-warning to-warning/65'
        }`} />
      </div>
    </div>
  );
};

export default FloatingStatusNotice;