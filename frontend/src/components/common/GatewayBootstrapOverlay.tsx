import React from 'react';
import { Loader2, ShieldAlert, Smartphone } from 'lucide-react';
import QRCodePanel from './QRCodePanel';

interface GatewayBootstrapOverlayProps {
  progress: number;
  title: string;
  description: string;
  authRequired?: boolean;
  qrValue?: string | null;
  authMessage?: string | null;
}

export const GatewayBootstrapOverlay: React.FC<GatewayBootstrapOverlayProps> = ({
  progress,
  title,
  description,
  authRequired = false,
  qrValue,
  authMessage,
}) => {
  const clamped = Math.max(8, Math.min(100, progress));

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/92 backdrop-blur-sm">
      <div className="w-full max-w-2xl px-6">
        <div className="rounded-[28px] border border-border-light bg-white shadow-elevated">
          <div className="border-b border-border-light px-6 py-5">
            <div className="flex items-start gap-4">
              <div className={`mt-0.5 flex h-11 w-11 items-center justify-center rounded-2xl ${authRequired ? 'bg-safety-red/10 text-safety-red' : 'bg-deep-trust/10 text-deep-trust'}`}>
                {authRequired ? <ShieldAlert className="h-5 w-5" strokeWidth={1.75} /> : <Loader2 className="h-5 w-5 animate-spin" strokeWidth={1.75} />}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-base font-semibold tracking-tight text-deep-slate">{title}</p>
                <p className="mt-1 text-sm leading-6 text-medium-gray">{description}</p>
              </div>
            </div>
          </div>

          <div className="px-6 py-5">
            {!authRequired && (
              <>
                <div className="flex items-center justify-between text-xs font-medium text-medium-gray">
                  <span>正在同步 Nanobot 与 WhatsApp 会话</span>
                  <span>{Math.round(clamped)}%</span>
                </div>
                <div className="mt-3 h-3 overflow-hidden rounded-full bg-surface-warm">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-deep-trust via-warm-navy to-deep-trust transition-all duration-500 ease-out"
                    style={{ width: `${clamped}%` }}
                  />
                </div>
                <div className="mt-4 grid grid-cols-3 gap-3 text-xs text-medium-gray">
                  <div className={`rounded-xl border px-3 py-2 ${clamped >= 20 ? 'border-deep-trust/25 bg-deep-trust/5 text-deep-slate' : 'border-border-light bg-surface-warm'}`}>
                    启动网关
                  </div>
                  <div className={`rounded-xl border px-3 py-2 ${clamped >= 55 ? 'border-deep-trust/25 bg-deep-trust/5 text-deep-slate' : 'border-border-light bg-surface-warm'}`}>
                    连接 WhatsApp
                  </div>
                  <div className={`rounded-xl border px-3 py-2 ${clamped >= 90 ? 'border-deep-trust/25 bg-deep-trust/5 text-deep-slate' : 'border-border-light bg-surface-warm'}`}>
                    载入会话内容
                  </div>
                </div>
              </>
            )}

            {authRequired && qrValue && (
              <div className="mt-1 grid gap-5 md:grid-cols-[minmax(0,1fr)_280px] md:items-center">
                <div>
                  <div className="inline-flex items-center gap-2 rounded-full bg-safety-red/8 px-3 py-1 text-xs font-medium text-safety-red">
                    <Smartphone className="h-3.5 w-3.5" strokeWidth={1.75} />
                    WhatsApp 需要重新关联设备
                  </div>
                  <p className="mt-4 text-sm leading-6 text-medium-gray">
                    {authMessage || 'You need to login again to the whatsapp web browser'}
                  </p>
                  <p className="mt-2 text-xs leading-5 text-medium-gray/80">
                    保持当前浏览器窗口开启，使用手机 WhatsApp 扫码后，系统会继续自动载入客户列表和历史消息。
                  </p>
                </div>
                <QRCodePanel value={qrValue} className="mx-auto w-full max-w-[280px]" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default GatewayBootstrapOverlay;
