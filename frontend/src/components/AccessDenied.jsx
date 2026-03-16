import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, ArrowLeft } from 'lucide-react';

export default function AccessDenied() {
  const navigate = useNavigate();

  return (
    <div className="flex items-center justify-center min-h-[60vh]" role="alert">
      <div className="text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center mx-auto mb-4">
          <Lock size={32} className="text-red-400" />
        </div>
        <h1 className="text-xl font-bold text-dark-text mb-2">Access Denied</h1>
        <p className="text-sm text-dark-muted mb-6">
          You don't have permission to view this page. Contact your administrator if you believe this is a mistake.
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
