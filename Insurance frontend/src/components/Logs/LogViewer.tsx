import React, { useState, useMemo } from 'react';
import {
  X, Download, Trash2, Filter, Search,
  User, MessageSquare, Bot, Mic,
  FileText, Activity
} from 'lucide-react';
import type { KnownLogAction, LogAction, LogEntry, LogFilter } from '../../types/log';

interface LogViewerProps {
  logs: LogEntry[];
  isOpen: boolean;
  onClose: () => void;
  onDownload: () => void;
  onClear: () => void;
  clients: { id: string; name: string }[];
}

interface ActionInfo {
  label: string;
  icon: React.ReactNode;
  color: string;
}

const actionLabels: Record<KnownLogAction, ActionInfo> = {
  LOGIN: { label: '登录', icon: <User className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  LOGOUT: { label: '登出', icon: <User className="w-3.5 h-3.5" />, color: 'bg-gray-100 text-gray-600' },
  LOGIN_HISTORY_PARSE: { label: '登录后同步', icon: <Activity className="w-3.5 h-3.5" />, color: 'bg-medium-gray/10 text-medium-gray' },
  INBOUND_MESSAGE: { label: '收到消息', icon: <MessageSquare className="w-3.5 h-3.5" />, color: 'bg-warning/10 text-warning' },
  SEND_MESSAGE: { label: '发送消息', icon: <MessageSquare className="w-3.5 h-3.5" />, color: 'bg-deep-trust/10 text-deep-trust' },
  AI_GENERATE: { label: 'AI生成', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-ai-blue text-deep-trust' },
  AI_DRAFT_READY: { label: '草稿完成', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-ai-blue text-deep-trust' },
  AUTO_DRAFT_READY: { label: '自动草稿', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  AI_SEND_DRAFT: { label: '发送AI草稿', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  AI_SEND_EDITED: { label: '发送编辑草稿', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  AI_EDIT_DRAFT: { label: '编辑AI草稿', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-warning/10 text-warning' },
  AI_DISCARD_DRAFT: { label: '丢弃AI草稿', icon: <Bot className="w-3.5 h-3.5" />, color: 'bg-gray-100 text-gray-600' },
  TOGGLE_AUTO_DRAFT: { label: '切换自动草稿', icon: <Activity className="w-3.5 h-3.5" />, color: 'bg-warm-navy/10 text-warm-navy' },
  SYNC_WHATSAPP: { label: '同步聊天', icon: <Activity className="w-3.5 h-3.5" />, color: 'bg-deep-trust/10 text-deep-trust' },
  ADD_REPLY_TARGET: { label: '添加目标', icon: <User className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  START_RECORDING: { label: '开始录音', icon: <Mic className="w-3.5 h-3.5" />, color: 'bg-safety-red/10 text-safety-red' },
  STOP_RECORDING: { label: '停止录音', icon: <Mic className="w-3.5 h-3.5" />, color: 'bg-success/10 text-success' },
  SELECT_CLIENT: { label: '选择客户', icon: <User className="w-3.5 h-3.5" />, color: 'bg-deep-trust/10 text-deep-trust' },
  BROADCAST: { label: '广播消息', icon: <MessageSquare className="w-3.5 h-3.5" />, color: 'bg-warning/10 text-warning' },
  VIEW_LOGS: { label: '查看日志', icon: <FileText className="w-3.5 h-3.5" />, color: 'bg-medium-gray/10 text-medium-gray' },
};

const formatUnknownActionLabel = (action: string): string =>
  action
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Unknown Action';

const getActionInfo = (action: LogAction): ActionInfo => {
  const actionInfo = actionLabels[action as KnownLogAction];
  if (actionInfo) {
    return actionInfo;
  }

  return {
    label: formatUnknownActionLabel(action),
    icon: <Activity className="w-3.5 h-3.5" />,
    color: 'bg-gray-100 text-gray-600',
  };
};

export const LogViewer: React.FC<LogViewerProps> = ({
  logs,
  isOpen,
  onClose,
  onDownload,
  onClear,
  clients,
}) => {
  const [filter, setFilter] = useState<LogFilter>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  const filteredLogs = useMemo(() => {
    let result = logs;

    // Apply filter
    if (filter.action) {
      result = result.filter((log) => log.action === filter.action);
    }
    if (filter.clientId) {
      result = result.filter((log) => log.clientId === filter.clientId);
    }
    if (filter.startDate) {
      result = result.filter((log) => new Date(log.timestamp) >= new Date(filter.startDate!));
    }
    if (filter.endDate) {
      result = result.filter((log) => new Date(log.timestamp) <= new Date(filter.endDate!));
    }

    // Apply search
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (log) =>
          log.description.toLowerCase().includes(query) ||
          log.clientName?.toLowerCase().includes(query) ||
          log.userName?.toLowerCase().includes(query)
      );
    }

    return result;
  }, [logs, filter, searchQuery]);

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

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-4xl h-[80vh] bg-white rounded-2xl shadow-modal flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border-light">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-deep-trust/[0.07] flex items-center justify-center">
              <FileText className="w-5 h-5 text-deep-trust" strokeWidth={1.5} />
            </div>
            <div>
              <h2 className="text-base font-bold text-deep-slate tracking-tight">活动日志</h2>
              <p className="text-[11px] text-medium-gray font-medium">共 {filteredLogs.length} 条记录</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold rounded-lg transition-all ${
                showFilters ? 'bg-deep-trust text-white shadow-sm' : 'bg-surface-warm text-deep-slate hover:bg-gray-100 border border-border-subtle'
              }`}
            >
              <Filter className="w-4 h-4" strokeWidth={1.5} />
              筛选
            </button>
            <button
              onClick={onDownload}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold bg-surface-warm text-deep-slate rounded-lg hover:bg-gray-100 border border-border-subtle transition-all"
            >
              <Download className="w-4 h-4" strokeWidth={1.5} />
              导出
            </button>
            <button
              onClick={onClear}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold bg-safety-red/8 text-safety-red rounded-lg hover:bg-safety-red/15 transition-all"
            >
              <Trash2 className="w-4 h-4" strokeWidth={1.5} />
              清空
            </button>
            <button
              onClick={onClose}
              className="p-1.5 hover:bg-surface rounded-subtle transition-colors ml-2"
            >
              <X className="w-5 h-5 text-medium-gray" strokeWidth={1.5} />
            </button>
          </div>
        </div>

        {/* Filters */}
        {showFilters && (
          <div className="p-4 bg-surface-warm border-b border-border-subtle">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label className="block text-[11px] font-semibold text-medium-gray mb-1 uppercase tracking-wider">操作类型</label>
                <select
                  value={filter.action || ''}
                  onChange={(e) => {
                    const nextAction = e.target.value as KnownLogAction | '';
                    setFilter((f) => ({ ...f, action: nextAction || undefined }));
                  }}
                  className="w-full px-2 py-1.5 text-sm bg-white border border-border-light rounded-subtle focus:outline-none focus:ring-1 focus:ring-deep-trust/20"
                >
                  <option value="">全部</option>
                  {Object.entries(actionLabels).map(([key, { label }]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-medium-gray mb-1 uppercase tracking-wider">客户</label>
                <select
                  value={filter.clientId || ''}
                  onChange={(e) => setFilter((f) => ({ ...f, clientId: e.target.value || undefined }))}
                  className="w-full px-2 py-1.5 text-sm bg-white border border-border-light rounded-subtle focus:outline-none focus:ring-1 focus:ring-deep-trust/20"
                >
                  <option value="">全部</option>
                  {clients.map((client) => (
                    <option key={client.id} value={client.id}>{client.name.charAt(0)}**</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-medium-gray mb-1 uppercase tracking-wider">开始日期</label>
                <input
                  type="date"
                  value={filter.startDate || ''}
                  onChange={(e) => setFilter((f) => ({ ...f, startDate: e.target.value || undefined }))}
                  className="w-full px-2 py-1.5 text-sm bg-white border border-border-light rounded-subtle focus:outline-none focus:ring-1 focus:ring-deep-trust/20"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-medium-gray mb-1 uppercase tracking-wider">结束日期</label>
                <input
                  type="date"
                  value={filter.endDate || ''}
                  onChange={(e) => setFilter((f) => ({ ...f, endDate: e.target.value || undefined }))}
                  className="w-full px-2 py-1.5 text-sm bg-white border border-border-light rounded-subtle focus:outline-none focus:ring-1 focus:ring-deep-trust/20"
                />
              </div>
            </div>
          </div>
        )}

        {/* Search */}
        <div className="px-4 py-3 border-b border-border-light">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-medium-gray" strokeWidth={1.5} />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索日志内容..."
              className="w-full pl-9 pr-4 py-2 text-sm bg-surface-warm border border-border-subtle rounded-lg focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:bg-white transition-all placeholder:text-medium-gray/50"
            />
          </div>
        </div>

        {/* Log List */}
        <div className="flex-1 overflow-y-auto p-2">
          {filteredLogs.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-8">
              <FileText className="w-12 h-12 text-medium-gray/30 mb-3" strokeWidth={1.5} />
              <p className="text-sm text-medium-gray">暂无日志记录</p>
            </div>
          ) : (
            <div className="space-y-1">
              {filteredLogs.map((log, index) => {
                const actionInfo = getActionInfo(log.action);
                const showDate = index === 0 ||
                  new Date(log.timestamp).toDateString() !== new Date(filteredLogs[index - 1].timestamp).toDateString();

                return (
                  <React.Fragment key={log.id}>
                    {showDate && (
                      <div className="sticky top-0 z-10 py-2 px-3 bg-white/80 backdrop-blur-sm">
                        <span className="text-xs font-medium text-medium-gray">
                          {new Date(log.timestamp).toLocaleDateString('zh-CN', {
                            year: 'numeric',
                            month: 'long',
                            day: 'numeric',
                            weekday: 'short'
                          })}
                        </span>
                      </div>
                    )}
                    <div className="flex items-start gap-3 p-3 rounded-lg hover:bg-surface-warm transition-all group">
                      {/* Action Icon */}
                      <div className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center ${actionInfo.color}`}>
                        {actionInfo.icon}
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-medium text-deep-slate">
                                {actionInfo.label}
                              </span>
                              {log.clientName && (
                                <span className="text-xs text-medium-gray">
                                  · {log.clientName.charAt(0)}**
                                </span>
                              )}
                            </div>
                            <p className="text-sm text-deep-slate mt-0.5">{log.description}</p>
                            {log.details && Object.keys(log.details).length > 0 && (
                              <pre className="mt-2 p-2 bg-white rounded text-xs text-medium-gray overflow-x-auto">
                                {JSON.stringify(log.details, null, 2)}
                              </pre>
                            )}
                          </div>
                          <span className="flex-shrink-0 text-xs text-medium-gray font-mono">
                            {formatTime(log.timestamp)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </React.Fragment>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LogViewer;
