import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom';
import { BenchmarksPage } from './pages/Benchmarks';
import { Dashboard } from './pages/Dashboard';
import { ObservabilityPage } from './pages/Observability';

const DISCLAIMER =
  'Independent educational/research project inspired by publicly discussed database and ' +
  'infrastructure concepts. Not affiliated with, endorsed by, or a reproduction of Meta’s ' +
  'proprietary systems.';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header className="app-header">
          <div className="brand">
            <span className="brand-mark">MetaScale</span>
            <span className="brand-sub">Architecture Control Center</span>
          </div>
          <nav className="nav">
            <NavLink to="/dashboard" className={({ isActive }) => (isActive ? 'active' : '')}>
              Dashboard
            </NavLink>
            <NavLink to="/observability" className={({ isActive }) => (isActive ? 'active' : '')}>
              Observability
            </NavLink>
            <NavLink to="/benchmarks" className={({ isActive }) => (isActive ? 'active' : '')}>
              Benchmarks
            </NavLink>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/observability" element={<ObservabilityPage />} />
            <Route path="/benchmarks" element={<BenchmarksPage />} />
          </Routes>
        </main>
        <footer className="app-footer">
          <span>{DISCLAIMER}</span>
        </footer>
      </div>
    </BrowserRouter>
  );
}
