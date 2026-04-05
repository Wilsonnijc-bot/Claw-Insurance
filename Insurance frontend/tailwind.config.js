/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Prudential-inspired Design System
        'deep-trust': '#003399',
        'deep-trust-light': '#1a4fc4',
        'warm-navy': '#1e3a8a',
        'warm-navy-light': '#2b4ea0',
        'safety-red': '#dc2626',
        'deep-slate': '#0f172a',
        'medium-gray': '#64748b',
        'light-gray': '#f8fafc',
        'surface': '#f1f5f9',
        'surface-warm': '#f6f8fb',
        'border-light': '#e2e8f0',
        'border-subtle': '#edf2f7',
        'success': '#10b981',
        'warning': '#f59e0b',
        'ai-blue': '#eff6ff',
        'ai-blue-border': '#bfdbfe',
      },
      fontFamily: {
        sans: ['Inter', 'SF Pro Display', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['SF Mono', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        'soft': '0 4px 6px -1px rgba(0, 0, 0, 0.05)',
        'card': '0 1px 3px 0 rgba(0, 0, 0, 0.05)',
        'card-hover': '0 4px 12px -2px rgba(0, 0, 0, 0.08), 0 2px 4px -1px rgba(0, 0, 0, 0.04)',
        'elevated': '0 8px 24px -4px rgba(0, 0, 0, 0.08), 0 2px 8px -2px rgba(0, 0, 0, 0.04)',
        'header': '0 1px 3px 0 rgba(0, 0, 0, 0.04), 0 1px 2px -1px rgba(0, 0, 0, 0.03)',
        'inner-glow': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.05)',
        'blue-glow': '0 0 0 3px rgba(0, 51, 153, 0.08)',
        'modal': '0 20px 60px -12px rgba(0, 0, 0, 0.15), 0 8px 20px -8px rgba(0, 0, 0, 0.1)',
      },
      borderRadius: {
        'subtle': '8px',
        'card': '12px',
        'modal': '24px',
      },
      animation: {
        'bounce-dot': 'bounce 1.2s ease-in-out infinite',
        'pulse-ring': 'ripple 2s infinite',
        'pulse-soft': 'pulse-soft 2s infinite',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'fade-out': 'fadeOut 0.3s ease-out',
        'shimmer': 'shimmer 2s linear infinite',
        'fade-in': 'fadeIn 0.4s ease-out',
      },
      keyframes: {
        bounce: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-4px)' },
        },
        ripple: {
          '0%': { transform: 'scale(1)', opacity: '0.8' },
          '50%': { transform: 'scale(1.1)', opacity: '1' },
          '100%': { transform: 'scale(1)', opacity: '0.8' },
        },
        'pulse-soft': {
          '0%, 100%': { opacity: '0.6' },
          '50%': { opacity: '1' },
        },
        slideInRight: {
          '0%': { transform: 'translateX(20px)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        fadeOut: {
          '0%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
