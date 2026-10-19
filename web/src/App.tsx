import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { getToken, setToken } from "./api";
import BookPage from "./pages/Book";
import ChatPage from "./pages/Chat";
import LoginPage from "./pages/Login";
import PlanPage from "./pages/Plan";
import ResultsPage from "./pages/Results";
import SearchPage from "./pages/Search";
import TripsPage from "./pages/Trips";

function RequireAuth({ authed, children }: { authed: boolean; children: JSX.Element }) {
  const loc = useLocation();
  // Keep the full target (including ?offers=... from chat/WhatsApp links) through login.
  return authed ? children : <Navigate to="/login" state={{ next: loc.pathname + loc.search }} replace />;
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  useEffect(() => {
    const out = () => setAuthed(false);
    window.addEventListener("wanderly:logout", out);
    return () => window.removeEventListener("wanderly:logout", out);
  }, []);

  return (
    <>
      <header className="nav">
        <Link to="/" className="brand">
          Wanderly
        </Link>
        <nav>
          <NavLink to="/">Search</NavLink>
          <NavLink to="/plan">Plan</NavLink>
          <NavLink to="/assistant">Assistant</NavLink>
          <NavLink to="/trips">My trips</NavLink>
          {authed ? (
            <a href="#logout" onClick={() => { setToken(null); setAuthed(false); }}>Log out</a>
          ) : (
            <NavLink to="/login">Log in</NavLink>
          )}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/results" element={<ResultsPage />} />
          <Route path="/plan" element={<RequireAuth authed={authed}><PlanPage /></RequireAuth>} />
          <Route path="/assistant" element={<RequireAuth authed={authed}><ChatPage /></RequireAuth>} />
          <Route path="/book" element={<RequireAuth authed={authed}><BookPage /></RequireAuth>} />
          <Route path="/trips" element={<RequireAuth authed={authed}><TripsPage /></RequireAuth>} />
          <Route path="/login" element={<LoginPage onDone={() => setAuthed(true)} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
