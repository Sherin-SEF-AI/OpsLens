import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ArrowLeft } from 'lucide-react';

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center mx-auto mb-4">
          <Search size={32} className="text-blue-400/50" />
        </div>
        <h1 className="text-4xl font-bold text-dark-text mb-2">404</h1>
        <h2 className="text-lg font-semibold text-dark-text mb-2">Page Not Found</h2>
        <p className="text-sm text-dark-muted mb-6">
          The page you are looking for does not exist or has been moved.
        </p>
        <button
          onClick={() => navigate('/', { replace: true })}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
          aria-label="Go back to dashboard"
        >
          <ArrowLeft size={14} />
          Back to Dashboard
        </button>
      </div>
    </div>
  );
}
