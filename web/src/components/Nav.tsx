import { NavLink } from "react-router-dom";

const LINKS: [string, string][] = [
  ["/", "Today"], ["/meetings", "Meetings"], ["/library", "Library"], ["/ask", "Ask across meetings"], ["/settings", "Settings"],
];

export default function Nav() {
  return (
    <nav aria-label="Main" className="nav">
      <div className="brand"><span className="brand-dot" />Fleck</div>
      {LINKS.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
    </nav>
  );
}
