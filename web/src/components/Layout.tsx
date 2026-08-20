import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import type { Tenant } from "../types";

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<Tenant[]>([]);

  useEffect(() => {
    api.tenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          ai-sales <span>admin</span>
        </Link>
        <nav className="nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          {tenants.map((t) => (
            <NavLink key={t.id} to={`/tenants/${t.id}/chats`}>
              {t.name.split("—")[0].trim()}
            </NavLink>
          ))}
        </nav>
        <div className="user">
          <span className="muted">{user}</span>
          <button className="link" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
