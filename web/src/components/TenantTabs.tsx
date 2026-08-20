import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
import type { Tenant } from "../types";

/** Secondary nav for a tenant's views. Chats is always present; Orders /
 *  Catalogue appear only for tenants whose registry entry advertises them. */
export default function TenantTabs({ tenant }: { tenant: string }) {
  const [info, setInfo] = useState<Tenant | null>(null);

  useEffect(() => {
    api
      .tenants()
      .then((ts) => setInfo(ts.find((t) => t.id === tenant) ?? null))
      .catch(() => setInfo(null));
  }, [tenant]);

  return (
    <nav className="subnav">
      <NavLink to={`/tenants/${tenant}/chats`}>Chats</NavLink>
      {info?.has_orders && (
        <NavLink to={`/tenants/${tenant}/orders`}>Orders</NavLink>
      )}
      {info?.has_catalog && (
        <NavLink to={`/tenants/${tenant}/catalog`}>Catalogue</NavLink>
      )}
      {info?.has_services && (
        <NavLink to={`/tenants/${tenant}/services`}>Services</NavLink>
      )}
      {info?.has_doctors && (
        <NavLink to={`/tenants/${tenant}/doctors`}>Doctors</NavLink>
      )}
      {info?.has_prompts && (
        <NavLink to={`/tenants/${tenant}/prompts`}>Prompts</NavLink>
      )}
    </nav>
  );
}
