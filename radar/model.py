"""L2-regularised logistic regression (Newton's method, numpy only) plus the daily
model-selection routine that decides how heavily to weight recent days."""
import numpy as np


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


class LogReg:
    def __init__(self, names):
        self.names = list(names)

    def _prep(self, X):
        X = np.where(np.isnan(X), self.med, X)
        X = np.clip(X, self.lo, self.hi)
        return (X - self.mu) / self.sd

    def fit(self, X, y, w=None, l2=2.0):
        w = np.ones(len(y)) if w is None else w / w.mean()
        self.lo = np.nanpercentile(X, 1, axis=0)
        self.hi = np.nanpercentile(X, 99, axis=0)
        self.med = np.nanmedian(X, axis=0)
        Xc = np.clip(np.where(np.isnan(X), self.med, X), self.lo, self.hi)
        self.mu, self.sd = Xc.mean(0), Xc.std(0) + 1e-9
        A = np.hstack([np.ones((len(y), 1)), (Xc - self.mu) / self.sd])
        beta = np.zeros(A.shape[1])
        beta[0] = np.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6))
        reg = np.full(A.shape[1], l2)
        reg[0] = 0
        for _ in range(50):
            p = sigmoid(A @ beta)
            g = A.T @ (w * (p - y)) + reg * beta
            H = (A.T * (w * p * (1 - p))) @ A + np.diag(reg)
            step = np.linalg.solve(H, g)
            beta -= step
            if np.abs(step).max() < 1e-7:
                break
        self.b0, self.coef = beta[0], beta[1:]
        return self

    def predict(self, X):
        return sigmoid(self.b0 + self._prep(np.atleast_2d(X)) @ self.coef)

    def contributions(self, x):
        """Per-feature push on the log-odds for one row (positive = raises probability)."""
        return self._prep(np.atleast_2d(x))[0] * self.coef

    def to_dict(self):
        return {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                for k, v in dict(names=self.names, lo=self.lo, hi=self.hi, med=self.med,
                                 mu=self.mu, sd=self.sd, b0=float(self.b0), coef=self.coef).items()}

    @classmethod
    def from_dict(cls, d):
        m = cls(d["names"])
        for k in ("lo", "hi", "med", "mu", "sd", "coef"):
            setattr(m, k, np.array(d[k]))
        m.b0 = d["b0"]
        return m


def logloss(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(y, p):
    y = np.asarray(y)
    pos, neg = y.sum(), len(y) - y.sum()
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = np.argsort(np.argsort(p)) + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def recency_weights(day_index, half_life):
    """day_index: integer days-ago (0 = most recent). half_life None = equal weights."""
    if half_life is None:
        return np.ones(len(day_index))
    return 0.5 ** (day_index / half_life)


def select_and_fit(X, y, days_ago, extra_w, holdout_days, metric="logloss"):
    """Pick the recency half-life that best predicts the most recent `holdout_days`
    (trained only on data before them), then refit on everything with that choice."""
    hold = days_ago < holdout_days
    train = ~hold
    best = None
    for hl in (None, 500, 250, 120, 60):
        w = recency_weights(days_ago[train] - holdout_days, hl) * extra_w[train]
        m = LogReg(range(X.shape[1])).fit(X[train], y[train], w)
        p = m.predict(X[hold])
        score = logloss(y[hold], p) if metric == "logloss" else -np.mean((p > 0.5) == y[hold])
        if best is None or score < best[0]:
            best = (score, hl, p)
    score, hl, p_hold = best
    base = y[train].mean()
    stats = dict(
        half_life=hl,
        holdout_logloss=logloss(y[hold], p_hold),
        baseline_logloss=logloss(y[hold], np.full(hold.sum(), base)),
        holdout_auc=auc(y[hold], p_hold),
        holdout_acc=float(np.mean((p_hold > 0.5) == y[hold])),
        baseline_acc=float(max(y[hold].mean(), 1 - y[hold].mean())),
        base_rate=float(y.mean()),
    )
    final = LogReg(range(X.shape[1])).fit(X, y, recency_weights(days_ago, hl) * extra_w)
    return final, stats
