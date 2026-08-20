import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Chats from "./pages/Chats";
import Orders from "./pages/Orders";
import Catalog from "./pages/Catalog";
import Services from "./pages/Services";
import Doctors from "./pages/Doctors";
import Prompts from "./pages/Prompts";
import Conversation from "./pages/Conversation";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="centered muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/tenants/:tenant/chats" element={<Chats />} />
        <Route path="/tenants/:tenant/orders" element={<Orders />} />
        <Route path="/tenants/:tenant/catalog" element={<Catalog />} />
        <Route path="/tenants/:tenant/services" element={<Services />} />
        <Route path="/tenants/:tenant/doctors" element={<Doctors />} />
        <Route path="/tenants/:tenant/prompts" element={<Prompts />} />
        <Route
          path="/tenants/:tenant/conversations/:channel/:chatId"
          element={<Conversation />}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
