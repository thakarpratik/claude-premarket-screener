-- PostgreSQL schema for Market Pullback Radar (generated from pullback_radar/db.py).
-- The backend creates these tables automatically on start-up (SQLAlchemy create_all).

CREATE TABLE users (
	id SERIAL NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE alert_rules (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	symbol VARCHAR(16), 
	kind VARCHAR(32) NOT NULL, 
	params JSON NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_alert_rules_user_id ON alert_rules (user_id);

CREATE TABLE alert_states (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	symbol VARCHAR(16) NOT NULL, 
	style VARCHAR(10) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	active BOOLEAN NOT NULL, 
	fingerprint VARCHAR(128), 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, symbol, style, kind), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_alert_states_user_id ON alert_states (user_id);

CREATE TABLE alerts (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	symbol VARCHAR(16) NOT NULL, 
	style VARCHAR(10) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	message TEXT NOT NULL, 
	price FLOAT, 
	price_time VARCHAR(40), 
	link VARCHAR(200) NOT NULL, 
	synthetic BOOLEAN NOT NULL, 
	read BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_alerts_created_at ON alerts (created_at);
CREATE INDEX ix_alerts_user_id ON alerts (user_id);

CREATE TABLE auth_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE backtest_runs (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	style VARCHAR(10) NOT NULL, 
	params JSON NOT NULL, 
	result JSON NOT NULL, 
	synthetic BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_backtest_runs_user_id ON backtest_runs (user_id);

CREATE TABLE scan_runs (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	mode VARCHAR(8) NOT NULL, 
	synthetic BOOLEAN NOT NULL, 
	ok BOOLEAN NOT NULL, 
	trigger VARCHAR(16) NOT NULL, 
	summary JSON NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scan_runs_user_id ON scan_runs (user_id);

CREATE TABLE trades (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	mode VARCHAR(8) NOT NULL, 
	symbol VARCHAR(16) NOT NULL, 
	style VARCHAR(10) NOT NULL, 
	strategy VARCHAR(80), 
	side VARCHAR(5) NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	entry_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	entry_price FLOAT NOT NULL, 
	quantity FLOAT NOT NULL, 
	stop FLOAT, 
	target1 FLOAT, 
	target2 FLOAT, 
	exit_time TIMESTAMP WITH TIME ZONE, 
	exit_price FLOAT, 
	exit_reason VARCHAR(40), 
	fees FLOAT NOT NULL, 
	rationale TEXT, 
	notes TEXT, 
	screenshot VARCHAR(255), 
	setup_snapshot JSON, 
	synthetic_data BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_trades_user_id ON trades (user_id);

CREATE TABLE user_settings (
	user_id INTEGER NOT NULL, 
	data JSON NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE watchlist_items (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	symbol VARCHAR(16) NOT NULL, 
	style VARCHAR(10) NOT NULL, 
	note TEXT, 
	added_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	plan_snapshot JSON, 
	last_status VARCHAR(24), 
	last_price FLOAT, 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, symbol, style), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_watchlist_items_user_id ON watchlist_items (user_id);

