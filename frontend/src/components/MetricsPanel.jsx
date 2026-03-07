import React, { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import { api } from '../api/client';

const SEVERITY_COLORS = {
  'P0-Critical': '#ff4444',
  'P1-High': '#ff8c00',
  'P2-Medium': '#ffd700',
  'P3-Low': '#4169e1',
};

const STATUS_COLORS = ['#22c55e', '#ff4444'];

export default function MetricsPanel() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.getStats().then(setStats).catch(console.error);
    const interval = setInterval(() => {
      api.getStats().then(setStats).catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  const severityData = Object.entries(stats.by_severity).map(([name, value]) => ({
    name,
    count: value,
    fill: SEVERITY_COLORS[name] || '#808080',
  }));

  const serviceData = Object.entries(stats.by_service)
    .map(([name, value]) => ({ name, count: value }))
    .sort((a, b) => b.count - a.count);

  const pieData = [
    { name: 'Resolved', value: stats.resolved },
    { name: 'Active', value: stats.active },
  ];

  const mttrData = Object.entries(stats.mttr_by_severity).map(([name, value]) => ({
    name: name.split('-')[0],
    minutes: Math.round(value / 60),
  }));

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <p className="text-xs text-dark-muted">Total Incidents</p>
          <p className="text-2xl font-bold text-dark-text mt-1">{stats.total}</p>
        </div>
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <p className="text-xs text-dark-muted">Active</p>
          <p className="text-2xl font-bold text-red-400 mt-1">{stats.active}</p>
        </div>
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <p className="text-xs text-dark-muted">Resolved</p>
          <p className="text-2xl font-bold text-green-400 mt-1">{stats.resolved}</p>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-2 gap-4">
        {/* By Severity */}
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <h3 className="text-sm font-medium text-dark-muted mb-3">By Severity</h3>
          {severityData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={severityData}>
                <XAxis dataKey="name" tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1a1d23', border: '1px solid #2a2d35', borderRadius: 8, fontSize: 12 }}
                />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {severityData.map((entry, i) => (
                    <Cell key={i} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-dark-muted text-sm text-center py-8">No data</p>
          )}
        </div>

        {/* Active vs Resolved */}
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <h3 className="text-sm font-medium text-dark-muted mb-3">Active vs Resolved</h3>
          {stats.total > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={70}
                  dataKey="value"
                  stroke="none"
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={STATUS_COLORS[i]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1a1d23', border: '1px solid #2a2d35', borderRadius: 8, fontSize: 12 }}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-dark-muted text-sm text-center py-8">No data</p>
          )}
        </div>

        {/* By Service */}
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <h3 className="text-sm font-medium text-dark-muted mb-3">By Service</h3>
          {serviceData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={serviceData} layout="vertical">
                <XAxis type="number" tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis dataKey="name" type="category" tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} width={100} />
                <Tooltip
                  contentStyle={{ background: '#1a1d23', border: '1px solid #2a2d35', borderRadius: 8, fontSize: 12 }}
                />
                <Bar dataKey="count" fill="#4169e1" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-dark-muted text-sm text-center py-8">No data</p>
          )}
        </div>

        {/* MTTR */}
        <div className="bg-dark-card rounded-xl p-4 border border-dark-border">
          <h3 className="text-sm font-medium text-dark-muted mb-3">MTTR (minutes)</h3>
          {mttrData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={mttrData}>
                <XAxis dataKey="name" tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1a1d23', border: '1px solid #2a2d35', borderRadius: 8, fontSize: 12 }}
                />
                <Bar dataKey="minutes" fill="#22c55e" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-dark-muted text-sm text-center py-8">No MTTR data yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
