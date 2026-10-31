"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearSession, getUser } from "@/lib/auth";
import type { User } from "@/lib/types";

const links = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/projects", label: "Projects" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const current = getUser();
    if (!current) {
      router.replace("/login");
      return;
    }
    setUser(current);
  }, [router]);

  function logout() {
    clearSession();
    router.replace("/login");
  }

  if (!user) {
    return <div className="auth-shell">Loading…</div>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          StoreFinder
          <span>Shopify lead discovery</span>
        </div>
        <nav className="nav">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={pathname.startsWith(link.href) ? "active" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user">
            <div>{user.name}</div>
            <div>{user.email}</div>
          </div>
          <button type="button" className="btn ghost" onClick={logout}>
            Log out
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
