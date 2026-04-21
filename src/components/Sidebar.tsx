import { TABS, type TabId } from "../router";

interface SidebarProps {
  activeTab: TabId;
  onTabChange: (id: TabId) => void;
  onSettings: () => void;
}

export default function Sidebar({ activeTab, onTabChange, onSettings }: SidebarProps) {
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">BETTER DISCOVERY</div>

      <ul className="sidebar-tabs">
        {TABS.map((tab) => (
          <li
            key={tab.id}
            className={`sidebar-tab${activeTab === tab.id ? " active" : ""}`}
            onClick={() => onTabChange(tab.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && onTabChange(tab.id)}
            aria-current={activeTab === tab.id ? "page" : undefined}
          >
            <span className="sidebar-icon" aria-hidden="true">{tab.icon}</span>
            <span>{tab.label}</span>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        <button className="sidebar-settings-btn" onClick={onSettings} title="Settings">
          <span aria-hidden="true">⚙</span>
          <span>Settings</span>
        </button>
      </div>
    </nav>
  );
}
