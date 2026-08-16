import React, { useState } from 'react';
import { X, UserPlus, Phone, Tag, Zap } from 'lucide-react';

interface AddReplyTargetModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAdd: (phone: string, label?: string, autoDraft?: boolean) => Promise<boolean>;
  backendConnected: boolean;
}

export const AddReplyTargetModal: React.FC<AddReplyTargetModalProps> = ({
  isOpen,
  onClose,
  onAdd,
  backendConnected,
}) => {
  const [phone, setPhone] = useState('');
  const [label, setLabel] = useState('');
  const [autoDraft, setAutoDraft] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess(false);

    const cleaned = phone.replace(/[^0-9]/g, '');
    if (!cleaned || cleaned.length < 5) {
      setError('请输入有效的电话号码（至少5位数字）');
      return;
    }

    setSubmitting(true);
    try {
      const ok = await onAdd(cleaned, label || undefined, autoDraft);
      if (ok) {
        setSuccess(true);
        setTimeout(() => {
          setPhone('');
          setLabel('');
          setAutoDraft(true);
          setSuccess(false);
          onClose();
        }, 1200);
      } else {
        setError('添加失败，请检查网络连接或号码是否已存在');
      }
    } catch {
      setError('添加失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    if (submitting) return;
    setPhone('');
    setLabel('');
    setAutoDraft(true);
    setError('');
    setSuccess(false);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="relative bg-white rounded-2xl shadow-elevated w-full max-w-md mx-4 overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-border-light">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center">
              <UserPlus className="w-4.5 h-4.5 text-white" strokeWidth={1.5} />
            </div>
            <div>
              <h2 className="text-base font-bold text-deep-slate tracking-tight">添加回复目标</h2>
              <p className="text-[11px] text-medium-gray mt-0.5">输入 WhatsApp 电话号码</p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-2 hover:bg-surface rounded-lg transition-colors"
          >
            <X className="w-4 h-4 text-medium-gray" strokeWidth={1.5} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {/* Phone input */}
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-medium-gray mb-2">
              <Phone className="w-3.5 h-3.5" strokeWidth={1.5} />
              电话号码 <span className="text-safety-red">*</span>
            </label>
            <input
              type="tel"
              value={phone}
              onChange={(e) => { setPhone(e.target.value); setError(''); }}
              placeholder="例如: 85268424658"
              className="w-full px-3.5 py-2.5 text-sm bg-surface-warm border border-border-subtle rounded-xl focus:outline-none focus:ring-2 focus:ring-deep-trust/20 focus:border-deep-trust/40 placeholder:text-medium-gray/50 transition-all"
              autoFocus
              disabled={submitting}
            />
            <p className="text-[10px] text-medium-gray/70 mt-1.5 ml-1">
              输入包含国家码的完整号码（不含 + 号）
            </p>
          </div>

          {/* Label input */}
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-medium-gray mb-2">
              <Tag className="w-3.5 h-3.5" strokeWidth={1.5} />
              备注名称 <span className="text-medium-gray/50 font-normal">(可选)</span>
            </label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例如: 张先生"
              className="w-full px-3.5 py-2.5 text-sm bg-surface-warm border border-border-subtle rounded-xl focus:outline-none focus:ring-2 focus:ring-deep-trust/20 focus:border-deep-trust/40 placeholder:text-medium-gray/50 transition-all"
              disabled={submitting}
            />
          </div>

          {/* Auto-draft toggle */}
          <div className="flex items-center justify-between py-2">
            <label className="flex items-center gap-2 text-xs font-semibold text-medium-gray">
              <Zap className="w-3.5 h-3.5 text-amber-500" strokeWidth={1.5} />
              启用 AI 自动草稿
            </label>
            <button
              type="button"
              onClick={() => setAutoDraft(!autoDraft)}
              disabled={submitting}
              className={`relative w-10 h-[22px] rounded-full transition-colors ${
                autoDraft ? 'bg-success' : 'bg-gray-300'
              }`}
            >
              <span
                className={`absolute top-[2px] w-[18px] h-[18px] rounded-full bg-white shadow-sm transition-transform ${
                  autoDraft ? 'left-[20px]' : 'left-[2px]'
                }`}
              />
            </button>
          </div>

          {/* Error */}
          {error && (
            <div className="px-3 py-2 text-xs text-safety-red bg-safety-red/5 border border-safety-red/15 rounded-lg">
              {error}
            </div>
          )}

          {/* Success */}
          {success && (
            <div className="px-3 py-2 text-xs text-success bg-success/5 border border-success/15 rounded-lg">
              ✓ 已成功添加回复目标
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="flex-1 px-4 py-2.5 text-sm font-medium text-medium-gray bg-surface-warm border border-border-subtle rounded-xl hover:bg-gray-100 transition-all"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting || !phone.trim() || !backendConnected}
              className={`flex-1 px-4 py-2.5 text-sm font-semibold rounded-xl transition-all btn-press ${
                submitting || !phone.trim() || !backendConnected
                  ? 'bg-gray-200 text-medium-gray cursor-not-allowed'
                  : 'bg-gradient-to-r from-deep-trust to-warm-navy text-white shadow-sm hover:shadow-md'
              }`}
            >
              {submitting ? '添加中...' : '保存'}
            </button>
          </div>

          {!backendConnected && (
            <p className="text-[10px] text-center text-warning">
              后端未连接，无法添加回复目标
            </p>
          )}
        </form>
      </div>
    </div>
  );
};
