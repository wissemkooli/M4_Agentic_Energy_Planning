import { Route, Routes } from "react-router-dom";
import { HomeRedirect, RequireAdmin, RequireAuth } from "./auth/guards";
import Layout from "./components/Layout";
import AccessDenied from "./pages/AccessDenied";
import Comparison from "./pages/Comparison";
import Dashboard from "./pages/Dashboard";
import DataSources from "./pages/DataSources";
import HistoryPage from "./pages/History";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import PlanningAgent from "./pages/PlanningAgent";
import RunResults from "./pages/RunResults";
import RunsList from "./pages/RunsList";
import ScenarioBuilder from "./pages/ScenarioBuilder";
import StrategiesList from "./pages/StrategiesList";
import StrategyDetail from "./pages/StrategyDetail";
import AdminDashboard from "./pages/admin/AdminDashboard";
import Decisions from "./pages/admin/Decisions";
import ReviewQueue from "./pages/admin/ReviewQueue";
import UsersPage from "./pages/admin/Users";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<HomeRedirect />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/agent" element={<PlanningAgent />} />
          <Route path="/agent/:requestId" element={<PlanningAgent />} />
          <Route path="/scenarios" element={<ScenarioBuilder />} />
          <Route path="/runs" element={<RunsList />} />
          <Route path="/runs/:runId" element={<RunResults />} />
          <Route path="/strategies" element={<StrategiesList />} />
          <Route path="/strategies/:strategyId" element={<StrategyDetail />} />
          <Route path="/compare" element={<Comparison />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/data" element={<DataSources />} />
          <Route path="/access-denied" element={<AccessDenied />} />
          <Route element={<RequireAdmin />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/review" element={<ReviewQueue />} />
            <Route path="/admin/decisions" element={<Decisions />} />
            <Route path="/admin/users" element={<UsersPage />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
