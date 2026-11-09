import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="card mx-auto mt-16 max-w-lg p-10 text-center">
      <div className="text-4xl font-semibold text-gold">404</div>
      <p className="mt-2 text-sm text-ink-soft">This page does not exist.</p>
      <Link to="/" className="btn-outline mt-6">Home</Link>
    </div>
  );
}
