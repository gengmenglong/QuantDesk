import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Markets } from './pages/Markets';
import { Trading } from './pages/Trading';
import { Strategies } from './pages/Strategies';
import { AIEvaluation } from './pages/AIEvaluation';
import { LedgerPage } from './pages/LedgerPage';
import { Settings } from './pages/Settings';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/trading" element={<Trading />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/ai" element={<AIEvaluation />} />
          <Route path="/ledger" element={<LedgerPage />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
