import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      navigate("/");
    } catch {
      setError("Invalid credentials");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="centered">
      <form className="card login" onSubmit={onSubmit}>
        <h1>
          ai-sales <span className="brand-sub">admin</span>
        </h1>
        <label>
          Username
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button disabled={busy} type="submit">
          {busy ? "…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
