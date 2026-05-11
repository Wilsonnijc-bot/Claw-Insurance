import React from 'react';

import type { Client } from '../../types';

interface ClientCardProps {
  client: Client;
  isSelected: boolean;
  onClick: () => void;
  onToggleAutoDraft: () => void;
  onContextMenu: (event: React.MouseEvent<HTMLDivElement>) => void;
}

export const ClientCard: React.FC<ClientCardProps> = ({
  client,
  isSelected,
  onClick,
  onToggleAutoDraft,
  onContextMenu,
}) => {
  // Privacy mask: show first character only
  const maskedName = client.name.charAt(0) + '**';

  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      className={`p-4 cursor-pointer transition-all duration-200 border-b border-border-subtle last:border-b-0 group ${
        isSelected
          ? 'bg-gradient-to-r from-ai-blue to-white border-l-[3px] border-l-deep-trust shadow-sm'
          : 'bg-white hover:bg-surface-warm border-l-[3px] border-l-transparent hover:border-l-deep-trust/20'
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Avatar with Status */}
        <div className="relative flex-shrink-0">
          <div className={`w-10 h-10 rounded-full bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center text-white font-semibold text-sm shadow-sm ${isSelected ? 'ring-2 ring-deep-trust/20' : ''}`}>
            {client.name.charAt(0)}
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white shadow-sm ${
              client.status === 'online' ? 'bg-success' : 'bg-gray-300'
            }`}
          />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h3
              className="text-sm font-bold text-deep-slate truncate tracking-tight"
              title={`${client.name} (仅显示首字保护隐私)`}
            >
              {maskedName}
            </h3>
            <span className="text-[11px] text-medium-gray/70 font-medium">{client.lastMessageTime}</span>
          </div>
          <p className="text-xs text-medium-gray/80 mt-1 truncate">{client.lastMessage}</p>

          {/* Tags & Auto-reply Toggle */}
          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-1">
              {client.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="px-1.5 py-0.5 text-[10px] font-medium bg-surface text-medium-gray rounded-md border border-border-subtle"
                >
                  {tag}
                </span>
              ))}
            </div>

            {/* Auto-draft Toggle */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggleAutoDraft();
              }}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-all duration-200 ${
                client.autoDraftEnabled ? 'bg-deep-trust shadow-sm' : 'bg-gray-200 hover:bg-gray-300'
              }`}
              title={client.autoDraftEnabled ? 'AI自动草稿已开启' : 'AI自动草稿已关闭'}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform duration-200 ${
                  client.autoDraftEnabled ? 'translate-x-5' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ClientCard;
