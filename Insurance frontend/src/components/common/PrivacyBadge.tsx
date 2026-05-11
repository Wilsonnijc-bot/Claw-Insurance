import React from 'react';
import { Shield } from 'lucide-react';

export const PrivacyBadge: React.FC = () => {
  return (
    <div 
      className="flex items-center gap-1.5 px-2.5 py-1 bg-gradient-to-r from-surface-warm to-surface rounded-full border border-border-subtle shadow-sm"
      title="仅显示客户姓名首字，保护客户隐私"
    >
      <Shield className="w-3.5 h-3.5 text-deep-trust" strokeWidth={1.5} />
      <span className="text-[10px] font-semibold text-deep-slate">隐私保护</span>
    </div>
  );
};

export default PrivacyBadge;
