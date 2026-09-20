import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  FileText,
  Upload,
  Search,
  Users,
  LogOut,
} from "lucide-react";

function Sidebar() {
  const location = useLocation();

  const menuItems = [
    {
      name: "Dashboard",
      path: "/dashboard",
      icon: <LayoutDashboard size={20} />,
    },
    {
      name: "Documents",
      path: "/documents",
      icon: <FileText size={20} />,
    },
    {
      name: "Upload Document",
      path: "/upload",
      icon: <Upload size={20} />,
    },
    {
      name: "Search",
      path: "/search",
      icon: <Search size={20} />,
    },
    {
      name: "Users",
      path: "/users",
      icon: <Users size={20} />,
    },
  ];

  return (
    <div className="sidebar">
      <div className="logo">
        <h2>SecureDocs</h2>
        <p>Document Management</p>
      </div>

      <div className="menu">
        {menuItems.map((item) => (
          <Link
            key={item.name}
            to={item.path}
            className={
              location.pathname === item.path
                ? "menu-item active"
                : "menu-item"
            }
          >
            {item.icon}
            <span>{item.name}</span>
          </Link>
        ))}
      </div>

      <Link to="/" className="logout">
        <LogOut size={20} />
        Logout
      </Link>
    </div>
  );
}

export default Sidebar;