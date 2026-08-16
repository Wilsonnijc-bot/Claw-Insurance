import React, { useState } from 'react';
import { X, Send, Users, AlertCircle } from 'lucide-react';
import type { Client } from '../../types';

interface BroadcastModalProps {
  isOpen: boolean;
  onClose: () => void;
  clients: Client[];
  onSendBroadcast: (clientIds: string[], message: string) => void;
}

export const BroadcastModal: React.FC<BroadcastModalProps> = ({
  isOpen,
  onClose,
  clients,
  onSendBroadcast,
}) => {
  const [selectedClients, setSelectedClients] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [step, setStep] = useState<1 | 2>(1);

  if (!isOpen) return null;

  const toggleClient = (clientId: string) => {
    setSelectedClients((prev) =>
      prev.includes(clientId)
        ? prev.filter((id) => id !== clientId)
        : [...prev, clientId]
    );
  };

  const toggleAll = () => {
    setSelectedClients((prev) =>
      prev.length === clients.length ? [] : clients.map((c) => c.id)
    );
  };

  const handleSend = () => {
    if (message.trim() && selectedClients.length > 0) {
      onSendBroadcast(selectedClients, message.trim());
      onClose();
      setMessage('');
      setSelectedClients([]);
      setStep(1);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="w-[500px] max-h-[80vh] bg-white rounded-2xl shadow-modal overflow-hidden animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border-light">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-warning/10 flex items-center justify-center">
              <Users className="w-4 h-4 text-warning" strokeWidth={1.5} />
            </div>
            <div>
              <h3 className="text-base font-bold text-deep-slate tracking-tight">广播消息</h3>
              <p className="text-[11px] text-medium-gray font-medium">
                步骤 {step}/2: {step === 1 ? '选择客户' : '编辑消息'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-surface rounded-subtle transition-colors"
          >
            <X className="w-5 h-5 text-medium-gray" strokeWidth={1.5} />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-[50vh]">
          {step === 1 ? (
            <>
              {/* Select All */}
              <div className="flex items-center justify-between mb-3 pb-3 border-b border-border-light">
                <span className="text-sm font-medium text-deep-slate">
                  已选择 {selectedClients.length} 位客户
                </span>
                <button
                  onClick={toggleAll}
                  className="text-xs text-deep-trust hover:underline"
                >
                  {selectedClients.length === clients.length ? '取消全选' : '全选'}
                </button>
              </div>

              {/* Client List */}
              <div className="space-y-2">
                {clients.map((client) => (
                  <label
                    key={client.id}
                    className="flex items-center gap-3 p-2.5 rounded-lg hover:bg-surface-warm cursor-pointer transition-all group"
                  >
                    <input
                      type="checkbox"
                      checked={selectedClients.includes(client.id)}
                      onChange={() => toggleClient(client.id)}
                      className="w-4 h-4 rounded border-border-light text-deep-trust focus:ring-deep-trust/20"
                    />
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center text-white text-xs font-semibold shadow-sm">
                      {client.name.charAt(0)}
                    </div>
                    <div className="flex-1">
                      <p className="text-sm font-medium text-deep-slate">
                        {client.name.charAt(0)}**
                      </p>
                      <p className="text-xs text-medium-gray">{client.tags.join(', ')}</p>
                    </div>
                  </label>
                ))}
              </div>
            </>
          ) : (
            <>
              {/* Selected Count */}
              <div className="flex items-center gap-2 mb-3 p-2.5 bg-surface-warm rounded-lg border border-border-subtle">
                <AlertCircle className="w-4 h-4 text-warning" strokeWidth={1.5} />
                <span className="text-xs text-deep-slate font-medium">
                  将向 <strong>{selectedClients.length}</strong> 位客户发送消息
                </span>
              </div>

              {/* Message Input */}
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="输入广播消息内容..."
                className="w-full h-32 px-3 py-2.5 bg-surface-warm border border-border-subtle rounded-xl resize-none focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:bg-white text-sm text-deep-slate placeholder:text-medium-gray/50 transition-all"
              />
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t border-border-light bg-surface-warm">
          {step === 2 && (
            <button
              onClick={() => setStep(1)}
              className="px-4 py-2 text-sm font-medium text-medium-gray hover:text-deep-slate hover:bg-white rounded-lg transition-all"
            >
              上一步
            </button>
          )}
          {step === 1 ? (
            <button
              onClick={() => selectedClients.length > 0 && setStep(2)}
              disabled={selectedClients.length === 0}
              className="px-4 py-2 bg-deep-trust text-white text-sm font-semibold rounded-lg hover:bg-deep-trust/90 hover:shadow-md hover:shadow-deep-trust/15 transition-all disabled:opacity-50 btn-press"
            >
              下一步
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!message.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-deep-trust text-white text-sm font-semibold rounded-lg hover:bg-deep-trust/90 hover:shadow-md hover:shadow-deep-trust/15 transition-all disabled:opacity-50 btn-press"
            >
              <Send className="w-4 h-4" strokeWidth={1.5} />
              发送广播
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default BroadcastModal;
