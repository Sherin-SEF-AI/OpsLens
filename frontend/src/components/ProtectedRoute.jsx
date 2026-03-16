import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useAuthStore } from '../stores/authStore';

export default function ProtectedRoute({ children, minRole }) {
  const { isAuthenticated, isLoading, user } = useAuthStore();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-dark-bg" aria-label="Checking authentication">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={32} className="animate-spin text-blue-400" />
          <p className="text-sm text-dark-muted">Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (minRole) {
    const roles = ['viewer', 'responder', 'commander', 'admin'];
    const userLevel = roles.indexOf(user?.role || 'viewer');
    const requiredLevel = roles.indexOf(minRole);
    if (userLevel < requiredLevel) {
      return <Navigate to="/access-denied" replace />;
    }
  }

  return children;
}
