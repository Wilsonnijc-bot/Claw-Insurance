import React, { useMemo } from 'react';
import { AIThinkingLoader } from './AIThinkingLoader';
import { getMessagesViewUrl } from '../../services/api';

interface MessageThreadProps {
  clientId: string;
  clientName: string;
  isAILoading: boolean;
  reloadToken: number;
}

export const MessageThread: React.FC<MessageThreadProps> = ({
  clientId,
  clientName,
  isAILoading,
  reloadToken,
}) => {
  const transcriptUrl = useMemo(
    () => getMessagesViewUrl(clientId, reloadToken),
    [clientId, reloadToken]
  );

  return (
    <div className="relative flex-1 overflow-hidden bg-gradient-to-b from-white to-slate-50/30">
      <iframe
        key={transcriptUrl}
        src={transcriptUrl}
        title={`${clientName} transcript`}
        className="h-full w-full border-0 bg-transparent"
      />

      {isAILoading && (
        <div className="pointer-events-none absolute inset-x-5 bottom-5 z-10">
          <AIThinkingLoader />
        </div>
      )}
    </div>
  );
};

export default MessageThread;
