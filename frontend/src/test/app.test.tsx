import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "axios";
import App from "../App";
import { api } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";

const ADMIN = { id: 1, email: "admin@example.com", username: "admin", full_name: "Admin", role: "ADMIN", is_active: true };
const USER = { ...ADMIN, id: 2, username: "planner", role: "USER" };

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><AuthProvider><App /></AuthProvider></MemoryRouter>);
}

describe("application shell", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("redirects unauthenticated users to the login page", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("no session"));
    renderAt("/dashboard");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows a validation message before calling the API", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("no session"));
    const post = vi.spyOn(api, "post");
    renderAt("/login");
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/username or email/);
    expect(post).not.toHaveBeenCalled();
  });

  it("shows the backend error for invalid credentials", async () => {
    vi.spyOn(axios, "post").mockRejectedValue(new Error("no session"));
    const err = Object.assign(new Error("401"), { isAxiosError: true, response: { status: 401, data: { detail: "Invalid username/email or password." } } });
    vi.spyOn(api, "post").mockRejectedValue(err);
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Username or email"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username/email or password.");
  });

  it("blocks standard users from admin routes", async () => {
    localStorage.setItem("m4_session_hint", "1");
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "t" } });
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/api/auth/me") return { data: USER };
      throw new Error("unexpected " + url);
    });
    renderAt("/admin/review");
    expect(await screen.findByRole("heading", { name: "Access denied" })).toBeInTheDocument();
  });

  it("renders admin navigation and the empty review queue from the API", async () => {
    localStorage.setItem("m4_session_hint", "1");
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "t" } });
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/api/auth/me") return { data: ADMIN };
      if (url === "/api/admin/review-queue") return { data: [] };
      throw new Error("unexpected " + url);
    });
    renderAt("/admin/review");
    expect(await screen.findByText("No proposal awaiting review")).toBeInTheDocument();
    expect(screen.getAllByText("Review queue").length).toBeGreaterThan(0);
  });

  it("shows an error state when the backend fails", async () => {
    localStorage.setItem("m4_session_hint", "1");
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "t" } });
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/api/auth/me") return { data: USER };
      throw Object.assign(new Error("down"), { isAxiosError: true, response: undefined });
    });
    renderAt("/runs");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Cannot reach the M4 backend/));
  });

  it("shows an empty state when there are no runs", async () => {
    localStorage.setItem("m4_session_hint", "1");
    vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "t" } });
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/api/auth/me") return { data: USER };
      if (url.startsWith("/api/optimization")) return { data: [] };
      throw new Error("unexpected " + url);
    });
    renderAt("/runs");
    expect(await screen.findByText("No results yet")).toBeInTheDocument();
  });
});
