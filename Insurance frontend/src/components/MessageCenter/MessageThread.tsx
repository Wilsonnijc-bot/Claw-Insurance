import React from 'react';
import { Bot } from 'lucide-react';
import { AIThinkingLoader } from './AIThinkingLoader';
import type { Message } from '../../types';

interface MessageThreadProps {
  messages: Message[];
  clientName: string;
  isAILoading: boolean;
}

export const MessageThread: React.FC<MessageThreadProps> = ({
  messages,
  clientName,
  isAILoading,
}) => {
  const maskedName = clientName.charAt(0) + '**';

  return (
    <div className="flex-1 overflow-y-auto p-5 space-y-4 bg-gradient-to-b from-white to-slate-50/30">
      {/* Welcome Message */}
      {messages.length === 0 && (
        <div className="text-center py-12">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-gradient-to-br from-deep-trust/[0.06] to-deep-trust/[0.02] flex items-center justify-center border border-deep-trust/10">
            <Bot className="w-8 h-8 text-deep-trust" strokeWidth={1.5} />
          </div>
          <h3 className="text-lg font-bold text-deep-slate mb-1 tracking-tight">
            开始与 {maskedName} 的对话
          </h3>
          <p className="text-sm text-medium-gray/80">AI助手将帮助您提供专业的保险建议</p>
        </div>
      )}

      {/* Messages */}
      {messages.map((message, index) => {
        const isClient = message.sender === 'client';
        const isAI = message.sender === 'ai';
        const showAvatar = index === 0 || messages[index - 1].sender !== message.sender;

        return (
          <div
            key={message.id}
            className={`flex ${isClient ? 'justify-start' : 'justify-end'} animate-slide-in-right`}
            style={{ animationDelay: `${index * 50}ms` }}
          >
            <div className={`flex max-w-[80%] ${isClient ? 'flex-row' : 'flex-row-reverse'} gap-2`}>
              {/* Avatar */}
              {showAvatar && (
                <div className="flex-shrink-0">
                  {isClient ? (
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center text-white text-xs font-semibold shadow-sm">
                      {clientName.charAt(0)}
                    </div>
                  ) : isAI ? (
                    <div className="w-8 h-8 rounded-full bg-ai-blue border border-ai-blue-border flex items-center justify-center shadow-sm">
                      <Bot className="w-4 h-4 text-deep-trust" strokeWidth={1.5} />
                    </div>
                  ) : (
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-warm-navy to-deep-trust flex items-center justify-center text-white text-xs font-semibold shadow-sm">
                      我
                    </div>
                  )}
                </div>
              )}

              {/* Message Bubble */}
              <div className="flex flex-col">
                <div
                  className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed ${
                    isClient
                      ? 'bg-white border border-border-light text-deep-slate rounded-tl-sm shadow-card'
                      : isAI
                      ? 'bg-ai-blue border border-ai-blue-border text-deep-slate rounded-tr-sm shadow-card'
                      : 'bg-deep-trust text-white rounded-tr-sm shadow-sm shadow-deep-trust/15'
                  }`}
                >
                  {message.content}
                </div>
                <span className={`text-[10px] text-medium-gray mt-1 ${isClient ? 'ml-1' : 'mr-1 text-right'}`}>
                  {message.timestamp}
                </span>
              </div>
            </div>
          </div>
        );
      })}

      {/* AI Thinking Loader */}
      {isAILoading && (
        <div className="animate-slide-in-right">
          <AIThinkingLoader />
        </div>
      )}
    </div>
  );
};

export default MessageThread;
