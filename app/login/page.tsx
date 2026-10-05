import { authConfigError } from "@/lib/session";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const query = await searchParams;
  const configError = authConfigError();
  const rejected = query.error === "1";

  return (
    <main className="panel">
      <p className="kicker">Private</p>
      <h1>Move Radar</h1>
      <p className="lede">Big-move stock screener. Private to you.</p>
      <form method="post" action="/api/login">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          required
        />
        <button type="submit">Continue</button>
      </form>
      {configError ? <p className="error">{configError}</p> : null}
      {rejected ? <p className="error">That password does not match.</p> : null}
    </main>
  );
}
