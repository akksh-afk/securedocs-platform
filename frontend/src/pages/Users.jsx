import { useState } from "react";
import Sidebar from "../components/Sidebar";
import {
  Users as UsersIcon,
  Search,
  UserPlus,
  Trash2,
  Shield,
  UserCheck,
  X,
} from "lucide-react";

function Users() {
  const [searchTerm, setSearchTerm] = useState("");

  const [users, setUsers] = useState([
    {
      id: 1,
      name: "Admin User",
      email: "admin@securedocs.com",
      role: "Admin",
      status: "Active",
    },
    {
      id: 2,
      name: "John Smith",
      email: "john@example.com",
      role: "Lawyer",
      status: "Active",
    },
    {
      id: 3,
      name: "Sarah Johnson",
      email: "sarah@example.com",
      role: "User",
      status: "Active",
    },
    {
      id: 4,
      name: "Michael Brown",
      email: "michael@example.com",
      role: "User",
      status: "Inactive",
    },
  ]);

  const [showAddUser, setShowAddUser] = useState(false);

  const [showDeleteModal, setShowDeleteModal] = useState(false);

  const [userToDelete, setUserToDelete] = useState(null);

  const [newUser, setNewUser] = useState({
    name: "",
    email: "",
    role: "User",
  });

  // Search users
  const filteredUsers = users.filter((user) => {
    return (
      user.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      user.email.toLowerCase().includes(searchTerm.toLowerCase())
    );
  });

  // Open delete confirmation popup
  const deleteUser = (user) => {
    setUserToDelete(user);
    setShowDeleteModal(true);
  };

  // Confirm delete
  const confirmDelete = () => {
    if (!userToDelete) return;

    setUsers(
      users.filter((user) => user.id !== userToDelete.id)
    );

    setShowDeleteModal(false);
    setUserToDelete(null);
  };

  // Cancel delete
  const cancelDelete = () => {
    setShowDeleteModal(false);
    setUserToDelete(null);
  };

  // Add user
  const handleAddUser = (e) => {
    e.preventDefault();

    if (!newUser.name || !newUser.email) {
      return;
    }

    const userToAdd = {
      id: Date.now(),
      name: newUser.name,
      email: newUser.email,
      role: newUser.role,
      status: "Active",
    };

    setUsers([...users, userToAdd]);

    setNewUser({
      name: "",
      email: "",
      role: "User",
    });

    setShowAddUser(false);
  };

  // Role icons
  const getRoleIcon = (role) => {
    if (role === "Admin") {
      return <Shield size={15} />;
    }

    return <UserCheck size={15} />;
  };

  return (
    <div className="app-layout">
      <Sidebar />

      <main className="main-content">
        {/* PAGE HEADER */}
        <div className="page-header">
          <div>
            <h1>User Management</h1>
            <p>Manage users and their access permissions.</p>
          </div>

          <button
            className="primary-button add-user-button"
            onClick={() => setShowAddUser(true)}
          >
            <UserPlus size={18} />
            Add User
          </button>
        </div>

        {/* TOOLBAR */}
        <div className="users-toolbar">
          <div className="user-search-box">
            <Search size={20} />

            <input
              type="text"
              placeholder="Search users..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="user-count">
            <UsersIcon size={18} />
            {filteredUsers.length} Users
          </div>
        </div>

        {/* USERS TABLE */}
        <div className="users-table-container">
          <table className="users-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>

            <tbody>
              {filteredUsers.map((user) => (
                <tr key={user.id}>
                  <td>
                    <div className="user-profile">
                      <div className="user-avatar">
                        {user.name.charAt(0).toUpperCase()}
                      </div>

                      <div>
                        <h4>{user.name}</h4>
                        <p>{user.email}</p>
                      </div>
                    </div>
                  </td>

                  {/* ROLE */}
                  <td>
                    <span
                      className={`role-badge ${user.role.toLowerCase()}`}
                    >
                      {getRoleIcon(user.role)}
                      {user.role}
                    </span>
                  </td>

                  {/* STATUS */}
                  <td>
                    <span
                      className={
                        user.status === "Active"
                          ? "status active-status"
                          : "status inactive-status"
                      }
                    >
                      {user.status}
                    </span>
                  </td>

                  {/* DELETE BUTTON */}
                  <td>
                    <button
                      className="delete-user-button"
                      onClick={() => deleteUser(user)}
                      title="Remove User"
                    >
                      <Trash2 size={18} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* ADD USER MODAL */}
        {showAddUser && (
          <div className="modal-overlay">
            <div className="user-modal">
              <div className="modal-header">
                <div>
                  <h2>Add New User</h2>
                  <p>Create a new user account.</p>
                </div>

                <button
                  className="close-modal"
                  onClick={() => setShowAddUser(false)}
                >
                  <X size={20} />
                </button>
              </div>

              <form onSubmit={handleAddUser}>
                <div className="form-group">
                  <label>Full Name</label>

                  <input
                    type="text"
                    placeholder="Enter full name"
                    value={newUser.name}
                    onChange={(e) =>
                      setNewUser({
                        ...newUser,
                        name: e.target.value,
                      })
                    }
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Email Address</label>

                  <input
                    type="email"
                    placeholder="Enter email address"
                    value={newUser.email}
                    onChange={(e) =>
                      setNewUser({
                        ...newUser,
                        email: e.target.value,
                      })
                    }
                    required
                  />
                </div>

                <div className="form-group">
                  <label>User Role</label>

                  <select
                    value={newUser.role}
                    onChange={(e) =>
                      setNewUser({
                        ...newUser,
                        role: e.target.value,
                      })
                    }
                  >
                    <option value="Admin">Admin</option>
                    <option value="Lawyer">Lawyer</option>
                    <option value="User">User</option>
                  </select>
                </div>

                <div className="modal-actions">
                  <button
                    type="button"
                    className="cancel-button"
                    onClick={() => setShowAddUser(false)}
                  >
                    Cancel
                  </button>

                  <button type="submit" className="primary-button">
                    Add User
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* DELETE CONFIRMATION MODAL */}
        {showDeleteModal && userToDelete && (
          <div className="modal-overlay">
            <div className="delete-modal">
              <div className="delete-icon">
                <Trash2 size={32} />
              </div>

              <h2>Delete User?</h2>

              <p>
                Are you sure you want to delete
                <strong> {userToDelete.name}</strong>?
              </p>

              <p className="delete-warning">
                This action cannot be undone.
              </p>

              <div className="delete-modal-actions">
                <button
                  className="cancel-button"
                  onClick={cancelDelete}
                >
                  Cancel
                </button>

                <button
                  className="confirm-delete-button"
                  onClick={confirmDelete}
                >
                  Delete User
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default Users;