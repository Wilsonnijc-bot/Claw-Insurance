import React, { useEffect, useState } from 'react';
import { Search, Trash2, Users } from 'lucide-react';
import { ClientCard } from './ClientCard';
import { PrivacyBadge } from '../common/PrivacyBadge';
import type { Client } from '../../types';

interface ClientListProps {
  clients: Client[];
  selectedClientId: string | null;
  onSelectClient: (clientId: string) => void;
  onToggleAutoDraft: (clientId: string) => void;
  onRequestDeleteClient: (clientId: string) => void;
}

export const ClientList: React.FC<ClientListProps> = ({
  clients,
  selectedClientId,
  onSelectClient,
  onToggleAutoDraft,
  onRequestDeleteClient,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [contextMenu, setContextMenu] = useState<{ clientId: string; x: number; y: number } | null>(null);

  const filteredClients = clients.filter((client) =>
    client.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  useEffect(() => {
    if (!contextMenu) return;

    const handleClose = () => setContextMenu(null);
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setContextMenu(null);
      }
    };

    window.addEventListener('click', handleClose);
    window.addEventListener('scroll', handleClose, true);
    window.addEventListener('resize', handleClose);
    window.addEventListener('keydown', handleEscape);

    return () => {
      window.removeEventListener('click', handleClose);
      window.removeEventListener('scroll', handleClose, true);
      window.removeEventListener('resize', handleClose);
      window.removeEventListener('keydown', handleEscape);
    };
  }, [contextMenu]);

  const menuPosition = contextMenu ? {
    left: Math.min(contextMenu.x, Math.max(16, window.innerWidth - 180)),
    top: Math.min(contextMenu.y, Math.max(16, window.innerHeight - 72)),
  } : null;

  return (
    <div className="w-[320px] flex flex-col h-full bg-light-gray border-r border-border-light">
      {/* Header */}
      <div className="p-4 border-b border-border-light bg-gradient-to-b from-white to-light-gray">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-deep-trust/8 flex items-center justify-center">
              <Users className="w-4 h-4 text-deep-trust" strokeWidth={1.5} />
            </div>
            <h2 className="text-[15px] font-bold text-deep-slate tracking-tight">客户列表</h2>
          </div>
          <PrivacyBadge />
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-medium-gray/60" strokeWidth={1.5} />
          <input
            type="text"
            placeholder="搜索客户..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm bg-white border border-border-light rounded-lg focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:shadow-blue-glow transition-all placeholder:text-medium-gray/50"
          />
        </div>
      </div>

      {/* Client List */}
      <div className="flex-1 overflow-y-auto">
        {filteredClients.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-sm text-medium-gray">未找到匹配的客户</p>
          </div>
        ) : (
          filteredClients.map((client) => (
            <ClientCard
              key={client.id}
              client={client}
              isSelected={selectedClientId === client.id}
              onClick={() => onSelectClient(client.id)}
              onToggleAutoDraft={() => onToggleAutoDraft(client.id)}
              onContextMenu={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setContextMenu({ clientId: client.id, x: event.clientX, y: event.clientY });
              }}
            />
          ))
        )}
      </div>

      {/* Footer Stats */}
      <div className="p-3 border-t border-border-light bg-white">
        <div className="flex items-center justify-between text-[11px] text-medium-gray">
          <span className="font-medium">共 {clients.length} 位客户</span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-success ring-2 ring-success/20" />
            {clients.filter((c) => c.status === 'online').length} 在线
          </span>
        </div>
      </div>

      {contextMenu && menuPosition && (
        <div
          className="fixed z-50 min-w-[160px] rounded-xl border border-border-light bg-white p-1.5 shadow-xl"
          style={menuPosition}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            onClick={() => {
              onRequestDeleteClient(contextMenu.clientId);
              setContextMenu(null);
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-safety-red transition-colors hover:bg-safety-red/[0.06]"
          >
            <Trash2 className="h-4 w-4" strokeWidth={1.5} />
            删除聊天
          </button>
        </div>
      )}
    </div>
  );
};

export default ClientList;
