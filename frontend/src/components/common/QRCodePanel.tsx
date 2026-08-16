import React, { useEffect, useState } from 'react';
import QRCode from 'qrcode';

interface QRCodePanelProps {
  value: string;
  title?: string;
  description?: string;
  className?: string;
}

export const QRCodePanel: React.FC<QRCodePanelProps> = ({
  value,
  title = 'WhatsApp 登录二维码',
  description = '请使用 WhatsApp 的“关联设备”扫描此二维码。',
  className = '',
}) => {
  const [dataUrl, setDataUrl] = useState('');

  useEffect(() => {
    let cancelled = false;

    QRCode.toDataURL(value, {
      errorCorrectionLevel: 'M',
      margin: 1,
      width: 220,
      color: {
        dark: '#10243f',
        light: '#ffffff',
      },
    })
      .then((url: string) => {
        if (!cancelled) {
          setDataUrl(url);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDataUrl('');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [value]);

  return (
    <div className={`rounded-2xl border border-border-light bg-white px-5 py-5 shadow-card ${className}`}>
      <div className="flex flex-col items-center text-center">
        <div className="rounded-2xl bg-surface-warm p-3 shadow-inner">
          {dataUrl ? (
            <img src={dataUrl} alt={title} className="h-[220px] w-[220px] rounded-xl" />
          ) : (
            <div className="flex h-[220px] w-[220px] items-center justify-center rounded-xl bg-white text-sm text-medium-gray">
              正在生成二维码...
            </div>
          )}
        </div>
        <h3 className="mt-4 text-sm font-semibold text-deep-slate">{title}</h3>
        <p className="mt-1 max-w-[260px] text-xs leading-5 text-medium-gray">{description}</p>
      </div>
    </div>
  );
};

export default QRCodePanel;
