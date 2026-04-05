import React, { useState } from 'react';
import { Shield, Eye, EyeOff, Lock, User } from 'lucide-react';
import { loginAndLaunchGateway } from '../../services/api';

interface LoginPageProps {
  onLogin: (username: string, password: string) => void;
  error?: string;
}

export const LoginPage: React.FC<LoginPageProps> = ({ onLogin, error }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loginError, setLoginError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) return;
    
    setIsLoading(true);
    setLoginError('');

    try {
      // Call the launcher login API — it starts the global gateway on demand
      await loginAndLaunchGateway(username.trim(), password);
      onLogin(username.trim(), password);
    } catch (err) {
      const message = err instanceof Error ? err.message : '无法连接到 Nanobot 网关';
      setLoginError(message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-blue-50/30 flex items-center justify-center p-4">
      {/* Background Pattern */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/4 -right-1/4 w-[800px] h-[800px] bg-gradient-to-br from-deep-trust/[0.04] to-transparent rounded-full blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] bg-gradient-to-tr from-warm-navy/[0.04] to-transparent rounded-full blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[400px] bg-deep-trust/[0.02] rounded-full blur-3xl" />
      </div>

      {/* Login Card */}
      <div className="relative w-full max-w-[420px] bg-white/95 backdrop-blur-sm rounded-2xl shadow-elevated border border-white/80 p-8 md:p-10">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-deep-trust to-warm-navy flex items-center justify-center mb-4 shadow-lg shadow-deep-trust/25 ring-1 ring-deep-trust/10">
            <Shield className="w-8 h-8 text-white" strokeWidth={1.5} />
          </div>
          <h1 className="text-2xl font-bold text-gradient-brand tracking-tight">
            InsureAI
          </h1>
          <p className="text-sm text-medium-gray mt-1.5 tracking-wide">智能销售助手系统</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Username */}
          <div>
            <label className="block text-sm font-medium text-deep-slate mb-1.5">
              用户名
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-medium-gray" strokeWidth={1.5} />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                className="w-full pl-10 pr-4 py-2.5 bg-surface-warm border border-border-light rounded-[10px] text-sm text-deep-slate placeholder:text-medium-gray/50 focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:bg-white input-glow transition-all"
                autoFocus
              />
            </div>
          </div>

          {/* Password */}
          <div>
            <label className="block text-sm font-medium text-deep-slate mb-1.5">
              密码
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-medium-gray" strokeWidth={1.5} />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                className="w-full pl-10 pr-11 py-2.5 bg-surface-warm border border-border-light rounded-[10px] text-sm text-deep-slate placeholder:text-medium-gray/50 focus:outline-none focus:ring-2 focus:ring-deep-trust/15 focus:border-deep-trust/40 focus:bg-white input-glow transition-all"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-medium-gray hover:text-deep-slate transition-colors"
              >
                {showPassword ? (
                  <EyeOff className="w-4 h-4" strokeWidth={1.5} />
                ) : (
                  <Eye className="w-4 h-4" strokeWidth={1.5} />
                )}
              </button>
            </div>
          </div>

          {/* Error Message */}
          {(error || loginError) && (
            <div className="p-3 bg-safety-red/10 border border-safety-red/20 rounded-subtle">
              <p className="text-sm text-safety-red">{error || loginError}</p>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={isLoading || !username.trim() || !password.trim()}
            className="w-full py-2.5 bg-deep-trust text-white text-sm font-semibold rounded-[10px] hover:bg-deep-trust/90 hover:shadow-lg hover:shadow-deep-trust/20 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 disabled:hover:shadow-none"
          >
            {isLoading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                登录中...
              </span>
            ) : (
              '登录'
            )}
          </button>
        </form>

        {/* Access Hint */}
        <div className="mt-6 p-3 bg-surface-warm rounded-[10px] border border-border-subtle">
          <p className="text-xs text-medium-gray text-center">
            登录后系统会自动连接 Nanobot 与 WhatsApp 会话
          </p>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center">
          <p className="text-[11px] text-medium-gray/70 tracking-wide">
            © 2024 InsureAI · 保险智能销售平台
          </p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
