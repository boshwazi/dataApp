import { NavLink, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">AI Data Analytics</div>
        <nav>
          <NavLink to="/" end>
            Home
          </NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
