import { useMemo, useState } from 'react';
import { Download, FileText, Filter, Trash2, X } from 'lucide-react';
import type { KnownLogAction, LogAction, LogEntry } from '../../types/log';

interface LogViewerProps {
  logs: LogEntry[];
  isOpen: boolean;
  onClose: () => void;
  onDownload: () => void;
  onClear: () => void;
  clients: { id: string; name: string }[];
}

const ACTION_LABELS: Record<KnownLogAction, string> = {
  LOGIN: '登录',
  LOGOUT: '退出',
  LOGIN_HISTORY_PARSE: '解析历史记录',
  INBOUND_MESSAGE: '收到消息',
  SEND_MESSAGE: '发送消息',
  AI_GENERATE: 'AI生成',
  AI_DRAFT_READY: 'AI草稿就绪',
  AUTO_DRAFT_READY: '自动草稿就绪',
  AI_SEND_DRAFT: 'AI发送草稿',
  AI_SEND_EDITED: 'AI发送编辑',
  AI_EDIT_DRAFT: 'AI编辑草稿',
  AI_DISCARD_DRAFT: 'AI放弃草稿',
  TOGGLE_AUTO_DRAFT: '切换自动草稿',
  SYNC_WHATSAPP: '同步WhatsApp',
  ADD_REPLY_TARGET: '添加回复目标',
  START_RECORDING: '开始录音',
  STOP_RECORDING: '停止录音',
  SELECT_CLIENT: '选择客户',
  BROADCAST: '广播',
  VIEW_LOGS: '查看日志',
};

const ACTION_COLORS: Record<string, string> = {
  LOGIN: 'bg-success/10 text-success',
  LOGOUT: 'bg-medium-gray/10 text-medium-gray',
  INBOUND_MESSAGE: 'bg-deep-trust/10 text-deep-trust',
  SEND_MESSAGE: 'bg-warm-navy/10 text-warm-navy',
  AI_GENERATE: 'bg-purple-500/10 text-purple-600',
  AI_DRAFT_READY: 'bg-purple-500/10 text-purple-600',
  AUTO_DRAFT_READY: 'bg-purple-500/10 text-purple-600',
  BROADCAST: 'bg-orange-500/10 text-orange-600',
  SYNC_WHATSAPP: 'bg-blue-500/10 text-blue-600',
  default: 'bg-surface text-deep-slate',
};

export function LogViewer({
  logs,
  isOpen,
  onClose,
  onDownload,
  onClear,
  clients,
}: LogViewerProps) {
  const [filterAction, setFilterAction] = useState<LogAction | ''>('');
  const [filterClient, setFilterClient] = useState('');

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      if (filterAction && log.action !== filterAction) return false;
      if (filterClient && log.clientId !== filterClient) return false;
      return true;
    });
  }, [logs, filterAction, filterClient]);

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const getActionColor = (action: LogAction) => {
    return ACTION_COLORS[action] || ACTION_COLORS.default;
  };

  const getActionLabel = (action: LogAction) => {
    return ACTION_LABELS[action as KnownLogAction] || action;
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-deep-slate/40 px-4 backdrop-blur-[2px]">
      <div className="flex max-h-[85vh] w-full max-w-4xl flex-col rounded-2xl border border-border-light bg-white shadow-2xl">
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border-light px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-deep-trust/10">
              <FileText className="h-5 w-5 text-deep-trust" strokeWidth={1.5} />
            </div>
            <div>
              <h3 className="text-lg font-bold text-deep-slate">系统日志</h3>
              <p className="text-xs text-medium-gray">共 {filteredLogs.length} 条记录</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onDownload}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-deep-trust transition-colors hover:bg-deep-trust/5"
            >
              <Download className="h-4 w-4" strokeWidth={1.5} />
              导出
            </button>
            <button
              onClick={onClear}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-safety-red transition-colors hover:bg-safety-red/5"
            >
              <Trash2 className="h-4 w-4" strokeWidth={1.5} />
              清空
            </button>
            <button
              onClick={onClose}
              className="rounded-lg p-2 transition-colors hover:bg-surface"
            >
              <X className="h-5 w-5 text-medium-gray" strokeWidth={1.5} />
            </button>
          </div>
        </div>

        <div className="flex flex-shrink-0 items-center gap-3 border-b border-border-light bg-surface/50 px-6 py-3">
          <Filter className="h-4 w-4 text-medium-gray" strokeWidth={1.5} />
          <select
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value as LogAction | '')}
            className="rounded-lg border border-border-light bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-deep-trust/20"
          >
            <option value="">所有操作</option>
            {Object.entries(ACTION_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <select
            value={filterClient}
            onChange={(e) => setFilterClient(e.target.value)}
            className="rounded-lg border border-border-light bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-deep-trust/20"
          >
            <option value="">所有客户</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {filteredLogs.length === 0 ? (
            <div className="py-12 text-center">
              <FileText className="mx-auto mb-3 h-12 w-12 text-medium-gray/30" strokeWidth={1.5} />
              <p className="text-sm text-medium-gray">暂无日志记录</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredLogs.map((log) => (
                <div
                  key={log.id}
                  className="flex items-start gap-3 rounded-xl bg-surface/50 p-3 transition-colors hover:bg-surface"
                >
                  <span className="mt-0.5 whitespace-nowrap text-xs text-medium-gray">
                    {formatTime(log.timestamp)}
                  </span>
                  <span
                    className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold ${getActionColor(
                      log.action
                    )}`}
                  >
                    {getActionLabel(log.action)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-deep-slate">{log.description}</p>
                    {log.clientName && (
                      <p className="mt-0.5 text-xs text-medium-gray">客户: {log.clientName}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
